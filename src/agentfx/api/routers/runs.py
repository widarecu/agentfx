from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..services import run as run_service
from ..services import user as user_service

router = APIRouter()


@router.get("/", response_model=schemas.RunList)
def list_runs(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    strategy_id: str | None = None,
    pair: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Retrieve runs with optional filtering.
    """
    runs = run_service.get_runs(
        db,
        skip=skip,
        limit=limit,
        strategy_id=strategy_id,
        pair=pair,
        environment=environment,
        status=status,
    )
    total = db.query(models.Run).count()
    return {"items": runs, "total": total}


@router.post("/", response_model=schemas.Run, status_code=status.HTTP_201_CREATED)
def create_run(
    run_in: schemas.RunCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Create a new run.
    """
    return run_service.create_run(db, run_in=run_in)


@router.get("/{run_id}", response_model=schemas.Run)
def read_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get a specific run by ID.
    """
    db_run = run_service.get_run(db, run_id=run_id)
    if not db_run:
        raise HTTPException(status_code=404, detail="Run not found")
    return db_run


@router.put("/{run_id}", response_model=schemas.Run)
def update_run(
    run_id: str,
    run_in: schemas.RunUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Update a run.
    """
    db_run = run_service.get_run(db, run_id=run_id)
    if not db_run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run_service.update_run(db, db_run=db_run, run_in=run_in)


@router.delete("/{run_id}", response_model=dict)
def delete_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Delete a run.
    """
    if not run_service.delete_run(db, run_id=run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"success": True, "message": "Run deleted successfully"}


@router.get("/{run_id}/trades", response_model=list[schemas.Trade])
def list_run_trades(
    run_id: str,
    skip: int = 0,
    limit: int = 100,
    side: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List trades for a specific run.
    """
    return run_service.get_trades(
        db,
        run_id=run_id,
        skip=skip,
        limit=limit,
        side=side,
        from_time=from_time,
        to_time=to_time,
    )


@router.get("/{run_id}/orders", response_model=list[schemas.Order])
def list_run_orders(
    run_id: str,
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    side: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List orders for a specific run.
    """
    return run_service.get_orders(
        db,
        run_id=run_id,
        skip=skip,
        limit=limit,
        status=status,
        side=side,
        from_time=from_time,
        to_time=to_time,
    )


@router.get("/{run_id}/summary", response_model=dict)
def get_run_summary(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get a summary of a run's performance.
    """
    return run_service.get_run_summary(db, run_id=run_id)


@router.get("/{run_id}/metrics", response_model=list[dict])
def get_run_metrics(
    run_id: str,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = 1000,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get metrics for a specific run.
    """
    return run_service.get_run_metrics(
        db,
        run_id=run_id,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
    )
