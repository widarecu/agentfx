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


def _mark_run_failed_after_crash(run_id: str, exit_code: int | None) -> None:
    """DB-update half of the watchdog, isolated for testability.

    Called after the child process exits with a non-zero code. Flips
    the run row to FAILED — covering BOTH the running-then-crashed
    case AND the crashed-before-init case (QUEUED row whose
    subprocess died during module import before transitioning to
    RUNNING). Without the QUEUED branch, runs whose subprocess hit
    RLIMIT_AS / missing-import / version-conflict during boot sit
    silently in `queued` forever with no error trail.

    Idempotent: never overwrites a terminal state (COMPLETED /
    FAILED / STOPPED). Distinguishes the two crash shapes via the
    persisted ``stop_reason`` so the portal can surface a specific
    banner — module-import crashes are very different from mid-run
    crashes for the operator.
    """
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
            if run.status in {
                EvolutionRunStatus.RUNNING,
                EvolutionRunStatus.QUEUED,
            }:
                pre_init = run.status == EvolutionRunStatus.QUEUED
                run.status = EvolutionRunStatus.FAILED
                # Both reasons + " (exit_code=NNN)" must fit in the
                # `varchar(40)` `stop_reason` column. The shortened
                # `crashed_pre_init` variant keeps the whole string
                # well under 40 chars even for negative exit codes.
                reason = "crashed_pre_init" if pre_init else "process_crashed"
                run.stop_reason = f"{reason} (exit_code={exit_code})"
                run.completed_at = datetime.now(UTC)
                db.commit()
                logger.error(
                    "watchdog: marked run %s FAILED (child exit_code=%s, was_in=%s)",
                    run_id,
                    exit_code,
                    "QUEUED" if pre_init else "RUNNING",
                )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("watchdog: failed to mark run %s as FAILED", run_id)


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

        _mark_run_failed_after_crash(run_id, exit_code)

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
# Internal: config rehydration
# ─────────────────────────────────────────────────────────────────────────


def _build_orchestrator_config(
    config_dict: dict,
    *,
    instrument: str,
    granularity: str,
):
    """Rehydrate an OrchestratorConfig from the persisted JSON blob.

    Every modal-exposed knob MUST be listed here. Missing one means the
    user-set value is silently ignored and the dataclass default applies
    — which is exactly what bit us with ``min_cagr_pct`` (the validator
    kept enforcing 15% no matter what the user typed). When you add a
    new knob to ``OrchestratorConfig``, add it here too. The test in
    ``tests/unit/workers/test_evolution_runner.py`` pins the round-trip
    for the knobs we care about.
    """
    from agentfx.agents.orchestrator import OrchestratorConfig

    return OrchestratorConfig(
        instrument=instrument,
        granularity=granularity,
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
        leverage=int(config_dict.get("leverage", 30)),
        use_prior_winners=bool(config_dict.get("use_prior_winners", True)),
        n_prior_winners=int(config_dict.get("n_prior_winners", 3)),
        llm_seed_gen0=bool(config_dict.get("llm_seed_gen0", True)),
        n_llm_gen0_proposals=int(config_dict.get("n_llm_gen0_proposals", 5)),
        min_cagr_pct=float(config_dict.get("min_cagr_pct", 0.15)),
    )


class DataWindowNotCoveredError(RuntimeError):
    """Raised when an explicitly-pinned data window extends outside the
    candles actually in the DB. Silent clamping is misleading — the
    user would believe the strategy was evaluated over the requested
    span while the GA actually trained on a much smaller slice. We
    fail-fast instead, with a structured ``stop_reason`` the exception
    handler in ``_subprocess_entry`` picks up and persists on the run
    row so the portal shows a meaningful failure."""

    stop_reason = "data_window_not_covered"

    def __init__(
        self,
        message: str,
        *,
        requested: tuple[datetime, datetime],
        available: tuple[datetime, datetime] | None,
    ) -> None:
        super().__init__(message)
        self.requested = requested
        self.available = available


def _was_window_explicitly_pinned(config_dict: dict) -> bool:
    """True if the user (or any caller) set at least one of the explicit
    window keys. Distinguishes intentional pinning from the legacy
    rolling-window mode. We treat *either* key being set as an explicit
    anchor — partial pins still imply the user has a specific date in
    mind they don't want silently shifted."""
    return (
        config_dict.get("data_window_start") is not None
        or config_dict.get("data_window_end") is not None
    )


def _assert_window_covered(
    requested: tuple[datetime, datetime],
    available: tuple[datetime, datetime] | None,
) -> None:
    """Strict variant of ``_clamp_window_to_data``: raise when the
    requested window isn't fully within the available data range.

    Only call this when the caller pinned the window explicitly — for
    rolling windows the clamp path is the right behaviour ("give me
    approximately N years of history"). When the user nailed exact
    dates, silent shrinkage breaks the experiment."""
    if available is None:
        raise DataWindowNotCoveredError(
            (
                f"No candle data available; cannot evaluate the pinned window "
                f"{requested[0].isoformat()} → {requested[1].isoformat()}. "
                f"Backfill the instrument first or choose a different window."
            ),
            requested=requested,
            available=None,
        )
    oldest, newest = available
    problems: list[str] = []
    if requested[0] < oldest:
        gap_days = (oldest - requested[0]).total_seconds() / 86400
        problems.append(
            f"start {requested[0].isoformat()} predates the oldest candle "
            f"{oldest.isoformat()} by {gap_days:.0f} days"
        )
    if requested[1] > newest:
        gap_days = (requested[1] - newest).total_seconds() / 86400
        problems.append(
            f"end {requested[1].isoformat()} extends past the newest candle "
            f"{newest.isoformat()} by {gap_days:.0f} days"
        )
    if problems:
        raise DataWindowNotCoveredError(
            (
                "Pinned data window is not fully covered by available candles: "
                + "; ".join(problems)
                + ". Pick a window inside "
                f"{oldest.isoformat()} → {newest.isoformat()} "
                "or backfill more history."
            ),
            requested=requested,
            available=available,
        )


def _resolve_requested_window(config_dict: dict) -> tuple[datetime, datetime]:
    """Return the *requested* (pre-clamp) data window for this run.

    Three modes:
      1. Both `data_window_start` and `data_window_end` present → use
         them verbatim. This is the reproducible mode — once set, the
         run uses the SAME window regardless of when it's re-launched
         (no `now()` reference).
      2. Only one of start/end present → the other is computed by
         shifting `data_years * 365` days from the provided anchor.
      3. Neither present → rolling `(now - data_years, now)` window,
         the legacy behaviour. The worker will still snapshot the
         resolved window back to `effective_data_window_*` after
         clamping, so even the rolling-window case becomes
         reproducible AFTER the run starts.
    """
    # `data_years` may be missing entirely (v0.4.0+ form omits it when
    # explicit dates are pinned) or persisted as `None` (transient runs
    # mid-migration). Coerce both to the legacy default so the rolling
    # fallback paths below still produce a sensible window.
    raw_data_years = config_dict.get("data_years")
    data_years = int(raw_data_years) if raw_data_years is not None else 5
    raw_start = config_dict.get("data_window_start")
    raw_end = config_dict.get("data_window_end")

    def _parse(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=UTC)
        return datetime.fromisoformat(v).astimezone(UTC)

    start = _parse(raw_start)
    end = _parse(raw_end)

    if start is not None and end is not None:
        return start, end
    if end is not None:
        return end - timedelta(days=data_years * 365), end
    if start is not None:
        return start, start + timedelta(days=data_years * 365)

    now = datetime.now(UTC)
    return (now - timedelta(days=data_years * 365), now)


def _build_stop_conditions(stop_dict: dict):
    """Rehydrate ``StopConditions`` from the persisted JSON blob.

    Same drop-a-knob-silently-and-default risk as
    ``_build_orchestrator_config`` — kept symmetric and tested so a new
    stop condition added to the dataclass also gets wired here. The
    ``enable_target_fitness_stop`` flag specifically was added after
    several runs early-stopped on training fitness only to fail OOS; the
    knob is opt-in default-off so the modal must explicitly toggle it.
    """
    from agentfx.agents.orchestrator import StopConditions

    return StopConditions(
        max_generations=int(stop_dict.get("max_generations", 30)),
        max_wall_clock_seconds=int(stop_dict.get("max_wall_clock_seconds", 6 * 3600)),
        target_fitness=float(stop_dict.get("target_fitness", 1.5)),
        enable_target_fitness_stop=bool(
            stop_dict.get("enable_target_fitness_stop", False)
        ),
        no_improvement_generations=int(stop_dict.get("no_improvement_generations", 5)),
    )


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

        # Resolve a fresh random seed when the user didn't pin one. The
        # previous default-42 made independent runs deterministically
        # converge on the same winning DSL — turning the GA into a
        # confirmation loop instead of a search. Writing the resolved
        # value back into `config_dict` lets the snapshot block below
        # persist it on the row, so a past run remains reproducible by
        # pinning the same value on a new run.
        if config_dict.get("rng_seed") is None:
            import secrets

            config_dict["rng_seed"] = secrets.randbits(32)
            logger.info(
                "resolved fresh rng_seed=%s for run %s (no seed pinned by caller)",
                config_dict["rng_seed"],
                run_id,
            )

        config = _build_orchestrator_config(
            config_dict,
            instrument=run.instrument,
            granularity=run.granularity,
        )
        stop_conditions = _build_stop_conditions(stop_dict)

        requested_window = _resolve_requested_window(config_dict)
        explicit_pin = _was_window_explicitly_pinned(config_dict)

        # Resolve the available candle range once so we can either
        # validate strictly (pinned window) or clamp gracefully
        # (rolling window).
        from agentfx.api.models import Candle

        oldest_ts_row = (
            db.query(Candle.timestamp)
            .filter(
                Candle.instrument == run.instrument,
                Candle.timeframe == run.granularity,
            )
            .order_by(Candle.timestamp.asc())
            .first()
        )
        newest_ts_row = (
            db.query(Candle.timestamp)
            .filter(
                Candle.instrument == run.instrument,
                Candle.timeframe == run.granularity,
            )
            .order_by(Candle.timestamp.desc())
            .first()
        )
        available = None
        if oldest_ts_row is not None and newest_ts_row is not None:
            oldest_ts = oldest_ts_row[0]
            newest_ts = newest_ts_row[0]
            if oldest_ts.tzinfo is None:
                oldest_ts = oldest_ts.replace(tzinfo=UTC)
            if newest_ts.tzinfo is None:
                newest_ts = newest_ts.replace(tzinfo=UTC)
            available = (oldest_ts, newest_ts)

        if explicit_pin:
            # Strict mode: never silently shrink a user-pinned window.
            # The user has stated an exact experimental range; if we
            # can't satisfy it, raise and let the run fail with a
            # clear stop_reason. The exception handler downstream
            # records `stop_reason='data_window_not_covered'` on the
            # row so the portal shows a meaningful failure instead of
            # "completed" against a silently-shrunk slice.
            _assert_window_covered(requested_window, available)
            data_window = requested_window
        else:
            # Rolling-window: legacy behaviour. The user asked for
            # "approx N years" — clamping is the right answer when
            # the DB has less.
            data_window = _clamp_window_to_data(
                db,
                instrument=run.instrument,
                granularity=run.granularity,
                requested_window=requested_window,
            )
            if data_window is None:
                # No candles at all for this instrument+granularity —
                # not a process crash, just an unmet precondition. Raise
                # the structured exception so the downstream handler in
                # `_subprocess_entry` records `data_window_not_covered`
                # on the row and surfaces the human-readable message to
                # the portal via `config_json.failure_message`.
                raise DataWindowNotCoveredError(
                    (
                        f"No candle data available for "
                        f"{run.instrument}:{run.granularity}. "
                        f"Backfill the instrument first or pick one "
                        f"that already has history loaded."
                    ),
                    requested=requested_window,
                    available=None,
                )
        logger.info(
            "data window for run %s: requested=%s..%s clamped=%s..%s",
            run_id,
            requested_window[0].isoformat(),
            requested_window[1].isoformat(),
            data_window[0].isoformat(),
            data_window[1].isoformat(),
        )

        # Resolve effective spreads NOW so the run is reproducible.
        # v0.4.0: per-pair dict shape. Today the orchestrator runs
        # one pair per run, so the dict has a single entry — but the
        # shape is the upgrade path; v0.5.0+ multi-symbol runs will
        # fill multiple entries without any further migration.
        #
        # Single-pair semantics preserved: `config.spread_pips`
        # (scalar) is still set so the orchestrator's existing fitness
        # + validator call paths continue to work unchanged. The dict
        # is purely additive metadata for now.
        from agentfx.services.backtest import _lookup_typical_spreads

        # The instruments this run will evaluate. Today: just one.
        # Tomorrow: pulled off config.instruments_list or similar.
        run_instruments = [run.instrument]

        effective_spread_per_pair: dict[str, float] = {}
        if config.spread_pips is not None:
            # User pinned a single value via the modal. Apply uniformly
            # to every instrument this run touches (today: just one).
            for pair in run_instruments:
                effective_spread_per_pair[pair] = float(config.spread_pips)
            logger.info(
                "pinned spread_pips=%.4f for run %s (user-supplied, applied to %d pair(s))",
                config.spread_pips,
                run_id,
                len(run_instruments),
            )
        else:
            looked_up = _lookup_typical_spreads(run_instruments)
            effective_spread_per_pair.update(looked_up)
            missing = [p for p in run_instruments if p not in looked_up]
            if missing:
                logger.warning(
                    "no typical_spread_pips on instruments rows for %s; "
                    "those backtests will fall back to the hardcoded default",
                    missing,
                )
            # Single-pair back-compat: set scalar from the dict entry
            # so the orchestrator's existing `config.spread_pips`
            # readers don't need to change.
            if run.instrument in effective_spread_per_pair:
                config.spread_pips = effective_spread_per_pair[run.instrument]
                logger.info(
                    "pinned spread_pips=%.4f for run %s "
                    "(live snapshot at run-start, %d pair(s))",
                    config.spread_pips,
                    run_id,
                    len(effective_spread_per_pair),
                )

        # Snapshot the EFFECTIVE inputs back into config_json so the row
        # is self-describing. A reader (or a re-run, or the portal)
        # never has to guess what "now" meant or what the live snapshot
        # was — those are frozen here.
        #
        # CRITICAL: SQLAlchemy's plain JSON column does not auto-detect
        # dict mutations. Even a full reassignment (`run.config_json =
        # new_dict`) can be silently skipped on UPDATE if SQLAlchemy
        # decides the value "didn't change" by identity. `flag_modified`
        # forces the column into the dirty set so the next commit
        # always emits an UPDATE. Without this the snapshot block runs
        # but the row keeps the original config_json — exactly the
        # silent-no-op the user hit in evorun_20260526_0d89dbe0.
        from sqlalchemy.orm.attributes import flag_modified

        config_dict["effective_data_window_start"] = data_window[0].isoformat()
        config_dict["effective_data_window_end"] = data_window[1].isoformat()
        if config.spread_pips is not None:
            # Legacy scalar — kept for v0.3.0 portal back-compat. Always
            # equals effective_spread_pips_per_pair[run.instrument]
            # in v0.4.0 single-pair runs.
            config_dict["effective_spread_pips"] = float(config.spread_pips)
        if effective_spread_per_pair:
            # v0.4.0 per-pair shape. One entry today; many entries when
            # multi-symbol runs ship. The portal reads this preferentially
            # and falls back to the scalar for older rows.
            config_dict["effective_spread_pips_per_pair"] = {
                pair: float(spread)
                for pair, spread in effective_spread_per_pair.items()
            }
        run.config_json = dict(config_dict)
        flag_modified(run, "config_json")
        db.commit()
        logger.info(
            "snapshotted effective inputs for run %s: window=%s..%s spread=%s",
            run_id,
            config_dict["effective_data_window_start"],
            config_dict["effective_data_window_end"],
            config_dict.get("effective_spread_pips"),
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

    except Exception as exc:  # noqa: BLE001
        logger.exception("evolution run %s crashed in child process", run_id)
        # Mark the run failed from inside the child so the DB reflects
        # reality even without parent-side monitoring. The original session
        # may be in a failed-transaction state (e.g. IntegrityError leaves
        # the session unusable), so roll back first AND fall back to a
        # fresh session if the existing one is unrecoverable. The parent
        # watchdog catches non-zero exits regardless, but this internal
        # handler gives faster visibility in the DB while the parent
        # watchdog is still waiting on proc.join().
        #
        # Specific structured exceptions (`DataWindowNotCoveredError`)
        # carry their own `stop_reason` and a human-readable message —
        # surface both so the portal shows the actual problem instead
        # of a generic "process_crashed".
        specific_reason: str | None = getattr(exc, "stop_reason", None)
        failure_message: str | None = str(exc) if specific_reason else None
        try:
            db.rollback()
            run = evolution_service.get_run(db, run_id)
            if run is not None:
                from agentfx.api.models import EvolutionRunStatus
                from sqlalchemy.orm.attributes import flag_modified

                run.status = EvolutionRunStatus.FAILED
                run.stop_reason = specific_reason or "process_crashed"
                run.completed_at = datetime.now(UTC)
                if failure_message:
                    cfg = dict(run.config_json or {})
                    cfg["failure_message"] = failure_message
                    run.config_json = cfg
                    flag_modified(run, "config_json")
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
