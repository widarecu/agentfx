"""
Optimization API router for Bayesian parameter optimization.

Endpoints follow REST conventions per STANDARDS.md:
- Plural resource names (/optimization-jobs)
- Hierarchical sub-resources (/optimization-jobs/{id}/trials)
- 202 Accepted for async job creation
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..services import optimization as optimization_service
from ..services import user as user_service
from ..workers import run_optimization

router = APIRouter()


# =============================================================================
# Job Endpoints
# =============================================================================


@router.get("/optimization-jobs", response_model=schemas.OptimizationJobList)
def list_optimization_jobs(
    page: int = 1,
    page_size: int = 10,
    strategy: str | None = None,
    pair: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List all optimization jobs with optional filtering.
    Requires VIEWER role or higher.
    """
    skip = (page - 1) * page_size
    items, total = optimization_service.get_jobs(
        db,
        skip=skip,
        limit=page_size,
        strategy=strategy,
        pair=pair,
        status=status,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/optimization-jobs",
    response_model=schemas.OptimizationJobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_optimization_job(
    job_in: schemas.OptimizationJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Create a new optimization job.
    Returns 202 Accepted with job ID for async processing.
    Requires TRADER role or higher.
    """
    # Check user role (TRADER or ADMIN required)
    if current_user.role not in [models.UserRole.TRADER, models.UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TRADER or ADMIN role required to create optimization jobs",
        )

    # Create the job
    db_job = optimization_service.create_job(
        db,
        job_in=job_in,
        created_by=current_user.id,
    )

    # Schedule background optimization task
    background_tasks.add_task(run_optimization, db_job.id)

    return {
        "job_id": db_job.id,
        "status": db_job.status,
        "status_url": f"/api/v1/optimization-jobs/{db_job.id}",
        "estimated_iterations": db_job.estimated_iterations,
        "created_at": db_job.created_at,
    }


@router.get("/optimization-jobs/{job_id}", response_model=schemas.OptimizationJobPublic)
def get_optimization_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get a specific optimization job by ID.
    Requires VIEWER role or higher.
    """
    db_job = optimization_service.get_job(db, job_id=job_id)
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Optimization job not found",
        )
    return db_job


@router.delete("/optimization-jobs/{job_id}")
def delete_optimization_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Delete an optimization job and all related data.
    Users can delete their own jobs. ADMIN can delete any job.
    """
    db_job = optimization_service.get_job(db, job_id=job_id)
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Optimization job not found",
        )

    # Check authorization
    if (
        db_job.created_by != current_user.id
        and current_user.role != models.UserRole.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this job",
        )

    optimization_service.delete_job(db, job_id=job_id)
    return {"success": True, "message": "Job deleted successfully"}


# =============================================================================
# Trial Endpoints
# =============================================================================


@router.get(
    "/optimization-jobs/{job_id}/trials",
    response_model=schemas.OptimizationTrialList,
)
def list_job_trials(
    job_id: str,
    skip: int = 0,
    limit: int = 100,
    passes_gate_b: bool | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List all trials for an optimization job.
    Ordered by Sharpe ratio (descending).
    Requires VIEWER role or higher.
    """
    # Verify job exists
    db_job = optimization_service.get_job(db, job_id=job_id)
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Optimization job not found",
        )

    items, total = optimization_service.get_trials(
        db,
        job_id=job_id,
        skip=skip,
        limit=limit,
        passes_gate_b=passes_gate_b,
    )
    return {
        "items": items,
        "total": total,
        "job_id": job_id,
    }


@router.get(
    "/optimization-jobs/{job_id}/trials/top",
    response_model=list[schemas.OptimizationTrialPublic],
)
def get_top_trials(
    job_id: str,
    limit: int = 3,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get top N trials by Sharpe ratio for a job.
    Default returns top 3.
    Requires VIEWER role or higher.
    """
    # Verify job exists
    db_job = optimization_service.get_job(db, job_id=job_id)
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Optimization job not found",
        )

    return optimization_service.get_top_trials(db, job_id=job_id, limit=limit)


@router.get(
    "/optimization-jobs/{job_id}/trials/{trial_id}",
    response_model=schemas.OptimizationTrialPublic,
)
def get_trial(
    job_id: str,
    trial_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get a specific trial by ID.
    Requires VIEWER role or higher.
    """
    db_trial = optimization_service.get_trial(db, trial_id=trial_id)
    if not db_trial or db_trial.job_id != job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trial not found",
        )
    return db_trial


# =============================================================================
# Signal Endpoints (for chart visualization)
# =============================================================================


@router.get(
    "/optimization-jobs/{job_id}/trials/{trial_id}/signals",
    response_model=schemas.TradeSignalList,
)
def list_trial_signals(
    job_id: str,
    trial_id: str,
    skip: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get trade signals for a trial (for chart visualization).
    Requires VIEWER role or higher.
    """
    # Verify trial exists and belongs to job
    db_trial = optimization_service.get_trial(db, trial_id=trial_id)
    if not db_trial or db_trial.job_id != job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trial not found",
        )

    items, total = optimization_service.get_signals(
        db,
        trial_id=trial_id,
        skip=skip,
        limit=limit,
    )
    return {
        "items": items,
        "total": total,
        "trial_id": trial_id,
    }


# =============================================================================
# Trade Endpoints
# =============================================================================


@router.get(
    "/optimization-jobs/{job_id}/trials/{trial_id}/trades",
    response_model=schemas.OptimizationTradeList,
)
def list_trial_trades(
    job_id: str,
    trial_id: str,
    skip: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get individual trades for a trial with financing breakdown.
    Requires VIEWER role or higher.
    """
    # Verify trial exists and belongs to job
    db_trial = optimization_service.get_trial(db, trial_id=trial_id)
    if not db_trial or db_trial.job_id != job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trial not found",
        )

    items, total = optimization_service.get_trades(
        db,
        trial_id=trial_id,
        skip=skip,
        limit=limit,
    )
    return {
        "items": items,
        "total": total,
        "trial_id": trial_id,
    }
