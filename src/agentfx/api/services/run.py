from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import models, schemas


def get_run(db: Session, run_id: str) -> models.Run | None:
    """Get a single run by ID."""
    return db.query(models.Run).filter(models.Run.id == run_id).first()


def get_runs(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    strategy_id: str | None = None,
    pair: str | None = None,
    environment: str | None = None,
    status: str | None = None,
) -> list[models.Run]:
    """Get multiple runs with optional filtering."""
    query = db.query(models.Run)

    if strategy_id:
        query = query.filter(models.Run.strategy_id == strategy_id)
    if pair:
        query = query.filter(models.Run.pair == pair.upper())
    if environment:
        query = query.filter(models.Run.environment == environment)
    if status:
        query = query.filter(models.Run.status == status)

    return query.offset(skip).limit(limit).all()


def create_run(db: Session, run_in: schemas.RunCreate) -> models.Run:
    """Create a new run."""
    db_run = models.Run(
        **run_in.dict(),
        status=models.RunStatus.PENDING,
        current_balance=run_in.initial_balance,
        pnl=0.0,
    )
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run


def update_run(
    db: Session, db_run: models.Run, run_in: schemas.RunUpdate
) -> models.Run:
    """Update a run."""
    update_data = run_in.dict(exclude_unset=True)

    # Handle status changes
    if "status" in update_data:
        if update_data["status"] == models.RunStatus.RUNNING and not db_run.started_at:
            update_data["started_at"] = datetime.utcnow()
        elif update_data["status"] in [
            models.RunStatus.COMPLETED,
            models.RunStatus.ERROR,
        ]:
            update_data["completed_at"] = datetime.utcnow()

    for field, value in update_data.items():
        setattr(db_run, field, value)

    db_run.updated_at = datetime.utcnow()
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run


def delete_run(db: Session, run_id: str) -> bool:
    """Delete a run."""
    db_run = get_run(db, run_id=run_id)
    if not db_run:
        return False

    db.delete(db_run)
    db.commit()
    return True


def get_run_metrics(
    db: Session,
    run_id: str,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Get metrics for a specific run."""
    query = db.query(models.RunMetric).filter(models.RunMetric.run_id == run_id)

    if from_time:
        query = query.filter(models.RunMetric.timestamp >= from_time)
    if to_time:
        query = query.filter(models.RunMetric.timestamp <= to_time)

    return query.order_by(desc(models.RunMetric.timestamp)).limit(limit).all()


def get_trades(
    db: Session,
    run_id: str,
    skip: int = 0,
    limit: int = 100,
    side: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> list[models.Trade]:
    """Get trades for a specific run."""
    query = db.query(models.Trade).filter(models.Trade.run_id == run_id)

    if side:
        query = query.filter(models.Trade.side == side.lower())
    if from_time:
        query = query.filter(models.Trade.entry_timestamp >= from_time)
    if to_time:
        query = query.filter(models.Trade.entry_timestamp <= to_time)

    return query.offset(skip).limit(limit).all()


def get_orders(
    db: Session,
    run_id: str,
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    side: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> list[models.Order]:
    """Get orders for a specific run."""
    query = db.query(models.Order).filter(models.Order.run_id == run_id)

    if status:
        query = query.filter(models.Order.status == status.lower())
    if side:
        query = query.filter(models.Order.side == side.lower())
    if from_time:
        query = query.filter(models.Order.created_at >= from_time)
    if to_time:
        query = query.filter(models.Order.created_at <= to_time)

    return query.order_by(desc(models.Order.created_at)).offset(skip).limit(limit).all()


def get_run_summary(db: Session, run_id: str) -> dict[str, Any]:
    """Get a summary of a run's performance."""
    run = get_run(db, run_id=run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Get trade statistics
    trades = db.query(models.Trade).filter(models.Trade.run_id == run_id).all()
    winning_trades = [t for t in trades if t.pnl and t.pnl > 0]
    losing_trades = [t for t in trades if t.pnl and t.pnl <= 0]

    # Calculate metrics
    total_trades = len(trades)
    win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
    avg_win = (
        sum(t.pnl for t in winning_trades) / len(winning_trades)
        if winning_trades
        else 0
    )
    avg_loss = (
        sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
    )

    return {
        "run_id": run.id,
        "strategy_id": run.strategy_id,
        "pair": run.pair,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "initial_balance": run.initial_balance,
        "current_balance": run.current_balance,
        "pnl": run.pnl,
        "pnl_pct": (run.pnl / run.initial_balance) * 100 if run.initial_balance else 0,
        "sharpe_ratio": run.sharpe_ratio,
        "max_drawdown": run.max_drawdown,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": (
            (sum(t.pnl for t in winning_trades) / -sum(t.pnl for t in losing_trades))
            if losing_trades and sum(t.pnl for t in losing_trades) < 0
            else float("inf")
        ),
    }
