"""Validator agent tests with a mocked backtest function.

The integrity property under test: Gate B is enforced **only on the OOS
fold**. Training-fold metrics are recorded but do not gate promotion.
A genome that fails Gate B on every training fold but passes it on OOS
should be marked `validator_passed=True`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agentfx.agents.validator import (
    FoldEvaluation,
    ValidationResult,
    ValidatorAgent,
)
from agentfx.core.dsl.seeds import MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _passing_metrics(sharpe: float = 1.5) -> dict:
    """Metrics that pass Gate B."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 500,
        "cagr": 0.20,  # 20% net annualised — above the 15% Gate B floor
    }


def _failing_metrics(sharpe: float = 0.3) -> dict:
    """Metrics that fail Gate B (insufficient trades)."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 100,  # < 300 required
        "cagr": 0.20,
    }


def _backtest_returning(metrics_per_fold: list[dict]) -> MagicMock:
    """Build a mock backtest_fn whose i-th call returns metrics_per_fold[i]."""
    side_effect = [{"metrics": m} for m in metrics_per_fold]
    return MagicMock(side_effect=side_effect)


# ─────────────────────────────────────────────────────────────────────────
# Validator basics
# ─────────────────────────────────────────────────────────────────────────


class TestValidatorBasics:
    def test_default_k_is_four(self) -> None:
        agent = ValidatorAgent()
        assert agent.k == 4

    def test_custom_k(self) -> None:
        agent = ValidatorAgent(k=6)
        assert agent.k == 6

    def test_k_below_two_raises(self) -> None:
        with pytest.raises(ValueError, match="k must be >= 2"):
            ValidatorAgent(k=1)

    def test_agent_type(self) -> None:
        assert ValidatorAgent().agent_type == "validator"


class TestValidateInputs:
    def test_missing_spec_fails(self) -> None:
        agent = ValidatorAgent()
        ok, errs = agent.validate_inputs(
            instrument="EUR_USD", start=_utc(2024, 1, 1), end=_utc(2024, 5, 1)
        )
        assert ok is False
        assert any("spec" in e for e in errs)

    def test_non_strategy_spec_fails(self) -> None:
        agent = ValidatorAgent()
        ok, errs = agent.validate_inputs(
            spec="not a spec",
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
        )
        assert ok is False
        assert any("StrategySpec" in e for e in errs)

    def test_full_inputs_ok(self) -> None:
        agent = ValidatorAgent()
        ok, errs = agent.validate_inputs(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
        )
        assert ok is True
        assert errs == []


# ─────────────────────────────────────────────────────────────────────────
# Walk-forward execution
# ─────────────────────────────────────────────────────────────────────────


class TestValidate:
    def test_calls_backtest_per_fold(self) -> None:
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            granularity="M1",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        assert backtest.call_count == 4
        assert len(result.folds) == 4

    def test_passes_full_dsl_spec_via_strategy_params(self) -> None:
        agent = ValidatorAgent(k=2)
        backtest = _backtest_returning([_passing_metrics()] * 2)
        agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 3, 1),
            backtest_fn=backtest,
        )
        # Each call must dispatch via the DSL path
        for call in backtest.call_args_list:
            kwargs = call.kwargs
            assert kwargs["strategy_name"] == "dsl"
            assert "spec" in kwargs["strategy_params"]
            assert kwargs["strategy_params"]["spec"]["version"] == 1

    def test_each_fold_gets_its_own_date_range(self) -> None:
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        # Extract (start, end) pairs from call args
        ranges = [(c.kwargs["start"], c.kwargs["end"]) for c in backtest.call_args_list]
        # Contiguous: fold[i].end == fold[i+1].start
        for i in range(3):
            assert ranges[i][1] == ranges[i + 1][0]
        # First starts at input start, last ends at input end
        assert ranges[0][0] == _utc(2024, 1, 1)
        assert ranges[3][1] == _utc(2024, 5, 1)

    def test_oos_fold_is_marked(self) -> None:
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        oos_folds = [fe for fe in result.folds if fe.is_oos]
        assert len(oos_folds) == 1
        # OOS is the LAST fold by index
        assert oos_folds[0].fold.index == 3


# ─────────────────────────────────────────────────────────────────────────
# Gate B integrity — the whole point of this agent
# ─────────────────────────────────────────────────────────────────────────


class TestGateBIntegrity:
    def test_passes_when_oos_passes(self) -> None:
        # All training folds also pass — straightforward case
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        assert result.validator_passed is True
        assert result.validation_failures == []

    def test_fails_when_oos_fails(self) -> None:
        # All training folds pass, OOS fails — must fail validation
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning(
            [
                _passing_metrics(),
                _passing_metrics(),
                _passing_metrics(),
                _failing_metrics(),
            ]
        )
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        assert result.validator_passed is False
        assert len(result.validation_failures) > 0
        # The failure must reference Trade Count (the failing dimension)
        assert any("Trade Count" in f for f in result.validation_failures)

    def test_passes_when_only_training_fails_but_oos_passes(self) -> None:
        # KEY INTEGRITY TEST — Gate B is enforced ONLY on OOS, training
        # folds are informational. A genome that randomly underperformed
        # on early folds but works on the held-out OOS still validates.
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning(
            [
                _failing_metrics(),
                _failing_metrics(),
                _failing_metrics(),
                _passing_metrics(),
            ]
        )
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        assert result.validator_passed is True
        assert result.validation_failures == []
        # But training-fold failures are still RECORDED for the audit trail
        train_evals = [fe for fe in result.folds if not fe.is_oos]
        assert all(fe.passed_gate_b is False for fe in train_evals)
        assert all(len(fe.gate_b_failures) > 0 for fe in train_evals)

    def test_fails_when_oos_fails_even_if_training_passes(self) -> None:
        # Conversely: training looked good but OOS doesn't generalize.
        # This is exactly the failure mode walk-forward validation exists
        # to catch.
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning(
            [
                _passing_metrics(),
                _passing_metrics(),
                _passing_metrics(),
                _failing_metrics(0.2),
            ]
        )
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        assert result.validator_passed is False

    def test_oos_evaluation_property_returns_held_out_fold(self) -> None:
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        result = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        oos_eval = result.oos_evaluation
        assert isinstance(oos_eval, FoldEvaluation)
        assert oos_eval.is_oos is True


# ─────────────────────────────────────────────────────────────────────────
# Run() — BaseAgent contract wrapper
# ─────────────────────────────────────────────────────────────────────────


class TestRunWrapper:
    def test_returns_failed_on_invalid_input(self) -> None:
        agent = ValidatorAgent()
        result = agent.run(
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            # missing spec
        )
        assert result.status == "failed"
        assert result.errors

    def test_returns_success_on_oos_pass(self) -> None:
        agent = ValidatorAgent(k=2)
        backtest = _backtest_returning([_passing_metrics()] * 2)
        result = agent.run(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 3, 1),
            backtest_fn=backtest,
        )
        assert result.status == "success"
        assert result.outputs["validator_passed"] is True

    def test_returns_partial_on_oos_fail(self) -> None:
        agent = ValidatorAgent(k=2)
        backtest = _backtest_returning([_passing_metrics(), _failing_metrics()])
        result = agent.run(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 3, 1),
            backtest_fn=backtest,
        )
        # "partial" because the validation completed but didn't pass
        assert result.status == "partial"
        assert result.outputs["validator_passed"] is False

    def test_outputs_include_per_fold_metrics(self) -> None:
        agent = ValidatorAgent(k=4)
        backtest = _backtest_returning([_passing_metrics()] * 4)
        result = agent.run(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 5, 1),
            backtest_fn=backtest,
        )
        fold_metrics = result.outputs["fold_metrics"]
        assert len(fold_metrics) == 4
        assert fold_metrics[3]["is_oos"] is True
        assert fold_metrics[0]["is_oos"] is False
        for fm in fold_metrics:
            assert "metrics" in fm
            assert "sharpe" in fm["metrics"]


# ─────────────────────────────────────────────────────────────────────────
# Multiple genomes — orchestrator-style usage
# ─────────────────────────────────────────────────────────────────────────


class TestMultipleGenomes:
    def test_validate_two_genomes_independently(self) -> None:
        # Verify the agent is reusable across genomes (no shared mutable
        # state between calls).
        agent = ValidatorAgent(k=2)
        backtest_a = _backtest_returning([_passing_metrics()] * 2)
        result_a = agent.validate(
            spec=MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 3, 1),
            backtest_fn=backtest_a,
        )

        backtest_b = _backtest_returning([_passing_metrics(), _failing_metrics()])
        result_b = agent.validate(
            spec=RSI_MEAN_REVERSION_SEED,
            instrument="EUR_USD",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 3, 1),
            backtest_fn=backtest_b,
        )

        assert result_a.validator_passed is True
        assert result_b.validator_passed is False
        # The two ValidationResult objects must hold their own data
        assert result_a.spec == MA_CROSSOVER_SEED
        assert result_b.spec == RSI_MEAN_REVERSION_SEED
