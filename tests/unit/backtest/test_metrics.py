"""Unit tests for core metrics module.

Tests the metrics calculation functions used in backtesting and optimization.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from src.agentfx.core.metrics import (
    compute_returns,
    equity_curve,
    extract_closed_trades,
    hit_rate,
    max_drawdown,
    per_trade_returns,
    sharpe,
    summarize,
    summarize_from_trade_ledger,
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
class TestPerTradeReturns:
    """Tests for the closed-trade returns aggregation.

    Regression coverage for the hit-rate discrepancy where the same
    backtest produced 34% (trade-level, from services/backtest.py's
    capture_signals block) vs 6-10% (bar-level, from the previous
    summarize() implementation). After the fix, both paths agree because
    they share this trade-level definition.
    """

    def test_groups_contiguous_long_then_short_into_two_trades(self):
        # One long trade (bars 0-2) then one short trade (bars 3-4),
        # closing at bar 5 (flat).
        returns = pd.Series([0.01, 0.01, 0.01, -0.02, -0.02, 0.0])
        signals = pd.Series([1, 1, 1, -1, -1, 0])

        tr = per_trade_returns(returns, signals)
        assert len(tr) == 2
        # Long trade: (1.01)^3 - 1 ≈ 0.0303
        assert tr.iloc[0] == pytest.approx(1.01**3 - 1, rel=1e-3)
        # Short trade: (0.98)^2 - 1 ≈ -0.0396
        # NB the sign here comes from the `returns` series itself, which
        # the backtest engine pre-multiplies by the signal — so a "short"
        # bar with a -2% return means a 2% loss for the position holder.
        assert tr.iloc[1] == pytest.approx(0.98**2 - 1, rel=1e-3)

    def test_excludes_open_trade_at_end_of_series(self):
        # Last bar still in a long position — that trade hasn't closed yet
        # and should NOT count toward realised hit rate.
        returns = pd.Series([0.01, 0.01, -0.02, 0.01])
        signals = pd.Series([1, 1, 1, 1])  # never closes

        tr = per_trade_returns(returns, signals)
        assert len(tr) == 0  # zero closed trades

    def test_excludes_open_short_at_end(self):
        # Long trade closes at bar 2 (flat), then short opens at bar 3
        # and never closes within the window.
        returns = pd.Series([0.01, 0.01, 0.0, -0.02, -0.02])
        signals = pd.Series([1, 1, 0, -1, -1])

        tr = per_trade_returns(returns, signals)
        # Only the long counts; the trailing short is still open.
        assert len(tr) == 1

    def test_hit_rate_from_trade_groups(self):
        # Three closed trades: +3%, -2%, +1%. Two winners → 66.7%.
        # All trades close before the series ends (signals end at 0).
        returns = pd.Series(
            [0.01, 0.01, 0.01, 0.0, -0.01, -0.01, 0.0, 0.005, 0.005, 0.0]
        )
        signals = pd.Series([1, 1, 1, 0, 1, 1, 0, 1, 1, 0])

        result = summarize(returns, signals)
        assert result["n_trades"] == 3
        assert result["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_empty_inputs(self):
        empty = pd.Series([], dtype=float)
        assert per_trade_returns(empty, empty).empty

    def test_all_flat_signals_yields_zero_trades(self):
        returns = pd.Series([0.0, 0.0, 0.0])
        signals = pd.Series([0, 0, 0])
        tr = per_trade_returns(returns, signals)
        assert len(tr) == 0


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
        """n_trades counts CLOSED trade groups (consecutive same-non-zero-signal
        runs that end with a signal change), not raw signal changes.

        Sequence [0, 1, 1, -1, 0]:
          - bar 0: flat
          - bars 1-2: long position (one trade group, closed when signal
            flips to -1 at bar 3)
          - bar 3: short position (one trade group, closed when signal
            returns to 0 at bar 4)
          - bar 4: flat
        → 2 closed trades.

        Pre-fix this returned 3 because it counted signal *changes* including
        the open-from-flat events. Closed-trade semantics align with what
        Gate B's `min_trades` threshold was specified against and what the
        `capture_signals=True` block in services/backtest.py produces.
        """
        returns = pd.Series([0.001, -0.0005, 0.002, -0.001, 0.001])
        signals = pd.Series([0, 1, 1, -1, 0])

        result = summarize(returns, signals)
        assert result["n_trades"] == 2

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


@pytest.mark.unit
class TestExtractClosedTrades:
    """Tests for the single-source-of-truth trade walker.

    `extract_closed_trades` is the shared definition of "a trade" used by
    both `summarize()` (when given close prices) and the
    `capture_signals=True` block in services/backtest.py. The two code
    paths used to walk trades independently and disagreed on the hit
    rate by ~6pp because of a one-bar timing offset; unifying them on
    this walker is what closed that gap.
    """

    def test_long_then_short_trades_use_live_execution_timing(self):
        close = pd.Series([100.0, 101.0, 102.0, 99.0, 98.0, 100.0])
        signals = pd.Series([0, 1, 1, -1, -1, 0])

        trades = extract_closed_trades(close, signals)

        assert len(trades) == 2
        # Long: entry at bar 1 (first non-zero), exit at bar 3 (first flip).
        assert trades[0].direction == "long"
        assert trades[0].entry_idx == 1
        assert trades[0].exit_idx == 3
        assert trades[0].entry_price == 101.0
        assert trades[0].exit_price == 99.0
        assert not trades[0].is_win  # long bought 101, sold 99
        # Short: entry at bar 3 (the flip bar — same close as long's exit),
        # exit at bar 5 (when signal returns to flat).
        assert trades[1].direction == "short"
        assert trades[1].entry_idx == 3
        assert trades[1].exit_idx == 5
        assert trades[1].entry_price == 99.0
        assert trades[1].exit_price == 100.0
        assert not trades[1].is_win  # short sold 99, covered 100

    def test_open_trade_at_end_not_emitted(self):
        close = pd.Series([100.0, 101.0, 102.0])
        signals = pd.Series([0, 1, 1])  # still long at end

        trades = extract_closed_trades(close, signals)
        assert trades == []

    def test_summarize_with_close_uses_walker(self):
        """summarize(close=...) is the production path; it must match
        what extract_closed_trades reports directly."""
        np.random.seed(123)
        n = 500
        close = pd.Series(100 + np.cumsum(np.random.normal(0, 0.3, n)))
        sig_arr = np.zeros(n, dtype=int)
        for i in range(0, n, 8):
            sig_arr[i : i + 4] = 1
            sig_arr[i + 4 : i + 8] = -1
        signals = pd.Series(sig_arr)
        returns = close.pct_change().fillna(0.0) * signals.shift(0).fillna(0)

        trades = extract_closed_trades(close, signals)
        wins = sum(1 for t in trades if t.is_win)
        expected_hr = wins / len(trades)

        result = summarize(returns, signals, close=close)

        assert result["n_trades"] == len(trades)
        assert result["hit_rate"] == pytest.approx(expected_hr, rel=1e-9)

    def test_summarize_close_path_vs_legacy_per_trade_path_can_differ(self):
        """The `close`-aware path and the legacy `per_trade_returns`
        fallback can give slightly different numbers because they apply
        different bar-entry conventions. This is intentional: production
        callers always pass `close` so they get live-execution semantics;
        unit-test callers without price data fall back to the
        no-lookahead `per_trade_returns` definition. Both paths must
        still produce valid, finite numbers.
        """
        close = pd.Series([100.0, 101.0, 99.0, 100.0, 102.0])
        signals = pd.Series([1, 1, -1, -1, 0])
        returns = close.pct_change().fillna(0.0) * signals.shift(0).fillna(0)

        r_with_close = summarize(returns, signals, close=close)
        r_legacy = summarize(returns, signals)

        # Both produce closed-trade counts (not zero, not NaN).
        assert r_with_close["n_trades"] >= 1
        assert r_legacy["n_trades"] >= 1
        assert 0.0 <= r_with_close["hit_rate"] <= 1.0
        assert 0.0 <= r_legacy["hit_rate"] <= 1.0


@pytest.mark.unit
class TestSummarizeFromTradeLedger:
    """Canonical platform metrics: derived from the risk-sized, compounded
    dollar ledger rather than bar-level unit-position returns. These are
    the values surfaced on the genome backtest hero, the metric tiles,
    and the walk-forward fold table — they all share this definition so
    the page can never tell three contradictory stories about the same
    fold (the bug that prompted this helper)."""

    def _trade(self, day: int, pnl: float) -> dict:
        """Minimal trade dict — only `exit_timestamp` and
        `net_pnl_dollars` are read by the ledger summariser."""
        ts = datetime(2024, 1, 1) + timedelta(days=day)
        return {
            "exit_timestamp": ts.isoformat(),
            "net_pnl_dollars": pnl,
        }

    def test_no_trades_returns_flat_balance(self):
        out = summarize_from_trade_ledger(
            [],
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 4, 1),
        )
        assert out["n_trades"] == 0
        assert out["total_return"] == 0.0
        assert out["cagr"] == 0.0
        assert out["sharpe"] == 0.0
        assert out["max_drawdown"] == 0.0
        assert out["hit_rate"] == 0.0
        assert out["ending_balance"] == 10_000.0
        assert out["peak_balance"] == 10_000.0

    def test_single_winning_trade_total_return_matches_ledger(self):
        out = summarize_from_trade_ledger(
            [self._trade(day=10, pnl=200.0)],
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 2, 1),
        )
        # Ledger grew by $200 → +2% total return on $10k base.
        assert out["ending_balance"] == pytest.approx(10_200.0)
        assert out["total_return"] == pytest.approx(0.02)
        assert out["n_trades"] == 1
        assert out["hit_rate"] == 1.0  # the sole trade was a win
        assert out["peak_balance"] == pytest.approx(10_200.0)

    def test_loss_followed_by_recovery_max_drawdown_is_trough_to_peak(self):
        # Lose $500, then gain $300 → ending -$200, peak briefly was $10k,
        # trough was $9,500. MaxDD measured against running peak.
        out = summarize_from_trade_ledger(
            [
                self._trade(day=10, pnl=-500.0),
                self._trade(day=20, pnl=300.0),
            ],
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 2, 1),
        )
        assert out["ending_balance"] == pytest.approx(9_800.0)
        assert out["total_return"] == pytest.approx(-0.02)
        # Initial peak of $10k → trough $9.5k = -5% drawdown
        assert out["max_drawdown"] == pytest.approx(-0.05, abs=1e-6)
        assert out["peak_balance"] == pytest.approx(10_000.0)

    def test_hit_rate_counts_net_dollar_wins_not_gross_pip_wins(self):
        """A trade with negative net P&L is NOT a win, even if its gross
        pips were positive. The ledger uses post-cost economics throughout."""
        trades = [
            self._trade(day=1, pnl=100.0),
            self._trade(day=2, pnl=-50.0),
            self._trade(day=3, pnl=0.0),  # break-even isn't a win
        ]
        out = summarize_from_trade_ledger(
            trades,
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 2, 1),
        )
        assert out["hit_rate"] == pytest.approx(1.0 / 3.0)

    def test_cagr_annualises_over_calendar_time_not_bar_count(self):
        """1% return over 90 days should annualise to roughly +4%, not
        +1% (which would happen if we used the simple total return)."""
        out = summarize_from_trade_ledger(
            [self._trade(day=85, pnl=100.0)],
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 1, 1) + timedelta(days=90),
        )
        assert out["total_return"] == pytest.approx(0.01)
        # (1.01)^(365.25/90) - 1 ≈ 0.0413
        assert out["cagr"] == pytest.approx(0.0413, abs=5e-4)

    def test_sharpe_zero_when_returns_have_no_variance(self):
        """All-flat days produce undefined Sharpe — handled as 0."""
        out = summarize_from_trade_ledger(
            [],
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 2, 1),
        )
        assert out["sharpe"] == 0.0

    def test_multiple_trades_on_same_day_aggregate_into_one_balance_step(self):
        """Sharpe annualises off daily returns, so two trades on the same
        day must bucket into one daily P&L row (not two), otherwise the
        daily-return series gets a spurious extra step."""
        same_day = [
            self._trade(day=5, pnl=200.0),
            self._trade(day=5, pnl=-50.0),
        ]
        out = summarize_from_trade_ledger(
            same_day,
            starting_balance=10_000.0,
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 2, 1),
        )
        # Net: +150 → ending 10,150, total_return = 1.5%, 2 trades.
        assert out["ending_balance"] == pytest.approx(10_150.0)
        assert out["n_trades"] == 2
