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
        raise CompilerError(f"unknown condition type: {type(cond).__name__}")


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
    exit_rules: ExitRules,
    pip_size: float,
) -> np.ndarray:
    """Walk bars with state and apply DSL exit rules to a raw position
    series.

    `raw_pos[t]` is the entry-condition direction (+1/-1/0) the spec
    *wants* to hold during bar t, already 1-bar-shifted for lookahead
    protection. The walker tracks the actual open position and emits the
    final per-bar signal:

      - Entry: when flat (`in_position == 0`) and `raw_pos[t] != 0`,
        enter at close[t]. The bar's signal becomes the new direction.
      - Stop / target: at each subsequent bar, compute the pip move
        from entry's close to bar `t`'s close. If it crosses the
        configured threshold (against → stop, for → target), emit 0
        at that bar (exit fires). `extract_closed_trades` then records
        exit_price = close[t] which is the stop/target hit price.
      - max_hold_bars: if `t - entry_bar >= max_hold_bars`, force exit.
      - opposite_signal: if true and `raw_pos[t]` is the opposite of
        the open position, exit AND flip in the same bar (signal
        becomes the new opposite direction). Matches the v1 behaviour
        when exits were not enforced.

    Exits are evaluated in the order: stop → target → max_hold →
    opposite. Same-bar exit + re-entry in the *same* direction is
    blocked (the `continue` skips the entry block on the exit bar) —
    same-bar opposite flip is allowed when `opposite_signal=True`.
    """
    n = len(raw_pos)
    final = np.zeros(n, dtype=np.int8)
    in_position = 0
    entry_close = 0.0
    entry_bar = -1

    stop_pips = exit_rules.stop_pips
    target_pips = exit_rules.target_pips
    max_hold = exit_rules.max_hold_bars
    flip_on_opposite = exit_rules.opposite_signal

    for t in range(n):
        if in_position != 0:
            # Direction-aware pip move from entry close to current close.
            move_pips = (close[t] - entry_close) / pip_size
            if in_position == -1:
                move_pips = -move_pips

            if move_pips <= -stop_pips:
                # Stop hit: exit at this bar's close. Signal flips to 0
                # so extract_closed_trades records exit_price = close[t].
                final[t] = 0
                in_position = 0
                continue
            if move_pips >= target_pips:
                final[t] = 0
                in_position = 0
                continue
            if (t - entry_bar) >= max_hold:
                final[t] = 0
                in_position = 0
                continue
            if flip_on_opposite and raw_pos[t] == -in_position:
                # Close current, open new in same bar — same as the
                # pre-exit-enforcement DSL flip semantics. entry_close
                # for the new trade is close[t].
                final[t] = raw_pos[t]
                in_position = int(raw_pos[t])
                entry_close = float(close[t])
                entry_bar = t
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

    return final


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
        final = _apply_exits(raw_pos, close, self.spec.exit, self.pip_size)
        return pd.Series(final, index=df.index, dtype=int)


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
