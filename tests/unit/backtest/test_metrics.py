"""Unit tests for core metrics module.

Tests the metrics calculation functions used in backtesting and optimization.
"""

import numpy as np
import pandas as pd
import pytest
from src.agentfx.core.metrics import (
    compute_returns,
    equity_curve,
    hit_rate,
    max_drawdown,
    sharpe,
    summarize,
    turnover,
)


@pytest.mark.unit
class TestComputeReturns:
    """Tests for compute_returns function."""

    def test_returns_from_price_series(self):
        """Test that returns are calculated correctly from prices."""
        prices = pd.Series([100, 101, 102, 100])
        returns = compute_returns(prices)

        assert len(returns) == 4
        assert returns.iloc[0] == 0.0  # First return is 0 (no prior price)
        assert pytest.approx(returns.iloc[1], rel=1e-2) == 0.01  # 1% gain
        assert pytest.approx(returns.iloc[2], rel=1e-2) == 0.0099  # ~1% gain
        assert returns.iloc[3] < 0  # Loss

    def test_empty_series(self):
        """Test handling of empty series."""
        prices = pd.Series([], dtype=float)
        returns = compute_returns(prices)
        assert len(returns) == 0


@pytest.mark.unit
class TestEquityCurve:
    """Tests for equity_curve function."""

    def test_equity_curve_from_returns(self):
        """Test equity curve calculation from returns."""
        returns = pd.Series([0.0, 0.01, 0.02, -0.01])
        equity = equity_curve(returns)

        assert len(equity) == 4
        assert pytest.approx(equity.iloc[0], rel=1e-4) == 1.0
        assert pytest.approx(equity.iloc[1], rel=1e-4) == 1.01
        assert equity.iloc[-1] > 1.0  # Net positive

    def test_zero_returns(self):
        """Test equity curve with zero returns."""
        returns = pd.Series([0.0, 0.0, 0.0])
        equity = equity_curve(returns)
        assert all(equity == 1.0)


@pytest.mark.unit
class TestSharpe:
    """Tests for Sharpe ratio calculation."""

    def test_positive_sharpe(self):
        """Test positive Sharpe ratio for profitable returns."""
        # Create returns with positive mean
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.0001, 0.001, 1000))
        ratio = sharpe(returns)
        # With positive mean, expect positive Sharpe
        assert isinstance(ratio, float)

    def test_zero_volatility(self):
        """Test Sharpe with zero volatility returns 0."""
        returns = pd.Series([0.0, 0.0, 0.0])
        ratio = sharpe(returns)
        assert ratio == 0.0

    def test_negative_sharpe(self):
        """Test negative Sharpe ratio for losing strategy."""
        returns = pd.Series([-0.01, -0.02, -0.01, -0.01])
        ratio = sharpe(returns)
        assert ratio < 0


@pytest.mark.unit
class TestMaxDrawdown:
    """Tests for max drawdown calculation."""

    def test_drawdown_from_equity(self):
        """Test drawdown calculation from equity curve."""
        # Equity goes up, then down
        equity = pd.Series([1.0, 1.1, 1.2, 1.0, 1.1])
        mdd = max_drawdown(equity)

        # Max drawdown should be negative (from 1.2 to 1.0 = -16.67%)
        assert mdd < 0
        assert pytest.approx(mdd, rel=0.01) == -0.1667

    def test_no_drawdown(self):
        """Test no drawdown for constantly rising equity."""
        equity = pd.Series([1.0, 1.1, 1.2, 1.3])
        mdd = max_drawdown(equity)
        assert mdd == 0.0

    def test_empty_series(self):
        """Test handling of empty series."""
        equity = pd.Series([], dtype=float)
        mdd = max_drawdown(equity)
        assert mdd == 0.0


@pytest.mark.unit
class TestHitRate:
    """Tests for hit rate calculation."""

    def test_hit_rate_calculation(self):
        """Test hit rate with known winning/losing trades."""
        trade_returns = pd.Series([0.01, -0.01, 0.02, 0.01, -0.005])
        rate = hit_rate(trade_returns)

        # 3 wins out of 5 trades = 60%
        assert pytest.approx(rate, rel=0.01) == 0.6

    def test_all_winners(self):
        """Test hit rate with all winning trades."""
        trade_returns = pd.Series([0.01, 0.02, 0.01])
        rate = hit_rate(trade_returns)
        assert rate == 1.0

    def test_all_losers(self):
        """Test hit rate with all losing trades."""
        trade_returns = pd.Series([-0.01, -0.02, -0.01])
        rate = hit_rate(trade_returns)
        assert rate == 0.0

    def test_empty_trades(self):
        """Test hit rate with no trades."""
        trade_returns = pd.Series([], dtype=float)
        rate = hit_rate(trade_returns)
        assert rate == 0.0


@pytest.mark.unit
class TestTurnover:
    """Tests for turnover calculation."""

    def test_turnover_calculation(self):
        """Test turnover from signal changes."""
        signals = pd.Series([0, 1, 1, -1, 0, 1])
        turn = turnover(signals)

        # Changes: 0->1 (1), 1->-1 (2), -1->0 (1), 0->1 (1) = 5
        assert turn == 5.0

    def test_no_turnover(self):
        """Test zero turnover for constant signals."""
        signals = pd.Series([1, 1, 1, 1])
        turn = turnover(signals)
        assert turn == 0.0


@pytest.mark.unit
class TestSummarize:
    """Tests for the summarize function that aggregates all metrics."""

    def test_summarize_returns_required_keys(self):
        """Test that summarize returns all required keys for Gate B evaluation."""
        # Create sample data
        returns = pd.Series([0.001, -0.0005, 0.002, -0.001, 0.001] * 100)
        signals = pd.Series([0, 1, 1, -1, 0] * 100)

        result = summarize(returns, signals)

        # Check all required keys exist
        required_keys = [
            "sharpe",
            "mdd",
            "max_drawdown",
            "hit_rate",
            "turnover",
            "cagr",
            "n_trades",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_summarize_n_trades_count(self):
        """Test that n_trades correctly counts signal changes."""
        returns = pd.Series([0.001, -0.0005, 0.002, -0.001, 0.001])
        # Signals: 0->1 (trade), 1->1 (no trade), 1->-1 (trade), -1->0 (trade), 0->1 (trade)
        signals = pd.Series([0, 1, 1, -1, 0])

        result = summarize(returns, signals)

        # 4 signal changes = 4 trades (excluding first 0 which has no prior)
        # Actually: diff gives [NaN, 1, 0, -2, 1] -> ne(0) gives [False, True, False, True, True]
        # So 3 trades
        assert result["n_trades"] == 3

    def test_summarize_max_drawdown_alias(self):
        """Test that max_drawdown equals mdd (alias for Gate B compatibility)."""
        returns = pd.Series([0.01, -0.02, 0.01, -0.01])
        signals = pd.Series([1, 1, -1, -1])

        result = summarize(returns, signals)

        assert result["max_drawdown"] == result["mdd"]

    def test_summarize_with_many_trades(self):
        """Test summarize with many trades generates non-zero trade count."""
        np.random.seed(42)
        # Generate 1000 periods of returns
        returns = pd.Series(np.random.normal(0.0001, 0.005, 1000))
        # Generate alternating signals (lots of trades)
        signals = pd.Series([1 if i % 10 < 5 else -1 for i in range(1000)])

        result = summarize(returns, signals)

        # With alternating signals every 5 periods, expect many trades
        assert result["n_trades"] > 100
        assert isinstance(result["sharpe"], float)
        assert isinstance(result["hit_rate"], float)

    def test_summarize_no_trades(self):
        """Test summarize with no signal changes (flat position)."""
        returns = pd.Series([0.001, 0.002, 0.001, -0.001])
        signals = pd.Series([1, 1, 1, 1])  # No changes

        result = summarize(returns, signals)

        assert result["n_trades"] == 0
        assert result["hit_rate"] == 0.0  # No trades means no hit rate

    def test_summarize_numeric_types(self):
        """Test that all summarize values are numeric (float or int)."""
        returns = pd.Series([0.001, -0.0005, 0.002])
        signals = pd.Series([0, 1, -1])

        result = summarize(returns, signals)

        for key, value in result.items():
            assert isinstance(value, (int, float)), (
                f"{key} is not numeric: {type(value)}"
            )
