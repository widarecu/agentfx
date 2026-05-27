"""Integration tests for each orchestrator stop condition firing.

Unit tests cover the in-memory logic; these tests confirm the
stop_reason gets persisted to the real DB correctly. Each test uses a
mocked compute_fitness function to keep runtime fast (no actual
backtests) — the stop-condition logic doesn't depend on real fitness
values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from random import Random
from typing import Any

import pytest

from agentfx.agents.orchestrator import (
    OrchestratorAgent,
    OrchestratorConfig,
    StopConditions,
)
from agentfx.api import models, schemas
from agentfx.api.services.evolution import (
    SqlAlchemyPersister,
    create_run,
)


pytestmark = pytest.mark.integration


def _fixed_fitness(value: float = 1.0):
    """Mock fitness function returning a constant fitness."""

    def fn(spec, **kwargs: Any) -> tuple[float, dict]:
        return value, {
            "sharpe": value,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
        }

    return fn


def _build_orchestrator(
    db_session,
    run_id: str,
    *,
    stop_conditions: StopConditions,
    fitness_value: float = 1.0,
    population_size: int = 5,
    clock=None,
) -> OrchestratorAgent:
    return OrchestratorAgent(
        run_id=run_id,
        config=OrchestratorConfig(
            instrument="EUR_USD",
            granularity="M1",
            population_size=population_size,
            llm_injection_every_n=99,
            n_validation_folds=4,
            rng_seed=42,
            # Stop-condition tests don't care about gen-0 seeding —
            # they care about WHEN the loop halts. Disable warm-start
            # and LLM gen-0 seeding so the test is deterministic
            # regardless of prior runs in the shared test DB.
            use_prior_winners=False,
            llm_seed_gen0=False,
        ),
        stop_conditions=stop_conditions,
        data_window=(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 8, tzinfo=UTC),
        ),
        persister=SqlAlchemyPersister(db_session),
        llm_injector=None,
        compute_fitness_fn=_fixed_fitness(fitness_value),
        rng=Random(42),
        clock=clock or _SimpleClock(),
    )


class _SimpleClock:
    """Always returns the wall-clock time. Tests that need to control
    time inject a stepped clock instead."""

    def __init__(self):
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 0.1
        return self._t


class _SteppedClock:
    """Advances by `step` per call — useful for wall-clock stop test."""

    def __init__(self, step: float = 100.0):
        self._t = 0.0
        self.step = step

    def __call__(self) -> float:
        self._t += self.step
        return self._t


def _make_run(db_session) -> models.EvolutionRun:
    return create_run(db_session, schemas.EvolutionRunCreate(instrument="EUR_USD"))


# Mock the validator to avoid pulling real backtest data — these tests
# don't care whether validation passes, just that the stop reason is
# correct.
@pytest.fixture(autouse=True)
def _mock_validator_for_all_tests(monkeypatch):
    """Stub ValidatorAgent.validate so we don't try to query candles
    that don't exist in the test DB for these stop-condition tests."""
    from agentfx.agents.validator import (
        FoldEvaluation,
        ValidationResult,
        ValidatorAgent,
    )
    from agentfx.core.validation import make_walk_forward_folds

    def _fake_validate(self, spec, *, instrument, granularity, start, end, **kwargs):
        folds = make_walk_forward_folds(start, end, k=self.k)
        fold_evals = [
            FoldEvaluation(
                fold=f,
                metrics={
                    "sharpe": 1.5,
                    "max_drawdown": 0.05,
                    "hit_rate": 0.55,
                    "n_trades": 500,
                },
                passed_gate_b=True,
                gate_b_failures=[],
            )
            for f in folds
        ]
        return ValidationResult(
            spec=spec,
            folds=fold_evals,
            validator_passed=True,
            validation_failures=[],
        )

    monkeypatch.setattr(ValidatorAgent, "validate", _fake_validate)


# ─────────────────────────────────────────────────────────────────────────
# Per-condition tests
# ─────────────────────────────────────────────────────────────────────────


class TestStopConditions:
    def test_max_generations_persists_to_db(self, db_session):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            stop_conditions=StopConditions(
                max_generations=3,
                max_wall_clock_seconds=600,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.COMPLETED
        assert run.stop_reason == "max_generations"
        # 3 generation rows persisted
        gens = (
            db_session.query(models.Generation)
            .filter(models.Generation.run_id == run.id)
            .count()
        )
        assert gens == 3

    def test_target_fitness_persists_to_db(self, db_session):
        run = _make_run(db_session)
        # Fitness is 2.0; target 1.5 → trips immediately on gen 0
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=600,
                target_fitness=1.5,
                # target_reached is opt-in (default off) since
                # training-fitness early-stop routinely sacrifices
                # robustness. This test explicitly opts in to verify
                # the trigger still fires when the user wants it.
                enable_target_fitness_stop=True,
                no_improvement_generations=99,
            ),
            fitness_value=2.0,
        )
        orchestrator.run_loop()

        db_session.refresh(run)
        assert run.stop_reason == "target_reached"
        gens = (
            db_session.query(models.Generation)
            .filter(models.Generation.run_id == run.id)
            .count()
        )
        # Should stop at gen 0
        assert gens == 1

    def test_wall_clock_persists_to_db(self, db_session):
        run = _make_run(db_session)
        # Stepped clock: each call advances 100s. Budget 50s → trips fast.
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=50,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
            clock=_SteppedClock(step=100.0),
        )
        orchestrator.run_loop()

        db_session.refresh(run)
        assert run.stop_reason == "max_wall_clock"

    def test_no_improvement_persists_to_db(self, db_session):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=600,
                target_fitness=999.0,
                no_improvement_generations=2,
            ),
            fitness_value=1.0,  # constant — plateau hits
        )
        orchestrator.run_loop()

        db_session.refresh(run)
        assert run.stop_reason == "no_improvement"
        gens = (
            db_session.query(models.Generation)
            .filter(models.Generation.run_id == run.id)
            .count()
        )
        assert gens < 30


# ─────────────────────────────────────────────────────────────────────────
# Decision log captures the stop reason
# ─────────────────────────────────────────────────────────────────────────


class TestDecisionLogIncludesStopReason:
    def test_run_completed_decision_records_stop_reason(self, db_session):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=600,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        completed_decision = (
            db_session.query(models.DecisionLog)
            .filter(models.DecisionLog.run_id == run.id)
            .filter(models.DecisionLog.action == "run_completed")
            .first()
        )
        assert completed_decision is not None
        assert completed_decision.outputs_json is not None
        assert completed_decision.outputs_json.get("stop_reason") == "max_generations"
