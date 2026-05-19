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
import threading
from datetime import UTC, datetime, timedelta

from agentfx.agents.evolution import ResourceLimits, apply_resource_limits

logger = logging.getLogger(__name__)


# Use spawn rather than fork on darwin (macOS) and Linux — fork inherits
# the FastAPI event loop + threads, which causes hangs and exhausted DB
# connections in the child. Spawn gives a clean Python process.
_MP_CONTEXT = multiprocessing.get_context("spawn")

# How often the parent-side watchdog checks subprocess liveness. Cheap
# (a single Process.is_alive() call), so polling every few seconds is fine.
_WATCHDOG_POLL_SECONDS = 5.0


# ─────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────


def schedule_run(run_id: str) -> None:
    """Fork a child process to run the evolution loop for `run_id`.

    Called from a FastAPI BackgroundTask. Returns immediately after
    starting the child; child completion is observed via the run row's
    status (the child writes its own status updates).

    A daemon watchdog thread polls `proc.is_alive()` every
    `_WATCHDOG_POLL_SECONDS`. When the child exits, the watchdog reaps
    it (preventing zombie accumulation) and — if the run row is still
    marked RUNNING — flips it to FAILED with `stop_reason='process_crashed'`.
    The watchdog is the safety net for crashes where the child can't
    update its own DB row (SIGKILL, segfault, hard OOM, anthropic SDK
    deadlock followed by external kill, etc.).
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
    _start_watchdog(run_id, proc)


def _start_watchdog(run_id: str, proc: multiprocessing.Process) -> None:
    """Start a daemon thread that reaps the child and marks the run
    FAILED if the child exits while the DB still says RUNNING."""

    def _watch() -> None:
        # Block until the child exits — this also reaps it (no zombie).
        proc.join()
        exit_code = proc.exitcode
        logger.info(
            "watchdog: evolution run %s child exited (pid=%s exit_code=%s)",
            run_id,
            proc.pid,
            exit_code,
        )

        if exit_code == 0:
            # Clean exit: the orchestrator marked the run COMPLETED itself.
            return

        # Non-zero exit: only mark FAILED if the orchestrator didn't
        # already record its own status (FAILED via internal handler, or
        # STOPPED via explicit stop). Idempotent — never overwrites a
        # terminal state.
        try:
            from agentfx.api.core.database import SessionLocal
            from agentfx.api.models import EvolutionRunStatus
            from agentfx.api.services import evolution as evolution_service

            db = SessionLocal()
            try:
                run = evolution_service.get_run(db, run_id)
                if run is None:
                    logger.warning(
                        "watchdog: run %s vanished from DB before update", run_id
                    )
                    return
                if run.status == EvolutionRunStatus.RUNNING:
                    run.status = EvolutionRunStatus.FAILED
                    run.stop_reason = f"process_crashed (exit_code={exit_code})"
                    run.completed_at = datetime.now(UTC)
                    db.commit()
                    logger.error(
                        "watchdog: marked run %s FAILED (child exit_code=%s)",
                        run_id,
                        exit_code,
                    )
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.exception("watchdog: failed to mark run %s as FAILED", run_id)

    t = threading.Thread(target=_watch, name=f"evorun-watchdog-{run_id}", daemon=True)
    t.start()


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


def _clamp_window_to_data(
    db,
    *,
    instrument: str,
    granularity: str,
    requested_window: tuple[datetime, datetime],
) -> tuple[datetime, datetime] | None:
    """Clamp the requested data window to what's actually in the DB.

    The GA + validator run backtests across this window. If the requested
    start predates the oldest candle (e.g. a 5-year request against a DB
    that's only been backfilling for a week), the walk-forward validator's
    earliest fold gets zero data and crashes. Clamping makes the run
    adapt to data availability instead of demanding more than exists.

    Returns None if no candles exist for the instrument:granularity at
    all — the caller should fail-fast rather than proceed with an empty
    window.
    """
    from agentfx.api.models import Candle

    oldest = (
        db.query(Candle.timestamp)
        .filter(Candle.instrument == instrument, Candle.timeframe == granularity)
        .order_by(Candle.timestamp.asc())
        .first()
    )
    newest = (
        db.query(Candle.timestamp)
        .filter(Candle.instrument == instrument, Candle.timeframe == granularity)
        .order_by(Candle.timestamp.desc())
        .first()
    )
    if oldest is None or newest is None:
        return None

    oldest_ts = oldest[0]
    newest_ts = newest[0]
    if oldest_ts.tzinfo is None:
        oldest_ts = oldest_ts.replace(tzinfo=UTC)
    if newest_ts.tzinfo is None:
        newest_ts = newest_ts.replace(tzinfo=UTC)

    clamped_start = max(requested_window[0], oldest_ts)
    clamped_end = min(requested_window[1], newest_ts)
    return (clamped_start, clamped_end)


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
    # Configure logging with force=True so it overrides any inherited
    # root handlers from the parent process's spawn context, AND with an
    # explicit StreamHandler pointed at stderr so log records flow into
    # docker's captured stdout/stderr stream. Unbuffer stdout/stderr so
    # log lines appear in real time, not after process exit.
    import sys

    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [child-%(process)d] %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
        force=True,
    )
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
        requested_window = (
            datetime.now(UTC) - timedelta(days=data_years * 365),
            datetime.now(UTC),
        )
        # Clamp the requested window to what's actually in the DB. The
        # validator runs K=4 walk-forward folds across this window — if
        # the oldest fold spans dates with no candles, the per-fold
        # backtest raises FileNotFoundError and crashes the run. Better
        # to run validation on a smaller-but-real window than to fail on
        # data scarcity. Also clamp the end so we don't validate against
        # the still-incomplete trailing candle.
        data_window = _clamp_window_to_data(
            db,
            instrument=run.instrument,
            granularity=run.granularity,
            requested_window=requested_window,
        )
        if data_window is None:
            raise RuntimeError(
                f"No candle data available for {run.instrument}:{run.granularity}; "
                f"cannot proceed with evolution run. Backfill the instrument first."
            )
        logger.info(
            "data window for run %s: requested=%s..%s clamped=%s..%s",
            run_id,
            requested_window[0].isoformat(),
            requested_window[1].isoformat(),
            data_window[0].isoformat(),
            data_window[1].isoformat(),
        )

        llm_budget_usd = float(config_dict.get("llm_budget_usd", 5.0))

        # LLM injector — only constructed if ANTHROPIC_API_KEY is set.
        # Without a key, runs proceed without LLM injection (the
        # orchestrator handles this gracefully). EVOLUTION_LLM_MODEL lets
        # deployments override the default model (e.g. haiku for cheap
        # diversity-injection variants) without API callers having to know.
        llm_injector: LLMHypothesisInjector | None = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_model = os.environ.get("EVOLUTION_LLM_MODEL", "claude-sonnet-4-6")
            llm_injector = LLMHypothesisInjector(
                model=llm_model, budget_usd=llm_budget_usd
            )
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
        # reality even without parent-side monitoring. The original session
        # may be in a failed-transaction state (e.g. IntegrityError leaves
        # the session unusable), so roll back first AND fall back to a
        # fresh session if the existing one is unrecoverable. The parent
        # watchdog catches non-zero exits regardless, but this internal
        # handler gives faster visibility in the DB while the parent
        # watchdog is still waiting on proc.join().
        try:
            db.rollback()
            run = evolution_service.get_run(db, run_id)
            if run is not None:
                from agentfx.api.models import EvolutionRunStatus

                run.status = EvolutionRunStatus.FAILED
                run.stop_reason = "process_crashed"
                run.completed_at = datetime.now(UTC)
                db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to record run failure in DB via primary session; "
                "watchdog will catch this via process exit code"
            )
        raise
    finally:
        db.close()


__all__ = [
    "recover_queued_evolution_runs",
    "schedule_run",
    "signal_run_stop",
]
