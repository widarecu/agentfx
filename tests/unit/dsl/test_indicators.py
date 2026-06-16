"""Pure indicator function unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl import indicators


def _make_df(closes: list[float]) -> pd.DataFrame:
    """Build an OHLCV DataFrame from a sequence of closes (high=low=close, vol=1)."""
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    arr = np.array(closes, dtype=float)
    return pd.DataFrame(
        {"open": arr, "high": arr, "low": arr, "close": arr, "volume": 1.0},
        index=idx,
    )


def _make_df_oh(highs: list[float], lows: list[float]) -> pd.DataFrame:
    n = len(highs)
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    closes = [(h + l) / 2 for h, l in zip(highs, lows, strict=True)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
        },
        index=idx,
    )


class TestSMA:
    def test_constant_close(self) -> None:
        df = _make_df([5.0] * 10)
        out = indicators.sma(df, 3)
        assert (out == 5.0).all()

    def test_min_periods_one(self) -> None:
        # value available from bar 1 — matches existing MACrossover behavior
        df = _make_df([1.0, 2.0, 3.0])
        out = indicators.sma(df, 5)
        assert out.iloc[0] == 1.0
        assert out.iloc[1] == 1.5
        assert out.iloc[2] == 2.0


class TestEMA:
    def test_constant_close(self) -> None:
        df = _make_df([3.0] * 10)
        out = indicators.ema(df, 5)
        assert (out == 3.0).all()


class TestRSI:
    def test_uptrend_with_pullbacks_high_rsi(self) -> None:
        # Generally up but with periodic pullbacks so avg_loss > 0
        # (pure strict trend → NaN — see test_pure_strict_trend_returns_nan).
        # Pattern: +0.05, +0.05, -0.01 repeating → strong net uptrend with
        # losses present in every 14-bar window.
        deltas = [0.05, 0.05, -0.01] * 25  # 75 bars, net up
        closes = [1.0]
        for d in deltas:
            closes.append(closes[-1] + d)
        df = _make_df(closes)
        out = indicators.rsi(df, 14)
        last_valid = out.dropna().iloc[-1]
        assert last_valid > 70  # solidly bullish

    def test_downtrend_with_bounces_low_rsi(self) -> None:
        deltas = [-0.05, -0.05, 0.01] * 25
        closes = [3.0]
        for d in deltas:
            closes.append(closes[-1] + d)
        df = _make_df(closes)
        out = indicators.rsi(df, 14)
        last_valid = out.dropna().iloc[-1]
        assert last_valid < 30  # solidly bearish

    def test_pure_strict_trend_returns_nan(self) -> None:
        # Documents the existing semantics: strict monotone trend → NaN
        # (because avg_loss == 0). Matches the classic RSIMeanReversion class.
        df = _make_df(list(np.linspace(1.0, 2.0, 30)))
        out = indicators.rsi(df, 14)
        # All-gain bars → avg_loss=0 → rs is NaN → rsi is NaN
        assert out.iloc[-1] != out.iloc[-1]  # NaN check


class TestATR:
    def test_constant_range(self) -> None:
        df = _make_df_oh(highs=[2.0] * 10, lows=[1.0] * 10)
        out = indicators.atr(df, 5)
        # First bar's TR is high-low = 1.0; subsequent bars same
        assert out.iloc[-1] == pytest.approx(1.0)


class TestBollinger:
    def test_constant_close_zero_std(self) -> None:
        df = _make_df([10.0] * 30)
        upper = indicators.bb_upper(df, 20, 2.0)
        lower = indicators.bb_lower(df, 20, 2.0)
        # All-constant => std=0 => upper == lower == close
        assert upper.dropna().iloc[-1] == pytest.approx(10.0)
        assert lower.dropna().iloc[-1] == pytest.approx(10.0)


class TestDonchian:
    def test_high_excludes_current_bar(self) -> None:
        # Closes 1..10, highs match closes
        df = _make_df_oh(highs=list(range(1, 11)), lows=[0.0] * 10)
        out = indicators.donchian_high(df, 3)
        # Bar 4 (index 3): channel = max of highs at indices 0,1,2 = max(1,2,3) = 3
        assert out.iloc[3] == 3.0
        # Bar 5: max of highs at 1,2,3 = 4
        assert out.iloc[4] == 4.0

    def test_low_excludes_current_bar(self) -> None:
        df = _make_df_oh(highs=[10.0] * 10, lows=list(range(10, 0, -1)))
        out = indicators.donchian_low(df, 3)
        # Bar 4: min of lows at 0,1,2 = min(10,9,8) = 8
        assert out.iloc[3] == 8.0


class TestEvaluateIndicator:
    def test_dispatch(self) -> None:
        df = _make_df([5.0] * 10)
        out = indicators.evaluate_indicator(df, "sma", [3])
        assert isinstance(out, pd.Series)
        assert (out == 5.0).all()


class TestArgBoundsTable:
    def test_all_indicators_have_bounds(self) -> None:
        for name in indicators.INDICATOR_FUNCTIONS:
            assert name in indicators.INDICATOR_ARG_BOUNDS, f"missing bounds for {name}"

    def test_is_oscillator_bounded_indicators(self) -> None:
        # RSI, stochastic, ADX all return values in [0, 100] and group
        # under the "oscillator" family for same-mechanical-event checks.
        assert indicators.is_oscillator("rsi")
        assert indicators.is_oscillator("stoch")
        assert indicators.is_oscillator("adx")
        # Moving averages and channel midpoints return price-units, not
        # bounded oscillators.
        assert not indicators.is_oscillator("sma")
        assert not indicators.is_oscillator("ema")
        assert not indicators.is_oscillator("macd_line")
        assert not indicators.is_oscillator("ichimoku_tenkan")


# ─────────────────────────────────────────────────────────────────────
# v0.4.0 indicator expansion (2026-06-16)
# ─────────────────────────────────────────────────────────────────────


def _make_df_full(highs, lows, closes, volumes=None) -> pd.DataFrame:
    n = len(highs)
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes if volumes is not None else [1.0] * n,
        },
        index=idx,
    )


class TestMACD:
    def test_macd_line_shape_and_zero_when_constant(self) -> None:
        # On a constant series both EMAs equal the constant → diff = 0
        df = _make_df([1.10] * 50)
        out = indicators.macd_line(df, 12, 26)
        assert isinstance(out, pd.Series)
        assert len(out) == 50
        assert out.iloc[-1] == pytest.approx(0.0, abs=1e-9)

    def test_macd_line_positive_on_uptrend(self) -> None:
        df = _make_df([1.10 + 0.0001 * i for i in range(100)])
        out = indicators.macd_line(df, 12, 26)
        # In a steady uptrend the fast EMA leads the slow EMA upward
        assert out.iloc[-1] > 0

    def test_macd_signal_is_ema_of_line(self) -> None:
        df = _make_df([1.10 + 0.0001 * i for i in range(100)])
        line = indicators.macd_line(df, 12, 26)
        signal = indicators.macd_signal(df, 12, 26, 9)
        # The signal smooths the line — for a monotonic uptrend the signal
        # lags below the current line value
        assert signal.iloc[-1] < line.iloc[-1]
        assert len(signal) == len(line)


class TestStoch:
    def test_stoch_range_is_zero_to_hundred(self) -> None:
        # Synthetic OHL data with random walk
        rng = np.random.default_rng(42)
        n = 200
        closes = 1.10 + rng.normal(0, 0.001, n).cumsum()
        highs = closes + 0.0002
        lows = closes - 0.0002
        df = _make_df_full(highs.tolist(), lows.tolist(), closes.tolist())
        out = indicators.stoch(df, 14, 3)
        # After the warmup, all values lie in [0, 100]
        finite = out.dropna()
        assert (finite >= 0).all()
        assert (finite <= 100).all()

    def test_stoch_at_top_of_range_when_close_at_high(self) -> None:
        # Constant lows, close == high → %K should be 100
        highs = [1.105] * 30
        lows = [1.100] * 30
        closes = [1.105] * 30
        df = _make_df_full(highs, lows, closes)
        out = indicators.stoch(df, 14, 1)
        assert out.iloc[-1] == pytest.approx(100.0)

    def test_stoch_at_bottom_of_range_when_close_at_low(self) -> None:
        highs = [1.105] * 30
        lows = [1.100] * 30
        closes = [1.100] * 30
        df = _make_df_full(highs, lows, closes)
        out = indicators.stoch(df, 14, 1)
        assert out.iloc[-1] == pytest.approx(0.0)


class TestADX:
    def test_adx_returns_non_negative_bounded(self) -> None:
        rng = np.random.default_rng(42)
        n = 200
        closes = 1.10 + rng.normal(0, 0.001, n).cumsum()
        highs = closes + 0.0002
        lows = closes - 0.0002
        df = _make_df_full(highs.tolist(), lows.tolist(), closes.tolist())
        out = indicators.adx(df, 14)
        finite = out.dropna()
        assert (finite >= 0).all()
        # ADX is bounded by 100 in theory, in practice rarely > 60
        assert (finite <= 100).all()

    def test_adx_higher_on_trending_than_choppy(self) -> None:
        # Strong uptrend
        n = 200
        trend_closes = [1.10 + 0.0001 * i for i in range(n)]
        trend = _make_df_full(
            [c + 0.00005 for c in trend_closes],
            [c - 0.00005 for c in trend_closes],
            trend_closes,
        )
        # Pure oscillation around 1.10
        chop_closes = [1.10 + 0.0005 * np.sin(i / 4) for i in range(n)]
        chop = _make_df_full(
            [c + 0.0002 for c in chop_closes],
            [c - 0.0002 for c in chop_closes],
            chop_closes,
        )
        trend_adx = indicators.adx(trend, 14).dropna().iloc[-1]
        chop_adx = indicators.adx(chop, 14).dropna().iloc[-1]
        assert trend_adx > chop_adx


class TestOBV:
    def test_obv_zero_args_returns_series(self) -> None:
        df = _make_df([1.10 + 0.001 * i for i in range(20)])
        out = indicators.obv(df)
        assert isinstance(out, pd.Series)
        assert len(out) == 20

    def test_obv_accumulates_on_up_bars(self) -> None:
        closes = [1.10, 1.11, 1.12, 1.13]
        df = _make_df_full(closes, closes, closes, volumes=[100, 200, 300, 400])
        out = indicators.obv(df)
        # First bar's diff is NaN → direction=0 → +0. Subsequent up bars
        # add their volume: 0 + 200 + 300 + 400 = 900
        assert out.iloc[-1] == pytest.approx(900.0)

    def test_obv_subtracts_on_down_bars(self) -> None:
        closes = [1.13, 1.12, 1.11, 1.10]
        df = _make_df_full(closes, closes, closes, volumes=[100, 200, 300, 400])
        out = indicators.obv(df)
        # 0 - 200 - 300 - 400 = -900
        assert out.iloc[-1] == pytest.approx(-900.0)

    def test_obv_degrades_to_zero_when_no_volume_column(self) -> None:
        # Defensive contract: if a degraded data source omits volume,
        # the indicator returns a zero series instead of crashing.
        df = pd.DataFrame(
            {
                "open": [1.10, 1.11],
                "high": [1.10, 1.11],
                "low": [1.10, 1.11],
                "close": [1.10, 1.11],
            }
        )
        out = indicators.obv(df)
        assert (out == 0.0).all()


class TestIchimoku:
    def test_tenkan_is_midpoint_of_range(self) -> None:
        # 9 bars with high=1.110, low=1.100 → midpoint = 1.105
        df = _make_df_full([1.110] * 9, [1.100] * 9, [1.105] * 9)
        out = indicators.ichimoku_tenkan(df, 9)
        assert out.iloc[-1] == pytest.approx(1.105)

    def test_kijun_uses_longer_window(self) -> None:
        # Bars 0-25 in range [1.10, 1.12], bar 25 at 1.13 → kijun(26)
        # midpoint should reflect the WIDER 26-bar range
        n = 26
        highs = [1.12] * (n - 1) + [1.13]
        lows = [1.10] * n
        closes = [1.11] * n
        df = _make_df_full(highs, lows, closes)
        out = indicators.ichimoku_kijun(df, n)
        # Midpoint = (1.13 + 1.10) / 2 = 1.115
        assert out.iloc[-1] == pytest.approx(1.115)


# ─────────────────────────────────────────────────────────────────────
# Dispatch table consistency
# ─────────────────────────────────────────────────────────────────────


class TestDispatchTableConsistency:
    def test_every_function_has_bounds(self) -> None:
        # Pickup new indicators automatically — no per-indicator update needed.
        for name in indicators.INDICATOR_FUNCTIONS:
            assert name in indicators.INDICATOR_ARG_BOUNDS

    def test_every_bounds_entry_has_function(self) -> None:
        for name in indicators.INDICATOR_ARG_BOUNDS:
            assert name in indicators.INDICATOR_FUNCTIONS

    def test_evaluate_indicator_dispatches_all_zero_arg(self) -> None:
        df = _make_df([1.10 + 0.001 * i for i in range(20)])
        # close + obv are the two zero-arg indicators
        for name in ("close", "obv"):
            out = indicators.evaluate_indicator(df, name, [])
            assert isinstance(out, pd.Series)
            assert len(out) == 20
