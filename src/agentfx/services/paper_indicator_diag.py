"""Per-bar live-vs-backtest entry-condition diagnostic (AFX-48).

The paper runner evaluates a genome's entry conditions on a
fold-anchored candle window every tick. When a validated genome goes
silent live (AFX-48: `stoch(16,3) <= 20.3` / `stoch(14,3) > 80` logged
0 trades on AUD_USD M30 while its OOS fold logged 189), the operator
needs to know, per bar, what the runner computed and whether it agrees
with what backtest would have computed for the same timestamp.

For every bar the walk consumes, and every `Comparison` in the
long/short condition trees, `diagnose_entry_bars` records:

- the live indicator value(s) and threshold, the comparator, and the
  compiler's boolean (`live_pass`);
- an independent plain-Python re-check of the comparator on the logged
  values (`manual_pass`);
- the backtest-path reference: the stored `dsl_json` freshly parsed
  and evaluated with the same `evaluate_indicator` backtest uses, on a
  warm rolling window from the shared candles table (`reference`,
  `ref_pass`).

Each record carries `issues`, which name the failure class:

- `candle_mismatch` — the bar is missing from the reference window or
  its OHLC differs (candle-feed staleness / gap).
- `live_nan_warmup` — live value is NaN while the warm reference is
  finite: the live window is too short for the indicator's lookback.
- `value_mismatch` — both finite but different.
- `arg_parse` — the running spec's comparison differs from the stored
  `dsl_json` (indicator name, args, comparator or literal).
- `threshold_compare` — compiler boolean disagrees with the plain
  re-check of the logged values.
"""

from __future__ import annotations

import logging
import math
import operator
from collections.abc import Callable, Iterator
from typing import Any

import pandas as pd

from agentfx.core.dsl.compiler import _eval_comparison, _eval_expr
from agentfx.core.dsl.grammar import (
    AndCondition,
    Comparison,
    IndicatorCall,
    OrCondition,
    StrategySpec,
)

logger = logging.getLogger(__name__)

DIVERGENCE_ACTION = "USER_SIGNAL_INDICATOR_DIVERGENCE"
SIGNATURE_PARAM = "indicator_diag_signature"

# Cumulative indicators anchor to bar 0 of whatever window they're fed,
# so a rolling reference window can't be value-compared against the
# fold-anchored live window.
_CUMULATIVE_INDICATORS = frozenset({"obv"})

_REL_TOL = 1e-9
_ABS_TOL = 1e-9
_MAX_BARS_IN_DECISION = 10

_SIMPLE_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
}


def _iter_comparisons(cond: Any, path: str) -> Iterator[tuple[str, Comparison]]:
    if isinstance(cond, Comparison):
        yield path, cond
    elif isinstance(cond, (AndCondition, OrCondition)):
        for i, sub in enumerate(cond.args):
            yield from _iter_comparisons(sub, f"{path}.{cond.op}[{i}]")


def _comparisons(spec: StrategySpec) -> dict[str, Comparison]:
    out: dict[str, Comparison] = {}
    for side in ("long", "short"):
        cond = getattr(spec.entry, side)
        if cond is not None:
            out.update(_iter_comparisons(cond, side))
    return out


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _label(expr: Any) -> str:
    if isinstance(expr, IndicatorCall):
        return f"{expr.ind}[{','.join(_fmt(a) for a in expr.args)}]"
    return _fmt(float(expr))


def _finite(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else x


def _value_at(series: pd.Series | float, ts, offset: int = 0) -> float | None:
    if not isinstance(series, pd.Series):
        return float(series)
    if ts not in series.index:
        return None
    pos = series.index.get_loc(ts) + offset
    if pos < 0:
        return None
    return _finite(series.iloc[pos])


def _manual_pass(cmp: str, lhs, rhs, lhs_prev, rhs_prev) -> bool:
    if lhs is None or rhs is None:
        return False
    if cmp in _SIMPLE_OPS:
        return _SIMPLE_OPS[cmp](lhs, rhs)
    if lhs_prev is None or rhs_prev is None:
        return False
    if cmp == "crosses_above":
        return lhs_prev <= rhs_prev and lhs > rhs
    if cmp == "crosses_below":
        return lhs_prev >= rhs_prev and lhs < rhs
    return False


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def _bool_at(series: pd.Series, ts) -> bool | None:
    if ts not in series.index:
        return None
    return bool(series.fillna(False).loc[ts])


def _reference_spec(dsl_json: Any) -> StrategySpec | None:
    if dsl_json is None:
        return None
    try:
        return StrategySpec.model_validate(dsl_json)
    except Exception:  # noqa: BLE001
        logger.warning("entry diag: stored dsl_json failed to parse", exc_info=True)
        return None


def diagnose_entry_bars(
    *,
    strategy,
    live_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    raw_entries: pd.Series,
    bar_index: pd.Index,
    dsl_json: Any,
) -> list[dict]:
    """One record per (bar in `bar_index`, entry comparison)."""
    spec = getattr(strategy, "spec", None)
    if not isinstance(spec, StrategySpec):
        return []

    live_cmps = _comparisons(spec)
    ref_spec = _reference_spec(dsl_json)
    ref_cmps = _comparisons(ref_spec) if ref_spec is not None else live_cmps

    live_cache: dict[tuple, pd.Series] = {}
    ref_cache: dict[tuple, pd.Series] = {}
    records: list[dict] = []

    for ts in bar_index:
        candle_ok = ts in reference_df.index and all(
            _close(_finite(live_df[col].loc[ts]), _finite(reference_df[col].loc[ts]))
            for col in ("high", "low", "close")
        )
        raw = raw_entries.loc[ts] if ts in raw_entries.index else 0
        for path, cmp in live_cmps.items():
            ref_cmp = ref_cmps.get(path)
            issues: list[str] = []
            if not candle_ok:
                issues.append("candle_mismatch")
            if ref_cmp is None or ref_cmp != cmp:
                issues.append("arg_parse")

            sides: dict[str, dict] = {}
            live_vals: dict[str, tuple[float | None, float | None]] = {}
            for name in ("lhs", "rhs"):
                expr = getattr(cmp, name)
                live_series = _eval_expr(expr, live_df, live_cache)
                live_now = _value_at(live_series, ts)
                live_vals[name] = (live_now, _value_at(live_series, ts, -1))
                side: dict[str, Any] = {"label": _label(expr), "live": live_now}
                ref_expr = getattr(ref_cmp, name) if ref_cmp is not None else None
                if ref_expr is None:
                    side["reference"] = None
                else:
                    if _label(ref_expr) != side["label"]:
                        side["reference_label"] = _label(ref_expr)
                    ref_series = _eval_expr(ref_expr, reference_df, ref_cache)
                    ref_now = _value_at(ref_series, ts)
                    side["reference"] = ref_now
                    comparable = not (
                        isinstance(expr, IndicatorCall)
                        and expr.ind in _CUMULATIVE_INDICATORS
                    )
                    if comparable and live_now is None and ref_now is not None:
                        issues.append("live_nan_warmup")
                    elif comparable and not _close(live_now, ref_now):
                        if live_now is not None and ref_now is not None:
                            issues.append("value_mismatch")
                sides[name] = side

            live_pass = bool(_bool_at(_eval_comparison(cmp, live_df, live_cache), ts))
            manual = _manual_pass(
                cmp.cmp,
                live_vals["lhs"][0],
                live_vals["rhs"][0],
                live_vals["lhs"][1],
                live_vals["rhs"][1],
            )
            if manual != live_pass:
                issues.append("threshold_compare")
            ref_pass = (
                _bool_at(_eval_comparison(ref_cmp, reference_df, ref_cache), ts)
                if ref_cmp is not None
                else None
            )

            records.append(
                {
                    "bar_ts": pd.Timestamp(ts).isoformat(),
                    "path": path,
                    "lhs": sides["lhs"],
                    "cmp": cmp.cmp,
                    "rhs": sides["rhs"],
                    "live_pass": live_pass,
                    "manual_pass": manual,
                    "ref_pass": ref_pass,
                    "raw_entry": int(raw),
                    "issues": list(dict.fromkeys(issues)),
                }
            )
    return records


def emit_tick_diagnostic(
    *,
    db,
    run,
    strategy,
    live_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    raw_entries: pd.Series,
    bar_index: pd.Index,
    write_decision: Callable[..., None],
) -> None:
    """Log every record; persist a decision-log row only when the set
    of (path, issue) pairs changes, so a persistent divergence doesn't
    spam the run's timeline once per bar."""
    params = dict(getattr(run, "parameters", None) or {})
    records = diagnose_entry_bars(
        strategy=strategy,
        live_df=live_df,
        reference_df=reference_df,
        raw_entries=raw_entries,
        bar_index=bar_index,
        dsl_json=params.get("dsl_json"),
    )
    for rec in records:
        logger.info(
            "entry diag %s %s %s: %s=%s %s %s=%s live_pass=%s ref=%s issues=%s",
            run.id,
            rec["bar_ts"],
            rec["path"],
            rec["lhs"]["label"],
            rec["lhs"]["live"],
            rec["cmp"],
            rec["rhs"]["label"],
            rec["rhs"]["live"],
            rec["live_pass"],
            rec["lhs"].get("reference"),
            ",".join(rec["issues"]) or "-",
            extra={"diag": rec},
        )

    pairs = sorted({(r["path"], i) for r in records for i in r["issues"]})
    signature = [f"{p}:{i}" for p, i in pairs]
    if signature == list(params.get(SIGNATURE_PARAM) or []):
        return
    run.parameters = {**params, SIGNATURE_PARAM: signature}
    if not signature:
        db.commit()
        return
    write_decision(
        db,
        run,
        action=DIVERGENCE_ACTION,
        reason=(
            "live vs backtest-path entry-condition divergence: " + ", ".join(signature)
        ),
        outputs={
            "paper_run_id": run.id,
            "issues": [{"path": p, "issue": i} for p, i in pairs],
            "bars": [r for r in records if r["issues"]][:_MAX_BARS_IN_DECISION],
        },
    )
