"""Gen-0 seed strategies expressed as DSL specs.

Each seed re-expresses one of the existing classic strategies
(MA crossover, RSI mean-reversion, Donchian breakout) in DSL form.

The DSL-completeness gate (in `tests/unit/dsl/test_compiler.py`) asserts that
the compiled seed produces a position series equal to the corresponding
classic strategy on identical data — confirming that the DSL is at least as
expressive as the hand-coded strategies the platform shipped with.

The exit-rule placeholders (large stop/target/max_hold, opposite_signal=True)
correspond to "no enforced exit beyond reversal" — matching the classic
position-driven semantics where position flips when the indicator condition
flips. In v0.4.0 when the backtest engine starts enforcing stops/targets,
seed exit rules can be tightened to realistic values.
"""

from __future__ import annotations

from agentfx.core.dsl.grammar import (
    Comparison,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    SizeRules,
    StrategySpec,
)


def _ind(name: str, *args: float) -> IndicatorCall:
    return IndicatorCall(ind=name, args=list(args))  # type: ignore[arg-type]


# Permissive exit rules used by all seeds — the v1 backtest engine ignores
# these anyway; tightening to realistic values is a v0.4.0 enhancement.
_PERMISSIVE_EXIT = ExitRules(
    stop_pips=400,
    target_pips=800,
    max_hold_bars=2880,
    opposite_signal=True,
)

_DEFAULT_SIZE = SizeRules(risk_pct=0.005)


def ma_crossover_seed(fast: int = 20, slow: int = 50) -> StrategySpec:
    """MA crossover — equivalent to MACrossover(fast, slow)."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("sma", fast),
                cmp=">",
                rhs=_ind("sma", slow),
            ),
            short=Comparison(
                lhs=_ind("sma", fast),
                cmp="<",
                rhs=_ind("sma", slow),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def rsi_mean_reversion_seed(
    window: int = 14, lower: float = 30.0, upper: float = 70.0
) -> StrategySpec:
    """RSI mean-reversion — equivalent to RSIMeanReversion(window, lower, upper)."""
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("rsi", window),
                cmp="<",
                rhs=lower,
            ),
            short=Comparison(
                lhs=_ind("rsi", window),
                cmp=">",
                rhs=upper,
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def donchian_breakout_seed(window: int = 20) -> StrategySpec:
    """Donchian breakout — equivalent to DonchianBreakout(window).

    Compares current close vs. the prior-window channel. The
    `donchian_high`/`donchian_low` indicators apply .shift(1) internally so
    the channel excludes the current bar — matching the existing class.
    """
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("close"),
                cmp=">",
                rhs=_ind("donchian_high", window),
            ),
            short=Comparison(
                lhs=_ind("close"),
                cmp="<",
                rhs=_ind("donchian_low", window),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


# Convenience: the canonical seeds with default parameters.
MA_CROSSOVER_SEED: StrategySpec = ma_crossover_seed()
RSI_MEAN_REVERSION_SEED: StrategySpec = rsi_mean_reversion_seed()
DONCHIAN_BREAKOUT_SEED: StrategySpec = donchian_breakout_seed()


SEED_SPECS: list[StrategySpec] = [
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    DONCHIAN_BREAKOUT_SEED,
]


__all__ = [
    "DONCHIAN_BREAKOUT_SEED",
    "MA_CROSSOVER_SEED",
    "RSI_MEAN_REVERSION_SEED",
    "SEED_SPECS",
    "donchian_breakout_seed",
    "ma_crossover_seed",
    "rsi_mean_reversion_seed",
]
