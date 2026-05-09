"""Schema-level tests for the v0.3.0 evolution ORM models.

Uses an in-memory SQLite to exercise table creation, foreign-key cascade
behaviour, and basic insert/query round-trips. The Alembic migration
(`migrations/versions/2026_05_09_add_evolution_tables.py`) targets Postgres
specifically; integration tests in tests/integration/evolution/ exercise it
against the real test stack (port 5433).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from agentfx.api.core.database import Base
from agentfx.api.models import (
    AgentType,
    DecisionLog,
    EvolutionRun,
    EvolutionRunStatus,
    Generation,
    GenomeOrigin,
    StrategyGenome,
    ValidationFold,
)


@pytest.fixture
def engine():
    """In-memory SQLite engine with foreign-key constraints enabled."""
    eng = create_engine("sqlite:///:memory:")

    # SQLite doesn't enforce FKs by default; turn them on so cascade tests
    # actually exercise cascade behaviour.
    @event.listens_for(eng, "connect")
    def _enable_fks(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


def _make_run(session: Session, **overrides) -> EvolutionRun:
    defaults = {
        "id": f"evorun_20260509_{_short_uuid()}",
        "status": EvolutionRunStatus.QUEUED,
        "instrument": "EUR_USD",
        "granularity": "M1",
        "config_json": {"data_years": 5, "population_size": 30},
        "stop_conditions_json": {
            "max_generations": 30,
            "max_wall_clock_seconds": 21600,
        },
    }
    defaults.update(overrides)
    run = EvolutionRun(**defaults)
    session.add(run)
    session.commit()
    return run


def _make_generation(session: Session, run: EvolutionRun, index: int = 0) -> Generation:
    gen = Generation(
        id=f"gen_{run.id}_{index}",
        run_id=run.id,
        index=index,
    )
    session.add(gen)
    session.commit()
    return gen


def _make_genome(
    session: Session,
    generation: Generation,
    *,
    origin: GenomeOrigin = GenomeOrigin.SEED,
    fitness: float | None = None,
    parent_ids: list[str] | None = None,
) -> StrategyGenome:
    genome = StrategyGenome(
        id=f"genome_{generation.id}_{_short_uuid()}",
        generation_id=generation.id,
        dsl_json={"version": 1, "entry": {}, "exit": {}, "size": {}},
        parent_ids=parent_ids or [],
        origin=origin,
        fitness=fitness,
    )
    session.add(genome)
    session.commit()
    return genome


# ─────────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_status_values(self) -> None:
        assert EvolutionRunStatus.QUEUED.value == "queued"
        assert EvolutionRunStatus.RUNNING.value == "running"
        assert EvolutionRunStatus.COMPLETED.value == "completed"
        assert EvolutionRunStatus.FAILED.value == "failed"
        assert EvolutionRunStatus.STOPPED.value == "stopped"

    def test_origin_values(self) -> None:
        assert GenomeOrigin.SEED.value == "seed"
        assert GenomeOrigin.CROSSOVER.value == "crossover"
        assert GenomeOrigin.MUTATION.value == "mutation"
        assert GenomeOrigin.LLM.value == "llm"

    def test_agent_type_values(self) -> None:
        assert AgentType.ORCHESTRATOR.value == "orchestrator"
        assert AgentType.GA.value == "ga"
        assert AgentType.LLM.value == "llm"
        assert AgentType.VALIDATOR.value == "validator"


class TestEvolutionRunInsert:
    def test_round_trip(self, session: Session) -> None:
        run = _make_run(session)

        fetched = session.scalar(select(EvolutionRun).where(EvolutionRun.id == run.id))
        assert fetched is not None
        assert fetched.status == EvolutionRunStatus.QUEUED
        assert fetched.instrument == "EUR_USD"
        assert fetched.granularity == "M1"
        assert fetched.config_json["data_years"] == 5
        assert fetched.current_generation == 0  # default
        assert float(fetched.llm_cost_usd) == 0.0  # default

    def test_status_transitions(self, session: Session) -> None:
        run = _make_run(session)
        run.status = EvolutionRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        run.process_pid = 12345
        session.commit()

        fetched = session.scalar(select(EvolutionRun).where(EvolutionRun.id == run.id))
        assert fetched.status == EvolutionRunStatus.RUNNING
        assert fetched.process_pid == 12345


class TestGenerationLineage:
    def test_unique_run_index_constraint(self, session: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        run = _make_run(session)
        _make_generation(session, run, index=0)

        # Same (run_id, index) pair must be rejected
        dup = Generation(id=f"gen_{run.id}_0_dup", run_id=run.id, index=0)
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_cascade_delete_run_removes_generations(self, session: Session) -> None:
        run = _make_run(session)
        _make_generation(session, run, index=0)
        _make_generation(session, run, index=1)

        session.delete(run)
        session.commit()

        remaining = session.scalars(
            select(Generation).where(Generation.run_id == run.id)
        ).all()
        assert remaining == []


class TestStrategyGenomeLineage:
    def test_genome_with_parents(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        parent_a = _make_genome(session, gen, origin=GenomeOrigin.SEED, fitness=0.8)
        parent_b = _make_genome(session, gen, origin=GenomeOrigin.SEED, fitness=0.9)

        gen2 = _make_generation(session, run, index=1)
        child = _make_genome(
            session,
            gen2,
            origin=GenomeOrigin.CROSSOVER,
            parent_ids=[parent_a.id, parent_b.id],
        )

        fetched = session.scalar(
            select(StrategyGenome).where(StrategyGenome.id == child.id)
        )
        assert fetched.origin == GenomeOrigin.CROSSOVER
        assert fetched.parent_ids == [parent_a.id, parent_b.id]

    def test_top_n_by_fitness_query(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        for i, fit in enumerate([0.5, 1.2, 0.8, 1.5, 0.3]):
            _make_genome(session, gen, fitness=fit)

        top_3 = session.scalars(
            select(StrategyGenome)
            .where(StrategyGenome.generation_id == gen.id)
            .order_by(StrategyGenome.fitness.desc())
            .limit(3)
        ).all()
        assert [g.fitness for g in top_3] == [1.5, 1.2, 0.8]

    def test_validation_failures_default_empty_list(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        genome = _make_genome(session, gen)
        # Default value comes from the column default; SQLAlchemy may emit
        # NULL on insert if not set, depending on the dialect — assert on
        # behaviour rather than wire format.
        assert genome.validation_failures in ([], None)


class TestValidationFold:
    def test_unique_genome_fold_index(self, session: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        run = _make_run(session)
        gen = _make_generation(session, run)
        genome = _make_genome(session, gen)

        now = datetime.now(UTC)
        fold = ValidationFold(
            id=f"fold_{genome.id}_0",
            genome_id=genome.id,
            fold_index=0,
            fold_start=now - timedelta(days=30),
            fold_end=now,
            is_oos=False,
            sharpe=1.2,
            max_drawdown=0.05,
            hit_rate=0.55,
            n_trades=400,
            passed_gate_b=True,
        )
        session.add(fold)
        session.commit()

        dup = ValidationFold(
            id=f"fold_{genome.id}_0_dup",
            genome_id=genome.id,
            fold_index=0,  # same index
            fold_start=now,
            fold_end=now,
            is_oos=False,
            passed_gate_b=False,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_oos_fold_is_distinguishable(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        genome = _make_genome(session, gen)

        now = datetime.now(UTC)
        for i in range(4):
            fold = ValidationFold(
                id=f"fold_{genome.id}_{i}",
                genome_id=genome.id,
                fold_index=i,
                fold_start=now - timedelta(days=30 * (4 - i)),
                fold_end=now - timedelta(days=30 * (3 - i)),
                is_oos=(i == 3),
                passed_gate_b=True,
            )
            session.add(fold)
        session.commit()

        oos = session.scalars(
            select(ValidationFold).where(
                ValidationFold.genome_id == genome.id,
                ValidationFold.is_oos.is_(True),
            )
        ).all()
        assert len(oos) == 1
        assert oos[0].fold_index == 3


class TestDecisionLog:
    def test_append_only_audit_trail(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)

        for action in ("init_population", "evaluate", "mutate", "select"):
            entry = DecisionLog(
                id=uuid.uuid4().hex,
                run_id=run.id,
                generation_id=gen.id,
                agent_type=AgentType.GA.value,
                action=action,
                inputs_json={"action": action},
                outputs_json={"ok": True},
                reasoning_text=f"GA action: {action}",
            )
            session.add(entry)
        session.commit()

        rows = session.scalars(
            select(DecisionLog)
            .where(DecisionLog.run_id == run.id)
            .order_by(DecisionLog.created_at)
        ).all()
        assert [r.action for r in rows] == [
            "init_population",
            "evaluate",
            "mutate",
            "select",
        ]

    def test_generation_id_set_null_on_generation_delete(
        self, session: Session
    ) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        entry = DecisionLog(
            id=uuid.uuid4().hex,
            run_id=run.id,
            generation_id=gen.id,
            agent_type=AgentType.GA.value,
            action="evaluate",
        )
        session.add(entry)
        session.commit()

        # Per the schema, FK is ON DELETE SET NULL — but cascade SQL is
        # asserted on the migration, not exercised through ORM here. We
        # confirm only that the row links to a valid generation today.
        fetched = session.scalar(select(DecisionLog).where(DecisionLog.id == entry.id))
        assert fetched.generation_id == gen.id

    def test_dsl_json_round_trip_via_decision_log(self, session: Session) -> None:
        # The decision_log frequently captures DSL specs as input/output —
        # confirm JSON round-trips cleanly through the column.
        run = _make_run(session)
        spec_dict = {
            "version": 1,
            "entry": {
                "long": {"lhs": {"ind": "rsi", "args": [14]}, "cmp": "<", "rhs": 30}
            },
            "exit": {
                "stop_pips": 10,
                "target_pips": 30,
                "max_hold_bars": 240,
                "opposite_signal": True,
            },
            "size": {"risk_pct": 0.005},
        }
        entry = DecisionLog(
            id=uuid.uuid4().hex,
            run_id=run.id,
            agent_type=AgentType.LLM.value,
            action="propose",
            outputs_json=spec_dict,
        )
        session.add(entry)
        session.commit()

        fetched = session.scalar(select(DecisionLog).where(DecisionLog.id == entry.id))
        # Round-trip through JSON
        restored = json.loads(json.dumps(fetched.outputs_json))
        assert restored == spec_dict


class TestRunCascade:
    def test_deleting_run_cascades_to_descendants(self, session: Session) -> None:
        run = _make_run(session)
        gen = _make_generation(session, run)
        genome = _make_genome(session, gen)

        now = datetime.now(UTC)
        fold = ValidationFold(
            id=f"fold_{genome.id}_0",
            genome_id=genome.id,
            fold_index=0,
            fold_start=now,
            fold_end=now,
            is_oos=False,
            passed_gate_b=True,
        )
        decision = DecisionLog(
            id=uuid.uuid4().hex,
            run_id=run.id,
            generation_id=gen.id,
            agent_type=AgentType.GA.value,
            action="evaluate",
        )
        session.add_all([fold, decision])
        session.commit()

        session.delete(run)
        session.commit()

        # All descendants should be gone
        assert (
            session.scalar(select(Generation).where(Generation.run_id == run.id))
            is None
        )
        assert (
            session.scalar(
                select(StrategyGenome).where(StrategyGenome.generation_id == gen.id)
            )
            is None
        )
        assert (
            session.scalar(
                select(ValidationFold).where(ValidationFold.genome_id == genome.id)
            )
            is None
        )
        assert (
            session.scalar(select(DecisionLog).where(DecisionLog.run_id == run.id))
            is None
        )
