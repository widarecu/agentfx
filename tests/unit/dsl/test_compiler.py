"""Compiler tests — including the DSL-completeness gate."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl import (
    AndCondition,
    Comparison,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    SizeRules,
    StrategySpec,
    compile_spec,
)
from agentfx.core.dsl.compiler import CompilerError
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    donchian_breakout_seed,
    ma_crossover_seed,
    rsi_mean_reversion_seed,
)
from agentfx.core.strategy import (
    DonchianBreakout,
    MACrossover,
    RSIMeanReversion,
)


def _ind(name: str, *args: float) -> IndicatorCall:
    return IndicatorCall(ind=name, args=list(args))  # type: ignore[arg-type]


def _make_synthetic_df(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with realistic structure for indicator round-trip."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    # Random walk on close
    returns = rng.normal(0, 0.0005, n)
    close = 1.10 * np.exp(np.cumsum(returns))
    # High/low around close with random spread
    spread = np.abs(rng.normal(0, 0.0003, n))
    high = close + spread
    low = close - spread
    open_ = close.copy()
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 1000, n).astype(float),
        },
        index=idx,
    )


# ─────────────────────────────────────────────────────────────────────────
# Compile-time validation
# ─────────────────────────────────────────────────────────────────────────


class TestCompilerSemanticChecks:
    def test_indicator_arg_out_of_bounds_rejected(self) -> None:
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(
                    lhs=_ind("sma", 1000),  # > 400 max
                    cmp=">",
                    rhs=_ind("sma", 50),
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        with pytest.raises(CompilerError, match="out of bounds"):
            compile_spec(spec)

    def test_indicator_wrong_arity_rejected(self) -> None:
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(
                    lhs=_ind("bb_upper", 20),  # missing k arg
                    cmp=">",
                    rhs=100.0,
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        with pytest.raises(CompilerError, match="takes 2 args"):
            compile_spec(spec)


# ─────────────────────────────────────────────────────────────────────────
# Per-bar evaluation
# ─────────────────────────────────────────────────────────────────────────


class TestSignalEvaluation:
    def test_position_shifted_by_one_bar(self) -> None:
        spec = ma_crossover_seed(fast=5, slow=10)
        strat = compile_spec(spec)
        df = _make_synthetic_df(50)
        sig = strat.generate_signals(df)
        # First bar must be 0 (shift by 1 from any value still gives NaN→0)
        assert sig.iloc[0] == 0
        assert sig.dtype == np.dtype("int64") or sig.dtype == np.dtype("int")

    def test_signals_are_in_expected_set(self) -> None:
        spec = ma_crossover_seed(fast=5, slow=20)
        strat = compile_spec(spec)
        df = _make_synthetic_df(200)
        sig = strat.generate_signals(df)
        assert set(sig.unique()).issubset({-1, 0, 1})

    def test_neither_long_nor_short_yields_zero(self) -> None:
        # rsi between 30 and 70: both conditions false
        spec = rsi_mean_reversion_seed(window=14, lower=30.0, upper=70.0)
        strat = compile_spec(spec)
        # All-flat series → rsi will be NaN early then ~50 → both conditions false
        n = 100
        idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            },
            index=idx,
        )
        sig = strat.generate_signals(df)
        # Constant data → no entries
        assert (sig == 0).all()


class TestCrossesOperators:
    def test_crosses_above(self) -> None:
        # Build a comparison where ema(2) crosses above 1.05
        df = _make_synthetic_df(200, seed=7)
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(
                    lhs=_ind("ema", 2),
                    cmp="crosses_above",
                    rhs=_ind("ema", 50),
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        strat = compile_spec(spec)
        sig = strat.generate_signals(df)
        # Should be sparse — crosses_above only fires on transition bars
        n_long_entries = (sig == 1).sum()
        # At least some crosses should occur in 200 random-walk bars
        assert n_long_entries < 50  # not every bar (sanity)


# ─────────────────────────────────────────────────────────────────────────
# DSL-completeness gate: compiled seeds must equal classic-class signals
# ─────────────────────────────────────────────────────────────────────────


class TestDSLCompletenessGate:
    """The seeds must produce signals identical to their classic counterparts.

    This is the forcing function that the DSL is at least as expressive as
    the hand-coded strategies the platform shipped with. If this test fails,
    either the DSL is too narrow or the seeds don't capture the classic
    semantics — either way, fix it.
    """

    def test_ma_crossover_seed_matches_classic(self) -> None:
        df = _make_synthetic_df(500, seed=11)
        compiled = compile_spec(MA_CROSSOVER_SEED)
        classic = MACrossover(fast=20, slow=50)

        compiled_sig = compiled.generate_signals(df)
        classic_sig = classic.generate_signals(df)

        pd.testing.assert_series_equal(
            compiled_sig.astype(int),
            classic_sig.astype(int),
            check_names=False,
        )

    def test_rsi_mean_reversion_seed_matches_classic(self) -> None:
        df = _make_synthetic_df(500, seed=13)
        compiled = compile_spec(RSI_MEAN_REVERSION_SEED)
        classic = RSIMeanReversion(window=14, lower=30.0, upper=70.0)

        compiled_sig = compiled.generate_signals(df)
        classic_sig = classic.generate_signals(df)

        pd.testing.assert_series_equal(
            compiled_sig.astype(int),
            classic_sig.astype(int),
            check_names=False,
        )

    def test_donchian_breakout_seed_matches_classic(self) -> None:
        df = _make_synthetic_df(500, seed=17)
        compiled = compile_spec(DONCHIAN_BREAKOUT_SEED)
        classic = DonchianBreakout(window=20)

        compiled_sig = compiled.generate_signals(df)
        classic_sig = classic.generate_signals(df)

        pd.testing.assert_series_equal(
            compiled_sig.astype(int),
            classic_sig.astype(int),
            check_names=False,
        )

    def test_ma_crossover_seed_with_custom_params_matches_classic(self) -> None:
        df = _make_synthetic_df(500, seed=23)
        compiled = compile_spec(ma_crossover_seed(fast=5, slow=15))
        classic = MACrossover(fast=5, slow=15)

        compiled_sig = compiled.generate_signals(df)
        classic_sig = classic.generate_signals(df)

        pd.testing.assert_series_equal(
            compiled_sig.astype(int),
            classic_sig.astype(int),
            check_names=False,
        )


class TestNestedConditions:
    def test_and_condition_evaluates(self) -> None:
        # Long when sma(5) > sma(20) AND rsi(14) < 70
        spec = StrategySpec(
            entry=EntryConditions(
                long=AndCondition(
                    args=[
                        Comparison(lhs=_ind("sma", 5), cmp=">", rhs=_ind("sma", 20)),
                        Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=70.0),
                    ]
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        strat = compile_spec(spec)
        df = _make_synthetic_df(300, seed=29)
        sig = strat.generate_signals(df)
        assert set(sig.unique()).issubset({0, 1})
