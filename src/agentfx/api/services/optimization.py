"""
Optimization service for managing Bayesian parameter optimization jobs.

Provides CRUD operations for optimization jobs, trials, signals, and trades.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import models, schemas


def generate_job_id() -> str:
    """Generate a unique job ID."""
    timestamp = int(datetime.utcnow().timestamp())
    short_uuid = uuid.uuid4().hex[:6]
    return f"optjob_{timestamp}_{short_uuid}"


def generate_trial_id(job_id: str, index: int) -> str:
    """Generate a unique trial ID."""
    return f"trial_{job_id}_{index:03d}"


def generate_trade_id(trial_id: str, index: int) -> str:
    """Generate a unique trade ID."""
    return f"trade_{trial_id}_{index:03d}"


# =============================================================================
# Job Operations
# =============================================================================


def get_job(db: Session, job_id: str) -> models.OptimizationJob | None:
    """Get a single optimization job by ID."""
    return (
        db.query(models.OptimizationJob)
        .filter(models.OptimizationJob.id == job_id)
        .first()
    )


def get_jobs(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    strategy: str | None = None,
    pair: str | None = None,
    status: str | None = None,
    created_by: str | None = None,
) -> tuple[list[models.OptimizationJob], int]:
    """Get optimization jobs with optional filtering. Returns (items, total)."""
    query = db.query(models.OptimizationJob)

    if strategy:
        query = query.filter(models.OptimizationJob.strategy == strategy)
    if pair:
        query = query.filter(models.OptimizationJob.pair == pair.upper())
    if status:
        query = query.filter(models.OptimizationJob.status == status)
    if created_by:
        query = query.filter(models.OptimizationJob.created_by == created_by)

    total = query.count()
    items = (
        query.order_by(desc(models.OptimizationJob.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return items, total


def create_job(
    db: Session,
    job_in: schemas.OptimizationJobCreate,
    created_by: str | None = None,
) -> models.OptimizationJob:
    """Create a new optimization job."""
    # Estimate iterations based on parameter dimensions
    param_count = len(job_in.param_ranges)
    estimated_iterations = 10 + (param_count * 15)  # 10 random + 15 per dimension

    db_job = models.OptimizationJob(
        id=generate_job_id(),
        strategy=job_in.strategy,
        pair=job_in.pair.upper(),
        status=models.JobStatus.QUEUED,
        progress=0,
        estimated_iterations=estimated_iterations,
        completed_iterations=0,
        account_balance=job_in.account_balance,
        position_size=job_in.position_size,
        data_years=job_in.data_years,
        timeframe=job_in.timeframe,
        param_ranges={
            k: {"min": v.min, "max": v.max, "step": v.step}
            for k, v in job_in.param_ranges.items()
        },
        created_by=created_by,
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job


def update_job(
    db: Session,
    db_job: models.OptimizationJob,
    **updates: Any,
) -> models.OptimizationJob:
    """Update an optimization job."""
    for field, value in updates.items():
        if hasattr(db_job, field):
            setattr(db_job, field, value)

    db_job.updated_at = datetime.utcnow()
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job


def start_job(db: Session, db_job: models.OptimizationJob) -> models.OptimizationJob:
    """Mark a job as running."""
    return update_job(
        db,
        db_job,
        status=models.JobStatus.RUNNING,
        started_at=datetime.utcnow(),
    )


def complete_job(
    db: Session,
    db_job: models.OptimizationJob,
    best_sharpe: float | None = None,
    best_trial_id: str | None = None,
    data_range_start=None,
    data_range_end=None,
) -> models.OptimizationJob:
    """Mark a job as completed."""
    updates = dict(
        status=models.JobStatus.COMPLETED,
        completed_at=datetime.utcnow(),
        progress=100,
        best_sharpe=best_sharpe,
        best_trial_id=best_trial_id,
    )
    if data_range_start is not None:
        updates["data_range_start"] = data_range_start
    if data_range_end is not None:
        updates["data_range_end"] = data_range_end
    return update_job(db, db_job, **updates)


def fail_job(
    db: Session,
    db_job: models.OptimizationJob,
    error_message: str,
) -> models.OptimizationJob:
    """Mark a job as failed."""
    return update_job(
        db,
        db_job,
        status=models.JobStatus.FAILED,
        completed_at=datetime.utcnow(),
        error_message=error_message,
    )


def delete_job(db: Session, job_id: str) -> bool:
    """Delete an optimization job and all related data."""
    db_job = get_job(db, job_id=job_id)
    if not db_job:
        return False

    db.delete(db_job)
    db.commit()
    return True


# =============================================================================
# Trial Operations
# =============================================================================


def get_trial(db: Session, trial_id: str) -> models.OptimizationTrial | None:
    """Get a single trial by ID."""
    return (
        db.query(models.OptimizationTrial)
        .filter(models.OptimizationTrial.id == trial_id)
        .first()
    )


def get_trials(
    db: Session,
    job_id: str,
    skip: int = 0,
    limit: int = 100,
    passes_gate_b: bool | None = None,
) -> tuple[list[models.OptimizationTrial], int]:
    """Get trials for a job. Returns (items, total)."""
    query = db.query(models.OptimizationTrial).filter(
        models.OptimizationTrial.job_id == job_id
    )

    if passes_gate_b is not None:
        query = query.filter(models.OptimizationTrial.passes_gate_b == passes_gate_b)

    total = query.count()
    items = (
        query.order_by(desc(models.OptimizationTrial.sharpe))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return items, total


def get_top_trials(
    db: Session,
    job_id: str,
    limit: int = 3,
) -> list[models.OptimizationTrial]:
    """Get top N trials by Sharpe ratio for a job."""
    return (
        db.query(models.OptimizationTrial)
        .filter(models.OptimizationTrial.job_id == job_id)
        .order_by(desc(models.OptimizationTrial.sharpe))
        .limit(limit)
        .all()
    )


def create_trial(
    db: Session,
    job_id: str,
    trial_index: int,
    params: dict[str, float],
    metrics: schemas.TrialMetrics,
    extended_metrics: schemas.TrialMetricsExtended | None = None,
) -> models.OptimizationTrial:
    """Create a new trial result."""
    # Evaluate Gate B
    passes, failures = schemas.evaluate_gate_b(metrics)

    db_trial = models.OptimizationTrial(
        id=generate_trial_id(job_id, trial_index),
        job_id=job_id,
        params=params,
        sharpe=metrics.sharpe,
        max_drawdown=metrics.max_drawdown,
        hit_rate=metrics.hit_rate,
        trade_count=metrics.trade_count,
        total_return=metrics.total_return,
        profit_factor=metrics.profit_factor,
        avg_win=metrics.avg_win,
        avg_loss=metrics.avg_loss,
        max_consecutive_losses=metrics.max_consecutive_losses,
        avg_trade_duration=metrics.avg_trade_duration,
        passes_gate_b=passes,
        gate_b_failures=failures if failures else None,
    )

    # Add extended metrics if provided
    if extended_metrics:
        db_trial.total_pips = extended_metrics.total_pips
        db_trial.avg_pips_per_trade = extended_metrics.avg_pips_per_trade
        db_trial.total_spread_cost_pips = extended_metrics.total_spread_cost_pips
        db_trial.total_swap_cost_pips = extended_metrics.total_swap_cost_pips
        db_trial.net_pips = extended_metrics.net_pips
        db_trial.gross_pnl_dollars = extended_metrics.gross_pnl_dollars
        db_trial.total_spread_cost_dollars = extended_metrics.total_spread_cost_dollars
        db_trial.total_swap_cost_dollars = extended_metrics.total_swap_cost_dollars
        db_trial.net_pnl_dollars = extended_metrics.net_pnl_dollars

    db.add(db_trial)
    db.commit()
    db.refresh(db_trial)

    # Update job progress
    job = get_job(db, job_id)
    if job:
        completed = job.completed_iterations + 1
        progress = min(100, int((completed / job.estimated_iterations) * 100))
        update_job(db, job, completed_iterations=completed, progress=progress)

    return db_trial


# =============================================================================
# Signal Operations
# =============================================================================


def get_signals(
    db: Session,
    trial_id: str,
    skip: int = 0,
    limit: int = 1000,
) -> tuple[list[models.OptimizationSignal], int]:
    """Get trade signals for a trial. Returns (items, total)."""
    query = db.query(models.OptimizationSignal).filter(
        models.OptimizationSignal.trial_id == trial_id
    )

    total = query.count()
    items = (
        query.order_by(models.OptimizationSignal.timestamp)
        .offset(skip)
        .limit(limit)
        .all()
    )

    return items, total


def create_signal(
    db: Session,
    trial_id: str,
    timestamp: datetime,
    signal: int,
    signal_type: str,
    price: float,
    pnl: float | None = None,
    indicator_values: dict[str, float] | None = None,
) -> models.OptimizationSignal:
    """Create a new trade signal."""
    db_signal = models.OptimizationSignal(
        trial_id=trial_id,
        timestamp=timestamp,
        signal=signal,
        signal_type=signal_type,
        price=price,
        pnl=pnl,
        indicator_values=indicator_values,
    )
    db.add(db_signal)
    db.commit()
    db.refresh(db_signal)
    return db_signal


def bulk_create_signals(
    db: Session,
    trial_id: str,
    signals: list[dict[str, Any]],
) -> int:
    """Bulk create trade signals. Returns count created."""
    db_signals = [
        models.OptimizationSignal(
            trial_id=trial_id,
            timestamp=s["timestamp"],
            signal=s["signal"],
            signal_type=s["signal_type"],
            price=s["price"],
            pnl=s.get("pnl"),
            indicator_values=s.get("indicator_values"),
        )
        for s in signals
    ]
    db.bulk_save_objects(db_signals)
    db.commit()
    return len(db_signals)


# =============================================================================
# Trade Operations
# =============================================================================


def get_trades(
    db: Session,
    trial_id: str,
    skip: int = 0,
    limit: int = 1000,
) -> tuple[list[models.OptimizationTrade], int]:
    """Get trades for a trial. Returns (items, total)."""
    query = db.query(models.OptimizationTrade).filter(
        models.OptimizationTrade.trial_id == trial_id
    )

    total = query.count()
    items = (
        query.order_by(models.OptimizationTrade.entry_timestamp)
        .offset(skip)
        .limit(limit)
        .all()
    )

    return items, total


def create_trade(
    db: Session,
    trial_id: str,
    trade_index: int,
    direction: str,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    entry_price: float,
    exit_price: float,
    pips: float,
    spread_cost_pips: float,
    swap_cost_pips: float,
    net_pips: float,
    gross_pnl_dollars: float,
    spread_cost_dollars: float,
    swap_cost_dollars: float,
    net_pnl_dollars: float,
    duration_minutes: int,
    position_size: int,
) -> models.OptimizationTrade:
    """Create a new trade record."""
    db_trade = models.OptimizationTrade(
        id=generate_trade_id(trial_id, trade_index),
        trial_id=trial_id,
        direction=direction,
        entry_timestamp=entry_timestamp,
        exit_timestamp=exit_timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        pips=pips,
        spread_cost_pips=spread_cost_pips,
        swap_cost_pips=swap_cost_pips,
        net_pips=net_pips,
        gross_pnl_dollars=gross_pnl_dollars,
        spread_cost_dollars=spread_cost_dollars,
        swap_cost_dollars=swap_cost_dollars,
        net_pnl_dollars=net_pnl_dollars,
        duration_minutes=duration_minutes,
        position_size=position_size,
    )
    db.add(db_trade)
    db.commit()
    db.refresh(db_trade)
    return db_trade


def bulk_create_trades(
    db: Session,
    trial_id: str,
    trades: list[dict[str, Any]],
) -> int:
    """Bulk create trades. Returns count created."""
    db_trades = []
    for i, trade in enumerate(trades):
        # Remove 'id' if present to avoid conflict with generated ID
        # The backtest service includes an 'id' field that would conflict
        trade_data = {k: v for k, v in trade.items() if k != "id"}
        db_trades.append(
            models.OptimizationTrade(
                id=generate_trade_id(trial_id, i),
                trial_id=trial_id,
                **trade_data,
            )
        )
    db.bulk_save_objects(db_trades)
    db.commit()
    return len(db_trades)
