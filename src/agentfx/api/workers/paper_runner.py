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

import asyncio
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
# Auto-restart supervisor (Wilson 2026-08-23)
#
# The watchdog reaps dead subprocesses and marks the run ERROR; it
# never re-schedules. Before this supervisor, every transient failure
# (OANDA outage, momentary crash) left the run dead until Wilson
# manually re-approved via the UI. Aug 19 + Aug 21 events sat dead
# for 14h and 37h respectively.
#
# The supervisor scans for ERROR runs whose stop_reason is in the
# transient set. It waits a backoff period based on how many times it
# has already restarted this same run, then flips ERROR → APPROVED
# and calls `schedule_paper_run`. Attempt count is stored in
# `run.parameters['auto_restart_attempts']`.
#
# Explicitly NOT auto-restarted: OANDA_AUTH_LOST (credential issue,
# user action), LOSS_CAP_HIT / BROKER_CIRCUIT (risk gate trips,
# should be reviewed), USER_KILL (operator intent). The gate is
# strictly the transient stop_reasons defined here.
# ─────────────────────────────────────────────────────────────────────

_TRANSIENT_STOP_REASONS: frozenset[str] = frozenset({"BROKER_NETWORK"})
_TRANSIENT_STOP_PREFIXES: tuple[str, ...] = ("process_crashed", "crashed_pre_init")

# Backoff between restart attempts (indexed by attempts_already_made).
# After the 3rd attempt fails, run is left permanently ERROR + Slack.
_RESTART_BACKOFF_SECONDS: tuple[float, ...] = (60.0, 300.0, 1800.0)
_MAX_AUTO_RESTART_ATTEMPTS = len(_RESTART_BACKOFF_SECONDS)

# How often the supervisor tick fires. Small enough that a 60s outage
# is picked up quickly after the backoff clears; large enough not to
# hammer the DB.
_SUPERVISOR_TICK_SECONDS = 120.0

# Reset the attempt counter after the run has been healthy this long.
# Prevents a run that fails once every 3 weeks from ever exhausting
# its restart budget.
_ATTEMPT_RESET_AFTER_SECONDS = 24 * 3600.0

# Wilson 2026-08-24: zombie RUNNING detection window. A RUNNING paper
# run whose heartbeat is older than this (or whose process_pid is NULL)
# is considered dead — restart via the same auto-restart flow. The
# subprocess emits a heartbeat every ~5 min, so 15 min is 3× interval
# and rules out normal loop backoffs.
_HEARTBEAT_STALE_SECONDS = 15 * 60.0


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
                # AFX-7: release the fleet pool slot if the crashed
                # subprocess held one. The subprocess's _mark_terminal
                # would have released on a clean exit; this covers hard
                # crashes (SIGKILL, OOM, container swap). Never re-raises.
                try:
                    from agentfx.agents.fleet.account_pool import release_slot

                    released = release_slot(db, run_id=run_id)
                    if released:
                        db.commit()
                        logger.info(
                            "watchdog: released pool slot for crashed run %s",
                            run_id,
                        )
                except Exception:  # noqa: BLE001 — watchdog stability
                    logger.exception(
                        "watchdog: pool release errored for crashed run %s — "
                        "lease TTL will reclaim",
                        run_id,
                    )
                # v0.6.2 (Wilson UAT 2026-07-19): Slack alert on
                # watchdog-detected crash. Idempotent — if the
                # subprocess got as far as calling `_mark_terminal`
                # itself before dying, its dedup entry in
                # `run.parameters['notified'][reason]` already exists
                # and `post_paper_run_alert` short-circuits. Otherwise
                # (SIGKILL, OOM, container swap) we're the only path
                # that fires. Never re-raises.
                try:
                    from agentfx.notifications import post_paper_run_alert

                    post_paper_run_alert(
                        run=run,
                        reason=run.stop_reason,
                        extra={"exit_code": exit_code},
                        db=db,
                    )
                except Exception:  # noqa: BLE001 — watchdog stability
                    logger.exception(
                        "watchdog: notification hook errored on %s — "
                        "ignoring so the ERROR state stays committed",
                        run_id,
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("paper-runner subprocess crashed for run %s", run_id)
        # Persist the traceback to `run.parameters.crash_detail` so the
        # operator can diagnose without spelunking container logs.
        # Best-effort — if the DB write fails (e.g. corrupted session),
        # the watchdog still catches the non-zero exit and marks ERROR
        # with the generic `process_crashed` reason.
        _persist_crash_detail(run_id, exc)
        # Non-zero exit tells the parent watchdog to flip the run to
        # ERROR. Bare `raise` re-uses the current traceback for the
        # crash log; the process exit code will be 1.
        raise


def _persist_crash_detail(run_id: str, exc: Exception) -> None:
    """Store a structured traceback into `runs.parameters.crash_detail`.

    The runner subprocess calls this from inside its own exception
    handler, so the write happens BEFORE the watchdog thread flips
    the run to ERROR. On resume the frontend clears the detail (or
    stashes it into `crash_history` if the user wants a log of past
    crashes — for now we just overwrite).

    Wilson's UAT scenario (2026-07-13): laptop hibernation → runner
    subprocess dies with exit_code=1 → previous UI just showed
    "process_crashed (exit_code=1)" with no traceback. This surfaces
    the actual Python exception + line.
    """
    try:
        import traceback

        from agentfx.api.core.database import SessionLocal
        from agentfx.api.services import paper as paper_service
        from sqlalchemy.orm.attributes import flag_modified

        detail = {
            "exception_type": type(exc).__name__,
            "message": str(exc)[:2000],  # cap to avoid huge JSON blobs
            "traceback": traceback.format_exc()[-8000:],  # tail only
            "crashed_at": datetime.now(UTC).isoformat(),
        }
        db = SessionLocal()
        try:
            run = paper_service.get_paper_run(db, run_id)
            if run is None:
                return
            params = dict(run.parameters or {})
            params["crash_detail"] = detail
            run.parameters = params
            # SQLAlchemy doesn't track in-place JSON mutations —
            # explicitly flag so the change hits the UPDATE.
            flag_modified(run, "parameters")
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist crash_detail for paper run %s", run_id)


# ─────────────────────────────────────────────────────────────────────
# Auto-restart supervisor
# ─────────────────────────────────────────────────────────────────────


def _is_transient_stop_reason(stop_reason: str | None) -> bool:
    """True if a stop_reason is safe to auto-restart from.

    Exact match against `_TRANSIENT_STOP_REASONS`; prefix match against
    `_TRANSIENT_STOP_PREFIXES` (so `process_crashed (exit_code=1)` is
    caught). Anything else — LOSS_CAP_HIT, BROKER_CIRCUIT, USER_KILL,
    OANDA_AUTH_LOST — is deliberately excluded: those need human
    review.
    """
    if not stop_reason:
        return False
    if stop_reason in _TRANSIENT_STOP_REASONS:
        return True
    return any(stop_reason.startswith(p) for p in _TRANSIENT_STOP_PREFIXES)


def _seconds_since(dt: datetime | None, now: datetime) -> float:
    if dt is None:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).total_seconds()


def scan_and_restart_transient_errors(db=None, now: datetime | None = None) -> int:
    """One supervisor tick — walk the paper runs table, restart each
    eligible run.

    Extracted from the async loop so tests can call it directly with
    a controlled `now` and stub `schedule_paper_run`. Returns the
    number of runs re-scheduled by this tick.

    Eligibility (all must hold):
      * status == ERROR
      * environment == PAPER
      * completed_at is set
      * _is_transient_stop_reason(stop_reason) True
      * attempts_so_far < _MAX_AUTO_RESTART_ATTEMPTS
      * seconds_since(completed_at) >= _RESTART_BACKOFF_SECONDS[attempts_so_far]

    On restart:
      * status → APPROVED, stop_reason → None, completed_at → None
      * parameters['auto_restart_attempts'] += 1
      * parameters['auto_restart_last_at'] = ISO timestamp
      * calls schedule_paper_run(run.id)
      * writes a decision-log entry so the operator sees the restart
        in the run's timeline
    """
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import Run, RunEnvironment, RunStatus
    from sqlalchemy.orm.attributes import flag_modified

    own_session = db is None
    if own_session:
        db = SessionLocal()
    if now is None:
        now = datetime.now(UTC)

    restarted = 0
    try:
        candidates = (
            db.query(Run)
            .filter(
                Run.environment == RunEnvironment.PAPER,
                Run.status == RunStatus.ERROR,
                Run.completed_at.isnot(None),
            )
            .all()
        )
        for run in candidates:
            if not _is_transient_stop_reason(run.stop_reason):
                continue
            params = dict(run.parameters or {})
            attempts_so_far = int(params.get("auto_restart_attempts", 0))

            # Reset the attempt counter if the run was healthy long
            # enough before this crash. Approximated by
            # `completed_at - approved_at` — if the whole run lasted
            # more than the reset window, we consider prior attempts
            # stale.
            approved_at = run.approved_at
            if approved_at is not None and attempts_so_far > 0:
                run_lifetime = _seconds_since(approved_at, run.completed_at)
                if run_lifetime >= _ATTEMPT_RESET_AFTER_SECONDS:
                    attempts_so_far = 0

            if attempts_so_far >= _MAX_AUTO_RESTART_ATTEMPTS:
                continue

            backoff = _RESTART_BACKOFF_SECONDS[attempts_so_far]
            since_halt = _seconds_since(run.completed_at, now)
            if since_halt < backoff:
                continue

            # Restart.
            prior_reason = run.stop_reason
            run.status = RunStatus.APPROVED
            run.process_pid = None
            run.stop_reason = None
            run.completed_at = None
            params["auto_restart_attempts"] = attempts_so_far + 1
            params["auto_restart_last_at"] = now.isoformat()
            params["auto_restart_prior_reason"] = prior_reason
            run.parameters = params
            flag_modified(run, "parameters")
            db.commit()

            logger.warning(
                "auto-restart: rescheduling paper run %s "
                "(attempt %d/%d, prior stop_reason=%s, waited %.0fs)",
                run.id,
                attempts_so_far + 1,
                _MAX_AUTO_RESTART_ATTEMPTS,
                prior_reason,
                since_halt,
            )
            _write_restart_decision(
                db, run, attempts_so_far + 1, prior_reason, since_halt
            )
            schedule_paper_run(run.id)
            restarted += 1
    finally:
        if own_session:
            db.close()

    return restarted


def _write_restart_decision(
    db,
    run,
    attempt_number: int,
    prior_reason: str | None,
    waited_seconds: float,
) -> None:
    """Append an auto-restart entry to the run's decision log so the
    operator sees the recovery attempt inline with trades and health
    ticks. Best-effort — a decision-log write failure must not block
    the restart itself."""
    try:
        import uuid as _uuid

        from agentfx.api.models import DecisionLog

        # Wilson 2026-08-31: prior version used field names that don't
        # exist on the DecisionLog model (`reason`, `metadata_json`,
        # `timestamp`, `run_id=paper_run_id`), so every restart's audit
        # entry silently exploded. DecisionLog.run_id FKs to
        # evolution_runs; paper run id is stashed in outputs_json.
        db.add(
            DecisionLog(
                id=_uuid.uuid4().hex,
                run_id=None,
                agent_type="PAPER_RUNNER_SUPERVISOR",
                action="AUTO_RESTART",
                outputs_json={
                    "paper_run_id": run.id,
                    "auto_restart_attempt": attempt_number,
                    "prior_stop_reason": prior_reason,
                    "waited_seconds": waited_seconds,
                },
                reasoning_text=(
                    f"supervisor rescheduled paper run "
                    f"(attempt {attempt_number}/{_MAX_AUTO_RESTART_ATTEMPTS}) — "
                    f"prior stop_reason={prior_reason}, "
                    f"waited {waited_seconds:.0f}s of "
                    f"{_RESTART_BACKOFF_SECONDS[attempt_number - 1]:.0f}s backoff"
                ),
                user_id=getattr(run, "user_id", None),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "auto-restart: failed to write decision-log entry for %s "
            "(restart itself already happened)",
            run.id,
        )


def scan_and_restart_zombie_running(db=None, now: datetime | None = None) -> int:
    """Second supervisor scan — Wilson 2026-08-24 audit.

    Catches paper runs that are RUNNING in the DB but whose subprocess
    is dead (or silent) — the failure mode where the watchdog never
    fired so status never flipped to ERROR. Two signals identify a
    zombie:

    1. `process_pid IS NULL` on a RUNNING row — the pid was cleared
       (e.g. by `recover_orphaned_paper_runs` during a container
       restart) but a fresh subprocess was never re-scheduled OR
       its pid never got recorded back.
    2. `parameters['last_heartbeat_at']` older than
       `_HEARTBEAT_STALE_SECONDS` — heartbeat writes every ~5 min
       when the loop is healthy; 15 min of silence = dead.

    Zombie runs go through the SAME auto-restart flow as ERROR runs:
    increment attempt counter, honour backoff, reschedule. Prevents
    the "Aug 21 stranded 37h" pattern where the DB claimed running
    but nothing was actually happening.
    """
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import Run, RunEnvironment, RunStatus
    from sqlalchemy.orm.attributes import flag_modified

    own_session = db is None
    if own_session:
        db = SessionLocal()
    if now is None:
        now = datetime.now(UTC)

    restarted = 0
    try:
        candidates = (
            db.query(Run)
            .filter(
                Run.environment == RunEnvironment.PAPER,
                Run.status == RunStatus.RUNNING,
            )
            .all()
        )
        for run in candidates:
            params = dict(run.parameters or {})
            pid_null = run.process_pid is None
            heartbeat_iso = params.get("last_heartbeat_at")
            heartbeat_stale = False
            if heartbeat_iso:
                try:
                    heartbeat_dt = datetime.fromisoformat(heartbeat_iso)
                    if heartbeat_dt.tzinfo is None:
                        heartbeat_dt = heartbeat_dt.replace(tzinfo=UTC)
                    heartbeat_stale = (
                        _seconds_since(heartbeat_dt, now) >= _HEARTBEAT_STALE_SECONDS
                    )
                except (ValueError, TypeError):
                    heartbeat_stale = True
            else:
                # No heartbeat yet + non-null pid → subprocess just
                # started; give it one full stale window before
                # declaring zombie. Non-null pid + no heartbeat + old
                # approved_at means we ARE zombie.
                if not pid_null and run.approved_at is not None:
                    if _seconds_since(run.approved_at, now) < _HEARTBEAT_STALE_SECONDS:
                        continue
                    heartbeat_stale = True

            if not (pid_null or heartbeat_stale):
                continue

            # Attempts counter shared with the ERROR-path restart.
            attempts_so_far = int(params.get("auto_restart_attempts", 0))
            approved_at = run.approved_at
            if approved_at is not None and attempts_so_far > 0:
                run_lifetime = _seconds_since(approved_at, now)
                if run_lifetime >= _ATTEMPT_RESET_AFTER_SECONDS:
                    attempts_so_far = 0

            if attempts_so_far >= _MAX_AUTO_RESTART_ATTEMPTS:
                logger.warning(
                    "auto-restart: zombie run %s exhausted "
                    "%d restart attempts; leaving RUNNING (manual "
                    "intervention required)",
                    run.id,
                    _MAX_AUTO_RESTART_ATTEMPTS,
                )
                continue

            # Zombie runs skip the backoff wait — the subprocess is
            # already dead so waiting doesn't help. But we still honour
            # the attempts counter so a persistent bug can't loop
            # forever.
            reason_desc = "process_pid=None" if pid_null else "heartbeat stale"
            logger.warning(
                "auto-restart (zombie): rescheduling paper run %s "
                "(attempt %d/%d, signal=%s, heartbeat=%s)",
                run.id,
                attempts_so_far + 1,
                _MAX_AUTO_RESTART_ATTEMPTS,
                reason_desc,
                heartbeat_iso or "never",
            )

            prior_pid = run.process_pid
            run.status = RunStatus.APPROVED
            run.process_pid = None
            params["auto_restart_attempts"] = attempts_so_far + 1
            params["auto_restart_last_at"] = now.isoformat()
            params["auto_restart_prior_reason"] = f"zombie:{reason_desc}"
            # Clear stale heartbeat so the next tick doesn't
            # immediately re-trigger zombie detection.
            params.pop("last_heartbeat_at", None)
            run.parameters = params
            flag_modified(run, "parameters")
            db.commit()

            _write_restart_decision(
                db,
                run,
                attempts_so_far + 1,
                f"zombie:{reason_desc}(pid={prior_pid})",
                _seconds_since(approved_at, now) if approved_at else 0.0,
            )
            schedule_paper_run(run.id)
            restarted += 1
    finally:
        if own_session:
            db.close()

    return restarted


async def auto_restart_supervisor_loop(
    tick_seconds: float = _SUPERVISOR_TICK_SECONDS,
) -> None:
    """Background task started from the FastAPI lifespan hook. Runs
    forever until cancelled at shutdown.

    Each tick fires TWO scans:
      1. `scan_and_restart_transient_errors` — ERROR runs with
         transient stop_reason (BROKER_NETWORK, process_crashed*).
      2. `scan_and_restart_zombie_running` — RUNNING runs whose
         subprocess is silently dead (pid=None or stale heartbeat).

    Both run inside an executor so blocking DB work doesn't tie up
    the event loop. Any per-tick exception is logged and swallowed —
    the loop itself must never die.
    """
    logger.info(
        "auto-restart supervisor started (tick=%.0fs, max_attempts=%d, "
        "heartbeat_stale=%.0fs)",
        tick_seconds,
        _MAX_AUTO_RESTART_ATTEMPTS,
        _HEARTBEAT_STALE_SECONDS,
    )
    loop = asyncio.get_event_loop()
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            try:
                error_restarts = await loop.run_in_executor(
                    None, scan_and_restart_transient_errors
                )
                zombie_restarts = await loop.run_in_executor(
                    None, scan_and_restart_zombie_running
                )
                total = error_restarts + zombie_restarts
                if total > 0:
                    logger.info(
                        "auto-restart supervisor: rescheduled %d run(s) "
                        "(error=%d, zombie=%d)",
                        total,
                        error_restarts,
                        zombie_restarts,
                    )
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("auto-restart supervisor: tick failed; continuing")
        except asyncio.CancelledError:
            logger.info("auto-restart supervisor: cancelled at shutdown")
            raise
