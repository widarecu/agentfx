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
    ) -> models.StrategyGenome:
        """Seed a genome. When `dsl_json` is None we generate a unique
        per-`idx` spec by perturbing MA_CROSSOVER_SEED's fast-window —
        otherwise dedup collapses all per-idx rows into one slot."""
        gen_id = f"{run.id}_gen_{gen_index:03d}"
        existing = db.get(models.Generation, gen_id)
        if existing is None:
            db.add(models.Generation(id=gen_id, run_id=run.id, index=gen_index))
            db.commit()
        genome_id = f"genome_{run.id}_{gen_index:03d}_{idx:03d}"
        from agentfx.api.models import GenomeOrigin

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
                origin=GenomeOrigin.SEED,
                fitness=fitness,
                validator_passed=validator_passed,
            )
        )
        db.commit()
        return db.get(models.StrategyGenome, genome_id)

    def test_tier3_carries_forward_when_all_runs_are_net_negative(
        self, db: Session
    ) -> None:
        """The regression the user hit: run completes with best fitness
        −0.38 (no positives, no validation passes). Next run's warm-start
        must still pick it up — otherwise GA progress is silently
        discarded every run."""
        run = self._make_completed_run(db)
        # Three negative-but-non-degenerate genomes
        self._seed_genome(db, run=run, fitness=-0.38, idx=0)
        self._seed_genome(db, run=run, fitness=-0.51, idx=1)
        self._seed_genome(db, run=run, fitness=-2.10, idx=2)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 3, (
            "Tier 3 must carry forward net-negative genomes; got "
            f"{len(rows)} candidates for a run with no positives and no "
            f"validator passes."
        )
        # Top-by-fitness ordering preserved even in tier 3
        assert rows[0]["fitness"] == pytest.approx(-0.38)
        assert rows[1]["fitness"] == pytest.approx(-0.51)
        assert rows[2]["fitness"] == pytest.approx(-2.10)

    def test_tier3_excludes_degenerate_zero_trade_penalty(self, db: Session) -> None:
        """Zero-trade genomes get fitness=−100 from the hard penalty.
        These must NEVER warm-start a future run — they're degenerate by
        construction (long == short condition, etc.)."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=-100.0, idx=0)  # zero-trade
        self._seed_genome(db, run=run, fitness=-95.5, idx=1)  # legacy float wobble
        self._seed_genome(db, run=run, fitness=-49.9, idx=2)  # right at the edge

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Only the -49.9 genome qualifies; the others are below the -50 floor
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(-49.9)

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
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=0.3, idx=0)
        self._seed_genome(db, run=run, fitness=-0.5, idx=1)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=2
        )
        # Tier 2 fills with the positive; tier 3 then tops up with the negative
        assert len(rows) == 2
        assert rows[0]["fitness"] == pytest.approx(0.3)
        assert rows[1]["fitness"] == pytest.approx(-0.5)

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
        """The user's incident: a prior run's elite genome was carried
        across generations 5, 6, 7. The warm-start query returned 3
        rows with the SAME dsl_json, so all three warm-start slots in
        the next run got identical specs — population collapsed to a
        monoculture, crossover-of-clones produced clones, GA stuck."""
        run = self._make_completed_run(db)
        # Same canonical spec replicated three times (simulating the
        # elite carried across 3 generations of a prior run).
        for i in range(3):
            self._seed_genome(db, run=run, fitness=-0.09, gen_index=i, idx=0)
        # A second, structurally-different spec at lower fitness.
        from agentfx.api.models import GenomeOrigin

        gen_id = f"{run.id}_gen_other"
        db.add(models.Generation(id=gen_id, run_id=run.id, index=10))
        db.commit()
        db.add(
            models.StrategyGenome(
                id="genome_diff_spec",
                generation_id=gen_id,
                # Different DSL → not deduped against the elite
                dsl_json=RSI_MEAN_REVERSION_SEED.model_dump(mode="json"),
                origin=GenomeOrigin.SEED,
                fitness=-0.50,
            )
        )
        db.commit()

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        assert len(rows) == 2, (
            f"Expected 2 unique specs (one elite-cluster + one diff "
            f"spec); got {len(rows)} which means dedup didn't fire."
        )
        # First slot = the higher-fitness elite (one copy, not three)
        assert rows[0]["fitness"] == pytest.approx(-0.09)
        # Second slot = the structurally-different lower-fitness spec
        assert rows[1]["fitness"] == pytest.approx(-0.50)

    def test_dedup_preserves_highest_fitness_representative(self, db: Session) -> None:
        """When the same spec appears multiple times at different
        fitnesses (e.g. elite scored under different generations'
        diversity penalties), dedup keeps the highest-fitness instance
        as the representative."""
        run = self._make_completed_run(db)
        self._seed_genome(db, run=run, fitness=-2.0, gen_index=0, idx=0)
        self._seed_genome(db, run=run, fitness=-0.5, gen_index=1, idx=0)
        self._seed_genome(db, run=run, fitness=-1.0, gen_index=2, idx=0)

        rows = evolution_service.find_prior_winners(
            db, instrument="EUR_USD", granularity="M1", limit=3
        )
        # Only one unique spec → exactly one row → highest fitness wins
        assert len(rows) == 1
        assert rows[0]["fitness"] == pytest.approx(-0.5)
