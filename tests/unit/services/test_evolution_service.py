"""Evolution service tests — CRUD + SqlAlchemyPersister.

Uses an in-memory SQLite session to exercise the SQLAlchemy-backed
persister against the real ORM. Persister-level behaviour is the
orchestrator's contract, so its correctness is critical even though the
orchestrator itself is tested with a mock recorder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from agentfx.agents.evolution import Genome, mutate_spec
from agentfx.agents.validator import FoldEvaluation
from agentfx.api import models, schemas
from agentfx.api.core.database import Base
from agentfx.api.services import evolution as evolution_service
from agentfx.api.services.evolution import SqlAlchemyPersister
from agentfx.core.dsl.seeds import MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED
from agentfx.core.validation import make_walk_forward_folds


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _enable_fks(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


# ─────────────────────────────────────────────────────────────────────────
# ID generation
# ─────────────────────────────────────────────────────────────────────────


class TestIdGeneration:
    def test_run_id_is_evorun_dated(self) -> None:
        run_id = evolution_service.generate_run_id()
        assert run_id.startswith("evorun_")
        parts = run_id.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 8  # short uuid

    def test_run_ids_are_unique(self) -> None:
        ids = {evolution_service.generate_run_id() for _ in range(50)}
        assert len(ids) == 50

    def test_generation_id_format(self) -> None:
        gen_id = evolution_service.generate_generation_id("evorun_X", 5)
        assert gen_id == "gen_evorun_X_005"

    def test_genome_id_includes_generation(self) -> None:
        g = evolution_service.generate_genome_id("gen_evorun_X_005", 12)
        assert g == "genome_gen_evorun_X_005_012"

    def test_validation_fold_id_includes_genome(self) -> None:
        f = evolution_service.generate_validation_fold_id("genome_X", 3)
        assert f == "fold_genome_X_3"


# ─────────────────────────────────────────────────────────────────────────
# Run CRUD
# ─────────────────────────────────────────────────────────────────────────


class TestCreateRun:
    def test_basic_create(self, db: Session) -> None:
        body = schemas.EvolutionRunCreate(instrument="EUR_USD", granularity="M1")
        run = evolution_service.create_run(db, body)

        assert run.id.startswith("evorun_")
        assert run.status == models.EvolutionRunStatus.QUEUED
        assert run.instrument == "EUR_USD"
        assert run.granularity == "M1"
        assert run.config_json["population_size"] == 30  # default
        assert run.stop_conditions_json["max_generations"] == 30  # default

    def test_create_persists_data_years_and_budget(self, db: Session) -> None:
        body = schemas.EvolutionRunCreate(
            instrument="GBP_USD", data_years=2, llm_budget_usd=10.0
        )
        run = evolution_service.create_run(db, body)
        assert run.config_json["data_years"] == 2
        assert run.config_json["llm_budget_usd"] == 10.0

    def test_create_with_custom_config(self, db: Session) -> None:
        body = schemas.EvolutionRunCreate(
            instrument="EUR_USD",
            config=schemas.OrchestratorConfigSchema(
                instrument="EUR_USD",
                population_size=50,
                rng_seed=999,
            ),
        )
        run = evolution_service.create_run(db, body)
        assert run.config_json["population_size"] == 50
        assert run.config_json["rng_seed"] == 999

    def test_rng_seed_defaults_to_none_when_caller_omits(self, db: Session) -> None:
        """The schema default was changed from 42 → None so the worker
        can pick a fresh random seed per run. If this regresses to 42
        (or any other fixed value), independent runs deterministically
        converge on the same winner — the GA stops searching and starts
        confirming. See evorun_20260528_2f23b758 et al. for the four
        identical-DSL runs that triggered this change."""
        body = schemas.EvolutionRunCreate(instrument="EUR_USD")
        assert body.config is None  # nothing supplied by the caller

        # When no config block is passed at all, the create_run service
        # picks up the OrchestratorConfigSchema defaults — including the
        # new None default for rng_seed.
        run = evolution_service.create_run(db, body)
        # The resolution happens in the worker, NOT the service layer.
        # The service must NOT silently substitute 42 here — that would
        # bypass the worker's random-seed-per-run logic.
        assert run.config_json.get("rng_seed") is None, (
            "Service-layer create_run must persist rng_seed=None for the "
            "worker to resolve. Got "
            f"{run.config_json.get('rng_seed')!r}. If this is 42 or any "
            "other fixed value, the GA's deterministic-convergence bug "
            "has regressed."
        )

    def test_create_records_creator(self, db: Session) -> None:
        # FK to users.id — create the user first so SQLite's enforced
        # foreign-key constraint is satisfied.
        user = models.User(
            email="creator@example.com",
            hashed_password="x",
            full_name="Creator",
            role=models.UserRole.TRADER,
        )
        db.add(user)
        db.commit()

        body = schemas.EvolutionRunCreate(instrument="EUR_USD")
        run = evolution_service.create_run(db, body, created_by=user.id)
        assert run.created_by == user.id


class TestListRuns:
    def test_filter_by_status(self, db: Session) -> None:
        evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        run2 = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="GBP_USD")
        )
        run2.status = models.EvolutionRunStatus.RUNNING
        db.commit()

        items, total = evolution_service.list_runs(db, status="queued")
        assert total == 1
        assert items[0].instrument == "EUR_USD"

    def test_filter_by_instrument(self, db: Session) -> None:
        evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="GBP_USD")
        )

        items, total = evolution_service.list_runs(db, instrument="GBP_USD")
        assert total == 1
        assert items[0].instrument == "GBP_USD"

    def test_pagination(self, db: Session) -> None:
        for _ in range(5):
            evolution_service.create_run(
                db, schemas.EvolutionRunCreate(instrument="EUR_USD")
            )
        items, total = evolution_service.list_runs(db, skip=0, limit=2)
        assert total == 5
        assert len(items) == 2


class TestStopRun:
    def test_stop_queued_run(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        stopped = evolution_service.stop_run(db, run.id)
        assert stopped is not None
        assert stopped.status == models.EvolutionRunStatus.STOPPED
        assert stopped.stop_reason == "manual_stop"
        assert stopped.completed_at is not None

    def test_stop_already_completed_is_idempotent(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        run.status = models.EvolutionRunStatus.COMPLETED
        db.commit()

        result = evolution_service.stop_run(db, run.id)
        assert result is not None
        assert result.status == models.EvolutionRunStatus.COMPLETED

    def test_stop_unknown_run(self, db: Session) -> None:
        assert evolution_service.stop_run(db, "evorun_nonexistent") is None


class TestRecoveryQueries:
    def test_find_orphaned_running(self, db: Session) -> None:
        run1 = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        run2 = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="GBP_USD")
        )
        run1.status = models.EvolutionRunStatus.RUNNING
        run2.status = models.EvolutionRunStatus.COMPLETED
        db.commit()

        orphaned = evolution_service.find_orphaned_running_runs(db)
        assert len(orphaned) == 1
        assert orphaned[0].id == run1.id

    def test_find_queued_returns_oldest_first(self, db: Session) -> None:
        # Create 3 with explicit timestamps
        runs = []
        for i in range(3):
            r = evolution_service.create_run(
                db, schemas.EvolutionRunCreate(instrument="EUR_USD")
            )
            r.created_at = datetime(2024, 1, i + 1, tzinfo=UTC)
            runs.append(r)
        db.commit()

        queued = evolution_service.find_queued_runs(db)
        # Oldest first
        assert queued[0].id == runs[0].id
        assert queued[-1].id == runs[-1].id


# ─────────────────────────────────────────────────────────────────────────
# SqlAlchemyPersister
# ─────────────────────────────────────────────────────────────────────────


def _make_genome(spec, fitness: float | None = None) -> Genome:
    from agentfx.api.models import GenomeOrigin

    g = Genome(spec=spec, origin=GenomeOrigin.SEED)
    if fitness is not None:
        g.set_fitness(fitness)
    return g


class TestSqlAlchemyPersisterUpdateRun:
    def test_status_transition(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)

        persister.update_run(run.id, status="running")
        db.refresh(run)
        assert run.status == models.EvolutionRunStatus.RUNNING

    def test_unknown_status_logged_not_raised(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        # Should NOT crash — just log and ignore
        persister.update_run(run.id, status="totally-fake")
        db.refresh(run)
        # Status unchanged
        assert run.status == models.EvolutionRunStatus.QUEUED

    def test_unknown_run_id_swallowed(self, db: Session) -> None:
        persister = SqlAlchemyPersister(db)
        # Must not raise — log and continue
        persister.update_run("evorun_does_not_exist", status="running")

    def test_multiple_fields_updated(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)

        now = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
        persister.update_run(
            run.id,
            status="completed",
            stop_reason="max_generations",
            best_genome_id="genome_X",
            llm_cost_usd=0.42,
            current_generation=15,
            completed_at=now,
        )
        db.refresh(run)
        assert run.status == models.EvolutionRunStatus.COMPLETED
        assert run.stop_reason == "max_generations"
        assert run.best_genome_id == "genome_X"
        assert float(run.llm_cost_usd) == pytest.approx(0.42)
        assert run.current_generation == 15
        # SQLite drops tz info on round-trip; compare wall-clock fields.
        # Postgres preserves tz (verified by integration tests against the
        # real test stack).
        assert run.completed_at.replace(tzinfo=None) == now.replace(tzinfo=None)


class TestSqlAlchemyPersisterSaveGeneration:
    def test_save_assigns_genome_ids(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)

        genomes = [
            _make_genome(MA_CROSSOVER_SEED, 1.5),
            _make_genome(RSI_MEAN_REVERSION_SEED, 1.2),
        ]
        persister.save_generation(
            run.id,
            generation_index=0,
            genomes=genomes,
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.5,
        )

        # Each input genome must now have a non-None id (orchestrator
        # contract — parent_ids tracking depends on this).
        for g in genomes:
            assert g.id is not None
            assert g.id.startswith("genome_gen_")

    def test_persists_generation_and_genomes(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        persister.save_generation(
            run.id,
            generation_index=0,
            genomes=[_make_genome(MA_CROSSOVER_SEED, 1.5)],
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.5,
        )

        gens = db.scalars(select(models.Generation)).all()
        assert len(gens) == 1
        assert gens[0].run_id == run.id
        assert gens[0].index == 0
        assert gens[0].best_fitness == pytest.approx(1.5)

        genome_rows = db.scalars(select(models.StrategyGenome)).all()
        assert len(genome_rows) == 1
        assert genome_rows[0].generation_id == gens[0].id
        assert genome_rows[0].fitness == pytest.approx(1.5)
        assert genome_rows[0].dsl_json["version"] == 1

    def test_llm_invoked_flag_persisted(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        persister.save_generation(
            run.id,
            generation_index=3,
            genomes=[_make_genome(MA_CROSSOVER_SEED, 1.5)],
            llm_invoked=True,
            llm_call_id="llm_call_xyz",
            best_fitness=1.5,
        )
        gens = db.scalars(select(models.Generation)).all()
        assert gens[0].llm_invoked is True
        assert gens[0].llm_call_id == "llm_call_xyz"


class TestSqlAlchemyPersisterSaveValidation:
    def test_persists_folds_and_marks_genome(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        # Save one generation + genome first
        genome = _make_genome(MA_CROSSOVER_SEED, 1.5)
        persister.save_generation(
            run.id,
            generation_index=0,
            genomes=[genome],
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.5,
        )

        # Build 4 fold evaluations for that genome
        folds = make_walk_forward_folds(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 5, 1, tzinfo=UTC),
            k=4,
        )
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

        persister.save_validation(
            genome.id,
            folds=fold_evals,
            validator_passed=True,
            validation_failures=[],
        )

        fold_rows = db.scalars(select(models.ValidationFold)).all()
        assert len(fold_rows) == 4
        # OOS fold marked correctly
        oos_rows = [f for f in fold_rows if f.is_oos]
        assert len(oos_rows) == 1
        assert oos_rows[0].fold_index == 3

        # Genome row updated
        db.refresh(_orm_genome := db.get(models.StrategyGenome, genome.id))
        assert _orm_genome.validator_passed is True
        assert _orm_genome.validation_failures == []


class TestSqlAlchemyPersisterLogDecision:
    def test_basic_log(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        persister.log_decision(
            run.id,
            agent_type="orchestrator",
            action="run_started",
            reasoning_text="kicking off",
        )
        rows = db.scalars(select(models.DecisionLog)).all()
        assert len(rows) == 1
        assert rows[0].action == "run_started"
        assert rows[0].agent_type == "orchestrator"
        assert rows[0].reasoning_text == "kicking off"

    def test_with_generation_link(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        # Generation must exist for the FK to be honoured (SQLite with
        # PRAGMA foreign_keys=ON enforces this).
        persister.save_generation(
            run.id,
            generation_index=2,
            genomes=[_make_genome(MA_CROSSOVER_SEED, 1.0)],
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.0,
        )
        persister.log_decision(
            run.id,
            agent_type="ga",
            action="evaluate_generation",
            generation_index=2,
        )
        rows = db.scalars(select(models.DecisionLog)).all()
        assert len(rows) == 1
        assert rows[0].generation_id is not None
        assert rows[0].generation_id.endswith("_002")

    def test_with_llm_call_id(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        persister.log_decision(
            run.id,
            agent_type="llm",
            action="inject",
            llm_call_id="llm_abc",
        )
        rows = db.scalars(select(models.DecisionLog)).all()
        assert rows[0].llm_call_id == "llm_abc"


# ─────────────────────────────────────────────────────────────────────────
# get_genome_with_lineage
# ─────────────────────────────────────────────────────────────────────────


class TestGetGenomeWithLineage:
    def _seed_two_generations(self, db: Session) -> tuple[str, list[Genome]]:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        # Gen 0 — seed parent
        parent = _make_genome(MA_CROSSOVER_SEED, 1.5)
        persister.save_generation(
            run.id,
            0,
            [parent],
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.5,
        )

        # Gen 1 — child genome with parent_ids referencing the seed
        from agentfx.api.models import GenomeOrigin

        child = Genome(
            spec=MA_CROSSOVER_SEED,
            origin=GenomeOrigin.MUTATION,
            parent_ids=[parent.id],
        )
        child.set_fitness(1.6)
        persister.save_generation(
            run.id,
            1,
            [child],
            llm_invoked=False,
            llm_call_id=None,
            best_fitness=1.6,
        )
        return run.id, [parent, child]

    def test_unknown_genome_returns_none(self, db: Session) -> None:
        result = evolution_service.get_genome_with_lineage(
            db, "evorun_X", "genome_does_not_exist"
        )
        assert result is None

    def test_returns_genome_with_lineage_fields(self, db: Session) -> None:
        run_id, [parent, child] = self._seed_two_generations(db)
        result = evolution_service.get_genome_with_lineage(db, run_id, parent.id)
        assert result is not None
        assert result["id"] == parent.id
        assert result["origin"] == "seed"
        assert result["fitness"] == pytest.approx(1.5)
        # Child is reported in the lineage
        children_ids = [c["id"] for c in result["children"]]
        assert child.id in children_ids

    def test_run_id_mismatch_returns_none(self, db: Session) -> None:
        # genome's generation belongs to a different run
        run_id, [parent, _] = self._seed_two_generations(db)
        result = evolution_service.get_genome_with_lineage(
            db, "evorun_other", parent.id
        )
        assert result is None


# ─────────────────────────────────────────────────────────────────────────
# list_decisions
# ─────────────────────────────────────────────────────────────────────────


class TestListDecisions:
    def test_paginates_and_filters_by_agent_type(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        for i in range(5):
            persister.log_decision(
                run.id,
                agent_type="ga" if i % 2 == 0 else "llm",
                action=f"action_{i}",
            )

        items, total = evolution_service.list_decisions(db, run.id, agent_type="ga")
        assert total == 3  # 3 ga, 2 llm
        assert all(d.agent_type == "ga" for d in items)

    def test_pagination(self, db: Session) -> None:
        run = evolution_service.create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        )
        persister = SqlAlchemyPersister(db)
        for i in range(10):
            persister.log_decision(run.id, agent_type="ga", action=f"a{i}")

        items, total = evolution_service.list_decisions(db, run.id, skip=3, limit=2)
        assert total == 10
        assert len(items) == 2


# ─────────────────────────────────────────────────────────────────────────
# find_prior_winners — tiered warm-start candidate selection
# ─────────────────────────────────────────────────────────────────────────


class TestFindPriorWinners:
    """The three-tier filter: validator_passed → fitness>0 → fitness>-50.

    Tier 3 is the load-bearing addition. Without it, a run whose best
    genome is net-negative produces zero warm-start candidates for the
    next run — even though that run is the latest, most-relevant signal
    in the DB. Hard search spaces (M1 majors at realistic spreads)
    routinely look like that for several iterations before the GA finds
    a profitable shape; throwing those signals away resets progress
    every time.
    """

    def _make_completed_run(
        self,
        db: Session,
        *,
        instrument: str = "EUR_USD",
        granularity: str = "M1",
    ) -> models.EvolutionRun:
        run = evolution_service.create_run(
            db,
            schemas.EvolutionRunCreate(instrument=instrument, granularity=granularity),
        )
        run.status = models.EvolutionRunStatus.COMPLETED
        db.commit()
        return run

    def _seed_genome(
        self,
        db: Session,
        *,
        run: models.EvolutionRun,
        fitness: float,
        validator_passed: bool | None = None,
        gen_index: int = 0,
        idx: int = 0,
        dsl_json: dict | None = None,
        origin: object | None = None,
    ) -> models.StrategyGenome:
        """Seed a genome. When `dsl_json` is None we generate a unique
        per-`idx` spec by perturbing MA_CROSSOVER_SEED's fast-window —
        otherwise dedup collapses all per-idx rows into one slot.

        `origin` defaults to GenomeOrigin.SEED for back-compat with
        existing tests. Plateau-exclusion tests pass
        GenomeOrigin.WARM_START to construct cascade-shaped scenarios.
        """
        from agentfx.api.models import GenomeOrigin

        gen_id = f"{run.id}_gen_{gen_index:03d}"
        existing = db.get(models.Generation, gen_id)
        if existing is None:
            db.add(models.Generation(id=gen_id, run_id=run.id, index=gen_index))
            db.commit()
        genome_id = f"genome_{run.id}_{gen_index:03d}_{idx:03d}"

        if dsl_json is None:
            # Each idx gets a structurally-distinct DSL by varying the
            # fast SMA window — otherwise the dedup-by-spec logic
            # collapses them into one slot.
            spec = MA_CROSSOVER_SEED.model_dump(mode="json")
            spec["entry"]["long"]["lhs"]["args"] = [10.0 + idx]
            spec["entry"]["short"]["lhs"]["args"] = [10.0 + idx]
            dsl_json = spec

        db.add(
            models.StrategyGenome(
                id=genome_id,
                generation_id=gen_id,
                dsl_json=dsl_json,
                origin=origin if origin is not None else GenomeOrigin.SEED,
                fitness=fitness,
                validator_passed=validator_passed,
            )
        )
        db.commit()
        return db.get(models.StrategyGenome, genome_id)

    def _mark_run_terminated(
        self,
        db: Session,
        *,
        run: models.EvolutionRun,
        stop_reason: str,
        best_genome_id: str,
    ) -> None:
        """Attach a stop_reason + best_genome_id to a completed run, the
        way the orchestrator does at end-of-loop. Plateau-exclusion
        tests need this so the picker can see "this run's elite came
        from warm-start AND the GA hit no_improvement"."""
        run.stop_reason = stop_reason
        run.best_genome_id = best_genome_id
        db.commit()

    def test_all_negative_run_returns_empty_after_tier3_removal(
        self, db: Session
    ) -> None:
        """Tier 3 (`fitness > -50`) was REMOVED in the 2026-06-19
        warm-start redesign. A run whose elites are all net-negative is
        no longer surfaced for warm-start — the orchestrator falls
        back to canonical seeds, which is strictly better than
        seeding the next run with confirmed-losing strategies.
        """
        run = self._make_completed_run(db)
        # Three negative-but-non-degenerate genomes — pre-redesign these
        # would have come back as tier-3 fallback.
        self._seed_genome(db, run=run, fitness=-0.38, idx=0)
        self._seed_genome(db, run=run, fitness=-0.51, idx=1)
        self._seed_genome(db, run=run, fitness=-2.10, idx=2)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Tier 1 (validator_passed) and tier 2 (fitness>0) are both empty;
        # tier 3 is gone, so the result is empty.
        assert rows == []

    def test_degenerate_zero_trade_penalty_never_surfaces(self, db: Session) -> None:
        """Zero-trade genomes get fitness=−100 from the hard penalty.
        These must NEVER warm-start a future run — they're degenerate
        by construction (long == short condition, etc.). Under the
        2026-06-19 redesign tier 2's `fitness > 0` filter handles this
        naturally (no need for tier 3's explicit `>-50` floor)."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=-100.0, idx=0)  # zero-trade
        self._seed_genome(db, run=run, fitness=-95.5, idx=1)  # legacy float wobble
        self._seed_genome(db, run=run, fitness=-49.9, idx=2)  # pre-redesign tier-3 edge

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # All three are below tier 2's `fitness > 0` cutoff → empty.
        assert rows == []

    def test_tier_ordering_validator_passed_wins_over_higher_fitness(
        self, db: Session
    ) -> None:
        """Tier 1 (validator_passed) must beat tier 2 even when the
        positive genome has higher raw fitness — generalisation > training."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=0.5, validator_passed=True, idx=0)
        self._seed_genome(db, run=run, fitness=10.0, validator_passed=False, idx=1)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=1
        )
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.5)

    def test_tier2_used_when_no_validator_passes(self, db: Session) -> None:
        """With tier 3 removed and source-run diversity enforced in
        tier 2, a single run contributes at most ONE non-validator-
        passed spec. The negative-fitness genome from the same run is
        excluded both by the `fitness > 0` filter and (redundantly)
        by source-run diversity."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=0.3, idx=0)
        self._seed_genome(db, run=run, fitness=-0.5, idx=1)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=2
        )
        # Only the positive-fitness genome qualifies.
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.3)

    def test_excludes_running_or_failed_runs(self, db: Session) -> None:
        run = self._make_completed_run(db)
        run.status = models.EvolutionRunStatus.RUNNING  # demote
        db.commit()
        self._seed_genome(db, run=run, fitness=0.5, idx=0)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert rows == []

    def test_empty_db_returns_empty(self, db: Session) -> None:
        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert rows == []

    def test_dedupes_by_spec_content_so_one_elite_doesnt_fill_all_slots(
        self, db: Session
    ) -> None:
        """The historical b60473a incident: a prior run's elite was
        carried across 3 generations and the picker returned 3 rows
        with the SAME dsl_json. Updated for the 2026-06-19 redesign:
        the same source run also can't fill more than one slot for
        non-validator-passed picks (source-run diversity), so even if
        dedup didn't fire, the diversity rule alone would clamp the
        count. Uses positive-fitness values so tier 2 surfaces them."""
        run = self._make_completed_run(db)
        # Same canonical spec replicated three times (simulating the
        # elite carried across 3 generations of a prior run). Positive
        # fitness so it lives in tier 2 post-redesign.
        for i in range(3):
            self._seed_genome(db, run=run, fitness=0.5, gen_index=i, idx=0)
        # Source-run diversity caps this run's contribution at 1 spec
        # regardless of whether they're dedup'd or not.
        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.5)

    def test_dedup_preserves_highest_fitness_representative(self, db: Session) -> None:
        """When the same spec appears multiple times at different
        fitnesses (e.g. elite scored under different generations'
        diversity penalties), dedup keeps the highest-fitness instance
        as the representative. Uses positive-fitness values so tier 2
        surfaces them post-redesign."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=0.2, gen_index=0, idx=0)
        self._seed_genome(db, run=run, fitness=0.7, gen_index=1, idx=0)
        self._seed_genome(db, run=run, fitness=0.4, gen_index=2, idx=0)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Only one unique spec → exactly one row → highest fitness wins
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.7)


class TestFindPriorWinnersAntiCascade:
    """The 2026-06-19 staging cascade: every new evolution was warm-
    starting from the previous run's elite, that elite was inherited
    from the run before, and so on. Each run hit `no_improvement` at
    gen 5 because the GA couldn't beat its inherited starting point.
    Two structural defences:
      A. Plateau exclusion: skip runs whose best genome's origin is
         warm_start AND stop_reason is no_improvement.
      B. Source-run diversity: at most 1 spec per source run in
         non-validator-passed tiers.
    Validator_passed=True genomes ALWAYS bypass both defences — Gate B
    clearance is real edge regardless of how the surrounding run ended.
    """

    # Reuse the helpers from TestFindPriorWinners by composition.
    _helpers = TestFindPriorWinners()

    def _make_run(self, db: Session, **kw):
        return self._helpers._make_completed_run(db, **kw)

    def _seed(self, db: Session, **kw):
        return self._helpers._seed_genome(db, **kw)

    def _terminate(self, db: Session, **kw):
        return self._helpers._mark_run_terminated(db, **kw)

    # ─────────────────────────────────────────────────────────────
    # Plateau exclusion
    # ─────────────────────────────────────────────────────────────

    def test_cascade_run_excluded_warm_start_origin_plus_no_improvement(
        self, db: Session
    ) -> None:
        """The literal cascade case: run's best genome was inherited as
        a warm-start and the GA hit no_improvement. That elite is by
        definition a local optimum the GA couldn't escape — passing it
        forward re-creates the plateau in the next run.
        """
        from agentfx.api.models import GenomeOrigin

        run = self._make_run(db)
        # The elite was inherited via warm_start, and the run terminated
        # at no_improvement → cascade shape.
        elite = self._seed(
            db,
            run=run,
            fitness=0.5,
            idx=0,
            origin=GenomeOrigin.WARM_START,
        )
        self._terminate(
            db, run=run, stop_reason="no_improvement", best_genome_id=elite.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert rows == [], (
            "Plateau filter must skip runs whose elite came from "
            "warm_start AND that hit no_improvement — propagating that "
            "elite is what caused the staging cascade."
        )

    def test_genuine_no_improvement_with_mutation_origin_still_surfaces(
        self, db: Session
    ) -> None:
        """User-raised scenario: a run starts from canonical seeds at
        fitness 0.3, the GA + LLM push to 1.2 across many generations,
        then plateaus at gen 37 → no_improvement. The elite's origin
        is `mutation` (or crossover / llm / seed) — genuine GA
        discovery, NOT inherited from warm_start. This run's elite
        IS worth preserving; the picker must include it.
        """
        from agentfx.api.models import GenomeOrigin

        run = self._make_run(db)
        elite = self._seed(
            db,
            run=run,
            fitness=1.2,
            idx=0,
            origin=GenomeOrigin.MUTATION,
        )
        self._terminate(
            db, run=run, stop_reason="no_improvement", best_genome_id=elite.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(1.2)

    def test_no_improvement_with_crossover_origin_still_surfaces(
        self, db: Session
    ) -> None:
        """Same as the mutation case — crossover origin is also genuine
        GA work, not an inherited starting point."""
        from agentfx.api.models import GenomeOrigin

        run = self._make_run(db)
        elite = self._seed(
            db,
            run=run,
            fitness=0.8,
            idx=0,
            origin=GenomeOrigin.CROSSOVER,
        )
        self._terminate(
            db, run=run, stop_reason="no_improvement", best_genome_id=elite.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.8)

    def test_validator_passed_bypasses_plateau_exclusion(self, db: Session) -> None:
        """Hypothetical: a run hits no_improvement, its elite is
        warm_start origin (so it would normally be plateau-excluded),
        BUT that elite also has validator_passed=True. Gate B
        clearance is real OOS edge regardless of the surrounding
        run's termination state — must always be picked.
        """
        from agentfx.api.models import GenomeOrigin

        run = self._make_run(db)
        elite = self._seed(
            db,
            run=run,
            fitness=0.5,
            validator_passed=True,
            idx=0,
            origin=GenomeOrigin.WARM_START,
        )
        self._terminate(
            db, run=run, stop_reason="no_improvement", best_genome_id=elite.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # validator_passed wins despite the plateau-exclusion criteria
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.5)

    def test_other_stop_reasons_are_not_plateau_excluded(self, db: Session) -> None:
        """`max_generations` or `target_fitness_reached` runs are NOT
        plateau-excluded even when their elite came from warm_start —
        the GA didn't formally give up trying. We're conservative: only
        the explicit `no_improvement` signal triggers exclusion.
        """
        from agentfx.api.models import GenomeOrigin

        run = self._make_run(db)
        elite = self._seed(
            db,
            run=run,
            fitness=0.6,
            idx=0,
            origin=GenomeOrigin.WARM_START,
        )
        self._terminate(
            db, run=run, stop_reason="max_generations", best_genome_id=elite.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.6)

    # ─────────────────────────────────────────────────────────────
    # Source-run diversity
    # ─────────────────────────────────────────────────────────────

    def test_three_winners_from_one_run_capped_to_one_in_tier_2(
        self, db: Session
    ) -> None:
        """Run A has 3 structurally-distinct positive-fitness genomes.
        Source-run diversity caps its contribution to 1 in tier 2 —
        forces gen-0 of the next run to draw from multiple historical
        lineages.
        """
        run = self._make_run(db)
        self._seed(db, run=run, fitness=1.5, idx=0)
        self._seed(db, run=run, fitness=1.2, idx=1)
        self._seed(db, run=run, fitness=0.8, idx=2)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # 3 distinct specs but all from one run → cap at 1
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(1.5)

    def test_three_runs_each_with_winner_each_contribute_one(self, db: Session) -> None:
        """3 prior runs each with a positive-fitness elite. Source-run
        diversity allows 1 from each → fills the picker."""
        run_a = self._make_run(db)
        run_b = self._make_run(db)
        run_c = self._make_run(db)
        # Different fitness magnitudes — picker should pick top by
        # fitness within the diversity constraint.
        self._seed(db, run=run_a, fitness=1.0, idx=0)
        self._seed(db, run=run_b, fitness=2.0, idx=1)
        self._seed(db, run=run_c, fitness=1.5, idx=2)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 3
        # Highest fitness first; one from each run
        fitnesses = sorted([r["fitness"] for r in rows], reverse=True)
        assert fitnesses == [pytest.approx(2.0), pytest.approx(1.5), pytest.approx(1.0)]
        source_runs = {r["source_run_id"] for r in rows}
        assert len(source_runs) == 3

    def test_source_diversity_does_not_apply_to_validator_passed_picks(
        self, db: Session
    ) -> None:
        """Multiple validator_passed genomes from the SAME run are all
        real winners — Gate B clearance is per-genome, not per-run.
        Source-run diversity must not strip these."""
        run = self._make_run(db)
        self._seed(db, run=run, fitness=0.5, validator_passed=True, idx=0)
        self._seed(db, run=run, fitness=0.8, validator_passed=True, idx=1)
        self._seed(db, run=run, fitness=1.0, validator_passed=True, idx=2)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # All 3 validator-passed picks survive; source diversity does
        # NOT apply to tier 1.
        assert len(rows) == 3

    def test_validator_passed_then_tier2_uses_remaining_slots(
        self, db: Session
    ) -> None:
        """Run A has 1 validator_passed (tier 1) + 2 distinct positive
        non-validator (tier 2) genomes. Tier 1 takes its slot (no
        diversity cap on tier 1). Tier 2 then enforces 1-per-source
        and skips the other 2 from Run A since Run A is already
        represented. The validator-passed slot doesn't count against
        the diversity constraint for tier 2 — but the run IS already
        in `seen_source_runs` once tier 1 picks one of its specs."""
        run_a = self._make_run(db)
        run_b = self._make_run(db)
        # Run A: 1 validator-passed + 2 positive non-validator
        self._seed(db, run=run_a, fitness=0.5, validator_passed=True, idx=0)
        self._seed(db, run=run_a, fitness=1.5, idx=1)
        self._seed(db, run=run_a, fitness=1.2, idx=2)
        # Run B: 1 positive non-validator
        self._seed(db, run=run_b, fitness=0.9, idx=3)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Expected:
        #   tier 1: Run A validator-passed (always included, fitness 0.5)
        #   tier 2: Run B's 0.9 (Run A is already in seen_source_runs
        #           from tier 1, so its non-validator-passed elites
        #           are diversity-skipped).
        assert len(rows) == 2
        fitnesses = sorted(r["fitness"] for r in rows)
        # validator-passed (0.5) + run B tier-2 pick (0.9)
        assert fitnesses[0] == pytest.approx(0.5)
        assert fitnesses[1] == pytest.approx(0.9)

    # ─────────────────────────────────────────────────────────────
    # Integration with config-match (already existing)
    # ─────────────────────────────────────────────────────────────

    def test_plateau_filter_runs_alongside_config_match(self, db: Session) -> None:
        """The two filters compose cleanly: a run must match the
        rubric AND not be plateau-cascaded to contribute."""
        from agentfx.api.models import GenomeOrigin

        # Run A: matching rubric, plateau-cascaded → excluded
        run_a = self._make_run(db)
        elite_a = self._seed(
            db, run=run_a, fitness=1.5, idx=0, origin=GenomeOrigin.WARM_START
        )
        self._terminate(
            db, run=run_a, stop_reason="no_improvement", best_genome_id=elite_a.id
        )

        # Run B: matching rubric, genuine progress → included
        run_b = self._make_run(db)
        elite_b = self._seed(
            db, run=run_b, fitness=0.7, idx=1, origin=GenomeOrigin.MUTATION
        )
        self._terminate(
            db, run=run_b, stop_reason="max_generations", best_genome_id=elite_b.id
        )

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Only Run B survives; Run A is plateau-excluded.
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(0.7)
        assert rows[0]["source_run_id"] == run_b.id


class TestFindPriorWinnersConfigMatch:
    """Cross-config fitness comparison guard — staging 2026-06-17.

    A run that scored fitness under one evaluation rubric (e.g. 1 year
    of data, min_cagr_pct=0.10) is not directly comparable to a run
    under a different rubric (2 years, min_cagr_pct=0.15). Without
    filtering by config, the picker chases ghosts: every new run keeps
    warm-starting from the loosest prior run's "winners" even when
    they re-evaluate to deeply negative fitness on the new run's harder
    rubric. The filter ensures only runs scored under the SAME rubric
    contribute warm-start candidates.
    """

    def _run_with_config(
        self,
        db: Session,
        *,
        data_window_start: str,
        data_window_end: str,
        min_cagr_pct: float,
        instrument: str = "EUR_USD",
        granularity: str = "M1",
    ) -> models.EvolutionRun:
        """Create a completed run whose config_json carries the given
        eval-rubric fields. Uses raw model construction since
        create_run's schema layer doesn't expose effective_* fields
        (those are populated by the worker at run-start)."""
        run = evolution_service.create_run(
            db,
            schemas.EvolutionRunCreate(instrument=instrument, granularity=granularity),
        )
        run.status = models.EvolutionRunStatus.COMPLETED
        # Match the field names the picker queries via
        # config_json["..."].astext — these mirror what
        # evolution_runner.py writes at run-start.
        cfg = dict(run.config_json or {})
        cfg["effective_data_window_start"] = data_window_start
        cfg["effective_data_window_end"] = data_window_end
        cfg["min_cagr_pct"] = min_cagr_pct
        run.config_json = cfg
        # SQLAlchemy doesn't track in-place mutations of JSON columns —
        # mark dirty so the change persists.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(run, "config_json")
        db.commit()
        return run

    def _seed_unique_genome(
        self,
        db: Session,
        *,
        run: models.EvolutionRun,
        fitness: float,
        idx: int,
    ) -> None:
        gen_id = f"{run.id}_gen_000"
        existing = db.get(models.Generation, gen_id)
        if existing is None:
            db.add(models.Generation(id=gen_id, run_id=run.id, index=0))
            db.commit()
        spec = MA_CROSSOVER_SEED.model_dump(mode="json")
        # Make each idx structurally unique so dedup doesn't collapse
        # them — picker would otherwise see only one row.
        spec["entry"]["long"]["lhs"]["args"] = [10.0 + idx]
        spec["entry"]["short"]["lhs"]["args"] = [10.0 + idx]
        from agentfx.api.models import GenomeOrigin

        db.add(
            models.StrategyGenome(
                id=f"genome_{run.id}_000_{idx:03d}",
                generation_id=gen_id,
                dsl_json=spec,
                origin=GenomeOrigin.SEED,
                fitness=fitness,
            )
        )
        db.commit()

    def test_filter_excludes_runs_with_different_data_window(self, db: Session) -> None:
        # Run A scored against a 1-year window. Run B against 2 years.
        # When asking for prior winners under the 2-year rubric, only
        # Run B's genomes should qualify even though Run A has higher
        # raw fitness (which it would, since smaller windows yield
        # easier-to-fit Sharpe numbers).
        run_a = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2024-07-01T00:00:00+00:00",
            min_cagr_pct=0.15,
        )
        run_b = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2025-07-01T00:00:00+00:00",
            min_cagr_pct=0.15,
        )
        self._seed_unique_genome(db, run=run_a, fitness=1.5, idx=0)  # higher
        self._seed_unique_genome(db, run=run_b, fitness=0.4, idx=1)  # lower

        rows = evolution_service.find_prior_winners(
            db,
            instrument="EUR_USD",
            granularity="M1",
            limit=3,
            data_window_start=datetime.fromisoformat("2023-07-01T00:00:00+00:00"),
            data_window_end=datetime.fromisoformat("2025-07-01T00:00:00+00:00"),
            min_cagr_pct=0.15,
        )
        assert len(rows) == 1
        assert rows[0]["source_run_id"] == run_b.id
        assert rows[0]["fitness"] == pytest.approx(0.4)

    def test_filter_excludes_runs_with_different_min_cagr(self, db: Session) -> None:
        # Same data window, different min_cagr_pct. Run with looser
        # 0.10 threshold can produce a higher fitness for a genome
        # that wouldn't clear 0.15 — it must be excluded.
        run_loose = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2025-07-01T00:00:00+00:00",
            min_cagr_pct=0.10,
        )
        run_strict = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2025-07-01T00:00:00+00:00",
            min_cagr_pct=0.15,
        )
        self._seed_unique_genome(db, run=run_loose, fitness=1.5, idx=0)
        self._seed_unique_genome(db, run=run_strict, fitness=0.4, idx=1)

        rows = evolution_service.find_prior_winners(
            db,
            instrument="EUR_USD",
            granularity="M1",
            limit=3,
            data_window_start=datetime.fromisoformat("2023-07-01T00:00:00+00:00"),
            data_window_end=datetime.fromisoformat("2025-07-01T00:00:00+00:00"),
            min_cagr_pct=0.15,
        )
        assert len(rows) == 1
        assert rows[0]["source_run_id"] == run_strict.id

    def test_no_config_args_preserves_legacy_behaviour(self, db: Session) -> None:
        # Without config args, the picker behaves as before: all
        # completed runs match. Required for legacy callers and tests
        # that don't care about config-match.
        run_a = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2024-07-01T00:00:00+00:00",
            min_cagr_pct=0.10,
        )
        run_b = self._run_with_config(
            db,
            data_window_start="2023-07-01T00:00:00+00:00",
            data_window_end="2025-07-01T00:00:00+00:00",
            min_cagr_pct=0.15,
        )
        self._seed_unique_genome(db, run=run_a, fitness=1.5, idx=0)
        self._seed_unique_genome(db, run=run_b, fitness=0.4, idx=1)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Both runs included; highest fitness wins ordering
        assert len(rows) == 2
        assert rows[0]["fitness"] == pytest.approx(1.5)
        assert rows[1]["fitness"] == pytest.approx(0.4)
