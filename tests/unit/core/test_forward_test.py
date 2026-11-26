"""Unit tests for pre-agreed paper forward-test contract and evaluation (AFX-154)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from agentfx.core.forward_test import (
    FORWARD_TEST_DEFAULTS,
    ForwardTestContract,
    compute_sharpe_divergence,
    evaluate_forward_test,
)


def _contract(**overrides) -> ForwardTestContract:
    kwargs = {
        "min_closed_trades": 100,
        "min_net_pnl": 0.0,
        "min_sharpe": 1.0,
        "max_drawdown": 0.10,
        "max_divergence": 0.30,
        "backtest_oos_sharpe": 1.6,
        "locked_at": datetime(2026, 11, 25, 12, 0, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return ForwardTestContract.create(**kwargs)


class TestForwardTestContract:
    def test_defaults_match_mvp_constraints(self) -> None:
        assert FORWARD_TEST_DEFAULTS["min_closed_trades"] == 100
        assert FORWARD_TEST_DEFAULTS["min_net_pnl"] == 0.0
        assert FORWARD_TEST_DEFAULTS["min_sharpe"] == 1.0
        assert FORWARD_TEST_DEFAULTS["max_drawdown"] == 0.10
        assert FORWARD_TEST_DEFAULTS["max_divergence"] == 0.30

    def test_roundtrip_serialization(self) -> None:
        c = _contract(backtest_oos_hit_rate=0.54, backtest_oos_max_drawdown=-0.06)
        raw = c.to_dict()
        restored = ForwardTestContract.from_dict(raw)
        assert restored == c

    @pytest.mark.parametrize(
        "bad_input",
        [
            None,
            {},
            {"min_closed_trades": 100},
            {"locked_at": "2026-11-25", "min_closed_trades": 0},
        ],
    )
    def test_from_dict_returns_none_on_invalid_payload(self, bad_input) -> None:
        assert ForwardTestContract.from_dict(bad_input) is None

    def test_rejects_invalid_thresholds(self) -> None:
        with pytest.raises(ValueError, match="min_closed_trades"):
            _contract(min_closed_trades=0)
        with pytest.raises(ValueError, match="max_drawdown"):
            _contract(max_drawdown=0.0)
        with pytest.raises(ValueError, match="max_divergence"):
            _contract(max_divergence=1.5)


class TestComputeSharpeDivergence:
    def test_zero_when_paper_matches_or_beats_backtest(self) -> None:
        assert compute_sharpe_divergence(1.6, 1.6) == 0.0
        assert compute_sharpe_divergence(2.2, 1.6) == 0.0

    def test_positive_degradation_fraction(self) -> None:
        assert compute_sharpe_divergence(1.2, 2.0) == pytest.approx(0.40)

    def test_none_when_inputs_missing_or_non_positive_baseline(self) -> None:
        assert compute_sharpe_divergence(None, 1.5) is None
        assert compute_sharpe_divergence(1.5, None) is None
        assert compute_sharpe_divergence(1.5, 0.0) is None
        assert compute_sharpe_divergence(1.5, -0.5) is None
        assert compute_sharpe_divergence(math.nan, 1.5) is None


class TestEvaluateForwardTest:
    def test_missing_contract_fails_closed(self) -> None:
        ev = evaluate_forward_test(
            None,
            {
                "closed_trades": 150,
                "net_pnl": 500.0,
                "sharpe": 1.8,
                "max_drawdown": -0.04,
            },
        )
        assert ev.status == "no_contract"
        assert ev.eligible_for_live is False
        assert ev.contract is None
        assert any("no locked forward-test contract" in f for f in ev.failures)

    def test_insufficient_trades_blocks_live_and_reports_progress(self) -> None:
        ev = evaluate_forward_test(
            _contract(),
            {
                "closed_trades": 11,
                "net_pnl": 120.0,
                "sharpe": 1.5,
                "max_drawdown": -0.03,
                "hit_rate": 0.55,
            },
        )
        assert ev.status == "in_progress"
        assert ev.eligible_for_live is False
        assert ev.early_failure is False
        assert ev.closed_trades == 11
        assert ev.min_closed_trades == 100
        assert ev.trades_remaining == 89
        assert ev.progress_pct == 11.0
        assert ev.progress_label == "11 / 100 trades"
        assert any("11 / 100" in f for f in ev.failures)
        by_name = {c.name: c for c in ev.criteria}
        assert by_name["min_closed_trades"].status == "pending"
        assert by_name["max_drawdown"].status == "passed"
        assert by_name["net_positive_pnl"].status == "pending"
        assert by_name["min_sharpe"].status == "pending"
        assert by_name["backtest_divergence"].status == "pending"

    @pytest.mark.parametrize("mdd", [-0.12, 0.12])
    def test_early_live_risk_breach_fails_before_min_trades(self, mdd: float) -> None:
        ev = evaluate_forward_test(
            _contract(max_drawdown=0.10),
            {
                "closed_trades": 11,
                "net_pnl": -1200.0,
                "sharpe": -0.5,
                "max_drawdown": mdd,
            },
        )
        assert ev.status == "failed"
        assert ev.eligible_for_live is False
        assert ev.early_failure is True
        by_name = {c.name: c for c in ev.criteria}
        assert by_name["max_drawdown"].status == "failed"
        assert any("Early live-risk breach" in f for f in ev.failures)

    def test_passing_contract_is_eligible_for_live(self) -> None:
        ev = evaluate_forward_test(
            _contract(backtest_oos_sharpe=1.6),
            {
                "closed_trades": 100,
                "net_pnl": 425.50,
                "sharpe": 1.4,  # 12.5% degradation <= 30%
                "max_drawdown": -0.06,
                "hit_rate": 0.52,
            },
        )
        assert ev.status == "passed"
        assert ev.eligible_for_live is True
        assert ev.early_failure is False
        assert ev.trades_remaining == 0
        assert ev.progress_pct == 100.0
        assert ev.progress_label == "100 / 100 trades"
        assert ev.failures == []
        assert all(c.status == "passed" for c in ev.criteria)

    def test_exact_boundary_values(self) -> None:
        ev = evaluate_forward_test(
            _contract(backtest_oos_sharpe=2.0, max_divergence=0.30, max_drawdown=0.10),
            {
                "closed_trades": 100,
                "net_pnl": 0.01,
                "sharpe": 1.4,  # exact 30% degradation
                "max_drawdown": -0.10,  # exact 10% cap
            },
        )
        assert ev.status == "passed"
        assert ev.eligible_for_live is True

    def test_zero_net_pnl_fails_net_positive_criterion(self) -> None:
        ev = evaluate_forward_test(
            _contract(backtest_oos_sharpe=1.5),
            {
                "closed_trades": 105,
                "net_pnl": 0.0,
                "sharpe": 1.4,
                "max_drawdown": -0.05,
            },
        )
        assert ev.status == "failed"
        assert ev.eligible_for_live is False
        by_name = {c.name: c for c in ev.criteria}
        assert by_name["net_positive_pnl"].status == "failed"

    def test_fails_when_sharpe_below_threshold(self) -> None:
        ev = evaluate_forward_test(
            _contract(backtest_oos_sharpe=None, min_sharpe=1.0),
            {
                "closed_trades": 120,
                "net_pnl": 300.0,
                "sharpe": 0.75,
                "max_drawdown": -0.04,
            },
        )
        assert ev.status == "failed"
        assert ev.eligible_for_live is False
        by_name = {c.name: c for c in ev.criteria}
        assert by_name["min_sharpe"].status == "failed"
        assert by_name["backtest_divergence"].status == "skipped"

    def test_fails_when_backtest_divergence_exceeds_limit(self) -> None:
        # Paper Sharpe 1.2 clears min_sharpe=1.0, but degrades 40% from OOS 2.0 > 30% cap.
        ev = evaluate_forward_test(
            _contract(backtest_oos_sharpe=2.0, max_divergence=0.30),
            {
                "closed_trades": 110,
                "net_pnl": 600.0,
                "sharpe": 1.2,
                "max_drawdown": -0.05,
            },
        )
        assert ev.status == "failed"
        assert ev.eligible_for_live is False
        by_name = {c.name: c for c in ev.criteria}
        assert by_name["min_sharpe"].status == "passed"
        assert by_name["backtest_divergence"].status == "failed"
        assert any("degradation 40.0%" in f for f in ev.failures)

    def test_non_finite_metrics_fail_closed_once_min_trades_reached(self) -> None:
        ev = evaluate_forward_test(
            _contract(),
            {
                "closed_trades": 100,
                "net_pnl": math.nan,
                "sharpe": math.inf,
                "max_drawdown": math.nan,
            },
        )
        assert ev.status == "failed"
        assert ev.eligible_for_live is False
        assert len(ev.failures) == 4
