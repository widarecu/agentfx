"""
Background worker for executing optimization jobs.

This module provides the async task that runs Bayesian optimization
in a background thread, persisting trial results to the database.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from agentfx.agents.optimizer import BayesianOptimizerAgent

from ..core.database import SessionLocal
from ..models.optimization import JobStatus
from ..schemas.optimization import TrialMetrics, TrialMetricsExtended
from ..services import optimization as optimization_service

logger = logging.getLogger(__name__)

# Thread pool for running CPU-intensive optimization
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="optimizer")

# Track running jobs to prevent duplicate execution
_running_jobs: set[str] = set()


async def recover_queued_jobs() -> int:
    """
    Recover and process any jobs left in QUEUED or RUNNING status.

    Called on server startup to handle:
    - Jobs that were queued but never started (e.g., created before worker was implemented)
    - Jobs that were running when the server crashed/restarted

    Returns:
        Number of jobs scheduled for recovery
    """
    db: Session = SessionLocal()
    recovered_count = 0

    try:
        # Find all jobs in QUEUED status
        queued_jobs, _ = optimization_service.get_jobs(
            db, skip=0, limit=100, status=JobStatus.QUEUED.value
        )

        # Find jobs stuck in RUNNING status (server crashed mid-execution)
        running_jobs, _ = optimization_service.get_jobs(
            db, skip=0, limit=100, status=JobStatus.RUNNING.value
        )

        # Reset RUNNING jobs back to QUEUED so they can be reprocessed
        for job in running_jobs:
            logger.warning(
                f"Found orphaned RUNNING job {job.id}, resetting to QUEUED for recovery"
            )
            optimization_service.update_job(
                db, job, status=JobStatus.QUEUED, progress=0, started_at=None
            )
            queued_jobs.append(job)

        if not queued_jobs:
            logger.info("No queued optimization jobs to recover")
            return 0

        logger.info(f"Recovering {len(queued_jobs)} queued optimization jobs")

        # Schedule each job for processing
        for job in queued_jobs:
            logger.info(f"Scheduling recovery for job {job.id}")
            asyncio.create_task(run_optimization(job.id))
            recovered_count += 1

        return recovered_count

    except Exception as e:
        logger.exception(f"Failed to recover queued jobs: {e}")
        return recovered_count
    finally:
        db.close()


async def run_optimization(job_id: str) -> None:
    """
    Async entry point for BackgroundTasks.

    Runs the optimization in a thread pool to avoid blocking the event loop.

    Args:
        job_id: The optimization job ID to execute
    """
    # Prevent duplicate execution
    if job_id in _running_jobs:
        logger.warning(f"Job {job_id} is already running, skipping duplicate execution")
        return

    _running_jobs.add(job_id)

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, _execute_optimization, job_id)
    finally:
        _running_jobs.discard(job_id)


def _execute_optimization(job_id: str) -> None:
    """
    Synchronous optimization execution in thread pool.

    Creates its own database session and manages the full job lifecycle:
    1. Mark job as RUNNING
    2. Execute Bayesian optimization iterations
    3. Persist trial results after each iteration
    4. Mark job as COMPLETED or FAILED

    Args:
        job_id: The optimization job ID to execute
    """
    db: Session = SessionLocal()

    try:
        # Get the job
        job = optimization_service.get_job(db, job_id=job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        # Check job is still queued (may have been cancelled)
        if job.status != JobStatus.QUEUED:
            logger.warning(f"Job {job_id} is not in QUEUED status, skipping")
            return

        # Mark job as running
        optimization_service.start_job(db, job)
        logger.info(f"Started optimization job {job_id}")

        # Create the optimizer agent
        agent = BayesianOptimizerAgent()

        # Track trial index for progress callback
        trial_index = [0]  # Use list for mutable closure

        def progress_callback(current: int, total: int, trial_info: dict) -> None:
            """Callback to persist each trial result."""
            trial_index[0] = current

            # Convert agent trial output to service format
            _persist_trial_result(db, job_id, current, trial_info)

            # Update job progress
            progress = int((current / total) * 100)
            optimization_service.update_job(
                db,
                job,
                progress=min(progress, 99),  # Keep at 99 until fully complete
            )
            logger.debug(f"Job {job_id} progress: {progress}% ({current}/{total})")

        # Run the optimization
        result = agent.run(
            strategy=job.strategy.value,
            pair=job.pair,
            param_ranges=job.param_ranges,
            data_years=job.data_years,
            account_balance=job.account_balance,
            position_size=job.position_size,
            max_iterations=job.estimated_iterations,
            capture_signals=True,
            progress_callback=progress_callback,
        )

        # Get best result
        best_trial = result.outputs.get("best_trial") if result.outputs else None
        best_sharpe = best_trial.get("sharpe") if best_trial else None

        # Find best trial ID from database
        best_trial_id = None
        if best_sharpe is not None:
            top_trials = optimization_service.get_top_trials(db, job_id=job_id, limit=1)
            if top_trials:
                best_trial_id = top_trials[0].id

        # Mark job as completed
        optimization_service.complete_job(
            db,
            job,
            best_sharpe=best_sharpe,
            best_trial_id=best_trial_id,
        )
        logger.info(
            f"Completed optimization job {job_id} with best Sharpe: {best_sharpe}"
        )

    except Exception as e:
        logger.exception(f"Optimization job {job_id} failed: {e}")
        try:
            # Re-fetch job in case session state is stale
            job = optimization_service.get_job(db, job_id=job_id)
            if job:
                optimization_service.fail_job(db, job, error_message=str(e))
        except Exception as fail_error:
            logger.error(f"Failed to mark job {job_id} as failed: {fail_error}")
    finally:
        db.close()


def _persist_trial_result(
    db: Session, job_id: str, trial_index: int, trial_info: dict
) -> None:
    """
    Persist a single trial result to the database.

    Args:
        db: Database session
        job_id: The parent job ID
        trial_index: The sequential trial number
        trial_info: Dictionary containing trial metrics and parameters from agent
    """
    try:
        # Extract metrics from agent's trial output
        metrics_dict = trial_info.get("metrics", {})

        # Build TrialMetrics schema
        metrics = TrialMetrics(
            sharpe=metrics_dict.get("sharpe", 0.0),
            max_drawdown=metrics_dict.get("max_drawdown", 0.0),
            hit_rate=metrics_dict.get("hit_rate", 0.0),
            trade_count=metrics_dict.get("n_trades", 0),
            total_return=metrics_dict.get("total_return", 0.0),
            profit_factor=metrics_dict.get("profit_factor", 0.0),
            avg_win=metrics_dict.get("avg_win"),
            avg_loss=metrics_dict.get("avg_loss"),
            max_consecutive_losses=metrics_dict.get("max_consecutive_losses"),
            avg_trade_duration=metrics_dict.get("avg_trade_duration"),
        )

        # Build extended metrics if available
        extended_metrics = None
        if any(
            k in metrics_dict for k in ["total_pips", "net_pips", "gross_pnl_dollars"]
        ):
            extended_metrics = TrialMetricsExtended(
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
                total_pips=metrics_dict.get("total_pips"),
                avg_pips_per_trade=metrics_dict.get("avg_pips_per_trade"),
                total_spread_cost_pips=metrics_dict.get("total_spread_cost_pips"),
                total_swap_cost_pips=metrics_dict.get("total_swap_cost_pips"),
                net_pips=metrics_dict.get("net_pips"),
                gross_pnl_dollars=metrics_dict.get("gross_pnl_dollars"),
                total_spread_cost_dollars=metrics_dict.get("total_spread_cost_dollars"),
                total_swap_cost_dollars=metrics_dict.get("total_swap_cost_dollars"),
                net_pnl_dollars=metrics_dict.get("net_pnl_dollars"),
            )

        # Create trial record
        trial = optimization_service.create_trial(
            db,
            job_id=job_id,
            trial_index=trial_index,
            params=trial_info.get("params", {}),
            metrics=metrics,
            extended_metrics=extended_metrics,
        )

        # Bulk insert signals if present
        signals = trial_info.get("signals", [])
        if signals:
            optimization_service.bulk_create_signals(db, trial.id, signals)

        # Bulk insert trades if present
        trades = trial_info.get("trades", [])
        if trades:
            optimization_service.bulk_create_trades(db, trial.id, trades)

        logger.debug(
            f"Persisted trial {trial_index} for job {job_id}: Sharpe={metrics.sharpe:.3f}"
        )

    except Exception as e:
        logger.error(f"Failed to persist trial {trial_index} for job {job_id}: {e}")
        # Don't re-raise - allow optimization to continue even if one trial fails to persist
