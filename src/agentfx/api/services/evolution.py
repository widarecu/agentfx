"""Evolution-run service layer.

Two responsibilities:

1. CRUD on runs / generations / genomes / decisions for the API
   (`get_run`, `get_runs`, `create_run`, `get_genome_with_lineage`, etc.)

2. The SQLAlchemy-backed `EvolutionStatePersister` implementation that the
   orchestrator depends on (`SqlAlchemyPersister`). The orchestrator runs
   inside a forked child process; this class owns the child's DB session.

The split keeps the API endpoints simple (call functions, return ORM rows
that get serialized via Pydantic schemas) while giving the orchestrator a
typed dependency it can mock.

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from agentfx.agents.evolution import Genome
from agentfx.agents.validator import FoldEvaluation
from agentfx.api import models, schemas

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# ID generation
# ─────────────────────────────────────────────────────────────────────────


def generate_run_id() -> str:
    """`evorun_{YYYYMMDD}_{8 hex}` — sortable, traceable in logs."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    short = uuid.uuid4().hex[:8]
    return f"evorun_{today}_{short}"


def generate_generation_id(run_id: str, index: int) -> str:
    return f"gen_{run_id}_{index:03d}"


def generate_genome_id(generation_id: str, index_in_gen: int) -> str:
    return f"genome_{generation_id}_{index_in_gen:03d}"


def generate_validation_fold_id(genome_id: str, fold_index: int) -> str:
    return f"fold_{genome_id}_{fold_index}"


# ─────────────────────────────────────────────────────────────────────────
# Run CRUD
# ─────────────────────────────────────────────────────────────────────────


def get_run(db: Session, run_id: str) -> models.EvolutionRun | None:
    return (
        db.query(models.EvolutionRun).filter(models.EvolutionRun.id == run_id).first()
    )


def list_runs(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 10,
    status: str | None = None,
    instrument: str | None = None,
) -> tuple[list[models.EvolutionRun], int]:
    """Paginated listing. Returns (items, total)."""
    q = db.query(models.EvolutionRun)
    if status:
        q = q.filter(models.EvolutionRun.status == status)
    if instrument:
        q = q.filter(models.EvolutionRun.instrument == instrument)
    total = q.count()
    items = (
        q.order_by(desc(models.EvolutionRun.created_at)).offset(skip).limit(limit).all()
    )
    return items, total


def create_run(
    db: Session,
    create: schemas.EvolutionRunCreate,
    *,
    created_by: int | None = None,
) -> models.EvolutionRun:
    """Persist a new evolution_run row in QUEUED status. The actual
    loop is launched separately by the worker."""
    config_dict = (
        create.config.model_dump()
        if create.config is not None
        else schemas.OrchestratorConfigSchema(
            instrument=create.instrument, granularity=create.granularity
        ).model_dump()
    )
    stop_dict = (
        create.stop_conditions.model_dump()
        if create.stop_conditions is not None
        else schemas.StopConditionsSchema().model_dump()
    )
    # Capture the caller-facing fields too so the row is fully self-describing
    config_dict.setdefault("instrument", create.instrument)
    config_dict.setdefault("granularity", create.granularity)
    config_dict["data_years"] = create.data_years
    config_dict["llm_budget_usd"] = create.llm_budget_usd

    run = models.EvolutionRun(
        id=generate_run_id(),
        status=models.EvolutionRunStatus.QUEUED,
        instrument=create.instrument,
        granularity=create.granularity,
        config_json=config_dict,
        stop_conditions_json=stop_dict,
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def stop_run(db: Session, run_id: str) -> models.EvolutionRun | None:
    """Mark a run for manual stop. The worker (next chunk) actually
    sends SIGTERM to the child process."""
    run = get_run(db, run_id)
    if run is None:
        return None
    if run.status not in (
        models.EvolutionRunStatus.QUEUED,
        models.EvolutionRunStatus.RUNNING,
    ):
        return run  # idempotent — already stopped/completed/failed
    run.status = models.EvolutionRunStatus.STOPPED
    run.stop_reason = "manual_stop"
    run.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(run)
    return run


def find_orphaned_running_runs(db: Session) -> list[models.EvolutionRun]:
    """Runs in RUNNING state on app startup are orphaned by definition —
    their child process died with the previous app instance. The worker's
    recovery hook flips them back to QUEUED so they can be re-scheduled.
    """
    return (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.status == models.EvolutionRunStatus.RUNNING)
        .all()
    )


def find_queued_runs(db: Session) -> list[models.EvolutionRun]:
    return (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.status == models.EvolutionRunStatus.QUEUED)
        .order_by(models.EvolutionRun.created_at)
        .all()
    )


# ─────────────────────────────────────────────────────────────────────────
# Generation / genome / decision read paths
# ─────────────────────────────────────────────────────────────────────────


def list_generations(db: Session, run_id: str) -> list[models.Generation]:
    return (
        db.query(models.Generation)
        .filter(models.Generation.run_id == run_id)
        .order_by(models.Generation.index)
        .all()
    )


def get_genome_with_lineage(
    db: Session, run_id: str, genome_id: str
) -> dict[str, Any] | None:
    """Fetch one genome with its lineage and validation folds.

    Returns a plain dict the router can serialize — using a dict (rather
    than the ORM object) lets us include synthetic fields (children
    summaries, generation_index) the schema requires but the model
    doesn't carry directly.
    """
    genome = (
        db.query(models.StrategyGenome)
        .filter(models.StrategyGenome.id == genome_id)
        .first()
    )
    if genome is None:
        return None

    # Generation must belong to the run (defence in depth — caller's
    # responsibility too, but cheap to verify).
    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .first()
    )
    if generation is None or generation.run_id != run_id:
        return None

    # Children: any genome whose parent_ids JSON list contains this id.
    # Postgres-specific operator is jsonb @>, but the column is JSON not
    # JSONB; fall back to a Python-side filter using a coarse query.
    candidate_children = (
        db.query(models.StrategyGenome, models.Generation.index.label("gen_index"))
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .filter(models.Generation.run_id == run_id)
        .filter(models.Generation.index > generation.index)
        .all()
    )
    children = [
        {"id": c[0].id, "generation_index": c[1]}
        for c in candidate_children
        if isinstance(c[0].parent_ids, list) and genome.id in c[0].parent_ids
    ]

    folds = (
        db.query(models.ValidationFold)
        .filter(models.ValidationFold.genome_id == genome.id)
        .order_by(models.ValidationFold.fold_index)
        .all()
    )

    return {
        "id": genome.id,
        "generation_id": genome.generation_id,
        "generation_index": generation.index,
        "dsl_json": genome.dsl_json,
        "parent_ids": list(genome.parent_ids or []),
        "origin": (
            genome.origin.value if hasattr(genome.origin, "value") else genome.origin
        ),
        "fitness": genome.fitness,
        "validator_passed": genome.validator_passed,
        "validation_failures": list(genome.validation_failures or []),
        "llm_reasoning": genome.llm_reasoning,
        "llm_call_id": genome.llm_call_id,
        "created_at": genome.created_at,
        "children": children,
        "validation_folds": folds,
    }


def list_decisions(
    db: Session,
    run_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
    agent_type: str | None = None,
) -> tuple[list[models.DecisionLog], int]:
    """Paginated decision-log query for the portal."""
    q = db.query(models.DecisionLog).filter(models.DecisionLog.run_id == run_id)
    if agent_type:
        q = q.filter(models.DecisionLog.agent_type == agent_type)
    total = q.count()
    items = q.order_by(models.DecisionLog.created_at).offset(skip).limit(limit).all()
    return items, total


# ─────────────────────────────────────────────────────────────────────────
# SQLAlchemy-backed EvolutionStatePersister (orchestrator dependency)
# ─────────────────────────────────────────────────────────────────────────


class SqlAlchemyPersister:
    """Real implementation of `agents.orchestrator.EvolutionStatePersister`.

    Owns the child process's DB session. The orchestrator runs inside a
    forked child; this class is constructed once at the start of that
    child, then receives every `update_run`/`save_generation`/etc. call.

    Each method commits its own transaction — the orchestrator doesn't
    know about transactions, and we want incremental durable writes
    (an interrupted run leaves a partial-but-readable state behind).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── update_run ──────────────────────────────────────────────────

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
    ) -> None:
        run = (
            self.db.query(models.EvolutionRun)
            .filter(models.EvolutionRun.id == run_id)
            .first()
        )
        if run is None:
            logger.error(
                "update_run for unknown run_id=%s — skipping (orchestrator bug?)",
                run_id,
            )
            return

        if status is not None:
            try:
                run.status = models.EvolutionRunStatus(status)
            except ValueError:
                logger.warning("ignoring unknown status=%s on run %s", status, run_id)
        if current_generation is not None:
            run.current_generation = current_generation
        if best_genome_id is not None:
            run.best_genome_id = best_genome_id
        if llm_cost_usd is not None:
            run.llm_cost_usd = llm_cost_usd
        if stop_reason is not None:
            run.stop_reason = stop_reason
        if started_at is not None:
            run.started_at = started_at
        if completed_at is not None:
            run.completed_at = completed_at

        self.db.commit()

    # ── save_generation ─────────────────────────────────────────────

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

        Mutates `genome.id` on each input genome to the assigned
        semantic key — the orchestrator relies on this for parent_ids
        recording in subsequent generations.
        """
        gen_id = generate_generation_id(run_id, generation_index)
        now = datetime.now(UTC)

        # Upsert the generation row. If we're re-running due to crash
        # recovery, the row may already exist — overwrite metadata.
        existing = (
            self.db.query(models.Generation)
            .filter(models.Generation.id == gen_id)
            .first()
        )
        if existing is None:
            generation = models.Generation(
                id=gen_id,
                run_id=run_id,
                index=generation_index,
                started_at=now,
                completed_at=now,
                llm_invoked=llm_invoked,
                llm_call_id=llm_call_id,
                best_fitness=best_fitness,
            )
            self.db.add(generation)
        else:
            existing.completed_at = now
            existing.llm_invoked = llm_invoked
            existing.llm_call_id = llm_call_id
            existing.best_fitness = best_fitness

        # Assign IDs and persist each genome
        for i, genome in enumerate(genomes):
            if genome.id is None:
                genome.id = generate_genome_id(gen_id, i)

            existing_genome = (
                self.db.query(models.StrategyGenome)
                .filter(models.StrategyGenome.id == genome.id)
                .first()
            )
            if existing_genome is not None:
                continue  # already persisted; skip

            origin_value = (
                genome.origin.value
                if hasattr(genome.origin, "value")
                else str(genome.origin)
            )
            row = models.StrategyGenome(
                id=genome.id,
                generation_id=gen_id,
                dsl_json=genome.spec.model_dump(mode="json"),
                parent_ids=list(genome.parent_ids),
                origin=models.GenomeOrigin(origin_value),
                fitness=(genome.fitness_value if genome.has_fitness() else None),
                validator_passed=None,
                validation_failures=[],
                llm_reasoning=genome.llm_reasoning,
                llm_call_id=genome.llm_call_id,
            )
            self.db.add(row)

        self.db.commit()

    # ── save_validation ─────────────────────────────────────────────

    def save_validation(
        self,
        genome_id: str,
        folds: list[FoldEvaluation],
        *,
        validator_passed: bool,
        validation_failures: list[str],
    ) -> None:
        # Persist each fold row
        for fe in folds:
            fold_id = generate_validation_fold_id(genome_id, fe.fold.index)
            existing = (
                self.db.query(models.ValidationFold)
                .filter(models.ValidationFold.id == fold_id)
                .first()
            )
            if existing is not None:
                continue

            row = models.ValidationFold(
                id=fold_id,
                genome_id=genome_id,
                fold_index=fe.fold.index,
                fold_start=fe.fold.start,
                fold_end=fe.fold.end,
                is_oos=fe.fold.is_oos,
                sharpe=fe.metrics.get("sharpe"),
                max_drawdown=fe.metrics.get("max_drawdown"),
                hit_rate=fe.metrics.get("hit_rate"),
                n_trades=fe.metrics.get("n_trades"),
                passed_gate_b=fe.passed_gate_b,
            )
            self.db.add(row)

        # Update the genome's validator status
        genome = (
            self.db.query(models.StrategyGenome)
            .filter(models.StrategyGenome.id == genome_id)
            .first()
        )
        if genome is not None:
            genome.validator_passed = validator_passed
            genome.validation_failures = list(validation_failures)

        self.db.commit()

    # ── log_decision ────────────────────────────────────────────────

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
    ) -> None:
        generation_id: str | None = None
        if generation_index is not None:
            generation_id = generate_generation_id(run_id, generation_index)

        row = models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=run_id,
            generation_id=generation_id,
            agent_type=agent_type,
            action=action,
            inputs_json=inputs_json,
            outputs_json=outputs_json,
            reasoning_text=reasoning_text,
            llm_call_id=llm_call_id,
        )
        self.db.add(row)
        self.db.commit()


__all__ = [
    "SqlAlchemyPersister",
    "create_run",
    "find_orphaned_running_runs",
    "find_queued_runs",
    "generate_genome_id",
    "generate_generation_id",
    "generate_run_id",
    "generate_validation_fold_id",
    "get_genome_with_lineage",
    "get_run",
    "list_decisions",
    "list_generations",
    "list_runs",
    "stop_run",
]
