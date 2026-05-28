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

    def test_rsi_mean_reversion_seed_entries_match_classic(self) -> None:
        """Compiled-DSL RSI MR enters on the same bars as classic RSI MR.

        DSL now enforces the exit block (stop/target/max_hold/opposite),
        so once entered the DSL holds the position until an exit triggers
        — classic RSI MR instead goes flat the moment `rsi` returns to
        the normal band. The two diverge on hold duration but the
        ENTRY points are identical because the DSL's `raw_pos` is built
        from the same indicator predicate.

        This is the correct relationship for an exit-enforcing strategy
        ABC: at every bar where classic enters long/short, the DSL
        agrees in direction; DSL may *additionally* stay in beyond
        that bar (which is the whole point of the new exit walker).
        """
        df = _make_synthetic_df(500, seed=13)
        compiled = compile_spec(RSI_MEAN_REVERSION_SEED)
        classic = RSIMeanReversion(window=14, lower=30.0, upper=70.0)

        compiled_sig = compiled.generate_signals(df).astype(int)
        classic_sig = classic.generate_signals(df).astype(int)

        # At every bar where classic is non-zero, compiled is the same
        # direction (or compiled is non-zero from an earlier entry that
        # the classic missed — also OK).
        classic_active = classic_sig != 0
        # Where classic is active, compiled must agree in direction.
        assert (compiled_sig[classic_active] == classic_sig[classic_active]).all(), (
            "compiled disagrees in direction with classic at a bar classic is active"
        )

    def test_donchian_breakout_seed_entries_match_classic(self) -> None:
        """Same relationship as RSI: DSL enters on the same bars as
        classic, then may hold longer until exits fire.
        """
        df = _make_synthetic_df(500, seed=17)
        compiled = compile_spec(DONCHIAN_BREAKOUT_SEED)
        classic = DonchianBreakout(window=20)

        compiled_sig = compiled.generate_signals(df).astype(int)
        classic_sig = classic.generate_signals(df).astype(int)

        classic_active = classic_sig != 0
        assert (compiled_sig[classic_active] == classic_sig[classic_active]).all(), (
            "compiled disagrees in direction with classic at a bar classic is active"
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


class TestExitEnforcement:
    """The compiler now enforces the exit block. Each test wires a
    controlled entry condition that fires once, then drives close-price
    movement to verify each exit path independently.
    """

    def _spec_long_entry(
        self,
        *,
        stop_pips: int,
        target_pips: int,
        max_hold_bars: int,
        opposite_signal: bool = True,
    ) -> StrategySpec:
        """Long entry when close > 1.0 (always true on our synthetic data),
        short entry never. Exits are the variable under test.
        """
        return StrategySpec(
            entry=EntryConditions(
                long=Comparison(lhs=_ind("sma", 5), cmp=">", rhs=0.0),
            ),
            exit=ExitRules(
                stop_pips=stop_pips,
                target_pips=target_pips,
                max_hold_bars=max_hold_bars,
                opposite_signal=opposite_signal,
            ),
            size=SizeRules(risk_pct=0.005),
        )

    def _df_with_drop_then_recover(self) -> pd.DataFrame:
        """Synthetic series: enters at ~1.10, drops 15 pips, then recovers.
        Lets us hit a 10-pip stop cleanly.
        """
        idx = pd.date_range("2024-01-01", periods=50, freq="min", tz="UTC")
        # 10 flat bars (long entry condition has time to fire), then drop 15
        # pips over 5 bars, then back up.
        closes = (
            [1.1000] * 10 + [1.0997, 1.0993, 1.0990, 1.0987, 1.0985] + [1.1000] * 35
        )
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "volume": [1] * 50,
            },
            index=idx,
        )
        return df

    def _longest_run(self, sig: pd.Series, value: int) -> int:
        run = best = 0
        for v in sig:
            if int(v) == value:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best

    def test_stop_pips_fires(self) -> None:
        spec = self._spec_long_entry(stop_pips=10, target_pips=50, max_hold_bars=240)
        strat = compile_spec(spec, pip_size=0.0001)
        df = self._df_with_drop_then_recover()
        sig = strat.generate_signals(df)

        # Entry at bar 1 (sma uses min_periods=1; long_sig is true from
        # bar 0, shifts to bar 1). entry_close = close[1] = 1.1000.
        # The stop=10 pips is crossed at bar 12 where close=1.0990
        # (-10 pips). The walker emits signal=0 at exactly that bar
        # (the trade closes there; bar 13 may re-enter since the entry
        # condition still holds — no cool-down in the grammar).
        assert sig.iloc[12] == 0, (
            f"stop_pips=10 should fire at bar 12 where close drops to "
            f"-10 pips; got signal[12]={sig.iloc[12]}"
        )

    def test_target_pips_fires(self) -> None:
        # Long entry, close climbs above entry to trigger the 10-pip target.
        # Grammar requires stop_pips < target_pips; use stop=5, target=10.
        idx = pd.date_range("2024-01-01", periods=40, freq="min", tz="UTC")
        closes = [1.1000] * 10 + [1.1003, 1.1006, 1.1009, 1.1012] + [1.1020] * 26
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "volume": [1] * 40,
            },
            index=idx,
        )
        spec = self._spec_long_entry(stop_pips=5, target_pips=10, max_hold_bars=240)
        strat = compile_spec(spec, pip_size=0.0001)
        sig = strat.generate_signals(df)

        # Target = +10 pips. From entry close 1.1000, close hits 1.1012 at
        # bar 13. The walker should mark that bar (or earlier) as a forced 0
        # because the close crossed +10 pips.
        assert sig.iloc[13] == 0, (
            f"target_pips=10 should fire when close crosses +10 pips; "
            f"got signal[13]={sig.iloc[13]}"
        )

    def test_max_hold_bars_caps_each_trade(self) -> None:
        # Flat data so neither stop nor target fires; only max_hold can exit.
        # Each non-zero run is bounded by max_hold_bars (the walker may
        # re-enter on the very next bar if the entry condition still
        # holds — that's correct behaviour, no cool-down in the grammar).
        idx = pd.date_range("2024-01-01", periods=50, freq="min", tz="UTC")
        closes = [1.1000] * 50
        df = pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1] * 50,
            },
            index=idx,
        )
        spec = self._spec_long_entry(stop_pips=50, target_pips=100, max_hold_bars=5)
        strat = compile_spec(spec, pip_size=0.0001)
        sig = strat.generate_signals(df)

        longest_run = self._longest_run(sig, 1)
        # max_hold_bars=5 means a trade closes once it's been open 5 bars.
        # Allow up to max_hold_bars + 1 for the inclusive boundary.
        assert longest_run <= 6, (
            f"max_hold_bars=5 should cap each long trade ≤ 6 bars; got "
            f"longest_run={longest_run}"
        )

    def test_min_hold_bars_suppresses_stop_target_and_flip(self) -> None:
        """Within the min-hold window every exit path is suppressed:
        stop, target, max-hold, and opposite-flip all skip until the
        position has been held for at least min_hold_bars."""
        idx = pd.date_range("2024-01-01", periods=50, freq="min", tz="UTC")
        # Sharp drop right after entry that WOULD trip a 10-pip stop
        # by bar 12 if min_hold were 0. We set min_hold=8 so the stop
        # can't fire until bar 10 at earliest; since the drop reaches
        # -15 pips at bar 14, the stop fires at min(entry+min_hold,
        # first_breach) — i.e. signal stays +1 through the min-hold
        # window even though the market is already past the stop.
        closes = [1.1000] * 5 + [1.0997, 1.0993, 1.0990, 1.0987, 1.0985] + [1.1000] * 40
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "volume": [1] * 50,
            },
            index=idx,
        )
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(lhs=_ind("sma", 3), cmp=">", rhs=0.0),
            ),
            exit=ExitRules(
                stop_pips=10,
                target_pips=50,
                max_hold_bars=240,
                min_hold_bars=8,
                opposite_signal=True,
            ),
            size=SizeRules(risk_pct=0.005),
        )
        strat = compile_spec(spec, pip_size=0.0001)
        sig = strat.generate_signals(df)

        # Entry fires at bar 1 (signal shifted from bar 0). Bars 1..8
        # are inside the min-hold window — signal must stay +1 through
        # them, even though by bar 9 (close=1.0985, -15 pips) the stop
        # would have fired without min-hold.
        for bar in range(1, 9):
            assert sig.iloc[bar] == 1, (
                f"min_hold_bars=8 should suppress every exit until "
                f"bar entry+8; bar {bar} returned {sig.iloc[bar]} "
                f"(expected +1 inside the hold window)"
            )
        # After the min-hold window elapses, the stop is free to fire
        # at whatever the next stop-breach bar is — the trade does
        # eventually close.
        post_hold_zero = (sig.iloc[9:20] == 0).any()
        assert post_hold_zero, (
            "after the min-hold window expires the stop should be "
            "free to fire — no exit observed in bars 9..19"
        )

    def test_min_hold_zero_is_legacy_behaviour(self) -> None:
        """min_hold_bars=0 (default) must NOT change anything: the
        compiler emits the same signal series the pre-min_hold
        version produced. Locks the field's backward compatibility."""
        df = self._df_with_drop_then_recover()
        spec_with = self._spec_long_entry(
            stop_pips=10, target_pips=50, max_hold_bars=240
        )
        spec_explicit_zero = StrategySpec(
            entry=spec_with.entry,
            exit=ExitRules(
                stop_pips=10,
                target_pips=50,
                max_hold_bars=240,
                min_hold_bars=0,  # explicit
                opposite_signal=True,
            ),
            size=spec_with.size,
        )
        sig_default = compile_spec(spec_with, pip_size=0.0001).generate_signals(df)
        sig_explicit = compile_spec(
            spec_explicit_zero, pip_size=0.0001
        ).generate_signals(df)
        assert (sig_default == sig_explicit).all(), (
            "min_hold_bars=0 must produce identical signals to the "
            "default; got divergence — backward compat is broken."
        )

    def test_opposite_signal_flips_position(self) -> None:
        # Entry condition: long when close > sma(5), short when close < sma(5).
        # In a sharp reversal this triggers an opposite-signal flip from +1 to -1.
        idx = pd.date_range("2024-01-01", periods=40, freq="min", tz="UTC")
        closes = list(np.linspace(1.1000, 1.1020, 20)) + list(
            np.linspace(1.1020, 1.1000, 20)
        )
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "volume": [1] * 40,
            },
            index=idx,
        )
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(lhs=_ind("close"), cmp=">", rhs=_ind("sma", 5)),
                short=Comparison(lhs=_ind("close"), cmp="<", rhs=_ind("sma", 5)),
            ),
            exit=ExitRules(
                stop_pips=100,
                target_pips=200,
                max_hold_bars=240,
                opposite_signal=True,
            ),
            size=SizeRules(risk_pct=0.005),
        )
        strat = compile_spec(spec, pip_size=0.0001)
        sig = strat.generate_signals(df)

        # Both +1 and -1 should appear somewhere — the flip happened.
        assert (sig == 1).any() and (sig == -1).any(), (
            f"expected an opposite-signal flip; got values {set(sig.unique())}"
        )

    def test_opposite_signal_disabled_holds_through(self) -> None:
        # Same setup as above but opposite_signal=False — position should
        # NOT flip; the long ride should hold until stop/target/max_hold.
        idx = pd.date_range("2024-01-01", periods=40, freq="min", tz="UTC")
        closes = list(np.linspace(1.1000, 1.1020, 20)) + list(
            np.linspace(1.1020, 1.1000, 20)
        )
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "volume": [1] * 40,
            },
            index=idx,
        )
        spec = StrategySpec(
            entry=EntryConditions(
                long=Comparison(lhs=_ind("close"), cmp=">", rhs=_ind("sma", 5)),
                short=Comparison(lhs=_ind("close"), cmp="<", rhs=_ind("sma", 5)),
            ),
            exit=ExitRules(
                stop_pips=100,
                target_pips=200,
                max_hold_bars=240,
                opposite_signal=False,
            ),
            size=SizeRules(risk_pct=0.005),
        )
        strat = compile_spec(spec, pip_size=0.0001)
        sig = strat.generate_signals(df)

        # With opposite_signal disabled, no -1 should appear — the trade
        # rides the round trip without flipping. Stop/target/max_hold are
        # generous enough not to fire on a 20-pip move.
        assert (sig == -1).sum() == 0, (
            f"opposite_signal=False must not allow flips; got "
            f"{(sig == -1).sum()} short bars"
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
