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

    def test_is_oscillator_only_rsi(self) -> None:
        assert indicators.is_oscillator("rsi")
        assert not indicators.is_oscillator("sma")
        assert not indicators.is_oscillator("ema")
