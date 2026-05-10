"""Evolution-run API router (v0.3.0 Autonomous Strategy Loop).

Endpoints:
- POST   /api/v1/evolution-runs                          (TRADER+, 202)
- GET    /api/v1/evolution-runs                          (paginated)
- GET    /api/v1/evolution-runs/{run_id}                 (detail)
- DELETE /api/v1/evolution-runs/{run_id}                 (ADMIN)
- POST   /api/v1/evolution-runs/{run_id}/stop            (TRADER+, manual halt)
- GET    /api/v1/evolution-runs/{run_id}/generations     (lineage)
- GET    /api/v1/evolution-runs/{run_id}/genomes/{id}    (genome detail)
- GET    /api/v1/evolution-runs/{run_id}/decisions       (decision log)

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md for the full contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..services import evolution as evolution_service
from ..services import user as user_service

router = APIRouter()


def _ensure_trader(current_user: models.User) -> None:
    if current_user.role not in (
        models.UserRole.TRADER,
        models.UserRole.ADMIN,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TRADER or ADMIN role required",
        )


def _ensure_admin(current_user: models.User) -> None:
    if current_user.role != models.UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN role required",
        )


# ─────────────────────────────────────────────────────────────────────────
# Run lifecycle
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/evolution-runs",
    response_model=schemas.EvolutionRunCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_evolution_run(
    body: schemas.EvolutionRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunCreated:
    """Schedule a new evolution run.

    The run is persisted in QUEUED status and the worker is invoked via
    BackgroundTasks. The actual loop runs in a forked subprocess with
    `resource.setrlimit()` caps.
    """
    _ensure_trader(current_user)

    run = evolution_service.create_run(db, body, created_by=current_user.id)

    # Lazy import to avoid circular dependency on app startup
    from ..workers.evolution_runner import schedule_run

    background_tasks.add_task(schedule_run, run.id)

    estimated_max_generations = (
        body.stop_conditions.max_generations
        if body.stop_conditions
        else schemas.StopConditionsSchema().max_generations
    )

    return schemas.EvolutionRunCreated(
        run_id=run.id,
        status=run.status.value,
        status_url=f"/api/v1/evolution-runs/{run.id}",
        instrument=run.instrument,
        granularity=run.granularity,
        estimated_max_generations=estimated_max_generations,
        created_at=run.created_at,
    )


@router.get("/evolution-runs", response_model=schemas.EvolutionRunList)
def list_evolution_runs(
    page: int = 1,
    page_size: int = 10,
    status_filter: str | None = None,
    instrument: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunList:
    skip = (page - 1) * page_size
    items, total = evolution_service.list_runs(
        db,
        skip=skip,
        limit=page_size,
        status=status_filter,
        instrument=instrument,
    )
    return schemas.EvolutionRunList(
        items=[schemas.EvolutionRunPublic.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/evolution-runs/{run_id}", response_model=schemas.EvolutionRunPublic)
def get_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunPublic:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    return schemas.EvolutionRunPublic.model_validate(run)


@router.delete("/evolution-runs/{run_id}")
def delete_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> dict[str, bool | str]:
    """ADMIN only — deletes a run and all its lineage (cascades)."""
    _ensure_admin(current_user)
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    db.delete(run)
    db.commit()
    return {"success": True, "message": "Run deleted"}


@router.post("/evolution-runs/{run_id}/stop", response_model=schemas.EvolutionRunPublic)
def stop_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunPublic:
    """Request a manual halt. The worker will SIGTERM the child process if
    it's running; if the run hasn't started yet, status flips to STOPPED
    immediately."""
    _ensure_trader(current_user)
    run = evolution_service.stop_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    # Best-effort: if the worker is running, kill the child process. The
    # worker's monitor will see the exit and finalize state.
    if run.process_pid:
        from ..workers.evolution_runner import signal_run_stop

        signal_run_stop(run.process_pid)

    return schemas.EvolutionRunPublic.model_validate(run)


# ─────────────────────────────────────────────────────────────────────────
# Sub-resources: generations / genomes / decisions
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/evolution-runs/{run_id}/generations",
    response_model=schemas.GenerationList,
)
def list_run_generations(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.GenerationList:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )

    gens = evolution_service.list_generations(db, run_id)
    return schemas.GenerationList(
        items=[schemas.GenerationPublic.model_validate(g) for g in gens],
        total=len(gens),
    )


@router.get(
    "/evolution-runs/{run_id}/genomes/{genome_id}",
    response_model=schemas.StrategyGenomePublic,
)
def get_genome_detail(
    run_id: str,
    genome_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.StrategyGenomePublic:
    detail = evolution_service.get_genome_with_lineage(db, run_id, genome_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Genome not found"
        )
    # The service returns a dict with mixed plain values + ORM ValidationFold
    # rows. Pydantic's `from_attributes=True` (BaseSchema) lets us serialize
    # the folds directly.
    return schemas.StrategyGenomePublic.model_validate(detail)


@router.get(
    "/evolution-runs/{run_id}/decisions", response_model=schemas.DecisionLogList
)
def list_run_decisions(
    run_id: str,
    page: int = 1,
    page_size: int = 50,
    agent_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.DecisionLogList:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )

    skip = (page - 1) * page_size
    items, total = evolution_service.list_decisions(
        db, run_id, skip=skip, limit=page_size, agent_type=agent_type
    )
    return schemas.DecisionLogList(
        items=[schemas.DecisionLogPublic.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# Used internally by the worker to derive a default data window when the
# request body doesn't specify one. Exposed as a helper so tests can
# mock the clock easily.
def default_data_window_for(years: int) -> tuple[datetime, datetime]:
    from datetime import UTC

    end = datetime.now(UTC)
    start = end - timedelta(days=years * 365)
    return start, end
