"""Orchestrator agent — drives the v0.3.0 evolution loop.

Wires GA + LLM injection + validator + persistence into one runnable
loop. Lives in-process; the subprocess fork (`multiprocessing.Process`
with `resource.setrlimit()` caps) is in `api/workers/evolution_runner.py`,
so this module stays pure-Python and fully unit-testable with mocks.

The loop:

  1. Initialize gen-0: seeds verbatim + mutated copies (per population.py)
  2. For each generation up to a stop condition:
     a. Evaluate fitness on the training window (one backtest per genome)
     b. Persist the generation + every genome row
     c. Log a decision with reasoning
     d. Check stop conditions (max_generations, wall-clock, plateau,
        target fitness, LLM budget)
     e. Every N generations: invoke the LLM injector, replace the
        bottom-K genomes with proposals (origin=LLM)
     f. GA step → next population (selection + crossover + mutation)
  3. Validate the top-K with walk-forward + Gate B on held-out OOS
  4. Mark the run COMPLETED with stop_reason and best_genome_id

The persister Protocol lets the SQLAlchemy-backed implementation live in
`api/services/evolution.py`; tests inject a mock recorder.

See `planning/requirements/US-003-evolution-run-lifecycle.md` for the full
contract and `planning/requirements/US-016-evolution-observability.md` for
the decision-log expectations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from random import Random
from typing import Any, Protocol
from collections.abc import Callable

from agentfx.agents.base import AgentResult, BaseAgent
from agentfx.agents.evolution import (
    Genome,
    Population,
    elite_select,
    evolve_to_next_generation,
    initialize_population,
    mutate_spec,
)
from agentfx.agents.evolution.fitness import compute_fitness
from agentfx.agents.evolution.llm_client import (
    InjectionResult,
    LLMHypothesisInjector,
)
from agentfx.agents.validator import FoldEvaluation, ValidatorAgent
from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl import StrategySpec
from agentfx.core.dsl.seeds import SEED_SPECS
from agentfx.core.regime import RegimeTag, compute_regime, regimes_match
from agentfx.core.validation import (
    make_rolling_walk_forward_folds,
    training_window,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class StopConditions:
    """Conditions that can halt the loop. First-to-fire wins.

    Notes:
    - `target_fitness` is checked against the *best training fitness* seen
      in-loop (not OOS Sharpe — OOS isn't evaluated until validation at
      end-of-run). It's an early-stop signal: "we found something
      promising on training, stop and validate." It is **opt-in** via
      ``enable_target_fitness_stop``; by default we exhaust
      ``max_generations`` (or ``no_improvement``) instead. The default
      was flipped because training fitness is GA-biased toward overfit
      shapes — stopping early on training fitness routinely yielded runs
      where the run-best then failed OOS Gate B, throwing away compute
      that could have found a more robust candidate.
    - `llm_budget_exhausted` is implicit — the LLM injector's
      `cost_remaining_usd` is checked when injection is scheduled; an
      empty `InjectionResult` is treated as a stop reason if budget hit
      *and* no other generation has produced improvement since.
    """

    max_generations: int = 30
    max_wall_clock_seconds: int = 6 * 3600  # 6h
    target_fitness: float = 1.5
    enable_target_fitness_stop: bool = False
    no_improvement_generations: int = 5


@dataclass
class OrchestratorConfig:
    """Tuning knobs for one evolution run. Persisted as JSON in
    `evolution_run.config_json` so a run is exactly reproducible from its
    row alone."""

    instrument: str
    granularity: str = "M1"
    population_size: int = 30
    llm_injection_every_n: int = 3
    n_llm_proposals: int = 5  # how many genomes the LLM injects per call
    n_validation_folds: int = 4
    top_n_to_validate: int = 5
    crossover_prob: float = 0.6
    mutation_prob: float = 0.3
    elite_fraction: float = 0.10
    tournament_size: int = 3
    rng_seed: int = 42

    # Backtest dispatch knobs (forwarded to the fitness evaluator)
    account_balance: float = 10000.0
    position_size: int = 10000
    spread_pips: float | None = None  # None = use default
    leverage: int = 30  # 30:1 OANDA retail default, 50:1 opt-in

    # Gen-0 seeding strategy. The canonical SEED_SPECS are always
    # included; these flags layer additional starting points on top.
    use_prior_winners: bool = True
    n_prior_winners: int = 3  # top-K genomes from completed matching runs
    llm_seed_gen0: bool = True
    n_llm_gen0_proposals: int = 5  # how many genomes the LLM seeds gen-0 with

    # Gate B override: minimum net annualised return (CAGR, after
    # spread + slippage + swap costs) a genome must hit on the OOS
    # fold to pass validation. The fitness function also halves
    # *positive* fitness for genomes that fail Gate B at this
    # threshold, biasing the GA toward profitable shapes.
    min_cagr_pct: float = 0.15  # 15% per year by default

    # v0.4.0 rolling walk-forward parameters. Three training folds in the
    # first `train_frac` of the window (the GA evaluates here); five OOS
    # folds in the remaining region (the validator aggregates by median
    # across these). See `core/validation.make_rolling_walk_forward_folds`
    # and the 2026-06-07 analysis for why this beats fixed-25%-tail OOS.
    n_train_folds: int = 3
    n_oos_folds: int = 5
    train_frac: float = 0.7

    # When True, the orchestrator computes a regime tag for the training
    # region and for the OOS region at run-start, and aborts the run with
    # stop_reason='train_oos_regime_mismatch' if they disagree (saves the
    # full 30 generations of doomed compute). Default off — surfaces the
    # mismatch info in the UI but lets every run finish. Wire a checkbox
    # on the Start New Run modal to flip this per-run.
    auto_stop_on_regime_mismatch: bool = False


# ─────────────────────────────────────────────────────────────────────────
# Persistence Protocol
# ─────────────────────────────────────────────────────────────────────────


class EvolutionStatePersister(Protocol):
    """Interface the orchestrator uses to write state.

    Production implementation in `api/services/evolution.py` writes to
    Postgres via SQLAlchemy. Tests inject a mock recorder.
    """

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_generation: int | None = None,
        best_genome_id: str | None = None,
        llm_cost_usd: float | None = None,
        stop_reason: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None: ...

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
        """Persist the generation row + every genome row.

        MUST mutate each `Genome.id` to the assigned semantic key
        (e.g. `genome_{run}_{gen}_{idx}`) so subsequent code can record
        parent_ids correctly. The orchestrator relies on `genome.id`
        being non-None after this call returns.
        """
        ...

    def save_validation(
        self,
        genome_id: str,
        folds: list[FoldEvaluation],
        *,
        validator_passed: bool,
        validation_failures: list[str],
    ) -> None: ...

    def log_decision(
        self,
        run_id: str,
        *,
        agent_type: str,
        action: str,
        generation_index: int | None = None,
        inputs_json: dict[str, Any] | None = None,
        outputs_json: dict[str, Any] | None = None,
        reasoning_text: str | None = None,
        llm_call_id: str | None = None,
    ) -> None: ...

    def find_prior_winners(
        self,
        *,
        instrument: str,
        granularity: str,
        limit: int,
        exclude_run_id: str | None = None,
        data_window_start: datetime | None = None,
        data_window_end: datetime | None = None,
        min_cagr_pct: float | None = None,
    ) -> list[dict]:
        """Return top-K genomes from completed runs matching the pair/timeframe.

        The `data_window_*` and `min_cagr_pct` args narrow the candidate
        pool to runs that scored fitness under the SAME evaluation
        rubric — without them, cross-config fitness numbers are not
        comparable and the picker chases ghosts (staging 2026-06-17).

        Each dict has keys: `id`, `dsl_json`, `fitness`,
        `source_run_id`. Returns an empty list if no prior winners
        exist or the implementation doesn't support warm-start (tests).
        """
        ...


# ─────────────────────────────────────────────────────────────────────────
# Loop summary
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class LoopSummary:
    """The orchestrator's return value. Captured into AgentResult.outputs
    by `run()`; the subprocess runner uses the typed form for control
    flow."""

    run_id: str
    generations_completed: int
    stop_reason: str
    best_genome_id: str | None
    best_fitness: float | None
    llm_cost_usd: float
    validated_count: int
    duration_seconds: float


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────


# Sentinel return value from a stop-condition check.
@dataclass(frozen=True)
class _StopDecision:
    should_stop: bool
    reason: str | None = None


class OrchestratorAgent(BaseAgent):
    """Drives one evolution run from gen-0 to validation.

    Deterministic given a fixed `rng_seed` and deterministic
    `compute_fitness_fn`. Persistence side effects go through the
    injected `EvolutionStatePersister`. The validator and LLM injector
    are likewise injected so tests can stub them out.
    """

    def __init__(
        self,
        run_id: str,
        config: OrchestratorConfig,
        stop_conditions: StopConditions,
        data_window: tuple[datetime, datetime],
        persister: EvolutionStatePersister,
        *,
        llm_injector: LLMHypothesisInjector | None = None,
        validator: ValidatorAgent | None = None,
        compute_fitness_fn: Callable[..., tuple[float, dict]] | None = None,
        seed_specs: list[StrategySpec] | None = None,
        rng: Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.run_id = run_id
        self.config = config
        self.stop_conditions = stop_conditions
        self.data_window = data_window
        self.persister = persister

        self.llm_injector = llm_injector  # may be None — runs without LLM injection
        # v0.4.0 rolling validator — overridable via tests/injection.
        self.validator = validator or ValidatorAgent(
            n_train_folds=config.n_train_folds,
            n_oos_folds=config.n_oos_folds,
            train_frac=config.train_frac,
        )
        self._compute_fitness_fn = compute_fitness_fn or compute_fitness
        self._seed_specs = seed_specs or SEED_SPECS
        self._rng = rng or Random(config.rng_seed)
        self._clock = clock
        # Accumulator for LLM-origin genomes across the run — fed back
        # into the next LLM call so the model can avoid re-proposing
        # shapes that already failed.
        self._all_llm_origin_genomes: list[Genome] = []

        # Hall of fame: every distinct genome that has been evaluated
        # this run, keyed by canonical spec JSON so elite carry-overs
        # don't get duplicated. Used by `_validate_top_genomes` to pick
        # the top-K by training fitness for end-of-run validation —
        # otherwise we'd only validate the single run-best and miss the
        # cases where the gen-5 #3 genome OOS-passes when the gen-0
        # elite doesn't.
        self._hall_of_fame: dict[str, Genome] = {}

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        # Inputs are validated at construction time. run() takes no kwargs.
        return True, []

    # ── BaseAgent contract ─────────────────────────────────────────

    def run(self, **kwargs) -> AgentResult:
        """Execute the loop. Wraps `run_loop()` with AgentResult."""
        self.started_at = datetime.now(UTC)
        try:
            summary = self.run_loop()
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrator run %s crashed", self.run_id)
            return self._create_result("failed", {"run_id": self.run_id}, [str(exc)])

        outputs = {
            "run_id": summary.run_id,
            "generations_completed": summary.generations_completed,
            "stop_reason": summary.stop_reason,
            "best_genome_id": summary.best_genome_id,
            "best_fitness": summary.best_fitness,
            "llm_cost_usd": summary.llm_cost_usd,
            "validated_count": summary.validated_count,
            "duration_seconds": summary.duration_seconds,
        }
        # Loop-level success means the loop completed cleanly. Whether any
        # genome passed validation is reported in `validated_count`; that's
        # an outcome, not a status.
        return self._create_result("success", outputs)

    # ── Main loop ─────────────────────────────────────────────────────

    def run_loop(self) -> LoopSummary:
        """Drive the evolution loop end-to-end.

        Idempotent at the row level: re-running with the same `run_id` is
        not safe (would create duplicate generations). Crash recovery is
        the caller's responsibility (see `recover_queued_evolution_runs`
        in the next chunk's evolution_runner).
        """
        loop_start = self._clock()
        started_at = datetime.now(UTC)

        self.persister.update_run(
            self.run_id,
            status="running",
            started_at=started_at,
        )
        self.persister.log_decision(
            self.run_id,
            agent_type="orchestrator",
            action="run_started",
            inputs_json={
                "instrument": self.config.instrument,
                "granularity": self.config.granularity,
                "population_size": self.config.population_size,
                "data_window": [
                    self.data_window[0].isoformat(),
                    self.data_window[1].isoformat(),
                ],
            },
            reasoning_text="Evolution run starting.",
        )

        # ── Regime tagging + auto-stop pre-check ─────────────────────
        # Compute per-fold regime tags and the train/OOS region-level
        # tags. The per-fold tags get forwarded to the validator later
        # for the audit trail; the region-level tags drive the optional
        # auto-stop check.
        self._fold_regimes, train_region_tag, oos_region_tag = (
            self._compute_fold_regimes()
        )
        regime_decision_inputs = {
            "train_region_regime": (
                train_region_tag.label if train_region_tag else None
            ),
            "oos_region_regime": (oos_region_tag.label if oos_region_tag else None),
            "regimes_match": (
                regimes_match(train_region_tag, oos_region_tag)
                if train_region_tag and oos_region_tag
                else None
            ),
            "auto_stop_enabled": self.config.auto_stop_on_regime_mismatch,
        }
        self.persister.log_decision(
            self.run_id,
            agent_type="orchestrator",
            action="regime_check",
            inputs_json=regime_decision_inputs,
            reasoning_text=(
                f"Training regime: {regime_decision_inputs['train_region_regime']}; "
                f"OOS regime: {regime_decision_inputs['oos_region_regime']}; "
                f"match={regime_decision_inputs['regimes_match']}."
            ),
        )

        if (
            self.config.auto_stop_on_regime_mismatch
            and train_region_tag is not None
            and oos_region_tag is not None
            and not regimes_match(train_region_tag, oos_region_tag)
        ):
            duration = self._clock() - loop_start
            self.persister.update_run(
                self.run_id,
                status="failed",
                completed_at=datetime.now(UTC),
                stop_reason="train_oos_regime_mismatch",
            )
            self.persister.log_decision(
                self.run_id,
                agent_type="orchestrator",
                action="run_stopped",
                outputs_json={
                    "stop_reason": "train_oos_regime_mismatch",
                    "train_region_regime": train_region_tag.label,
                    "oos_region_regime": oos_region_tag.label,
                },
                reasoning_text=(
                    "Auto-stop: training and OOS regimes disagree "
                    f"({train_region_tag.label} vs {oos_region_tag.label}). "
                    "Set auto_stop_on_regime_mismatch=False to override."
                ),
            )
            return LoopSummary(
                run_id=self.run_id,
                generations_completed=0,
                stop_reason="train_oos_regime_mismatch",
                best_genome_id=None,
                best_fitness=None,
                llm_cost_usd=0.0,
                validated_count=0,
                duration_seconds=duration,
            )

        # ── Gen 0 ────────────────────────────────────────────────────
        # Composite seeding strategy:
        #   1. Canonical seeds (MA / RSI / Donchian) — always included.
        #   2. Warm-start: top-K genomes from prior completed runs on
        #      the same (instrument, granularity). Persists institutional
        #      memory across runs so we don't re-discover from scratch
        #      every time.
        #   3. LLM-seeded proposals: one call with the enriched gen-0
        #      prompt asking for diverse, cost-aware starting strategies.
        #   4. Remaining slots: mutated copies of the above pool.
        # Each layer is gated by a config flag so a user can run a
        # pure-canonical baseline if they want.
        # Build typed Genome objects with the correct origin per layer so
        # the lineage tree / origin filters can distinguish canonical /
        # warm-start / LLM gen-0 / mutated. initialize_population accepts
        # pre-built Genome objects and preserves their origin — only the
        # mutated fill becomes GenomeOrigin.MUTATION.
        composite_seed_genomes: list[Genome] = [
            Genome(spec=s, origin=GenomeOrigin.SEED) for s in self._seed_specs
        ]
        warm_start_count = 0
        llm_seed_count = 0

        if self.config.use_prior_winners and self.config.n_prior_winners > 0:
            try:
                # Pass the current run's evaluation rubric so the picker
                # only considers runs scored under the SAME rules.
                # Without this, a run with a looser config (e.g. shorter
                # data window or lower min_cagr_pct) can produce a high
                # fitness number that the picker keeps re-selecting even
                # though that genome scores deeply negative under the
                # current run's harder rules — staging 2026-06-17 found
                # every new run starting from a Run-1 elite that
                # re-evaluated to fitness ≈ −1.4.
                prior = self.persister.find_prior_winners(
                    instrument=self.config.instrument,
                    granularity=self.config.granularity,
                    limit=self.config.n_prior_winners,
                    exclude_run_id=self.run_id,
                    data_window_start=self.data_window[0],
                    data_window_end=self.data_window[1],
                    min_cagr_pct=self.config.min_cagr_pct,
                )
            except Exception as e:
                logger.warning(
                    "warm-start lookup failed (%s); proceeding with canonical "
                    "seeds only.",
                    e,
                )
                prior = []
            for row in prior:
                try:
                    spec = StrategySpec.model_validate(row["dsl_json"])
                    composite_seed_genomes.append(
                        Genome(
                            spec=spec,
                            origin=GenomeOrigin.WARM_START,
                            parent_ids=[row["id"]] if row.get("id") else [],
                        )
                    )
                    warm_start_count += 1
                except Exception as e:
                    logger.warning(
                        "warm-start spec %s failed validation (%s); skipping.",
                        row.get("id"),
                        e,
                    )

        llm_gen0_proposals: list[Genome] = []
        if (
            self.config.llm_seed_gen0
            and self.llm_injector is not None
            and self.config.n_llm_gen0_proposals > 0
        ):
            result = self.llm_injector.inject(
                instrument=self.config.instrument,
                granularity=self.config.granularity,
                generation_index=0,
                fitness_history=[],
                top_genomes=[],
                n_proposals=self.config.n_llm_gen0_proposals,
                cost_per_trade_pips=self._cost_per_trade_pips(),
                is_gen_zero_seeding=True,
                min_cagr_target=self.config.min_cagr_pct,
            )
            self.persister.log_decision(
                self.run_id,
                agent_type="llm",
                action="seed_gen0",
                generation_index=0,
                outputs_json={
                    "n_proposals": len(result.proposals),
                    "skipped_reason": result.skipped_reason,
                    "call_cost_usd": result.cost.usd,
                    "cumulative_cost_usd": self.llm_injector.cost_used_usd,
                },
                reasoning_text=(
                    f"LLM seed of gen 0: {len(result.proposals)} proposals, "
                    f"${result.cost.usd:.4f} this call."
                ),
                llm_call_id=result.call_id,
            )
            if result.succeeded:
                for p in result.proposals:
                    llm_gen0_proposals.append(
                        Genome(
                            spec=p.spec,
                            origin=GenomeOrigin.LLM,
                            parent_ids=[],
                            llm_reasoning=p.reasoning,
                            llm_call_id=result.call_id,
                        )
                    )
                composite_seed_genomes.extend(llm_gen0_proposals)
                self._all_llm_origin_genomes.extend(llm_gen0_proposals)
                llm_seed_count = len(llm_gen0_proposals)

        # Pass run_id so the seeds get pre-assigned IDs and mutated copies
        # record correct parent_ids — without this, gen-0 mutations appear
        # parentless in the portal and the LineageExplainer can't show
        # which seed each variant came from.
        population = initialize_population(
            seeds=composite_seed_genomes,
            size=self.config.population_size,
            rng=self._rng,
            mutate_fn=mutate_spec,
            run_id=self.run_id,
        )
        self.persister.log_decision(
            self.run_id,
            agent_type="orchestrator",
            action="init_population",
            outputs_json={
                "population_size": len(population),
                "n_canonical_seeds": len(self._seed_specs),
                "n_warm_start_winners": warm_start_count,
                "n_llm_gen0_proposals": llm_seed_count,
            },
            reasoning_text=(
                f"Initialised generation 0 with {len(self._seed_specs)} canonical "
                f"seeds + {warm_start_count} prior winners + {llm_seed_count} LLM "
                f"proposals + "
                f"{len(population) - len(composite_seed_genomes)} mutated copies."
            ),
        )

        # Training window — the GA's fitness evaluation happens here only;
        # the OOS fold (last fold of the same window) is held out for the
        # validator. See `core/validation.make_walk_forward_folds`.
        train_start, train_end = self._training_range()
        # v0.5.0: GA fitness now runs ONE backtest per training fold and
        # aggregates per-fold Sharpes via mean − λ·std, instead of one
        # concatenated-training backtest. Rewards consistency across
        # folds (= more likely to generalise) rather than "high average
        # driven by one big fold". The fitness function falls back to
        # single-window mode if this list is empty (tests).
        training_fold_ranges = self._training_fold_ranges()

        # Track best fitness and plateau detection
        best_fitness_history: list[float] = []
        best_genome_overall: Genome | None = None

        gen_index = 0
        stop_reason: str | None = None
        # Did the *current* population receive LLM-injected genomes? The
        # flag is set when injection runs at the end of the previous
        # iteration; the next iteration's save_generation reads + clears it.
        pending_llm_invoked = False
        pending_llm_call_id: str | None = None

        while True:
            self._evaluate_population_fitness(
                population, train_start, train_end, training_fold_ranges
            )

            # Persist generation + genomes (assigns IDs)
            best_in_gen = population.best()
            best_fitness_in_gen = (
                best_in_gen.fitness_value if best_in_gen is not None else None
            )
            self.persister.save_generation(
                self.run_id,
                generation_index=gen_index,
                genomes=list(population.genomes),
                llm_invoked=pending_llm_invoked,
                llm_call_id=pending_llm_call_id,
                best_fitness=best_fitness_in_gen,
            )
            # After save_generation IDs are assigned; record into the
            # cross-generation hall of fame for top-K validation at
            # end-of-run.
            self._record_evaluated(population)
            # Consume the pending flag — the next save_generation starts
            # fresh.
            pending_llm_invoked = False
            pending_llm_call_id = None

            best_fitness_history.append(
                best_fitness_in_gen
                if best_fitness_in_gen is not None
                else float("-inf")
            )
            if best_in_gen is not None and (
                best_genome_overall is None
                or best_in_gen.fitness_value > best_genome_overall.fitness_value
            ):
                best_genome_overall = best_in_gen

            self.persister.update_run(
                self.run_id,
                current_generation=gen_index,
                best_genome_id=(
                    best_genome_overall.id if best_genome_overall else None
                ),
                llm_cost_usd=(
                    self.llm_injector.cost_used_usd if self.llm_injector else 0.0
                ),
            )

            self.persister.log_decision(
                self.run_id,
                agent_type="ga",
                action="evaluate_generation",
                generation_index=gen_index,
                outputs_json={
                    "best_fitness": best_fitness_in_gen,
                    "n_genomes": len(population),
                    "n_passed_gate_b": sum(
                        1
                        for g in population.genomes
                        if (g.metrics or {}).get("passes_gate_b", False)
                    ),
                },
                reasoning_text=(
                    f"Gen {gen_index}: best fitness {best_fitness_in_gen:.3f}"
                    if best_fitness_in_gen is not None
                    else f"Gen {gen_index}: no fitness recorded"
                ),
            )

            # ── Stop condition check ───────────────────────────────
            decision = self._check_stop_conditions(
                gen_index=gen_index,
                best_fitness_history=best_fitness_history,
                loop_start_monotonic=loop_start,
            )
            if decision.should_stop:
                stop_reason = decision.reason
                break

            # ── GA step → next generation ──────────────────────────
            population = evolve_to_next_generation(
                population,
                self._rng,
                crossover_prob=self.config.crossover_prob,
                mutation_prob=self.config.mutation_prob,
                elite_fraction=self.config.elite_fraction,
                tournament_size=self.config.tournament_size,
            )
            gen_index += 1

            # ── Periodic LLM injection (into the freshly-evolved gen) ──
            # Inject AFTER evolve so the new genomes appear in the next
            # generation's population. Their fitness is then evaluated at
            # the top of the next iteration so they can compete in
            # subsequent tournaments — if we injected before evolve, the
            # GA's selection would discard them (they have fitness=-inf
            # before evaluation, so they always lose tournaments).
            if self.llm_injector is not None and (
                gen_index % self.config.llm_injection_every_n == 0
            ):
                pending_llm_call_id = self._inject_llm_proposals(
                    population=population,
                    gen_index=gen_index,
                    best_fitness_history=best_fitness_history,
                )
                pending_llm_invoked = pending_llm_call_id is not None

        # ── Validation ───────────────────────────────────────────────
        validated_count = self._validate_top_genomes(best_genome_overall)

        completed_at = datetime.now(UTC)
        duration_seconds = self._clock() - loop_start

        self.persister.update_run(
            self.run_id,
            status="completed",
            stop_reason=stop_reason or "max_generations",
            completed_at=completed_at,
            best_genome_id=(best_genome_overall.id if best_genome_overall else None),
            llm_cost_usd=(
                self.llm_injector.cost_used_usd if self.llm_injector else 0.0
            ),
        )
        self.persister.log_decision(
            self.run_id,
            agent_type="orchestrator",
            action="run_completed",
            outputs_json={
                "stop_reason": stop_reason,
                "validated_count": validated_count,
                "best_fitness": (
                    best_genome_overall.fitness_value if best_genome_overall else None
                ),
            },
            reasoning_text=(
                f"Run completed: stop_reason={stop_reason}, "
                f"{validated_count} genomes validated."
            ),
        )

        return LoopSummary(
            run_id=self.run_id,
            generations_completed=gen_index + 1,
            stop_reason=stop_reason or "max_generations",
            best_genome_id=(best_genome_overall.id if best_genome_overall else None),
            best_fitness=(
                best_genome_overall.fitness_value if best_genome_overall else None
            ),
            llm_cost_usd=(
                self.llm_injector.cost_used_usd if self.llm_injector else 0.0
            ),
            validated_count=validated_count,
            duration_seconds=duration_seconds,
        )

    # ── Internals ──────────────────────────────────────────────────────

    def _rolling_folds(self):
        """Single source of truth for the rolling walk-forward geometry
        used by both the GA's fitness evaluation (training folds) and the
        validator (OOS folds). Aligning them through one helper guarantees
        the GA never accidentally sees data from the validator's OOS
        region.
        """
        return make_rolling_walk_forward_folds(
            start=self.data_window[0],
            end=self.data_window[1],
            n_train_folds=self.config.n_train_folds,
            n_oos_folds=self.config.n_oos_folds,
            train_frac=self.config.train_frac,
        )

    def _compute_fold_regimes(
        self,
    ) -> tuple[dict[int, RegimeTag], RegimeTag | None, RegimeTag | None]:
        """Tag each fold with a regime label + return aggregate train/OOS tags.

        Returns:
            (per_fold, train_region, oos_region) where:
              - ``per_fold`` is keyed by fold.index with the per-slice
                RegimeTag the UI surfaces in the walk-forward fold table.
              - ``train_region`` is the regime tag of the concatenated
                training window (used by the auto-stop check).
              - ``oos_region`` is the regime tag of the concatenated OOS
                window (used by the auto-stop check).

        Each is ``None`` if the underlying candle load fails — the
        orchestrator treats a missing tag as "unknown" and skips the
        auto-stop check rather than crashing the run.
        """
        from agentfx.services.backtest import load_price_df_from_db

        folds = self._rolling_folds()
        per_fold: dict[int, RegimeTag] = {}
        train_tag: RegimeTag | None = None
        oos_tag: RegimeTag | None = None

        try:
            df_full = load_price_df_from_db(
                instrument=self.config.instrument,
                granularity=self.config.granularity,
                start=self.data_window[0],
                end=self.data_window[1],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "regime tagging skipped — could not load candles for %s %s: %s",
                self.config.instrument,
                self.config.granularity,
                e,
            )
            return per_fold, None, None

        for fold in folds:
            slice_df = df_full[
                (df_full.index >= fold.start) & (df_full.index < fold.end)
            ]
            per_fold[fold.index] = compute_regime(
                slice_df, granularity=self.config.granularity
            )

        train_folds = [f for f in folds if not f.is_oos]
        oos_folds = [f for f in folds if f.is_oos]
        if train_folds:
            train_start, train_end = train_folds[0].start, train_folds[-1].end
            train_slice = df_full[
                (df_full.index >= train_start) & (df_full.index < train_end)
            ]
            train_tag = compute_regime(train_slice, granularity=self.config.granularity)
        if oos_folds:
            oos_start, oos_end = oos_folds[0].start, oos_folds[-1].end
            oos_slice = df_full[
                (df_full.index >= oos_start) & (df_full.index < oos_end)
            ]
            oos_tag = compute_regime(oos_slice, granularity=self.config.granularity)

        return per_fold, train_tag, oos_tag

    def _training_range(self) -> tuple[datetime, datetime]:
        """The GA evaluates fitness over the training region of the
        rolling design. Returns (start, end) of the union of all training
        folds."""
        return training_window(self._rolling_folds())

    def _training_fold_ranges(self) -> list[tuple[datetime, datetime]]:
        """The list of (start, end) tuples covering each *training* fold.

        Drives the v0.5.0 fold-consistent fitness function: instead of
        running one backtest over the concatenated training window, the
        GA runs one backtest per fold and rewards strategies whose
        Sharpe is *consistent* across folds (not just high on average).
        Excludes the OOS folds — those are the validator's, not the GA's.
        """
        return [(f.start, f.end) for f in self._rolling_folds() if not f.is_oos]

    def _record_evaluated(self, population: Population) -> None:
        """Stash this generation's genomes in the hall-of-fame, keyed by
        canonical spec JSON so elite carry-overs aren't duplicated.

        Must be called *after* ``save_generation`` so genome IDs are
        already assigned — the validator needs a stable ID to persist
        validation folds against.
        """
        for g in population.genomes:
            if not g.has_fitness() or g.id is None:
                continue
            key = g.spec.model_dump_json()
            existing = self._hall_of_fame.get(key)
            if existing is None or g.fitness_value > existing.fitness_value:
                self._hall_of_fame[key] = g

    def _top_k_by_fitness(self, k: int) -> list[Genome]:
        """Return the top-K unique genomes from the hall of fame, sorted
        by descending fitness. K is clamped to the hall size."""
        ranked = sorted(
            self._hall_of_fame.values(),
            key=lambda g: g.fitness_value,
            reverse=True,
        )
        return ranked[: max(0, k)]

    def _evaluate_population_fitness(
        self,
        population: Population,
        train_start: datetime,
        train_end: datetime,
        training_fold_ranges: list[tuple[datetime, datetime]] | None = None,
    ) -> None:
        """Evaluate every genome's fitness on the training window.

        When ``training_fold_ranges`` is supplied (v0.5.0+ default), the
        fitness function runs one backtest per fold and aggregates them
        via ``mean − λ·std`` of per-fold Sharpe. When omitted (older
        callers / tests), it falls back to a single backtest over
        ``[train_start, train_end]``. The concatenated-window args are
        still computed for back-compat callers and ignored in fold mode.

        Only genomes that haven't been evaluated yet are run — elites
        carried over from the previous generation already have their
        fitness set (via DEAP's `creator.FitnessMax.valid` flag).
        """
        # Elite specs for the diversity penalty: the top 10% of *already-
        # evaluated* genomes from the previous generation. On gen 0, this
        # is empty.
        evaluated = [g for g in population.genomes if g.has_fitness()]
        n_elites = max(1, int(len(evaluated) * self.config.elite_fraction))
        elite_specs = (
            [g.spec for g in elite_select(evaluated, n_elites)] if evaluated else []
        )

        backtest_kwargs: dict[str, Any] = {
            "account_balance": self.config.account_balance,
            "position_size": self.config.position_size,
            "leverage": self.config.leverage,
        }
        if self.config.spread_pips is not None:
            backtest_kwargs["spread_pips"] = self.config.spread_pips

        for genome in population.genomes:
            if genome.has_fitness():
                continue  # elite carried over — skip re-eval

            fitness, metrics = self._compute_fitness_fn(
                genome.spec,
                instrument=self.config.instrument,
                granularity=self.config.granularity,
                start=train_start,
                end=train_end,
                training_folds=training_fold_ranges,
                elite_specs=elite_specs,
                gate_b_overrides={"min_cagr": self.config.min_cagr_pct},
                **backtest_kwargs,
            )
            genome.set_fitness(fitness)
            genome.metrics = metrics

    def _cost_per_trade_pips(self) -> float:
        """Realistic round-trip cost in pips for the run's instrument.

        Uses the per-pair OANDA spread (from instruments table, or
        the global env default as fallback) plus 2× slippage per side.
        Surfaced to the LLM so it knows the minimum edge a strategy
        needs to be net-profitable on this pair/granularity.
        """
        from agentfx.services.backtest import _lookup_typical_spread
        from agentfx.shared.config import (
            backtest_default_slippage_pips,
            backtest_default_spread_pips,
        )

        if self.config.spread_pips is not None:
            spread = float(self.config.spread_pips)
        else:
            looked_up = _lookup_typical_spread(self.config.instrument)
            spread = (
                float(looked_up)
                if looked_up is not None
                else backtest_default_spread_pips()
            )
        slippage_per_side = backtest_default_slippage_pips()
        return spread + 2.0 * slippage_per_side

    def _inject_llm_proposals(
        self,
        population: Population,
        gen_index: int,
        best_fitness_history: list[float],
    ) -> str | None:
        """Invoke the LLM injector and replace the bottom-K genomes with
        the proposals. Returns the LLM call_id on success, None if
        skipped (budget exhausted or API failure).
        """
        if self.llm_injector is None:
            return None

        # Top-10 by fitness as context for the LLM. Carry the per-genome
        # metric breakdown so the model can diagnose what's specifically
        # failing on the incumbents (low hit rate? cost drag? drawdown?).
        evaluated = sorted(
            (g for g in population.genomes if g.has_fitness()),
            key=lambda g: g.fitness_value,
            reverse=True,
        )
        top_10 = [(g.spec, g.fitness_value, g.metrics) for g in evaluated[:10]]

        # Prior LLM proposals across all generations — surfaced so the
        # LLM doesn't re-propose shapes it already tried and failed.
        prior_llm = [
            (g.spec, g.fitness_value if g.has_fitness() else None, g.metrics)
            for g in self._all_llm_origin_genomes
        ]

        result: InjectionResult = self.llm_injector.inject(
            instrument=self.config.instrument,
            granularity=self.config.granularity,
            generation_index=gen_index,
            fitness_history=best_fitness_history,
            top_genomes=top_10,
            n_proposals=self.config.n_llm_proposals,
            cost_per_trade_pips=self._cost_per_trade_pips(),
            prior_llm_proposals=prior_llm if prior_llm else None,
            min_cagr_target=self.config.min_cagr_pct,
        )

        self.persister.log_decision(
            self.run_id,
            agent_type="llm",
            action="inject",
            generation_index=gen_index,
            outputs_json={
                "n_proposals": len(result.proposals),
                "skipped_reason": result.skipped_reason,
                "call_cost_usd": result.cost.usd,
                "cumulative_cost_usd": self.llm_injector.cost_used_usd,
            },
            reasoning_text=(
                f"LLM injection at gen {gen_index}: "
                f"{len(result.proposals)} proposals, "
                f"${result.cost.usd:.4f} this call, "
                f"${self.llm_injector.cost_used_usd:.4f} cumulative."
            ),
            llm_call_id=result.call_id,
        )

        if not result.succeeded:
            return None

        # Replace the bottom-K by fitness with the new proposals.
        n_replace = min(len(result.proposals), len(population.genomes))
        if n_replace == 0:
            return result.call_id

        population.genomes.sort(
            key=lambda g: (g.fitness_value if g.has_fitness() else float("-inf"))
        )
        new_proposals = [
            Genome(
                spec=p.spec,
                origin=GenomeOrigin.LLM,
                parent_ids=[],
                llm_reasoning=p.reasoning,
                llm_call_id=result.call_id,
            )
            for p in result.proposals[:n_replace]
        ]
        # Drop the bottom-K, append the new proposals.
        population.genomes = population.genomes[n_replace:] + new_proposals
        # Track for future LLM calls so the model sees its own history.
        self._all_llm_origin_genomes.extend(new_proposals)

        return result.call_id

    def _check_stop_conditions(
        self,
        gen_index: int,
        best_fitness_history: list[float],
        loop_start_monotonic: float,
    ) -> _StopDecision:
        # 1. Generation budget
        if gen_index + 1 >= self.stop_conditions.max_generations:
            return _StopDecision(True, "max_generations")

        # 2. Wall-clock budget
        elapsed = self._clock() - loop_start_monotonic
        if elapsed >= self.stop_conditions.max_wall_clock_seconds:
            return _StopDecision(True, "max_wall_clock")

        # 3. Target fitness reached (opt-in only — default off because
        # training-fitness early-stop routinely sacrifices a more robust
        # candidate the GA would have found in later generations).
        if (
            self.stop_conditions.enable_target_fitness_stop
            and best_fitness_history
            and best_fitness_history[-1] >= self.stop_conditions.target_fitness
        ):
            return _StopDecision(True, "target_reached")

        # 4. Plateau (no improvement for K generations)
        k = self.stop_conditions.no_improvement_generations
        if len(best_fitness_history) > k:
            recent = best_fitness_history[-(k + 1) :]
            # Any improvement over the last k+1 generations (incl. current)?
            if max(recent) <= recent[0]:
                return _StopDecision(True, "no_improvement")

        # 5. LLM budget exhausted (only triggers if injector is configured
        # AND budget has actually run out — non-LLM runs ignore this).
        if self.llm_injector is not None and self.llm_injector.cost_remaining_usd <= 0:
            return _StopDecision(True, "llm_budget_exhausted")

        return _StopDecision(False)

    def _validate_top_genomes(self, best_genome_overall: Genome | None) -> int:
        """Run the validator on the top-K genomes by training fitness and
        persist each result. Returns the count of genomes that passed
        validation.

        K = ``config.top_n_to_validate``. Genomes are drawn from the
        cross-generation hall of fame (deduped by spec content), so an
        elite carried across many generations is validated exactly once.

        Why top-K instead of just run-best: training fitness is
        GA-biased toward overfit shapes, so the single run-best
        frequently fails OOS. Validating top-K surfaces the genome that
        actually *generalises* — which may not be the one with the
        highest training fitness. Each validator call is one extra
        backtest pass per genome, but we already paid for the GA's
        thousands of evaluations; spending K more to actually find a
        promotable candidate is a strict improvement on throwing away
        the run when the #1 fails OOS.

        Falls back to ``[best_genome_overall]`` if the hall of fame is
        empty (defensive — should not happen on a normal run, but the
        old single-best contract is preserved).
        """
        k = max(1, int(self.config.top_n_to_validate))
        top_k = self._top_k_by_fitness(k)

        if not top_k and best_genome_overall is not None:
            # Defensive fallback — hall of fame should always contain
            # the run-best, but if something pathological cleared it,
            # at least validate the run-best so the old contract holds.
            top_k = [best_genome_overall]

        passed = 0
        for rank, genome in enumerate(top_k, start=1):
            if genome.id is None:
                logger.warning(
                    "skipping validation for genome with no ID at rank %d "
                    "(should have been assigned by save_generation)",
                    rank,
                )
                continue

            try:
                # Validator backtests must pay the *same* spread the GA's
                # fitness paid — otherwise the OOS verdict is being
                # judged at a different cost level than training. Pass
                # config.spread_pips through if pinned; otherwise the
                # backtest's live lookup applies, which means the
                # validator could drift from the GA. The runner pins
                # `config.spread_pips` at run-start (resolved live or
                # user-provided) precisely so we never hit the drift
                # case here.
                validator_kwargs: dict[str, Any] = {
                    "account_balance": self.config.account_balance,
                    "leverage": self.config.leverage,
                }
                if self.config.spread_pips is not None:
                    validator_kwargs["spread_pips"] = self.config.spread_pips
                result = self.validator.validate(
                    spec=genome.spec,
                    instrument=self.config.instrument,
                    granularity=self.config.granularity,
                    start=self.data_window[0],
                    end=self.data_window[1],
                    gate_b_overrides={"min_cagr": self.config.min_cagr_pct},
                    fold_regimes=getattr(self, "_fold_regimes", None),
                    **validator_kwargs,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "validator crashed for genome %s (rank %d/%d)",
                    genome.id,
                    rank,
                    len(top_k),
                )
                continue

            self.persister.save_validation(
                genome.id,
                folds=result.folds,
                validator_passed=result.validator_passed,
                validation_failures=result.validation_failures,
            )
            oos_eval = result.oos_evaluation
            self.persister.log_decision(
                self.run_id,
                agent_type="validator",
                action="validate",
                outputs_json={
                    "genome_id": genome.id,
                    "rank_by_training_fitness": rank,
                    "training_fitness": genome.fitness_value,
                    "validator_passed": result.validator_passed,
                    "oos_sharpe": oos_eval.metrics.get("sharpe"),
                    "oos_max_drawdown": oos_eval.metrics.get("max_drawdown"),
                    "oos_n_trades": oos_eval.metrics.get("n_trades"),
                    "validation_failures": result.validation_failures,
                },
                reasoning_text=(
                    f"Walk-forward validation on genome {genome.id} "
                    f"(rank {rank}/{len(top_k)} by training fitness): "
                    f"{'passed' if result.validator_passed else 'failed'} "
                    f"on OOS fold (Sharpe={oos_eval.metrics.get('sharpe', '?')})."
                ),
            )
            if result.validator_passed:
                passed += 1

        return passed


__all__ = [
    "EvolutionStatePersister",
    "LoopSummary",
    "OrchestratorAgent",
    "OrchestratorConfig",
    "StopConditions",
]
