"""Background workers for AgentFX API."""

from .candle_ingestion import start_ingestion_worker, stop_ingestion_worker
from .evolution_runner import (
    recover_queued_evolution_runs,
    schedule_run as schedule_evolution_run,
    signal_run_stop,
)
from .optimization_runner import recover_queued_jobs, run_optimization

__all__ = [
    "start_ingestion_worker",
    "stop_ingestion_worker",
    "run_optimization",
    "recover_queued_jobs",
    "recover_queued_evolution_runs",
    "schedule_evolution_run",
    "signal_run_stop",
]
