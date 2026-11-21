"""Gate B threshold evaluation."""

from __future__ import annotations

import pytest

from agentfx.api.schemas.optimization import TrialMetrics
from agentfx.api.schemas.optimization import evaluate_gate_b as evaluate_trial_gate_b
from agentfx.core.gates import GATE_B_THRESHOLDS, evaluate_gate_b

# metrics.py returns drawdown as a NEGATIVE fraction; Gate B must treat
# drawdown as a magnitude so either sign is judged against the 10% cap
# (AFX-63). Boundary ±0.10 is "at the cap" and passes.
MDD_SIGN_CASES = [
    pytest.param(+0.35, False, id="+0.35-fails"),
    pytest.param(-0.35, False, id="-0.35-fails"),
    pytest.param(+0.09, True, id="+0.09-passes"),
    pytest.param(-0.09, True, id="-0.09-passes"),
    pytest.param(+0.10, True, id="+0.10-boundary-passes"),
    pytest.param(-0.10, True, id="-0.10-boundary-passes"),
]


class TestEvaluateGateB:
    def test_passes_when_all_metrics_meet_thresholds(self) -> None:
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
            "cagr": 0.20,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is True
        assert failures == []

    def test_passes_at_exact_thresholds(self) -> None:
        # Boundary: each metric exactly at the threshold
        metrics = {
            "sharpe": GATE_B_THRESHOLDS["sharpe_min"],
            "max_drawdown": GATE_B_THRESHOLDS["max_drawdown"],
            "hit_rate": GATE_B_THRESHOLDS["hit_rate_min"],
            "n_trades": GATE_B_THRESHOLDS["min_trades"],
            "cagr": GATE_B_THRESHOLDS["min_cagr"],
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is True
        assert failures == []

    def test_fails_low_sharpe(self) -> None:
        metrics = {
            "sharpe": 0.8,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert any("Sharpe" in f for f in failures)

    def test_fails_high_drawdown(self) -> None:
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.15,
            "hit_rate": 0.55,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert any("Max DD" in f for f in failures)

    def test_fails_low_hit_rate(self) -> None:
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.40,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert any("Hit Rate" in f for f in failures)

    def test_fails_low_trade_count(self) -> None:
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 100,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert any("Trade Count" in f for f in failures)

    def test_reports_all_failures_not_just_first(self) -> None:
        metrics = {
            "sharpe": 0.5,
            "max_drawdown": 0.20,
            "hit_rate": 0.30,
            "n_trades": 50,
            "cagr": 0.05,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 5  # all five thresholds breached

    def test_missing_metrics_treated_as_failure(self) -> None:
        # Missing keys should default to failing values, not crash
        passes, failures = evaluate_gate_b({})
        assert passes is False
        assert len(failures) == 5  # all five thresholds breached via defaults

    def test_per_call_override_relaxes_min_cagr(self) -> None:
        # A profitable-but-not-15% strategy passes when min_cagr is
        # relaxed via the per-call override (e.g. a run-config knob).
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
            "cagr": 0.08,  # 8% — below the 15% default
        }
        passes_default, _ = evaluate_gate_b(metrics)
        assert passes_default is False  # 8% < 15% default

        passes_relaxed, _ = evaluate_gate_b(metrics, overrides={"min_cagr": 0.05})
        assert passes_relaxed is True  # 8% > 5% override


class TestMaxDrawdownSign:
    @pytest.mark.parametrize(("max_dd", "expected_pass"), MDD_SIGN_CASES)
    def test_core_gate_judges_drawdown_magnitude(
        self, max_dd: float, expected_pass: bool
    ) -> None:
        metrics = {
            "sharpe": 2.0,
            "max_drawdown": max_dd,
            "hit_rate": 0.55,
            "n_trades": 500,
            "cagr": 0.5,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is expected_pass
        if expected_pass:
            assert failures == []
        else:
            assert failures == [f"Max DD {abs(max_dd) * 100:.1f}% > cap 10%"]

    @pytest.mark.parametrize(("max_dd", "expected_pass"), MDD_SIGN_CASES)
    def test_optimization_trial_gate_judges_drawdown_magnitude(
        self, max_dd: float, expected_pass: bool
    ) -> None:
        metrics = TrialMetrics(
            sharpe=2.0,
            max_drawdown=max_dd,
            hit_rate=0.55,
            trade_count=500,
            total_return=0.5,
            profit_factor=1.5,
        )
        passes, failures = evaluate_trial_gate_b(metrics)
        assert passes is expected_pass
        assert any("Max DD" in f for f in failures) is (not expected_pass)


class TestThresholdConstants:
    def test_thresholds_match_mvp_constraints(self) -> None:
        # Documents the canonical values from planning/MVP_CONSTRAINTS.md.
        # If a roadmap-level decision changes a threshold, this test must be
        # updated alongside DECISIONS.md.
        assert GATE_B_THRESHOLDS["sharpe_min"] == 1.0
        assert GATE_B_THRESHOLDS["max_drawdown"] == 0.10
        assert GATE_B_THRESHOLDS["hit_rate_min"] == 0.48
        assert GATE_B_THRESHOLDS["min_trades"] == 300
        assert GATE_B_THRESHOLDS["min_cagr"] == 0.15


class TestBackwardCompatibilityImport:
    def test_can_still_import_from_optimizer(self) -> None:
        # v0.2.0 callers may have imported these from agents.optimizer.
        # Re-exports preserved during the refactor — this confirms no
        # downstream import path was silently broken.
        from agentfx.agents.optimizer import (
            GATE_B_THRESHOLDS as legacy_thresholds,
        )
        from agentfx.agents.optimizer import (
            evaluate_gate_b as legacy_evaluate,
        )

        assert legacy_thresholds is GATE_B_THRESHOLDS
        assert legacy_evaluate is evaluate_gate_b
