"""Parent-side worker: forks the orchestrator into a subprocess.

`schedule_run(run_id)` is what BackgroundTasks calls when a new evolution
run is created. It spawns a child process via `multiprocessing.Process`
that:

1. Opens its own DB session (the parent's can't cross the fork)
2. Applies `resource.setrlimit()` caps for RAM + CPU
3. Loads the run config
4. Builds the orchestrator with real persister + LLM injector + validator
5. Calls `orchestrator.run_loop()`
6. Exits

The parent records the child's PID on the run row so a later
`POST /evolution-runs/{id}/stop` can SIGTERM it. On non-zero child exit,
the parent marks the run FAILED with `stop_reason='process_crashed'`.

`recover_queued_evolution_runs()` is wired into the FastAPI lifespan and
fires at app startup. It re-schedules QUEUED runs and resets orphaned
RUNNING runs (their previous child died with the previous app instance).
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
from datetime import UTC, datetime, timedelta

from agentfx.agents.evolution import ResourceLimits, apply_resource_limits

logger = logging.getLogger(__name__)


# Use spawn rather than fork on darwin (macOS) and Linux — fork inherits
# the FastAPI event loop + threads, which causes hangs and exhausted DB
# connections in the child. Spawn gives a clean Python process.
_MP_CONTEXT = multiprocessing.get_context("spawn")


# ─────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────


def schedule_run(run_id: str) -> None:
    """Fork a child process to run the evolution loop for `run_id`.

    Called from a FastAPI BackgroundTask. Returns immediately after
    starting the child; child completion is observed via the run row's
    status (the child writes its own status updates).
    """
    proc = _MP_CONTEXT.Process(
        target=_subprocess_entry,
        args=(run_id,),
        name=f"evorun-{run_id}",
        daemon=False,
    )
    proc.start()
    logger.info("scheduled evolution run %s as pid=%s", run_id, proc.pid)
    _record_child_pid(run_id, proc.pid)


def signal_run_stop(pid: int) -> bool:
    """Send SIGTERM to a child process. Returns True if the signal was
    sent, False if the process couldn't be found (already exited).

    The orchestrator catches SIGTERM via Python's signal handler — we
    don't need a separate teardown protocol.
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("sent SIGTERM to pid=%s", pid)
        return True
    except ProcessLookupError:
        logger.info("pid=%s already exited; nothing to signal", pid)
        return False
    except PermissionError:
        logger.error("permission denied sending SIGTERM to pid=%s", pid)
        return False


def recover_queued_evolution_runs(db=None) -> int:
    """Lifespan startup hook.

    1. Finds runs in RUNNING state — these are orphaned (their child died
       with the previous app instance). Reset to QUEUED.
    2. Finds all QUEUED runs (including the ones we just reset) and
       schedules each via `schedule_run`.

    Returns the number of runs re-scheduled. The lifespan can log this.

    Args:
        db: Optional SQLAlchemy Session. Tests inject their own session;
            production passes None and we open one via SessionLocal.
    """
    # Lazy imports — the lifespan calls this *during* startup, before the
    # app is ready, but after settings/DB are initialized.
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import EvolutionRunStatus
    from agentfx.api.services import evolution as evolution_service

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        orphaned = evolution_service.find_orphaned_running_runs(db)
        for run in orphaned:
            run.status = EvolutionRunStatus.QUEUED
            run.process_pid = None
        if orphaned:
            db.commit()
            logger.warning(
                "reset %d orphaned RUNNING evolution runs to QUEUED",
                len(orphaned),
            )

        queued = evolution_service.find_queued_runs(db)
        for run in queued:
            schedule_run(run.id)

        return len(queued)
    finally:
        if own_session:
            db.close()


# ─────────────────────────────────────────────────────────────────────────
# Internal: parent-side bookkeeping
# ─────────────────────────────────────────────────────────────────────────


def _record_child_pid(run_id: str, pid: int | None) -> None:
    """Persist the child PID on the run row so /stop can find it."""
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.services import evolution as evolution_service

    if pid is None:
        return

    db = SessionLocal()
    try:
        run = evolution_service.get_run(db, run_id)
        if run is None:
            return
        run.process_pid = pid
        db.commit()
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────
# Internal: subprocess entry (runs in the child)
# ─────────────────────────────────────────────────────────────────────────


def _subprocess_entry(run_id: str) -> None:
    """The function the child process runs from start to exit.

    Steps:
    1. Configure logging (the spawn context loses the parent's logger)
    2. Apply resource limits (RAM + CPU caps)
    3. Open a fresh DB session
    4. Load the run config
    5. Build orchestrator dependencies (persister, validator, LLM injector)
    6. Run the loop
    7. Mark the run FAILED on any unhandled exception

    Any exception escaping this function ends the process with non-zero
    status; the parent's monitor sees that and marks the run failed via
    a separate path. We also write a status update from inside the
    handler so the failure is reflected in the DB even if the parent's
    monitor isn't running.
    """
    logging.basicConfig(level=logging.INFO)
    logger.info("evolution run %s child process starting (pid=%s)", run_id, os.getpid())

    try:
        apply_resource_limits(ResourceLimits())
    except Exception:  # noqa: BLE001
        # Don't let resource-limit setup failures kill the run — the
        # orchestrator's in-Python wall-clock check is still in place.
        logger.exception("apply_resource_limits failed; continuing without OS caps")

    from agentfx.agents.evolution.llm_client import LLMHypothesisInjector
    from agentfx.agents.orchestrator import (
        OrchestratorAgent,
        OrchestratorConfig,
        StopConditions,
    )
    from agentfx.agents.validator import ValidatorAgent
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.services import evolution as evolution_service
    from agentfx.api.services.evolution import SqlAlchemyPersister

    db = SessionLocal()
    try:
        run = evolution_service.get_run(db, run_id)
        if run is None:
            logger.error("run %s not found in DB; child exiting", run_id)
            return

        config_dict = run.config_json or {}
        stop_dict = run.stop_conditions_json or {}

        # Build typed configs from the persisted JSON. Unknown keys
        # are dropped (the dataclasses ignore them).
        config = OrchestratorConfig(
            instrument=run.instrument,
            granularity=run.granularity,
            population_size=int(config_dict.get("population_size", 30)),
            llm_injection_every_n=int(config_dict.get("llm_injection_every_n", 3)),
            n_llm_proposals=int(config_dict.get("n_llm_proposals", 5)),
            n_validation_folds=int(config_dict.get("n_validation_folds", 4)),
            top_n_to_validate=int(config_dict.get("top_n_to_validate", 5)),
            crossover_prob=float(config_dict.get("crossover_prob", 0.6)),
            mutation_prob=float(config_dict.get("mutation_prob", 0.3)),
            elite_fraction=float(config_dict.get("elite_fraction", 0.10)),
            tournament_size=int(config_dict.get("tournament_size", 3)),
            rng_seed=int(config_dict.get("rng_seed", 42)),
            account_balance=float(config_dict.get("account_balance", 10000.0)),
            position_size=int(config_dict.get("position_size", 10000)),
            spread_pips=config_dict.get("spread_pips"),
        )
        stop_conditions = StopConditions(
            max_generations=int(stop_dict.get("max_generations", 30)),
            max_wall_clock_seconds=int(
                stop_dict.get("max_wall_clock_seconds", 6 * 3600)
            ),
            target_fitness=float(stop_dict.get("target_fitness", 1.5)),
            no_improvement_generations=int(
                stop_dict.get("no_improvement_generations", 5)
            ),
        )

        data_years = int(config_dict.get("data_years", 5))
        data_window = (
            datetime.now(UTC) - timedelta(days=data_years * 365),
            datetime.now(UTC),
        )

        llm_budget_usd = float(config_dict.get("llm_budget_usd", 5.0))

        # LLM injector — only constructed if ANTHROPIC_API_KEY is set.
        # Without a key, runs proceed without LLM injection (the
        # orchestrator handles this gracefully).
        llm_injector: LLMHypothesisInjector | None = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_injector = LLMHypothesisInjector(budget_usd=llm_budget_usd)
        else:
            logger.warning(
                "ANTHROPIC_API_KEY not set; running without LLM hypothesis injection"
            )

        validator = ValidatorAgent(k=config.n_validation_folds)
        persister = SqlAlchemyPersister(db)

        orchestrator = OrchestratorAgent(
            run_id=run_id,
            config=config,
            stop_conditions=stop_conditions,
            data_window=data_window,
            persister=persister,
            llm_injector=llm_injector,
            validator=validator,
        )

        summary = orchestrator.run_loop()
        logger.info(
            "evolution run %s completed: stop_reason=%s, validated_count=%d",
            run_id,
            summary.stop_reason,
            summary.validated_count,
        )

    except Exception:  # noqa: BLE001
        logger.exception("evolution run %s crashed in child process", run_id)
        # Mark the run failed from inside the child so the DB reflects
        # reality even without parent-side monitoring.
        try:
            run = evolution_service.get_run(db, run_id)
            if run is not None:
                from agentfx.api.models import EvolutionRunStatus

                run.status = EvolutionRunStatus.FAILED
                run.stop_reason = "process_crashed"
                run.completed_at = datetime.now(UTC)
                db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("failed to record run failure in DB")
        raise
    finally:
        db.close()


__all__ = [
    "recover_queued_evolution_runs",
    "schedule_run",
    "signal_run_stop",
]
