"""Compile a `StrategySpec` into a runnable `Strategy`.

The compiler performs semantic checks beyond what the Pydantic grammar
enforces (e.g. indicator argument bounds, arity), then returns a Strategy
whose `generate_signals(df)` evaluates the entry conditions per bar and
emits a `{+1, -1, 0}` position series.

The signal series respects the DSL's `exit` block:
  - `stop_pips` / `target_pips`: when a held position's mark-to-close move
    against / for it reaches this many pips, the trade exits at that bar.
  - `max_hold_bars`: forces exit when the trade has been open this many
    bars (entry bar = 0).
  - `opposite_signal`: when true, an opposite-direction entry condition
    forces an exit-and-flip at that bar.

Exit checks are close-based (`close[t]` is the price compared to the
entry close) — matching the convention the rest of the backtest engine
uses for trade pricing. This slightly underestimates intra-bar stop hits
(a wick that touched the stop but closed back inside doesn't trigger);
the trade-off keeps the cost model and the trade ledger consistent
without a backtest-engine rewrite.
"""

from __future__ import annotations

import operator
from functools import reduce
from typing import Any

import numpy as np
import pandas as pd

from agentfx.core.dsl.grammar import (
    AndCondition,
    Comparison,
    Condition,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    OrCondition,
    StrategySpec,
)
from agentfx.core.dsl.indicators import (
    INDICATOR_ARG_BOUNDS,
    evaluate_indicator,
)
from agentfx.core.strategy import Strategy


class CompilerError(ValueError):
    """Raised when a spec is structurally valid but semantically invalid."""


# ─────────────────────────────────────────────────────────────────────────
# Compile-time semantic validation
# ─────────────────────────────────────────────────────────────────────────


def _check_indicator_call(call: IndicatorCall) -> None:
    bounds = INDICATOR_ARG_BOUNDS[call.ind]
    if len(call.args) != len(bounds):
        raise CompilerError(
            f"indicator '{call.ind}' takes {len(bounds)} args, got {len(call.args)}"
        )
    for i, (val, (lo, hi)) in enumerate(zip(call.args, bounds, strict=True)):
        if not (lo <= val <= hi):
            raise CompilerError(
                f"indicator '{call.ind}' arg[{i}]={val} out of bounds [{lo}, {hi}]"
            )


def _walk_condition(cond: Condition, visit) -> None:
    if isinstance(cond, Comparison):
        visit(cond)
    elif isinstance(cond, (AndCondition, OrCondition)):
        for sub in cond.args:
            _walk_condition(sub, visit)
    else:
        # Defensive — grammar only constructs Comparison / AndCondition /
        # OrCondition. Marked unreachable for the same reason as line 150
        # and lines 164-166 below.
        raise CompilerError(
            f"unknown condition type: {type(cond).__name__}"
        )  # pragma: no cover


def _validate_entry(entry: EntryConditions) -> None:
    seen_indicators: set[tuple[str, tuple[float, ...]]] = set()

    def _visit_comparison(cmp: Comparison) -> None:
        for side in (cmp.lhs, cmp.rhs):
            if isinstance(side, IndicatorCall):
                _check_indicator_call(side)
                seen_indicators.add((side.ind, tuple(side.args)))

    if entry.long is not None:
        _walk_condition(entry.long, _visit_comparison)
    if entry.short is not None:
        _walk_condition(entry.short, _visit_comparison)
    # Degenerate `long == short` specs are rejected at the Pydantic
    # grammar layer (see EntryConditions._long_and_short_must_differ)
    # so the GA mutate/crossover retry loop can find a different
    # operator. By the time we reach the compiler, this has already
    # been enforced.


# ─────────────────────────────────────────────────────────────────────────
# Per-bar evaluation
# ─────────────────────────────────────────────────────────────────────────


def _eval_expr(
    expr: Any, df: pd.DataFrame, cache: dict[tuple, pd.Series]
) -> pd.Series | float:
    if isinstance(expr, IndicatorCall):
        key = (expr.ind, tuple(expr.args))
        if key not in cache:
            cache[key] = evaluate_indicator(df, expr.ind, list(expr.args))
        return cache[key]
    return float(expr)


def _to_series(x: pd.Series | float, index: pd.Index) -> pd.Series:
    if isinstance(x, pd.Series):
        return x
    return pd.Series(float(x), index=index)


def _eval_comparison(
    cmp: Comparison, df: pd.DataFrame, cache: dict[tuple, pd.Series]
) -> pd.Series:
    lhs = _eval_expr(cmp.lhs, df, cache)
    rhs = _eval_expr(cmp.rhs, df, cache)

    if cmp.cmp == ">":
        return _to_series(lhs, df.index) > _to_series(rhs, df.index)
    if cmp.cmp == "<":
        return _to_series(lhs, df.index) < _to_series(rhs, df.index)
    if cmp.cmp == ">=":
        return _to_series(lhs, df.index) >= _to_series(rhs, df.index)
    if cmp.cmp == "<=":
        return _to_series(lhs, df.index) <= _to_series(rhs, df.index)
    if cmp.cmp == "crosses_above":
        l_s = _to_series(lhs, df.index)
        r_s = _to_series(rhs, df.index)
        prev_below_or_eq = l_s.shift(1) <= r_s.shift(1)
        now_above = l_s > r_s
        return (prev_below_or_eq & now_above).fillna(False)
    if cmp.cmp == "crosses_below":
        l_s = _to_series(lhs, df.index)
        r_s = _to_series(rhs, df.index)
        prev_above_or_eq = l_s.shift(1) >= r_s.shift(1)
        now_below = l_s < r_s
        return (prev_above_or_eq & now_below).fillna(False)
    raise CompilerError(f"unknown comparator: {cmp.cmp}")  # pragma: no cover


def _eval_condition(
    cond: Condition, df: pd.DataFrame, cache: dict[tuple, pd.Series]
) -> pd.Series:
    if isinstance(cond, Comparison):
        return _eval_comparison(cond, df, cache)
    if isinstance(cond, AndCondition):
        parts = [_eval_condition(c, df, cache).fillna(False) for c in cond.args]
        return reduce(operator.and_, parts)
    if isinstance(cond, OrCondition):
        parts = [_eval_condition(c, df, cache).fillna(False) for c in cond.args]
        return reduce(operator.or_, parts)
    raise CompilerError(
        f"unknown condition type: {type(cond).__name__}"
    )  # pragma: no cover


# ─────────────────────────────────────────────────────────────────────────
# Compiled Strategy
# ─────────────────────────────────────────────────────────────────────────


def _apply_exits(
    raw_pos: np.ndarray,
    close: np.ndarray,
    high: np.ndarray | None,
    low: np.ndarray | None,
    exit_rules: ExitRules,
    pip_size: float,
) -> tuple[np.ndarray, dict[int, str], dict[int, float]]:
    """Walk bars with state and apply DSL exit rules to a raw position
    series.

    Two evaluation modes (`exit_rules.eval_mode`):

      * `"close"` (default): SL and TP fire only when the bar's CLOSE
        breaches the threshold. Legacy behaviour — every genome
        validated before eval_mode existed uses this. Intra-bar wicks
        that touch the SL/TP but recover before close do NOT trigger.

      * `"wick"`: SL and TP fire when the bar's LOW (long SL / short
        TP) or HIGH (long TP / short SL) touches the threshold. Realistic
        model of how a broker-side stop / take-profit order fills.
        Trigger price = exactly the SL/TP price; the returned exit_price
        override reflects this so the trade P&L matches a real fill.

    Order of exits (both modes): stop → target → max_hold → opposite.
    First match wins. Same-bar exit + re-entry in the same direction
    is blocked; opposite flip is allowed when `opposite_signal=True`.

    When both stop AND target could trigger in the same wick-mode bar
    (bar's range spans both), we resolve conservatively: assume stop
    fires first. Can't tell tick order from OHLC alone; better to
    under-report profit than over-report it.

    Returns (signals, exit_reasons, exit_prices):
      * signals: per-bar target position after the walk.
      * exit_reasons: bar_idx → 'stop'/'target'/'max_hold'/'opposite_flip'.
      * exit_prices: bar_idx → override exit price. For wick-mode SL/TP
        this is the trigger price (better fidelity than close). For all
        other exits (close-mode, max_hold, opposite_flip) the dict has
        no entry and the caller should fall back to `close[t]`.
    """
    n = len(raw_pos)
    final = np.zeros(n, dtype=np.int8)
    exit_reasons: dict[int, str] = {}
    exit_prices: dict[int, float] = {}
    in_position = 0
    entry_close = 0.0
    entry_bar = -1

    stop_pips = exit_rules.stop_pips
    target_pips = exit_rules.target_pips
    max_hold = exit_rules.max_hold_bars
    min_hold = exit_rules.min_hold_bars
    flip_on_opposite = exit_rules.opposite_signal
    wick = exit_rules.eval_mode == "wick"
    if wick and (high is None or low is None):
        raise CompilerError(
            "eval_mode='wick' requires high and low arrays; caller "
            "must pass them to _apply_exits."
        )

    stop_price_dist = stop_pips * pip_size
    target_price_dist = target_pips * pip_size

    for t in range(n):
        if in_position != 0:
            bars_held = t - entry_bar
            # Min-hold gate: while the position is younger than
            # min_hold_bars, no exit path can fire (stop / target /
            # opposite-flip / max-hold are all suppressed). Bars
            # younger than the gate hold the existing direction.
            if bars_held < min_hold:
                final[t] = in_position
                continue

            # ── Evaluate stop + target ──────────────────────────────
            stop_hit = False
            target_hit = False
            trigger_price: float | None = None

            if wick:
                # Touch-based check against bar extremes.
                if in_position == 1:  # LONG
                    long_sl = entry_close - stop_price_dist
                    long_tp = entry_close + target_price_dist
                    if low[t] <= long_sl:
                        stop_hit = True
                        trigger_price = long_sl
                    elif high[t] >= long_tp:
                        target_hit = True
                        trigger_price = long_tp
                else:  # SHORT
                    short_sl = entry_close + stop_price_dist
                    short_tp = entry_close - target_price_dist
                    if high[t] >= short_sl:
                        stop_hit = True
                        trigger_price = short_sl
                    elif low[t] <= short_tp:
                        target_hit = True
                        trigger_price = short_tp
            else:
                # Close-based check (legacy default).
                move_pips = (close[t] - entry_close) / pip_size
                if in_position == -1:
                    move_pips = -move_pips
                if move_pips <= -stop_pips:
                    stop_hit = True
                elif move_pips >= target_pips:
                    target_hit = True

            if stop_hit:
                final[t] = 0
                in_position = 0
                exit_reasons[t] = "stop"
                if trigger_price is not None:
                    exit_prices[t] = float(trigger_price)
                continue
            if target_hit:
                final[t] = 0
                in_position = 0
                exit_reasons[t] = "target"
                if trigger_price is not None:
                    exit_prices[t] = float(trigger_price)
                continue

            if bars_held >= max_hold:
                final[t] = 0
                in_position = 0
                exit_reasons[t] = "max_hold"
                continue
            if flip_on_opposite and raw_pos[t] == -in_position:
                # Close current, open new in same bar — same as the
                # pre-exit-enforcement DSL flip semantics. entry_close
                # for the new trade is close[t].
                final[t] = raw_pos[t]
                in_position = int(raw_pos[t])
                entry_close = float(close[t])
                entry_bar = t
                exit_reasons[t] = "opposite_flip"
                continue

            # No exit triggered — hold.
            final[t] = in_position
            continue

        # in_position == 0 here. Look for a new entry.
        if raw_pos[t] != 0:
            in_position = int(raw_pos[t])
            entry_close = float(close[t])
            entry_bar = t
            final[t] = in_position
        # else: stay flat, final[t] is already 0.

    return final, exit_reasons, exit_prices


class CompiledDSLStrategy(Strategy):
    """A Strategy compiled from a `StrategySpec`.

    Per-bar position model: +1 when long entry condition holds, -1 when
    short, 0 when both or neither. Shifted by 1 bar to avoid lookahead.
    Then `_apply_exits` walks bars with state to enforce the spec's
    stop_pips / target_pips / max_hold_bars / opposite_signal block.

    `pip_size` is the price-unit-per-pip for the instrument the spec
    will be backtested on (0.01 for JPY crosses, 0.0001 otherwise). It
    is required because stop/target are expressed in pips and need to
    be compared against price moves; the same compiled spec can be
    re-used across instruments only by re-instantiating with the
    target instrument's pip_size.
    """

    def __init__(self, spec: StrategySpec, pip_size: float) -> None:
        self.spec = spec
        self.pip_size = float(pip_size)
        # Populated by generate_signals; maps timestamp → exit reason
        # for callers (the backtest engine) that need it to annotate
        # each closed trade / exit signal. Per-instance state because
        # we call generate_signals once per backtest invocation.
        self.exit_reasons_by_ts: dict[pd.Timestamp, str] = {}
        # Wilson 2026-08-15: for eval_mode="wick" exits, the trigger
        # price is exactly the SL/TP threshold, not the bar's close.
        # Backtest reads this map to override the default `close[t]`
        # exit price when present.
        self.exit_prices_by_ts: dict[pd.Timestamp, float] = {}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        cache: dict[tuple, pd.Series] = {}

        if self.spec.entry.long is not None:
            long_sig = _eval_condition(self.spec.entry.long, df, cache).fillna(False)
        else:
            long_sig = pd.Series(False, index=df.index)

        if self.spec.entry.short is not None:
            short_sig = _eval_condition(self.spec.entry.short, df, cache).fillna(False)
        else:
            short_sig = pd.Series(False, index=df.index)

        # Raw entry-condition position, 1-bar shifted (no-lookahead).
        raw_pos = (
            (long_sig.astype(int) - short_sig.astype(int))
            .shift(1)
            .fillna(0)
            .astype(int)
            .to_numpy()
        )
        close = df["close"].to_numpy()
        # High/low are only needed for eval_mode="wick" but always
        # available on OHLC DataFrames — pass them unconditionally.
        high = df["high"].to_numpy() if "high" in df.columns else None
        low = df["low"].to_numpy() if "low" in df.columns else None
        final, exit_reasons_by_idx, exit_prices_by_idx = _apply_exits(
            raw_pos, close, high, low, self.spec.exit, self.pip_size
        )
        # Translate bar-index → timestamp so the backtest engine can
        # look up the reason (and wick-mode trigger price) by signal
        # timestamp without re-deriving the bar offset.
        self.exit_reasons_by_ts = {
            df.index[i]: reason for i, reason in exit_reasons_by_idx.items()
        }
        self.exit_prices_by_ts = {
            df.index[i]: price for i, price in exit_prices_by_idx.items()
        }
        return pd.Series(final, index=df.index, dtype=int)

    def raw_entry_signals(self, df: pd.DataFrame) -> pd.Series:
        """Per-bar entry EVENTS with no state projection.

        Returns +1 at bars where the long entry condition fires (e.g.
        `crosses_above`), -1 at bars where the short entry fires, 0
        elsewhere. Unlike `generate_signals`, this method does NOT
        walk the exit machine — it does not project a hypothetical
        held position across bars. Callers get raw cross events only.

        Purpose: the paper runner asks "did an entry cross fire at the
        latest closed bar?" and acts on that event. It must not inherit
        a projected held state from before the runner was watching (the
        alternative model, `generate_signals`, walks in-position state
        from bar 0 of the window forward — for cold-deployed paper runs
        this makes the runner think it's mid-hold on a hypothetical
        trade that never actually opened, causing it to skip real fresh
        crosses that come later while `opposite_signal: false` or an
        active min_hold blocks the state from re-transitioning).

        The 1-bar shift (`raw_pos.shift(1)`) is preserved so the caller
        gets the same no-lookahead guarantee as backtest — the entry
        signal at bar T is based on the condition that closed AT bar T,
        available for action at bar T+1 (or, in the paper runner, at
        the signal-tick that fires shortly after bar T closes).
        """
        cache: dict[tuple, pd.Series] = {}
        if self.spec.entry.long is not None:
            long_sig = _eval_condition(self.spec.entry.long, df, cache).fillna(False)
        else:
            long_sig = pd.Series(False, index=df.index)
        if self.spec.entry.short is not None:
            short_sig = _eval_condition(self.spec.entry.short, df, cache).fillna(False)
        else:
            short_sig = pd.Series(False, index=df.index)
        raw = (
            (long_sig.astype(int) - short_sig.astype(int))
            .shift(1)
            .fillna(0)
            .astype(int)
        )
        return raw


def compile_spec(spec: StrategySpec, pip_size: float = 0.0001) -> Strategy:
    """Validate and compile a `StrategySpec` into a runnable `Strategy`.

    `pip_size` defaults to 0.0001 (EUR_USD-style majors). Callers must
    pass the correct value for JPY crosses (0.01) — the orchestrator /
    backtest engine handles this via `make_strategy("dsl", spec=…,
    pip_size=…)`.

    Raises `CompilerError` on any semantic problem (out-of-bounds args,
    wrong arity, etc.). Pydantic-level grammar errors raise `ValidationError`
    earlier (at spec construction time).
    """
    _validate_entry(spec.entry)
    return CompiledDSLStrategy(spec, pip_size=pip_size)
