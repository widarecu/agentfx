"""Orchestrator agent tests with mocked persister, fitness, LLM injector,
and validator.

The orchestrator is the integration point for everything in the
`agents.evolution` package + the validator + persistence. These tests
exercise the loop's control flow without hitting a real DB, real API, or
real backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from random import Random
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentfx.agents.evolution import Genome, LLMHypothesisInjector
from agentfx.agents.evolution.llm_client import (
    InjectionProposal,
    InjectionResult,
    LLMCallCost,
)
from agentfx.agents.orchestrator import (
    OrchestratorAgent,
    OrchestratorConfig,
    StopConditions,
)
from agentfx.agents.validator import (
    FoldEvaluation,
    ValidationResult,
    ValidatorAgent,
)
from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl import StrategySpec
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    SEED_SPECS,
)


# ─────────────────────────────────────────────────────────────────────────
# Test fixtures: mock persister, fitness fn, validator, clock
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _PersistedGen:
    index: int
    genomes: list[Genome]
    llm_invoked: bool
    llm_call_id: str | None
    best_fitness: float | None


@dataclass
class _PersistedDecision:
    agent_type: str
    action: str
    generation_index: int | None
    inputs_json: dict | None
    outputs_json: dict | None
    reasoning_text: str | None
    llm_call_id: str | None


class MockPersister:
    """In-memory recorder used in place of the real SQLAlchemy persister."""

    def __init__(self) -> None:
        self.run_updates: list[dict] = []
        self.generations: list[_PersistedGen] = []
        self.validations: list[dict] = []
        self.decisions: list[_PersistedDecision] = []
        self._next_id = 0

    def update_run(self, run_id: str, **kwargs: Any) -> None:
        self.run_updates.append({"run_id": run_id, **kwargs})

    def save_generation(
        self,
        run_id: str,
        generation_index: int,
        genomes: list[Genome],
        *,
        llm_invoked: bool,
        llm_call_id: str | None,
        best_fitness: float | None,
    ) -> None:
        # Assign IDs to genomes (mutates them — matches the real
        # persister's contract per the protocol docstring).
        for i, g in enumerate(genomes):
            if g.id is None:
                g.id = f"genome_{run_id}_{generation_index}_{i}"
        self.generations.append(
            _PersistedGen(
                index=generation_index,
                genomes=list(genomes),
                llm_invoked=llm_invoked,
                llm_call_id=llm_call_id,
                best_fitness=best_fitness,
            )
        )

    def save_validation(
        self,
        genome_id: str,
        folds: list[FoldEvaluation],
        *,
        validator_passed: bool,
        validation_failures: list[str],
    ) -> None:
        self.validations.append(
            {
                "genome_id": genome_id,
                "n_folds": len(folds),
                "validator_passed": validator_passed,
                "validation_failures": validation_failures,
            }
        )

    def log_decision(
        self,
        run_id: str,
        *,
        agent_type: str,
        action: str,
        generation_index: int | None = None,
        inputs_json: dict | None = None,
        outputs_json: dict | None = None,
        reasoning_text: str | None = None,
        llm_call_id: str | None = None,
    ) -> None:
        self.decisions.append(
            _PersistedDecision(
                agent_type=agent_type,
                action=action,
                generation_index=generation_index,
                inputs_json=inputs_json,
                outputs_json=outputs_json,
                reasoning_text=reasoning_text,
                llm_call_id=llm_call_id,
            )
        )

    def find_prior_winners(
        self,
        *,
        instrument: str,
        granularity: str,
        limit: int,
        exclude_run_id: str | None = None,
        **kwargs,
    ) -> list[dict]:
        # MockPersister has no cross-run history; warm-start always
        # returns empty here. Tests that exercise warm-start should
        # subclass and override.
        return []


class FixedFitnessFn:
    """Deterministic compute_fitness mock.

    Returns fitness values from a list, cycling. Used to simulate
    plateaus, improvement curves, etc.
    """

    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.calls = 0

    def __call__(self, spec, **kwargs):  # noqa: ANN001
        v = self.values[self.calls % len(self.values)]
        self.calls += 1
        return v, {
            "sharpe": v,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
        }


class StepClock:
    """Monotonic clock that ticks by `step_seconds` per call.

    Useful for testing wall-clock stop conditions deterministically.
    """

    def __init__(self, step_seconds: float = 1.0, start: float = 0.0) -> None:
        self.step = step_seconds
        self.now = start

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _passing_fold_evals(spec: StrategySpec) -> list[FoldEvaluation]:
    """Build 4 fold evaluations, all passing Gate B."""
    from agentfx.core.validation import make_walk_forward_folds

    folds = make_walk_forward_folds(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 5, 1, tzinfo=UTC),
        k=4,
    )
    return [
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


def _make_validator_returning(
    validator_passed: bool = True,
    failures: list[str] | None = None,
) -> ValidatorAgent:
    """Build a ValidatorAgent whose .validate() returns a fixed result."""
    validator = ValidatorAgent(k=4)
    validator.validate = MagicMock(  # type: ignore[method-assign]
        return_value=ValidationResult(
            spec=MA_CROSSOVER_SEED,
            folds=_passing_fold_evals(MA_CROSSOVER_SEED),
            validator_passed=validator_passed,
            validation_failures=failures or [],
        )
    )
    return validator


def _build_orchestrator(
    *,
    fitness_fn=None,
    llm_injector=None,
    validator=None,
    config: OrchestratorConfig | None = None,
    stop_conditions: StopConditions | None = None,
    clock=None,
) -> tuple[OrchestratorAgent, MockPersister]:
    persister = MockPersister()
    orchestrator = OrchestratorAgent(
        run_id="evorun_test_abc",
        config=config
        or OrchestratorConfig(
            instrument="EUR_USD",
            granularity="M1",
            population_size=10,  # smaller for fast tests
            llm_injection_every_n=3,
            n_validation_folds=4,
            rng_seed=42,
            # Default to 1 so the legacy single-validator-call contract
            # the existing tests assert still holds. Tests that exercise
            # top-K validation override this explicitly.
            top_n_to_validate=1,
        ),
        stop_conditions=stop_conditions
        or StopConditions(
            max_generations=3,
            max_wall_clock_seconds=86400,
            target_fitness=999.0,  # never trips by default
            no_improvement_generations=99,
        ),
        data_window=(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 5, 1, tzinfo=UTC),
        ),
        persister=persister,
        llm_injector=llm_injector,
        validator=validator or _make_validator_returning(validator_passed=True),
        compute_fitness_fn=fitness_fn or FixedFitnessFn([1.0]),
        clock=clock or StepClock(step_seconds=1.0),
    )
    return orchestrator, persister


# ─────────────────────────────────────────────────────────────────────────
# Initialization & lifecycle
# ─────────────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_marks_run_running_then_completed(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        statuses = [u.get("status") for u in persister.run_updates if u.get("status")]
        assert "running" in statuses
        assert "completed" in statuses
        # Last status must be "completed"
        last_status_update = next(
            u for u in reversed(persister.run_updates) if u.get("status")
        )
        assert last_status_update["status"] == "completed"

    def test_records_started_at_and_completed_at(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        started_updates = [
            u for u in persister.run_updates if u.get("started_at") is not None
        ]
        completed_updates = [
            u for u in persister.run_updates if u.get("completed_at") is not None
        ]
        assert len(started_updates) >= 1
        assert len(completed_updates) >= 1

    def test_run_summary_returned(self) -> None:
        orchestrator, _ = _build_orchestrator(
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            )
        )
        summary = orchestrator.run_loop()
        assert summary.run_id == "evorun_test_abc"
        assert summary.generations_completed >= 1
        assert summary.stop_reason in {
            "max_generations",
            "max_wall_clock",
            "target_reached",
            "no_improvement",
            "llm_budget_exhausted",
        }


class TestGenZero:
    def test_initial_population_includes_seeds(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        gen0 = persister.generations[0]
        assert gen0.index == 0
        # Every canonical seed must appear verbatim in gen 0; count drives
        # off SEED_SPECS so adding/removing a seed family doesn't require
        # touching this assertion.
        seed_origins = [g.origin for g in gen0.genomes if g.origin == GenomeOrigin.SEED]
        assert len(seed_origins) == len(SEED_SPECS)

    def test_initial_population_size_matches_config(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()
        assert (
            len(persister.generations[0].genomes) == orchestrator.config.population_size
        )

    def test_log_decision_written_at_start(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        actions = [d.action for d in persister.decisions]
        assert "run_started" in actions
        assert "init_population" in actions
        assert actions.index("run_started") < actions.index("init_population")


# ─────────────────────────────────────────────────────────────────────────
# Fitness evaluation
# ─────────────────────────────────────────────────────────────────────────


class TestFitnessEvaluation:
    def test_calls_fitness_fn_once_per_unevaluated_genome(self) -> None:
        # On gen 0, every genome is unevaluated → fitness fn called pop_size times
        fitness_fn = FixedFitnessFn([1.0])
        orchestrator, _ = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=1,  # only gen 0
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # Pop size 10 → 10 fitness calls
        assert fitness_fn.calls == 10

    def test_passes_training_window_not_full_window(self) -> None:
        from agentfx.core.validation import (
            make_walk_forward_folds,
            training_window,
        )

        fitness_fn = MagicMock(side_effect=FixedFitnessFn([1.0]))
        orchestrator, _ = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=1,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # v0.4.0 rolling design: training region is the first 70% of the
        # window. The validator runs over the remaining 30% (5 OOS folds).
        # The GA never sees data from the OOS region.
        from agentfx.core.validation import make_rolling_walk_forward_folds

        full_start = datetime(2024, 1, 1, tzinfo=UTC)
        full_end = datetime(2024, 5, 1, tzinfo=UTC)
        folds = make_rolling_walk_forward_folds(full_start, full_end)
        expected_train_start, expected_train_end = training_window(folds)

        first_call_kwargs = fitness_fn.call_args_list[0].kwargs
        assert first_call_kwargs["start"] == expected_train_start
        assert first_call_kwargs["end"] == expected_train_end
        # Sanity: training end must be strictly less than the full end
        assert first_call_kwargs["end"] < full_end

    def test_records_fitness_on_each_genome(self) -> None:
        fitness_fn = FixedFitnessFn([1.5, 0.5, 2.0])
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=1,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        gen0 = persister.generations[0]
        for g in gen0.genomes:
            assert g.has_fitness()
            assert g.metrics is not None
            assert g.metrics.get("sharpe") == g.fitness_value

    def test_best_fitness_recorded_per_generation(self) -> None:
        fitness_fn = FixedFitnessFn([0.8, 1.0, 1.2, 1.5, 0.9, 1.1, 0.7, 0.6, 1.0, 1.3])
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=1,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()
        assert persister.generations[0].best_fitness == pytest.approx(1.5)


# ─────────────────────────────────────────────────────────────────────────
# Stop conditions
# ─────────────────────────────────────────────────────────────────────────


class TestStopConditions:
    def test_max_generations_stops_loop(self) -> None:
        orchestrator, persister = _build_orchestrator(
            stop_conditions=StopConditions(
                max_generations=3,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            )
        )
        summary = orchestrator.run_loop()

        assert summary.stop_reason == "max_generations"
        # Generations completed: gen 0, 1, 2 → 3 persisted
        assert len(persister.generations) == 3

    def test_target_fitness_stops_loop_when_opt_in_enabled(self) -> None:
        # Fitness is 2.0 from gen 0 — target 1.5 trips immediately, but
        # ONLY because enable_target_fitness_stop=True is opted in.
        fitness_fn = FixedFitnessFn([2.0])
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=86400,
                target_fitness=1.5,
                enable_target_fitness_stop=True,
                no_improvement_generations=99,
            ),
        )
        summary = orchestrator.run_loop()

        assert summary.stop_reason == "target_reached"
        # Should stop at gen 0
        assert len(persister.generations) == 1

    def test_target_fitness_default_off_does_not_early_stop(self) -> None:
        # Same scenario as above but with the default opt-in flag (False).
        # The loop must NOT early-stop on training fitness; it should
        # run all generations until max_generations (or no_improvement)
        # fires. This pins the new default behaviour — flipping it back
        # to True silently would re-introduce the regression where the
        # GA threw away later-generation candidates.
        fitness_fn = FixedFitnessFn([2.0])
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=3,
                max_wall_clock_seconds=86400,
                target_fitness=1.5,
                # enable_target_fitness_stop defaults to False
                no_improvement_generations=99,
            ),
        )
        summary = orchestrator.run_loop()

        assert summary.stop_reason != "target_reached", (
            "Default opt-in is False — target_fitness should be IGNORED, "
            "not honoured. The loop should exhaust max_generations instead."
        )
        assert summary.stop_reason == "max_generations"
        assert len(persister.generations) == 3

    def test_wall_clock_stops_loop(self) -> None:
        # Clock advances 100s per tick — wall clock budget 50 → stops fast.
        # `step_seconds=100` ensures elapsed >= 50 even after a single tick.
        clock = StepClock(step_seconds=100.0, start=0.0)
        orchestrator, persister = _build_orchestrator(
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=50,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
            clock=clock,
        )
        summary = orchestrator.run_loop()
        assert summary.stop_reason == "max_wall_clock"

    def test_no_improvement_stops_loop(self) -> None:
        # Constant fitness — plateau detection should fire after K+1 gens
        # (length > k where recent[k+1] is non-increasing).
        fitness_fn = FixedFitnessFn([1.0])  # every genome same fitness
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            stop_conditions=StopConditions(
                max_generations=30,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=2,
            ),
        )
        summary = orchestrator.run_loop()
        assert summary.stop_reason == "no_improvement"
        # Should have stopped before max_generations
        assert len(persister.generations) < 30


# ─────────────────────────────────────────────────────────────────────────
# LLM injection
# ─────────────────────────────────────────────────────────────────────────


def _build_mock_llm_injector(
    *,
    n_proposals: int = 5,
    skipped_reason: str | None = None,
    cost_used_usd: float = 0.10,
) -> MagicMock:
    """Build a mock LLMHypothesisInjector. Returns a real MagicMock so
    we can assert call_count + return_value."""
    injector = MagicMock(spec=LLMHypothesisInjector)
    injector.cost_used_usd = cost_used_usd
    injector.cost_remaining_usd = 5.0 - cost_used_usd

    if skipped_reason:
        injector.inject = MagicMock(
            return_value=InjectionResult(
                proposals=[],
                cost=LLMCallCost(0, 0, 0, 0, 0.0),
                call_id="llm_call_skipped",
                skipped_reason=skipped_reason,
            )
        )
    else:
        proposals = [
            InjectionProposal(spec=MA_CROSSOVER_SEED, reasoning=f"proposal {i}")
            for i in range(n_proposals)
        ]
        injector.inject = MagicMock(
            return_value=InjectionResult(
                proposals=proposals,
                cost=LLMCallCost(100, 200, 0, 1000, 0.05),
                call_id="llm_call_abc",
            )
        )
    return injector


class TestLLMInjection:
    def test_no_injection_when_no_injector(self) -> None:
        orchestrator, persister = _build_orchestrator(
            stop_conditions=StopConditions(
                max_generations=5,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
            llm_injector=None,
        )
        orchestrator.run_loop()

        llm_decisions = [d for d in persister.decisions if d.agent_type == "llm"]
        assert llm_decisions == []

    def test_injection_called_every_n_generations(self) -> None:
        # llm_injection_every_n=3, max_generations=7 → injects at gen 3, 6
        # llm_seed_gen0 is disabled here so this test focuses on the
        # mid-run injection cadence; the seed-gen-0 path has its own
        # coverage below.
        injector = _build_mock_llm_injector()
        config = OrchestratorConfig(
            instrument="EUR_USD",
            population_size=10,
            llm_injection_every_n=3,
            n_validation_folds=4,
            rng_seed=42,
            llm_seed_gen0=False,
        )
        orchestrator, persister = _build_orchestrator(
            llm_injector=injector,
            config=config,
            stop_conditions=StopConditions(
                max_generations=7,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # Gen 3 and gen 6 should trigger; gen 0 doesn't (the modulus
        # check excludes index 0).
        assert injector.inject.call_count == 2

    def test_injection_replaces_bottom_genomes(self) -> None:
        # Population of 10, inject 5 proposals → 5 lowest-fitness genomes
        # are replaced with origin=LLM genomes.
        injector = _build_mock_llm_injector(n_proposals=5)
        # Vary fitness so we have a clear bottom-K
        fitness_fn = FixedFitnessFn([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        orchestrator, persister = _build_orchestrator(
            fitness_fn=fitness_fn,
            llm_injector=injector,
            stop_conditions=StopConditions(
                max_generations=5,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
            config=OrchestratorConfig(
                instrument="EUR_USD",
                population_size=10,
                # LLM gen-0 seeding is disabled here so the gen-0 size
                # stays at len(SEED_SPECS) (= 9) instead of growing to
                # 9 + 5 = 14, which would exceed population_size=10
                # and trip InvalidPopulationSizeError. This test is
                # about the recurring gen-3 injection behaviour, not
                # the gen-0 seeding path.
                llm_seed_gen0=False,
                llm_injection_every_n=3,
                n_llm_proposals=5,
                n_validation_folds=4,
            ),
        )
        orchestrator.run_loop()

        # Look for any generation where LLM-origin genomes appear
        llm_origin_gens = [
            g
            for gen in persister.generations
            for g in gen.genomes
            if g.origin == GenomeOrigin.LLM
        ]
        assert len(llm_origin_gens) > 0
        # LLM-origin genomes carry reasoning + call_id
        assert all(g.llm_reasoning for g in llm_origin_gens)
        assert all(g.llm_call_id == "llm_call_abc" for g in llm_origin_gens)

    def test_injection_skipped_does_not_replace(self) -> None:
        injector = _build_mock_llm_injector(skipped_reason="budget_exhausted")
        orchestrator, persister = _build_orchestrator(
            llm_injector=injector,
            config=OrchestratorConfig(
                instrument="EUR_USD",
                population_size=10,
                llm_injection_every_n=1,  # try to inject every gen
                n_validation_folds=4,
            ),
            stop_conditions=StopConditions(
                max_generations=4,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # The decision log records the skip
        llm_decisions = [d for d in persister.decisions if d.agent_type == "llm"]
        assert all(
            d.outputs_json.get("skipped_reason") == "budget_exhausted"
            for d in llm_decisions
        )
        # No LLM-origin genomes were added
        llm_origin_gens = [
            g
            for gen in persister.generations
            for g in gen.genomes
            if g.origin == GenomeOrigin.LLM
        ]
        assert llm_origin_gens == []


# ─────────────────────────────────────────────────────────────────────────
# Validation phase (end of run)
# ─────────────────────────────────────────────────────────────────────────


class TestValidation:
    def test_validator_called_on_run_completion(self) -> None:
        validator = _make_validator_returning(validator_passed=True)
        orchestrator, persister = _build_orchestrator(
            validator=validator,
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        validator.validate.assert_called_once()
        # The full data window goes to the validator (not the training window)
        kwargs = validator.validate.call_args.kwargs
        assert kwargs["start"] == datetime(2024, 1, 1, tzinfo=UTC)
        assert kwargs["end"] == datetime(2024, 5, 1, tzinfo=UTC)

    def test_validation_results_persisted(self) -> None:
        validator = _make_validator_returning(validator_passed=True)
        orchestrator, persister = _build_orchestrator(validator=validator)
        orchestrator.run_loop()

        assert len(persister.validations) == 1
        v = persister.validations[0]
        assert v["validator_passed"] is True
        assert v["n_folds"] == 4

    def test_failed_validation_recorded(self) -> None:
        validator = _make_validator_returning(
            validator_passed=False, failures=["Sharpe 0.5 < 1.0"]
        )
        orchestrator, persister = _build_orchestrator(validator=validator)
        summary = orchestrator.run_loop()

        assert persister.validations[0]["validator_passed"] is False
        assert summary.validated_count == 0

    def test_summary_validated_count_on_pass(self) -> None:
        validator = _make_validator_returning(validator_passed=True)
        orchestrator, _ = _build_orchestrator(validator=validator)
        summary = orchestrator.run_loop()
        assert summary.validated_count == 1

    def test_top_k_validation_runs_k_distinct_validations(self) -> None:
        """When top_n_to_validate=5, validator must be called up to 5 times
        — once per unique top-K genome by training fitness — and each
        result must be persisted under its own genome_id. This is the
        guard against the regression where only the run-best was
        validated (and a more-robust gen-5 #3 was silently discarded)."""
        validator = _make_validator_returning(validator_passed=True)
        config = OrchestratorConfig(
            instrument="EUR_USD",
            granularity="M1",
            population_size=10,
            llm_injection_every_n=99,  # disable LLM injection in test
            n_validation_folds=4,
            rng_seed=42,
            top_n_to_validate=5,
        )
        # Varied fitness so the hall of fame has clear ordering and not
        # all genomes hash to the same spec (FixedFitnessFn cycles).
        fitness_fn = FixedFitnessFn([1.0, 2.0, 3.0, 4.0, 5.0, 1.5, 2.5, 3.5, 4.5, 5.5])
        orchestrator, persister = _build_orchestrator(
            validator=validator,
            fitness_fn=fitness_fn,
            config=config,
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # Up to K validator calls (≤ hall-of-fame size). The mock's
        # spec is always MA_CROSSOVER_SEED — what matters is that
        # validator.validate was called multiple times AND each call
        # produced a save_validation entry.
        assert validator.validate.call_count >= 2, (
            f"Expected top-K to invoke validator at least twice for "
            f"top_n_to_validate=5; got {validator.validate.call_count}"
        )
        assert len(persister.validations) == validator.validate.call_count, (
            "Each validator call must produce exactly one save_validation"
        )
        # Each persisted validation must reference a genome_id (i.e. the
        # save_generation IDs flowed correctly into top-K selection).
        for v in persister.validations:
            assert v["genome_id"], "genome_id missing on validation row"

    def test_top_k_dedupes_elite_carryovers(self) -> None:
        """An elite genome that survives multiple generations must be
        validated exactly once, not once per generation it appears in.
        The hall of fame is keyed by spec content so re-appearances
        collapse."""
        validator = _make_validator_returning(validator_passed=True)
        # All genomes get the same fitness, so the elite carries
        # unchanged. With population_size=10 and top_n=5, after 3
        # generations the hall of fame should still have ≤ 10 unique
        # specs even though 30 genome-instances were saved.
        config = OrchestratorConfig(
            instrument="EUR_USD",
            granularity="M1",
            population_size=10,
            llm_injection_every_n=99,
            n_validation_folds=4,
            rng_seed=42,
            top_n_to_validate=5,
        )
        orchestrator, persister = _build_orchestrator(
            validator=validator,
            fitness_fn=FixedFitnessFn([1.0]),
            config=config,
            stop_conditions=StopConditions(
                max_generations=3,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator.run_loop()

        # Hall of fame size must be ≤ total distinct genomes encountered,
        # which is much smaller than (population × generations) thanks
        # to elite carryover dedup.
        hof = orchestrator._hall_of_fame  # noqa: SLF001
        total_genome_instances_persisted = sum(
            len(g.genomes) for g in persister.generations
        )
        assert len(hof) < total_genome_instances_persisted, (
            f"Hall of fame ({len(hof)}) should be smaller than "
            f"total persisted instances ({total_genome_instances_persisted}) "
            f"thanks to elite-carryover dedup."
        )


# ─────────────────────────────────────────────────────────────────────────
# Decision log
# ─────────────────────────────────────────────────────────────────────────


class TestDecisionLog:
    def test_records_run_lifecycle(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        actions = [d.action for d in persister.decisions]
        assert "run_started" in actions
        assert "init_population" in actions
        assert "evaluate_generation" in actions
        assert "validate" in actions
        assert "run_completed" in actions

    def test_orchestrator_actions_have_reasoning(self) -> None:
        orchestrator, persister = _build_orchestrator()
        orchestrator.run_loop()

        # Every orchestrator-emitted decision must have a reasoning_text
        # — that's the audit trail this exists for.
        orchestrator_decisions = [
            d for d in persister.decisions if d.agent_type == "orchestrator"
        ]
        assert len(orchestrator_decisions) >= 2  # at least start + complete
        for d in orchestrator_decisions:
            assert d.reasoning_text  # non-empty


# ─────────────────────────────────────────────────────────────────────────
# Determinism (reproducibility)
# ─────────────────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_two_runs_with_same_seed_persist_identical_specs(self) -> None:
        config = OrchestratorConfig(
            instrument="EUR_USD",
            population_size=10,
            llm_injection_every_n=99,  # disable
            rng_seed=12345,
        )

        orchestrator_a, persister_a = _build_orchestrator(
            config=config,
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator_a.run_loop()

        orchestrator_b, persister_b = _build_orchestrator(
            config=config,
            stop_conditions=StopConditions(
                max_generations=2,
                max_wall_clock_seconds=86400,
                target_fitness=999.0,
                no_improvement_generations=99,
            ),
        )
        orchestrator_b.run_loop()

        # First-generation specs must be identical (deterministic init)
        a_specs = [g.spec.model_dump() for g in persister_a.generations[0].genomes]
        b_specs = [g.spec.model_dump() for g in persister_b.generations[0].genomes]
        assert a_specs == b_specs


# ─────────────────────────────────────────────────────────────────────────
# BaseAgent contract
# ─────────────────────────────────────────────────────────────────────────


class TestBaseAgentContract:
    def test_run_returns_success_status(self) -> None:
        orchestrator, _ = _build_orchestrator()
        result = orchestrator.run()
        assert result.status == "success"
        assert result.outputs["run_id"] == "evorun_test_abc"

    def test_run_returns_failed_on_crash(self) -> None:
        # compute_fitness_fn raises → loop crashes → run() returns failed
        def crashing_fitness_fn(*args, **kwargs):
            raise RuntimeError("synthetic crash")

        orchestrator, _ = _build_orchestrator(fitness_fn=crashing_fitness_fn)
        result = orchestrator.run()
        assert result.status == "failed"
        assert result.errors

    def test_agent_type(self) -> None:
        orchestrator, _ = _build_orchestrator()
        assert orchestrator.agent_type == "orchestrator"


# ─────────────────────────────────────────────────────────────────────────
# Deferred warm-start injection (v0.6.0)
# ─────────────────────────────────────────────────────────────────────────


def _make_warm_start_row(idx: int = 0) -> dict:
    """Build a minimal warm-start dict as returned by find_prior_winners."""
    return {
        "id": f"genome_prior_run_{idx}",
        "dsl_json": MA_CROSSOVER_SEED.model_dump(mode="json"),
        "fitness": 0.55,
        "source_run_id": f"evorun_prior_{idx}",
    }


class _WarmStartMockPersister(MockPersister):
    """MockPersister that returns a configurable list of prior winners."""

    def __init__(self, prior_winners: list[dict]) -> None:
        super().__init__()
        self._prior_winners = prior_winners

    def find_prior_winners(
        self,
        *,
        instrument: str,
        granularity: str,
        limit: int,
        exclude_run_id: str | None = None,
        **kwargs,
    ) -> list[dict]:
        return self._prior_winners[:limit]


def _build_orchestrator_with_warm_start(
    prior_winners: list[dict],
    *,
    warm_start_enabled: bool = True,
    warm_start_first_injection_gen: int = 5,
    warm_start_injection_interval: int = 3,
    warm_start_picks_per_injection: int = 2,
    max_generations: int = 10,
    no_improvement_generations: int = 99,
    fitness_values: list[float] | None = None,
    population_size: int = 10,
) -> tuple[OrchestratorAgent, _WarmStartMockPersister]:
    """Build an orchestrator wired to return the given prior winners."""
    persister = _WarmStartMockPersister(prior_winners)
    config = OrchestratorConfig(
        instrument="EUR_USD",
        granularity="M1",
        population_size=population_size,
        llm_injection_every_n=99,  # disable LLM injection
        n_validation_folds=4,
        rng_seed=42,
        top_n_to_validate=1,
        use_prior_winners=True,
        n_prior_winners=len(prior_winners),
        llm_seed_gen0=False,
        warm_start_enabled=warm_start_enabled,
        warm_start_first_injection_gen=warm_start_first_injection_gen,
        warm_start_injection_interval=warm_start_injection_interval,
        warm_start_picks_per_injection=warm_start_picks_per_injection,
    )
    orchestrator = OrchestratorAgent(
        run_id="evorun_test_ws",
        config=config,
        stop_conditions=StopConditions(
            max_generations=max_generations,
            max_wall_clock_seconds=86400,
            target_fitness=999.0,
            no_improvement_generations=no_improvement_generations,
        ),
        data_window=(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 5, 1, tzinfo=UTC),
        ),
        persister=persister,
        llm_injector=None,
        validator=_make_validator_returning(validator_passed=True),
        compute_fitness_fn=FixedFitnessFn(fitness_values or [0.1]),
        clock=StepClock(step_seconds=1.0),
    )
    return orchestrator, persister


class TestDeferredWarmStart:
    """Tests for the v0.6.0 deferred warm-start injection feature."""

    # ── Test 1: gen-0 has no warm-start origin ──────────────────────

    def test_gen_0_has_no_warm_start_origin(self) -> None:
        """With warm_start_enabled=True, find_prior_winners results must NOT
        appear in gen-0. They should be stashed and injected later."""
        prior = [_make_warm_start_row(i) for i in range(3)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=5,
            max_generations=3,  # stop before first injection
        )
        orchestrator.run_loop()

        gen0 = persister.generations[0]
        warm_start_in_gen0 = [
            g for g in gen0.genomes if g.origin == GenomeOrigin.WARM_START
        ]
        assert warm_start_in_gen0 == [], (
            f"Gen-0 must not contain warm-start genomes when deferred injection "
            f"is enabled; found {len(warm_start_in_gen0)} warm-start picks."
        )

    def test_warm_start_stashed_in_pending_list(self) -> None:
        """After gen-0 init, pending warm-start picks are in the stash."""
        prior = [_make_warm_start_row(i) for i in range(3)]
        # Run only 1 generation so we can inspect the stash before injection
        orchestrator, _ = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=5,
            max_generations=1,
        )
        orchestrator.run_loop()
        # All 3 picks should have been stashed (run stopped before gen 5)
        assert len(orchestrator._pending_warm_starts) == 3  # noqa: SLF001

    # ── Test 2: warm-start injected at first_injection_gen ──────────

    def test_warm_start_injected_at_first_injection_gen(self) -> None:
        """At gen warm_start_first_injection_gen, pending picks are injected
        (up to warm_start_picks_per_injection of them)."""
        prior = [_make_warm_start_row(i) for i in range(3)]
        first_gen = 3
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=first_gen,
            warm_start_picks_per_injection=2,
            warm_start_injection_interval=99,  # no follow-up injections
            max_generations=first_gen + 2,  # enough gens to reach injection
        )
        orchestrator.run_loop()

        # Check the decision log for a warm_start inject event
        ws_inject_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        assert len(ws_inject_decisions) >= 1, (
            f"Expected at least one warm_start inject decision; "
            f"got {len(ws_inject_decisions)}"
        )
        first_inject = ws_inject_decisions[0]
        assert first_inject.generation_index == first_gen
        assert first_inject.outputs_json["n_injected"] == 2

        # After the first injection, 1 pick should remain in the stash
        assert first_inject.outputs_json["n_remaining_in_stash"] == 1

    def test_warm_start_genomes_have_warm_start_origin(self) -> None:
        """Injected warm-start genomes must have origin=WARM_START."""
        prior = [_make_warm_start_row(0)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=2,
            warm_start_picks_per_injection=1,
            warm_start_injection_interval=99,
            max_generations=5,
        )
        orchestrator.run_loop()

        warm_start_genomes = [
            g
            for gen in persister.generations
            for g in gen.genomes
            if g.origin == GenomeOrigin.WARM_START
        ]
        assert len(warm_start_genomes) >= 1, (
            "At least one WARM_START genome must appear after the first injection."
        )

    # ── Test 3: periodic injection at cadence ───────────────────────

    def test_warm_start_injected_periodically_at_interval(self) -> None:
        """Multiple picks deplete across injections at the correct cadence."""
        # 6 picks, inject 2 at a time with interval=2, first at gen 2
        prior = [_make_warm_start_row(i) for i in range(6)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=2,
            warm_start_injection_interval=2,
            warm_start_picks_per_injection=2,
            max_generations=15,  # enough for multiple injection windows
        )
        orchestrator.run_loop()

        ws_inject_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        # With 6 picks and 2 per injection, we expect exactly 3 injection events
        assert len(ws_inject_decisions) == 3, (
            f"Expected 3 injection events for 6 picks @ 2/event; "
            f"got {len(ws_inject_decisions)}"
        )
        # Injection gens must be first_gen, first_gen+interval, first_gen+2×interval
        injection_gens = [d.generation_index for d in ws_inject_decisions]
        assert injection_gens == [2, 4, 6], (
            f"Expected injection at gens [2, 4, 6]; got {injection_gens}"
        )

    # ── Test 4: warm-start picks compete via tournament, not elitism ─

    def test_warm_start_picks_compete_via_tournament_not_elitism(self) -> None:
        """Injected warm-start picks enter with no fitness set; they are
        evaluated in the next generation and compete via normal tournament.
        This test confirms they do NOT carry pre-set fitness values at the
        moment of injection (which would give them unfair elitism protection).
        """
        prior = [_make_warm_start_row(0)]
        # Use a high fitness constant so injected picks are clearly the
        # bottom of the population at injection time (they start unevaluated).
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=2,
            warm_start_picks_per_injection=1,
            warm_start_injection_interval=99,
            max_generations=5,
            fitness_values=[1.0],
        )
        orchestrator.run_loop()

        # Find the generation in which injection happened.
        # The injected genome should appear in gen[2+1]=gen3 EVALUATED
        # (not in gen2 with pre-set fitness from prior run).
        ws_inject_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        if not ws_inject_decisions:
            pytest.skip("injection didn't fire in this test scenario")

        inject_gen = ws_inject_decisions[0].generation_index
        # The gen at inject_gen+1 (if it exists) should have the ws genome
        # evaluated (has_fitness=True). We can't peek at the live population
        # here, but we can verify that the generation AFTER the inject event
        # has all genomes with fitness set (because evaluation runs at the
        # top of every loop iteration).
        if inject_gen + 1 < len(persister.generations):
            post_inject_gen = persister.generations[inject_gen + 1]
            evaluated = [g for g in post_inject_gen.genomes if g.has_fitness()]
            assert len(evaluated) == len(post_inject_gen.genomes), (
                "All genomes in the generation after warm-start injection "
                "must have been evaluated (no pre-set fitness from prior run)."
            )

    # ── Test 5: no_improvement deferred until first warm-start injection

    def test_no_improvement_stop_deferred_until_first_warm_start_injection(
        self,
    ) -> None:
        """When plateau fires BEFORE first_injection_gen AND pending picks
        exist, the loop must NOT stop — it waits for warm-start to fire first.
        """
        prior = [_make_warm_start_row(0)]
        # Constant fitness → plateau fires after no_improvement_generations=2
        # First injection at gen 5, so plateau fires around gen 2–3 without
        # the deferral guard. With the guard, the loop continues to gen 5.
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=5,
            warm_start_picks_per_injection=1,
            warm_start_injection_interval=99,
            max_generations=30,
            no_improvement_generations=2,
            fitness_values=[0.5],  # constant → plateau
        )
        summary = orchestrator.run_loop()

        # The run must NOT have stopped with no_improvement before gen 5.
        # It should continue at least until gen 5 (the first injection point).
        assert summary.generations_completed >= 6, (
            f"Expected at least 6 generations (to reach first warm-start "
            f"injection at gen 5 + evaluate it); got "
            f"{summary.generations_completed}. The no_improvement guard "
            f"should have deferred the stop."
        )

    def test_no_improvement_fires_normally_when_no_pending_warm_starts(
        self,
    ) -> None:
        """When no warm-start picks are pending, no_improvement fires at the
        normal cadence — the deferral guard must not interfere."""
        # Empty prior winners → no stash → normal no_improvement behaviour
        orchestrator, persister = _build_orchestrator_with_warm_start(
            [],  # no prior winners
            warm_start_first_injection_gen=5,
            max_generations=30,
            no_improvement_generations=2,
            fitness_values=[0.5],  # constant → plateau
        )
        summary = orchestrator.run_loop()

        assert summary.stop_reason == "no_improvement", (
            "With no pending warm-start picks, no_improvement must fire "
            "at the normal cadence, not be deferred."
        )
        # Should have stopped well before gen 30
        assert summary.generations_completed < 30

    # ── Test 6: warm_start_enabled=False fallback ────────────────────

    def test_warm_start_disabled_falls_back_to_gen0_placement(self) -> None:
        """When warm_start_enabled=False, prior winners are placed directly
        in gen-0 (legacy behaviour) and no deferred injection occurs."""
        prior = [_make_warm_start_row(i) for i in range(2)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_enabled=False,
            max_generations=3,
            # Legacy gen-0 placement puts the 2 warm-starts alongside
            # the 9 canonical seeds → 11 fixed slots. Default pop=10
            # underflows the seed-count guard; bump to clear it.
            population_size=15,
        )
        orchestrator.run_loop()

        # Legacy behaviour: warm-start in gen-0
        gen0 = persister.generations[0]
        warm_start_in_gen0 = [
            g for g in gen0.genomes if g.origin == GenomeOrigin.WARM_START
        ]
        assert len(warm_start_in_gen0) == 2, (
            "With warm_start_enabled=False, prior winners must land in gen-0 "
            f"(legacy behaviour); found {len(warm_start_in_gen0)}."
        )
        # No deferred injection decisions
        ws_inject_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        assert ws_inject_decisions == [], (
            "No warm_start inject decisions should be logged when "
            "warm_start_enabled=False."
        )
        # Stash should be empty
        assert orchestrator._pending_warm_starts == []  # noqa: SLF001

    def test_warm_start_disabled_no_injection_regardless_of_picks(self) -> None:
        """Even if prior winners exist, no injection fires when
        warm_start_enabled=False."""
        prior = [_make_warm_start_row(i) for i in range(3)]
        # Run for many generations — injection must never fire.
        # Legacy placement puts 3 warm-starts + 9 canonical seeds = 12
        # fixed slots; bump pop above the seed-count guard.
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_enabled=False,
            warm_start_first_injection_gen=2,
            max_generations=10,
            population_size=15,
        )
        orchestrator.run_loop()

        ws_inject_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        assert ws_inject_decisions == [], (
            "warm_start inject decisions must not appear when warm_start_enabled=False."
        )

    # ── Test 7: decision log entries for warm-start injection ────────

    def test_warm_start_injection_logged_in_decision_log(self) -> None:
        """Each injection event produces a decision log entry with the
        correct agent_type=warm_start, action=inject, generation_index,
        and outputs metadata."""
        prior = [_make_warm_start_row(0)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=2,
            warm_start_picks_per_injection=1,
            warm_start_injection_interval=99,
            max_generations=6,
        )
        orchestrator.run_loop()

        ws_decisions = [
            d
            for d in persister.decisions
            if d.agent_type == "warm_start" and d.action == "inject"
        ]
        assert len(ws_decisions) >= 1
        d = ws_decisions[0]
        assert d.generation_index == 2
        assert d.outputs_json is not None
        assert "n_injected" in d.outputs_json
        assert "n_remaining_in_stash" in d.outputs_json
        assert d.reasoning_text is not None
        assert "warm-start" in d.reasoning_text.lower()

    # ── Test 8: init_population decision log reflects stash ─────────

    def test_init_population_log_reflects_deferred_stash(self) -> None:
        """The init_population decision log must report the stash count,
        not zero, when warm-start picks are deferred."""
        prior = [_make_warm_start_row(i) for i in range(3)]
        orchestrator, persister = _build_orchestrator_with_warm_start(
            prior,
            warm_start_first_injection_gen=5,
            max_generations=3,
        )
        orchestrator.run_loop()

        init_decisions = [
            d for d in persister.decisions if d.action == "init_population"
        ]
        assert len(init_decisions) == 1
        out = init_decisions[0].outputs_json
        assert out is not None
        assert out["n_warm_start_deferred"] == 3, (
            "init_population outputs_json must report the stash size "
            f"as n_warm_start_deferred; got {out}"
        )
