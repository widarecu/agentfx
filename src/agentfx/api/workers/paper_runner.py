"""Parent-side worker for paper trading runs (US-021).

Mirrors the shape of `evolution_runner.py` — spawn a subprocess per
approved run, watch it for crashes, offer a `signal_run_stop` hook for
the router's kill switch. The v0.6.0 decision-loop body itself lives
in `agentfx.services.paper_runner`; this module is the parent-side
plumbing (spawn / watchdog / lifespan recovery).

Kill semantics: the router doesn't SIGTERM the child directly. It sets
`kill_requested_at` on the run row and the child polls that flag every
loop tick. `signal_run_stop(pid)` is kept as an escape hatch for the
watchdog (crash recovery) and for the admin CLI, but the happy path
uses cooperative shutdown via the DB flag so the child can flatten its
OANDA positions before exiting.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import threading
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Match evolution_runner: spawn over fork on macOS/Linux to avoid
# inheriting the FastAPI event loop or DB pool into the child.
_MP_CONTEXT = multiprocessing.get_context("spawn")

_WATCHDOG_POLL_SECONDS = 5.0


# ─────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────


def schedule_paper_run(run_id: str) -> None:
    """Spawn a subprocess to run the paper trading loop for `run_id`.

    Called from a FastAPI BackgroundTask right after the approve
    endpoint persists the row. Non-blocking; the child updates its own
    status.
    """
    proc = _MP_CONTEXT.Process(
        target=_subprocess_entry,
        args=(run_id,),
        name=f"paperrun-{run_id}",
        daemon=False,
    )
    proc.start()
    logger.info("scheduled paper run %s as pid=%s", run_id, proc.pid)
    _record_child_pid(run_id, proc.pid)
    _start_watchdog(run_id, proc)


def signal_run_stop(pid: int | None) -> bool:
    """Send SIGTERM to the runner subprocess. Returns True if delivered.

    Escape hatch. The normal kill flow is cooperative — the router
    sets `kill_requested_at` and the child polls it. This is here for
    the watchdog to reap orphans and for admin CLI use.
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("sent SIGTERM to paper-runner pid=%s", pid)
        return True
    except ProcessLookupError:
        logger.info("paper-runner pid=%s already exited", pid)
        return False
    except PermissionError:
        logger.error("permission denied sending SIGTERM to paper-runner pid=%s", pid)
        return False


def recover_orphaned_paper_runs(db=None) -> int:
    """Lifespan startup hook.

    Two responsibilities:
    1. Paper runs left in RUNNING when the app crashed — their
       subprocess died. Flip to APPROVED so the scheduler can re-pick
       them up.
    2. Paper runs in APPROVED that never got scheduled (crash between
       DB commit and BackgroundTask fire) — schedule them.

    Returns the number of runs (re-)scheduled.
    """
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import RunStatus
    from agentfx.api.services import paper as paper_service

    own_session = db is None
    if own_session:
        db = SessionLocal()

    scheduled = 0
    try:
        orphaned = paper_service.find_orphaned_running_paper_runs(db)
        for run in orphaned:
            run.status = RunStatus.APPROVED
            run.process_pid = None
        if orphaned:
            db.commit()
            logger.warning(
                "reset %d orphaned RUNNING paper runs to APPROVED",
                len(orphaned),
            )

        from agentfx.api.models import Run, RunEnvironment

        pending = (
            db.query(Run)
            .filter(
                Run.environment == RunEnvironment.PAPER,
                Run.status == RunStatus.APPROVED,
            )
            .all()
        )
        for run in pending:
            schedule_paper_run(run.id)
            scheduled += 1
    finally:
        if own_session:
            db.close()

    return scheduled


# ─────────────────────────────────────────────────────────────────────
# Watchdog + crash handling
# ─────────────────────────────────────────────────────────────────────


def _start_watchdog(run_id: str, proc: multiprocessing.Process) -> None:
    """Reap the child + mark the run ERROR if it exits non-zero while
    the DB still says RUNNING. Same pattern as evolution_runner.
    """

    def _watch() -> None:
        proc.join()
        exit_code = proc.exitcode
        logger.info(
            "watchdog: paper run %s child exited (pid=%s exit_code=%s)",
            run_id,
            proc.pid,
            exit_code,
        )
        if exit_code == 0:
            return
        _mark_run_failed_after_crash(run_id, exit_code)

    t = threading.Thread(
        target=_watch,
        name=f"paperrun-watchdog-{run_id}",
        daemon=True,
    )
    t.start()


def _mark_run_failed_after_crash(run_id: str, exit_code: int | None) -> None:
    """Flip a crashed paper run to ERROR with a specific stop reason.

    Idempotent — never overwrites terminal states. Distinguishes
    `crashed_pre_init` (child never reached RUNNING) from
    `process_crashed` (child died mid-loop) so the operator can
    diagnose faster.
    """
    try:
        from agentfx.api.core.database import SessionLocal
        from agentfx.api.models import RunStatus
        from agentfx.api.services import paper as paper_service

        db = SessionLocal()
        try:
            run = paper_service.get_paper_run(db, run_id)
            if run is None:
                logger.warning(
                    "watchdog: paper run %s vanished from DB before update",
                    run_id,
                )
                return
            if run.status in {RunStatus.RUNNING, RunStatus.APPROVED}:
                pre_init = run.status == RunStatus.APPROVED
                run.status = RunStatus.ERROR
                reason = "crashed_pre_init" if pre_init else "process_crashed"
                run.stop_reason = f"{reason} (exit_code={exit_code})"
                run.completed_at = datetime.now(UTC)
                db.commit()
                logger.error(
                    "watchdog: marked paper run %s ERROR (child exit_code=%s, was_in=%s)",
                    run_id,
                    exit_code,
                    "APPROVED" if pre_init else "RUNNING",
                )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("watchdog: failed to mark paper run %s as ERROR", run_id)


def _record_child_pid(run_id: str, pid: int | None) -> None:
    """Persist the subprocess PID onto the run row so the router can
    surface it (and the escape-hatch SIGTERM can find it).
    """
    try:
        from agentfx.api.core.database import SessionLocal
        from agentfx.api.services import paper as paper_service

        db = SessionLocal()
        try:
            run = paper_service.get_paper_run(db, run_id)
            if run is not None:
                run.process_pid = pid
                db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist paper-runner pid for run %s", run_id)


# ─────────────────────────────────────────────────────────────────────
# Subprocess entry
# ─────────────────────────────────────────────────────────────────────


def _subprocess_entry(run_id: str) -> None:
    """Child-process side. Set up logging, load the run, delegate to
    the service-layer loop.

    Kept thin — everything worth testing lives in
    `agentfx.services.paper_runner`, which the unit test suite calls
    directly with a mocked `OandaClient`.
    """
    # Configure logging inside the child; the parent's config doesn't
    # carry across a spawn.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("paper-runner subprocess starting for run %s", run_id)

    try:
        from agentfx.services.paper_runner import run_paper_run

        run_paper_run(run_id)
    except Exception:  # noqa: BLE001
        logger.exception("paper-runner subprocess crashed for run %s", run_id)
        # Non-zero exit tells the parent watchdog to flip the run to
        # ERROR. Bare `raise` re-uses the current traceback for the
        # crash log; the process exit code will be 1.
        raise
