"""Paper trading router (US-021 / US-023).

Endpoints — all mounted under `/api/v1`:

- `GET  /paper-runs`                — user-scoped list; ADMIN scope=all
- `GET  /paper-runs/approval-queue` — pending validated genomes
- `POST /paper-runs/approve`        — TRADER+, deploy a genome
- `POST /paper-runs/reject`         — TRADER+, decline with reason
- `GET  /paper-runs/{id}`           — RBAC-gated detail
- `POST /paper-runs/{id}/kill`      — TRADER+, request halt
- `DELETE /paper-runs/{id}`         — TRADER+, terminal states only

Ownership scoping (US-018) is enforced via the shared `rbac` helpers
so a stray endpoint can't accidentally leak another user's rows.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core import rbac
from ..core.database import get_db
from ..services import paper as paper_service
from ..services import user as user_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _ensure_trader(current_user: models.User) -> None:
    if current_user.role not in (models.UserRole.TRADER, models.UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TRADER or ADMIN role required",
        )


def _to_public(run: models.Run) -> schemas.PaperRunPublic:
    """Serialise a Run row into the public view. Handles both enum and
    plain-string values so ORM refreshes don't fluctuate the shape."""
    return schemas.PaperRunPublic(
        id=run.id,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        strategy_genome_id=run.strategy_genome_id,
        instrument=run.pair,
        granularity=(run.parameters or {}).get("granularity")
        or (run.parameters or {}).get("timeframe")
        or "",
        oanda_account_id=run.oanda_account_id,
        oanda_environment=(
            run.oanda_environment.value
            if run.oanda_environment is not None
            and hasattr(run.oanda_environment, "value")
            else run.oanda_environment
        ),
        snapshot_balance=run.snapshot_balance,
        current_balance=run.current_balance,
        pnl=run.pnl,
        risk_config=run.risk_config,
        stop_reason=run.stop_reason,
        kill_requested_at=run.kill_requested_at,
        approved_at=run.approved_at,
        approved_by_user_id=run.approved_by_user_id,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        user_id=run.user_id,
    )


def _to_list_item(run: models.Run) -> schemas.PaperRunListItem:
    return schemas.PaperRunListItem(
        id=run.id,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        instrument=run.pair,
        granularity=(run.parameters or {}).get("granularity") or "",
        oanda_account_id=run.oanda_account_id,
        pnl=run.pnl,
        snapshot_balance=run.snapshot_balance,
        stop_reason=run.stop_reason,
        created_at=run.created_at,
    )


# ─────────────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────────────


@router.get("/paper-runs", response_model=schemas.PaperRunList)
def list_paper_runs(
    page: int = 1,
    page_size: int = 10,
    status_filter: str | None = None,
    instrument: str | None = None,
    scope: rbac.Scope = "my",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.PaperRunList:
    rbac.require_admin_for_scope_all(current_user, scope)
    created_by_filter = (
        None
        if current_user.role == models.UserRole.ADMIN and scope == "all"
        else current_user.id
    )
    skip = (page - 1) * page_size
    items, total = paper_service.list_paper_runs(
        db,
        skip=skip,
        limit=page_size,
        status=status_filter,
        instrument=instrument,
        created_by=created_by_filter,
    )
    return schemas.PaperRunList(
        items=[_to_list_item(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─────────────────────────────────────────────────────────────────────
# Approval queue + approve + reject
# ─────────────────────────────────────────────────────────────────────


@router.get(
    "/paper-runs/approval-queue",
    response_model=schemas.ApprovalQueueList,
)
def list_approval_queue(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.ApprovalQueueList:
    _ensure_trader(current_user)
    genomes = paper_service.list_approval_queue_for_user(db, current_user.id)
    items: list[schemas.ApprovalQueueItem] = []
    for g in genomes:
        # Pull evolution run + instrument info via the parent
        # generation → run chain. Kept explicit because the schemas
        # module doesn't own model relationships.
        gen = getattr(g, "generation", None)
        evorun = gen.run if gen is not None else None
        items.append(
            schemas.ApprovalQueueItem(
                strategy_genome_id=g.id,
                # In v0.6.0 the approval queue and paper run share an
                # id — before approve, this stays the same value the
                # UI shows on the /paper/approvals card.
                run_id=g.id,
                instrument=evorun.instrument if evorun is not None else "",
                granularity=evorun.granularity if evorun is not None else "",
                fitness=g.fitness,
                metrics_summary=g.metrics_json,
                created_at=g.created_at,
            )
        )
    return schemas.ApprovalQueueList(items=items, total=len(items))


@router.post(
    "/paper-runs/approve",
    response_model=schemas.PaperRunApproval,
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_paper_run(
    body: schemas.DeployPaperRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.PaperRunApproval:
    _ensure_trader(current_user)
    try:
        run = paper_service.approve_and_deploy(db, body, current_user=current_user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    # Schedule the subprocess — lazy import so tests that don't
    # exercise the runner don't have to import multiprocessing.
    from ..workers.paper_runner import schedule_paper_run

    background_tasks.add_task(schedule_paper_run, run.id)

    return schemas.PaperRunApproval(
        run_id=run.id,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        approval_status="APPROVED",
        status_url=f"/api/v1/paper-runs/{run.id}",
    )


@router.post("/paper-runs/reject")
def reject_paper_run(
    body: schemas.RejectGenomeRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> dict[str, bool | str]:
    _ensure_trader(current_user)
    try:
        paper_service.reject_genome(
            db,
            strategy_genome_id=body.strategy_genome_id,
            reason=body.reason,
            current_user=current_user,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return {"success": True, "message": "Genome rejected"}


# ─────────────────────────────────────────────────────────────────────
# Detail / kill / delete
# ─────────────────────────────────────────────────────────────────────


@router.get("/paper-runs/{run_id}", response_model=schemas.PaperRunPublic)
def get_paper_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.PaperRunPublic:
    run = paper_service.get_paper_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Paper run not found"
        )
    _require_paper_run_owner(run, current_user)
    return _to_public(run)


@router.post(
    "/paper-runs/{run_id}/kill",
    response_model=schemas.KillPaperRunResponse,
)
def kill_paper_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.KillPaperRunResponse:
    _ensure_trader(current_user)
    run = paper_service.get_paper_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Paper run not found"
        )
    _require_paper_run_owner(run, current_user)
    if run.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run is {run.status.value}; already terminal. "
                "Delete instead if you want it out of the list."
            ),
        )
    updated = paper_service.request_kill(db, run, current_user=current_user)
    return schemas.KillPaperRunResponse(
        run_id=updated.id,
        kill_requested_at=updated.kill_requested_at,
    )


@router.delete("/paper-runs/{run_id}")
def delete_paper_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> dict[str, bool | str]:
    _ensure_trader(current_user)
    run = paper_service.get_paper_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Paper run not found"
        )
    _require_paper_run_owner(run, current_user)
    if run.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Run is {run.status.value}; kill it first before deleting."),
        )
    paper_service.delete_paper_run(db, run)
    return {"success": True, "message": "Paper run deleted"}


# ─────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────


def _require_paper_run_owner(run: models.Run, current_user: models.User) -> None:
    """Same fail-closed semantics as `rbac.require_owner_or_admin` but
    reads `run.user_id` (paper runs) instead of `created_by`.
    """
    if current_user.role == models.UserRole.ADMIN:
        return
    if run.user_id is None or run.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this paper run",
        )


_TERMINAL_STATUSES = {
    models.RunStatus.COMPLETED,
    models.RunStatus.ERROR,
    models.RunStatus.HALTED_MANUAL,
}
