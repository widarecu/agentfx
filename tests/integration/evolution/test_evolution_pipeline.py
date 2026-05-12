"""End-to-end evolution-pipeline integration tests.

Runs the orchestrator in-process (no subprocess fork — that's a unit-test
concern) against the real test Postgres + the real backtest engine
querying seeded candle data. The point is to verify that GA + persistence
+ validator wire together correctly under real DB constraints.

These tests are deliberately small (population=5, 2-3 generations) so
they finish in seconds — the goal is wiring correctness, not strategy
quality.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from agentfx.agents.orchestrator import (
    OrchestratorAgent,
    OrchestratorConfig,
    StopConditions,
)
from agentfx.api import models
from agentfx.api.services.evolution import (
    SqlAlchemyPersister,
    create_run,
)
from agentfx.api import schemas


pytestmark = pytest.mark.integration


def _make_run(db_session, instrument="EUR_USD", granularity="M1"):
    body = schemas.EvolutionRunCreate(
        instrument=instrument,
        granularity=granularity,
        data_years=1,
    )
    return create_run(db_session, body)


def _build_orchestrator(
    db_session,
    run_id: str,
    *,
    instrument: str,
    granularity: str,
    data_window: tuple[datetime, datetime],
    population_size: int = 5,
    max_generations: int = 2,
    rng_seed: int = 7,
    target_fitness: float = 999.0,
    no_improvement_generations: int = 99,
) -> OrchestratorAgent:
    return OrchestratorAgent(
        run_id=run_id,
        config=OrchestratorConfig(
            instrument=instrument,
            granularity=granularity,
            population_size=population_size,
            llm_injection_every_n=99,  # disabled for these tests
            n_validation_folds=4,
            rng_seed=rng_seed,
        ),
        stop_conditions=StopConditions(
            max_generations=max_generations,
            max_wall_clock_seconds=600,
            target_fitness=target_fitness,
            no_improvement_generations=no_improvement_generations,
        ),
        data_window=data_window,
        persister=SqlAlchemyPersister(db_session),
        llm_injector=None,  # no LLM injection in these tests
        rng=Random(rng_seed),
    )


# ─────────────────────────────────────────────────────────────────────────
# Pipeline: gen-0 only, no LLM
# ─────────────────────────────────────────────────────────────────────────


class TestEvolutionPipeline:
    def test_full_pipeline_persists_run_to_completion(
        self, db_session, seed_evolution_candles
    ):
        """Orchestrator runs end-to-end; DB reflects every step."""
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        summary = orchestrator.run_loop()

        # Run row marked completed
        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.COMPLETED
        assert run.stop_reason in {
            "max_generations",
            "max_wall_clock",
            "target_reached",
            "no_improvement",
        }
        assert run.completed_at is not None

        # Summary returned by run_loop matches DB state
        assert summary.run_id == run.id
        assert summary.generations_completed >= 1

    def test_generation_rows_persisted(self, db_session, seed_evolution_candles):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        orchestrator.run_loop()

        gens = (
            db_session.query(models.Generation)
            .filter(models.Generation.run_id == run.id)
            .order_by(models.Generation.index)
            .all()
        )
        assert len(gens) == 2  # max_generations
        # First generation is index 0
        assert gens[0].index == 0
        # All generations have a completed_at
        for g in gens:
            assert g.completed_at is not None

    def test_genome_rows_persisted_with_assigned_ids(
        self, db_session, seed_evolution_candles
    ):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        orchestrator.run_loop()

        # 5 genomes × 2 generations = 10 genome rows (some carryovers via
        # elitism are persisted with new IDs in their own generation rows)
        genome_count = (
            db_session.query(models.StrategyGenome)
            .join(
                models.Generation,
                models.StrategyGenome.generation_id == models.Generation.id,
            )
            .filter(models.Generation.run_id == run.id)
            .count()
        )
        assert genome_count == 10

        # Every genome has a non-null assigned ID matching the
        # `genome_{generation_id}_{idx}` convention
        sample = (
            db_session.query(models.StrategyGenome)
            .join(
                models.Generation,
                models.StrategyGenome.generation_id == models.Generation.id,
            )
            .filter(models.Generation.run_id == run.id)
            .first()
        )
        assert sample is not None
        assert sample.id.startswith("genome_gen_")
        # DSL spec is persisted as JSON
        assert isinstance(sample.dsl_json, dict)
        assert sample.dsl_json.get("version") == 1

    def test_decision_log_captures_run_lifecycle(
        self, db_session, seed_evolution_candles
    ):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        orchestrator.run_loop()

        decisions = (
            db_session.query(models.DecisionLog)
            .filter(models.DecisionLog.run_id == run.id)
            .order_by(models.DecisionLog.created_at)
            .all()
        )
        actions = [d.action for d in decisions]

        # The audit trail spans the whole lifecycle
        assert "run_started" in actions
        assert "init_population" in actions
        assert "evaluate_generation" in actions
        assert "validate" in actions
        assert "run_completed" in actions
        # And the orchestrator-emitted entries have reasoning
        for d in decisions:
            if d.agent_type == "orchestrator":
                assert d.reasoning_text  # non-empty

    def test_validation_runs_on_run_best(self, db_session, seed_evolution_candles):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        orchestrator.run_loop()

        # Validation produced 4 fold rows for the run-best genome
        db_session.refresh(run)
        assert run.best_genome_id is not None
        folds = (
            db_session.query(models.ValidationFold)
            .filter(models.ValidationFold.genome_id == run.best_genome_id)
            .order_by(models.ValidationFold.fold_index)
            .all()
        )
        assert len(folds) == 4
        # Exactly one OOS fold
        oos = [f for f in folds if f.is_oos]
        assert len(oos) == 1
        assert oos[0].fold_index == 3

    def test_run_best_genome_is_recorded(self, db_session, seed_evolution_candles):
        run = _make_run(db_session)
        orchestrator = _build_orchestrator(
            db_session,
            run.id,
            instrument=seed_evolution_candles["instrument"],
            granularity=seed_evolution_candles["timeframe"],
            data_window=(
                seed_evolution_candles["start"],
                seed_evolution_candles["end"],
            ),
            population_size=5,
            max_generations=2,
        )
        orchestrator.run_loop()

        db_session.refresh(run)
        assert run.best_genome_id is not None
        # The best_genome_id must reference a genome in this run's lineage
        best = (
            db_session.query(models.StrategyGenome)
            .filter(models.StrategyGenome.id == run.best_genome_id)
            .first()
        )
        assert best is not None
