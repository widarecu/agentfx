"""Compile a `StrategySpec` into a runnable `Strategy`.

The compiler performs semantic checks beyond what the Pydantic grammar
enforces (e.g. indicator argument bounds, arity), then returns a Strategy
whose `generate_signals(df)` evaluates the entry conditions per bar and
emits a `{+1, -1, 0}` position series with a 1-bar shift to avoid lookahead
— matching the existing classic strategies' contract.

In v1 the exit rules in the spec are validated and stored but NOT enforced
by the compiler — the existing backtest engine doesn't apply stops/targets
either. Enforcing them is a v0.4.0 backtest-engine extension.
"""

from __future__ import annotations

import operator
from functools import reduce
from typing import Any

import pandas as pd

from agentfx.core.dsl.grammar import (
    AndCondition,
    Comparison,
    Condition,
    EntryConditions,
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


class CompiledDSLStrategy(Strategy):
    """A Strategy compiled from a `StrategySpec`.

    Per-bar position model: +1 when long entry condition holds, -1 when
    short, 0 when both or neither. Shifted by 1 bar to avoid lookahead.
    """

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec

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

        pos = long_sig.astype(int) - short_sig.astype(int)
        return pos.shift(1).fillna(0).astype(int)


def compile_spec(spec: StrategySpec) -> Strategy:
    """Validate and compile a `StrategySpec` into a runnable `Strategy`.

    Raises `CompilerError` on any semantic problem (out-of-bounds args,
    wrong arity, etc.). Pydantic-level grammar errors raise `ValidationError`
    earlier (at spec construction time).
    """
    _validate_entry(spec.entry)
    return CompiledDSLStrategy(spec)
