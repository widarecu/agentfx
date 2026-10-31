"""Paper runner decision loop (US-021 + US-024).

Runs inside the subprocess spawned by
`agentfx.api.workers.paper_runner.schedule_paper_run`. The parent-side
plumbing (spawn / watchdog / kill-signal escape hatch) is intentionally
separate so this module can be exercised by unit tests without
touching multiprocessing.

Loop shape (v0.6.1):
    Two interleaved cadences in one subprocess.

    1. **Health tick** — every 5 seconds. Reloads the run row, checks
       the kill flag, fetches balance from OANDA, evaluates risk
       gates. This preserves the sub-5s kill-switch guarantee from
       v0.6.0.

    2. **Signal tick** — aligned to the strategy's granularity
       (M1 = every 60s at `:00`, H1 = every `HH:00`, etc.). Fetches
       fresh candles from the shared `candles` table, compiles the
       genome's DSL via `compile_spec()` (same helper the backtest
       engine uses — training-vs-live parity), evaluates
       `strategy.generate_signals(df)`, diffs against the current
       OANDA position, and places / closes orders. Every action
       writes a `USER_ORDER_PLACED` / `USER_POSITION_CLOSED` /
       `USER_SIGNAL_EVALUATED` / `USER_SIGNAL_SKIPPED_STALE`
       DecisionLog row for the audit trail.

The v0.6.0 ship of this module was a monitoring skeleton — it polled
balance + risk gates but never fetched candles, compiled the DSL, or
called `place_market_order`. Wilson's 24-hour UAT run on 2026-07-08
halted with zero trades because of that gap. US-024 closes it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────


# Fixed lookback window for live signal evaluation. Covers every DSL
# indicator commonly used (SMA_200 needs 200; MACD_26 needs ~35; etc.)
# with a comfortable safety margin so early-bar NaNs from indicators
# never propagate into the last-row signal we act on. Chosen as a
# constant rather than walking the spec so a strategy that stretches
# its lookback doesn't silently under-fetch — if we ever ship indicators
# with lookbacks past this ceiling, bump the constant + write a
# regression test.
DEFAULT_LOOKBACK_BARS = 500

# Wall-clock tolerance for ingester lag on top of the granularity.
#
# Two-layer lag reality (Wilson UAT observation 2026-07-14, verified
# by candles.updated_at vs candles.timestamp):
#
#   1. The bar labeled T (OANDA open-time convention) closes at
#      wall-clock T+g. Our ingester polls OANDA every ~80s and writes
#      the row as complete=false during the bar, then UPDATES it to
#      complete=true only on the poll AFTER the bar closes.
#   2. Observed `updated_at - timestamp` for M1 bars: 89-130s
#      (median ~110s). Meaning: a bar isn't `complete=true` in our
#      DB until ~2 bar-lengths after it opened.
#
# So at wall-clock T, the newest `complete=true` bar is typically
# `T - 2*g` and can be `T - 3*g` in the window between "bar T-g
# closed" and "next poll marks it complete." Under healthy ops the
# gap from wall-clock to newest complete bar therefore oscillates
# between `g` and `~2g + poll_cadence`.
#
# Threshold must cover the tail (~180s for M1) without hiding real
# outages. 150s tolerance → M1 threshold 210s = 3.5 bar lengths.
INGEST_LAG_TOLERANCE_SECONDS = 150

# Consecutive non-auth OANDA failures on the balance refresh before we
# give up and halt with `BROKER_NETWORK`.
#
# Wilson 2026-08-23 audit: the Aug 21 22:12 UTC outage was a ~60-second
# OANDA hiccup that halted BOTH live paper runs. Under the old 3-strike
# / 5s-sleep policy the runner tolerated ~1 minute of unavailability;
# real-world OANDA transient outages routinely run 30s–15min. Widened
# to 6 outer attempts with exponential backoff between them so we
# tolerate up to ~15 minutes of continuous unavailability before halting
# cleanly (still with BROKER_NETWORK). Each adapter call itself burns
# ~75s of internal retries; 6 outer attempts × ~75s + backoff sleeps
# (15 + 30 + 60 + 120 + 240 = 465s) ≈ 15.25 min total tolerance.
_BALANCE_RETRY_LIMIT = 6
_BALANCE_RETRY_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0, 240.0)


def _balance_retry_sleep_seconds(consecutive_failures: int) -> float:
    """Sleep length after the Nth (1-indexed) consecutive balance
    failure. Clamped to the last defined backoff so callers can't run
    off the end of the list. Also caps at 5 minutes for safety.
    """
    idx = max(0, min(consecutive_failures - 1, len(_BALANCE_RETRY_BACKOFF_SECONDS) - 1))
    return min(300.0, _BALANCE_RETRY_BACKOFF_SECONDS[idx])


# Granularity → seconds. Mirrors OANDA's naming; keep in sync with the
# frontend's `granularityToSeconds` in `portal/web/src/utils/candles.ts`.
_GRANULARITY_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D": 86400,
}


# ─────────────────────────────────────────────────────────────────────
# Decision dataclass — the pure output the loop wants per tick
# ─────────────────────────────────────────────────────────────────────


Direction = Literal["long", "short", "flat"]


@dataclass(frozen=True)
class TickAction:
    """What the decision function tells the loop to do on this tick."""

    action: Literal["place", "close", "hold"]
    signal_direction: Direction
    signal_units: int
    reason: str  # "MACD ↑", "kill_requested", "flat_signal", …


class PositionSource(Protocol):
    """Minimal shape the decision function needs from an OandaClient.

    Extracted as a protocol so tests can inject a plain namespace.
    """

    def get_position(self, instrument: str) -> dict: ...


# ─────────────────────────────────────────────────────────────────────
# Pure helpers — tested in isolation
# ─────────────────────────────────────────────────────────────────────


def decide_action(
    *,
    signal_direction: Direction,
    signal_units: int,
    current_net_units: int,
) -> TickAction:
    """Diff a fresh signal against the current OANDA position.

    Returns:
        `place` — signal says long/short and current position is flat
                  (or opposite direction; the runner closes first).
        `close` — signal says flat and current position is non-zero.
        `hold`  — signal matches the current position within units
                  (small units-drift under the cap is ignored).
    """
    if signal_direction == "flat":
        return TickAction(
            action="close" if current_net_units != 0 else "hold",
            signal_direction="flat",
            signal_units=0,
            reason="flat_signal" if current_net_units != 0 else "already_flat",
        )

    proposed = signal_units if signal_direction == "long" else -signal_units

    # Same direction, same magnitude — hold. The runner treats "hold"
    # as no-op so we don't spam OANDA with identical orders.
    if proposed == current_net_units:
        return TickAction(
            action="hold",
            signal_direction=signal_direction,
            signal_units=signal_units,
            reason="signal_matches_position",
        )

    # Opposite direction — the loop will close the current side then
    # place the new one; from `decide_action`'s point of view that's
    # still a "place" because the caller flattens first if needed.
    return TickAction(
        action="place",
        signal_direction=signal_direction,
        signal_units=signal_units,
        reason="direction_change" if current_net_units != 0 else "new_entry",
    )


def granularity_to_seconds(granularity: str) -> int:
    """OANDA-standard granularity → seconds.

    Falls back to 60s (M1) on unknown values so a misconfigured run
    still yields a legible signal cadence. Matches the frontend
    helper in `candles.ts` — keep in sync.
    """
    return _GRANULARITY_SECONDS.get(granularity, 60)


def align_to_granularity(now_ts: float, granularity_seconds: int) -> float:
    """Round `now_ts` UP to the next granularity boundary.

    Signal ticks fire at the top of the granularity (M1 → `:00`, H1 →
    `HH:00`) so evaluation always sees a freshly-closed bar. Rounding
    in UTC epoch-seconds keeps this DST-safe. If `now_ts` is exactly
    on a boundary we return the NEXT boundary — otherwise the tick
    would fire twice in the same instant.
    """
    if granularity_seconds <= 0:
        return now_ts
    remainder = now_ts % granularity_seconds
    return now_ts + (granularity_seconds - remainder)


def pip_size_for_pair(pair: str) -> float:
    """0.01 for JPY crosses, 0.0001 for majors.

    Mirrors the helper in `services/backtest.py`. Extracted here so
    the runner doesn't drag a full backtest import chain just for pip
    location.
    """
    p = pair.upper()
    return 0.01 if p.endswith("JPY") or "_JPY" in p else 0.0001


# ─────────────────────────────────────────────────────────────────────
# Loop entry
# ─────────────────────────────────────────────────────────────────────


def _should_try_pool_on_boot(db, *, run) -> bool:
    """Return True iff this subprocess boot is safe to call
    `allocate_slot` on.

    Two paths qualify:
    1. First boot of a brand-new run (`started_at is None`). The run
       has no OANDA state yet — a fresh pool account is correct.
    2. Restart of a run that still holds a pool slot. The pool helper's
       idempotent-reuse branch returns the SAME account, preserving
       continuity.

    Everything else — restarts of pre-opt-in runs, restarts whose
    previous pool slot was released on terminal, orphan-recoveries —
    MUST NOT grab a fresh pool account. Doing so strands positions
    on the account the prior subprocess traded against (the AUD_USD
    crash loop on 2026-10-30 was exactly this: 8 pool accounts in
    2 hours, one held for 14 seconds, each subprocess crashing when
    the reconciler couldn't find positions on the new account).
    """
    from agentfx.api.models.user_credentials import (
        UserOandaAccount,
        UserOandaAccountPoolStatus,
    )

    if run.started_at is None:
        return True
    has_held_slot = (
        db.query(UserOandaAccount)
        .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
        .filter(UserOandaAccount.current_run_id == str(run.id))
        .first()
        is not None
    )
    return has_held_slot


def _ensure_started_at(run) -> None:
    """Set `run.started_at` to now IF and ONLY IF it's currently None.

    `started_at` records the run's ORIGINAL paper-trading start —
    downstream consumers (LivePnLChart X-axis, run-tenure calcs, audit
    ordering) all treat it as immutable-once-set.

    Wilson UAT 2026-07-24: the previous unconditional overwrite in
    `run_paper_run` was clobbering the anchor on every subprocess
    boot, including the lifespan orphan-recovery path that fires on
    every Railway container swap. Callers that need the current
    subprocess's boot time should read a separate field (or a log
    line); they should not repurpose `started_at`.
    """
    if run.started_at is None:
        from datetime import UTC, datetime

        run.started_at = datetime.now(UTC)


def run_paper_run(
    run_id: str,
) -> None:  # pragma: no cover — exercised via subprocess in PR-G integration tests only
    """Subprocess entry point. Bootstraps the loop.

    In production this runs inside the `spawn`-forked subprocess. The
    parent watchdog observes exit code and DB state; anything that
    raises here bubbles as a non-zero exit and the watchdog flips the
    run to ERROR with `stop_reason=process_crashed`.

    Kept thin — the real logic is in `_execute_loop`, which takes
    injectables (OandaClient, strategy, candle_fetch_fn, sleep) so
    tests can exercise it without pulling real DSL / DB / OANDA
    infrastructure.
    """
    # Lazy imports so this module can be imported without pulling
    # SQLAlchemy or the OANDA HTTP client (unit tests hit
    # `decide_action` and `_snapshot_balance_if_needed` directly).
    from agentfx.adapters.oanda import OandaClient
    from agentfx.agents.fleet.account_pool import SlotAllocation, allocate_slot
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.core.secrets import decrypt_secret
    from agentfx.api.models import Run, RunStatus, UserOandaCredentials
    from agentfx.api.models.user_credentials import OandaEnvironment
    from agentfx.core.dsl import StrategySpec, compile_spec

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).one_or_none()
        if run is None:
            logger.error("paper runner: run %s not found; exiting", run_id)
            return

        # Try to acquire a credential + account from the fleet pool
        # first (AFX-7 wiring). The pool is derived from
        # UserOandaAccount rows under credentials with
        # `allow_fleet_pool_use=True`. On empty pool / cap hit / any
        # other refusal, fall through to the owner's own credentials
        # exactly as before — the pool is additive, never a blocker.
        #
        # Restart safety (2026-10-31 incident): pool allocation on a
        # subprocess boot is only safe when EITHER (a) this is the run's
        # very first boot (started_at is None), OR (b) the run already
        # holds a pool slot (so `allocate_slot`'s idempotent-reuse branch
        # returns the same account_id). Any other restart — including
        # recovery of a run that pre-dated the opt-in or whose previous
        # pool slot was released on terminal — must NOT grab a fresh
        # pool account, because that would strand positions on the
        # account the earlier subprocess was trading against. On those
        # restarts we skip the pool entirely and fall back to the
        # run's stored `oanda_account_id` + owner creds.
        api_key: str | None = None
        env_value: str | None = None
        account_id: str | None = None
        used_pool = False

        is_first_boot = run.started_at is None
        should_try_pool = _should_try_pool_on_boot(db, run=run)

        if should_try_pool:
            allocation = allocate_slot(
                db, run_id=run_id, environment=OandaEnvironment.PRACTICE
            )
        else:
            allocation = SlotAllocation(
                accepted=False, reason="restart_without_held_slot_skipped"
            )
            logger.info(
                "paper runner: run %s restart without held pool slot — "
                "skipping pool allocation to preserve account_id continuity "
                "(stored oanda_account_id=%s)",
                run_id,
                run.oanda_account_id,
            )

        if allocation.accepted:
            api_key = allocation.api_key_plaintext
            env_value = allocation.environment
            account_id = allocation.account_id
            run.oanda_account_id = account_id
            db.commit()
            used_pool = True
            # NB: never log api_key — just the identifiers.
            logger.info(
                "paper runner: run %s using pool slot account_id=%s env=%s "
                "(first_boot=%s)",
                run_id,
                account_id,
                env_value,
                is_first_boot,
            )
        elif should_try_pool:
            # Only log a "refused" diagnostic when we actually ASKED the
            # pool. The restart-skip path above has its own log line.
            logger.info(
                "paper runner: run %s pool allocation refused (%s); "
                "falling back to owner UserOandaCredentials",
                run_id,
                allocation.reason,
            )

        if not used_pool:
            # Load user's credentials and decrypt the API key. If the
            # credentials were revoked between approval and start, halt
            # cleanly with a specific stop reason.
            creds = (
                db.query(UserOandaCredentials)
                .filter(UserOandaCredentials.user_id == run.user_id)
                .one_or_none()
            )
            if creds is None:
                _mark_terminal(db, run, RunStatus.ERROR, "CREDENTIALS_REVOKED")
                return

            try:
                api_key = decrypt_secret(creds.oanda_api_key_encrypted)
            except Exception:  # noqa: BLE001
                logger.exception("paper runner: decrypt failed for run %s", run_id)
                _mark_terminal(db, run, RunStatus.ERROR, "CREDENTIALS_DECRYPT_FAILED")
                return

            env_value = (
                creds.oanda_environment.value
                if hasattr(creds.oanda_environment, "value")
                else str(creds.oanda_environment)
            )
            account_id = run.oanda_account_id

        client = OandaClient(
            api_key=api_key,
            environment=env_value,
            account_id=account_id,
        )

        # Compile the strategy from the deployed genome's DSL. The
        # `dsl_json` is copied onto `run.parameters` at approve time so
        # a subsequent genome edit doesn't retroactively change what
        # the runner executes.
        params = run.parameters or {}
        dsl_json = params.get("dsl_json")
        granularity = params.get("granularity") or "M1"
        instrument = params.get("instrument") or run.pair
        strategy = None
        if dsl_json is not None:
            try:
                spec = StrategySpec.model_validate(dsl_json)
                strategy = compile_spec(spec, pip_size=pip_size_for_pair(instrument))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "paper runner: DSL compile failed for %s; running monitoring-only",
                    run_id,
                )
                strategy = None

        run.status = RunStatus.RUNNING
        _ensure_started_at(run)
        db.commit()

        _execute_loop(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=_default_candle_fetch(db),
            instrument=instrument,
            granularity_seconds=granularity_to_seconds(granularity),
        )
    finally:
        db.close()


def _default_candle_fetch(
    db,
) -> Callable[
    ..., pd.DataFrame
]:  # pragma: no cover — thin adapter, exercised by integration test
    """Return a `(instrument, granularity, *, bars | from_time, to_time)
    -> DataFrame` closure that pulls candles from the shared `candles`
    table.

    Two calling modes:

    - `bars=N` (legacy): return the last N closed candles. Used by the
      pre-Fix-A rolling-window path and by tests that mock candles by
      count.
    - `from_time=T`, optional `to_time=U` (Wilson UAT 2026-07-29 Fix A):
      return all closed candles in the closed interval `[T, U]`. Used
      by the fold-anchored signal path so cumulative indicators (OBV,
      ad_line) cumulate over exactly the same window backtest would.

    Extracted as a factory so `_execute_loop` doesn't take a DB
    dependency directly and unit tests inject their own arrays.
    """
    from agentfx.api.services.market_data import market_data_service

    def _fetch(
        instrument: str,
        granularity: str,
        bars: int | None = None,
        *,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ):
        import pandas as pd

        if from_time is not None:
            # Time-range mode (Wilson UAT 2026-07-29 Fix A). Fetch every
            # closed candle in [from_time, to_time]. Cap at a generous
            # limit; bounded above by fold_length (~22d M1 = 31,680 bars).
            # `get_candles` orders ASC when from_time is set, so no
            # reversal needed here — already chronological.
            candles, _total = market_data_service.get_candles(
                db=db,
                exchange="oanda",
                instrument=instrument,
                timeframe=granularity,
                from_time=from_time,
                to_time=to_time,
                limit=100_000,
            )
        else:
            # Legacy last-N-bars mode. Fetch one extra so if the newest
            # row is still-in-flight (`complete = False`) we drop it and
            # still return `bars` closed bars. Signals MUST run on
            # closed OHLC only — an in-flight bar's close moves
            # tick-by-tick, so evaluating on it produces non-
            # deterministic entries that don't match backtest.
            # `get_candles` without from_time returns DESC (newest-first);
            # we reverse below to hand `generate_signals` chronological
            # order.
            candles, _total = market_data_service.get_candles(
                db=db,
                exchange="oanda",
                instrument=instrument,
                timeframe=granularity,
                limit=(bars or DEFAULT_LOOKBACK_BARS) + 1,
            )
            candles = list(reversed(candles))
        candles = [c for c in candles if c.complete]
        if not candles:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"]
            ).astype(float)
        return pd.DataFrame(
            {
                "open": [c.open for c in candles],
                "high": [c.high for c in candles],
                "low": [c.low for c in candles],
                "close": [c.close for c in candles],
                "volume": [c.volume or 0.0 for c in candles],
            },
            index=pd.DatetimeIndex([c.timestamp for c in candles], tz="UTC"),
        )

    return _fetch


def _fold_anchor_from_time(
    *,
    run,
    db,
    now_utc: datetime,
) -> tuple[datetime | None, int]:
    """Compute the effective from_time for signal-tick candle fetch,
    walk-forward-fold style.

    Wilson UAT 2026-07-29 Fix A. The DSL evaluator was fed only the
    last 500 candles per tick; for cumulative-sum indicators (OBV,
    ad_line) this produced completely different values than backtest
    saw during validation, because those indicators anchor to bar 0
    of whatever DataFrame you hand them. Backtest and training used
    fold-anchored windows (each OOS fold ~22 days, its own bar-0);
    live paper was using an ~8h rolling window. Result: 42% of bars
    disagreed on `obv < 0` and paper opened trades on bars backtest
    would never have entered.

    Fix: mirror validation-fold semantics. Every `fold_length_days`
    (stored on `run.parameters` at deploy time from the genome's OOS
    fold length), reset the anchor to that fold boundary. Fetch from
    the current fold's start, so OBV cumulates over the same window
    length the genome was validated on.

    Boundary hold-through: only advance the current fold pointer
    when the position is FLAT. Matches walk-forward training's
    clean-slate-per-fold semantics — no position was ever carried
    across a fold boundary in training. If we're holding when the
    calendar rolls, keep the previous anchor until the position
    closes naturally (SL/TP/opposite-flip/max-hold), then roll on
    the next signal tick.

    Returns `(from_time, current_fold_index)`. Caller passes
    `from_time` to `candle_fetch_fn`.
    """
    from agentfx.api.models import Trade

    # Defensive: some legacy tests + very early Run rows lack
    # `approved_at`. Callers should fall back to the bar-count fetch
    # (mode signalled by returning (None, 0)) — fold-anchoring requires
    # a run start to anchor against.
    approved = getattr(run, "approved_at", None)
    if approved is None:
        return None, 0

    params = getattr(run, "parameters", None) or {}
    fold_days = int(params.get("fold_length_days") or 30)
    if approved.tzinfo is None:
        approved = approved.replace(tzinfo=UTC)

    days_since_approved = (now_utc - approved).total_seconds() / 86400
    target_fold_index = max(0, int(days_since_approved // fold_days))
    current_fold_index = int(params.get("current_fold_index") or 0)

    if target_fold_index > current_fold_index:
        has_open = (
            db.query(Trade)
            .filter(
                Trade.run_id == run.id,
                Trade.exit_timestamp.is_(None),
            )
            .count()
            > 0
        )
        if not has_open:
            # Safe to advance.
            current_fold_index = target_fold_index
            run.parameters = {**params, "current_fold_index": current_fold_index}
            db.commit()

    from_time = approved + timedelta(days=current_fold_index * fold_days)
    return from_time, current_fold_index


# ─────────────────────────────────────────────────────────────────────
# Loop body
# ─────────────────────────────────────────────────────────────────────


def _execute_loop(
    *,
    db,
    run,
    client,
    sleep_fn=time.sleep,
    now_fn: Callable[[], float] | None = None,
    max_iterations: int | None = None,
    strategy=None,
    candle_fetch_fn: Callable[[str, str, int], pd.DataFrame] | None = None,
    instrument: str | None = None,
    granularity_seconds: int = 60,
) -> None:
    """Body of the paper-runner main loop.

    Extracted from `run_paper_run` so tests can drive it with a mocked
    `client`, a mocked candle fetch, and a bounded `max_iterations`.
    Production callers pass `max_iterations=None` (unbounded) and rely
    on `kill_requested_at` or a terminal risk-gate refusal to exit.

    When `strategy is None` OR `candle_fetch_fn is None`, the signal
    tick is skipped entirely — the loop degrades to the v0.6.0
    monitoring-only shape. Every unit test that only exercises the
    kill / gate / balance paths runs in this mode.
    """
    from agentfx.adapters.oanda import OandaCircuitOpen
    from agentfx.api.models import RunStatus
    from agentfx.services.paper_policy import (
        RiskState,
        check_kill_switch_dd,
        check_loss_cap,
    )

    if now_fn is None:  # pragma: no cover — trivial

        def _wall_clock() -> float:
            return datetime.now(UTC).timestamp()

        now_fn = _wall_clock

    signal_enabled = (
        strategy is not None and candle_fetch_fn is not None and instrument is not None
    )
    # Wilson UAT 2026-08-02: signal ticks are offset PAST the granularity
    # boundary so ingestion has time to write the just-closed bar to
    # the DB before the runner evaluates. Without this offset, the tick
    # fires at :00 while ingestion doesn't write the bar until :03-:09
    # → runner sees stale df, misses the bar, then Fix D's watermark
    # catches it 60s later. With the offset, tick fires at :10, bar is
    # already in DB, order lands within seconds of the signal.
    # `SIGNAL_TICK_OFFSET_SECONDS` from settings — configurable via
    # Railway env var. Must be greater than
    # `INGESTION_ALIGNMENT_BUFFER_SECONDS` + typical cycle work.
    from agentfx.api.core.config import settings

    _tick_offset = float(settings.SIGNAL_TICK_OFFSET_SECONDS)
    next_signal_at = (
        align_to_granularity(now_fn(), granularity_seconds) + _tick_offset
        if signal_enabled
        else float("inf")
    )

    _snapshot_balance_if_needed(db=db, run=run, client=client)

    # Consecutive non-auth OANDA failures on the balance refresh. A
    # single 15s read-timeout no longer crashes the subprocess (Wilson
    # 2026-08-14): the tick is skipped, the loop retries next iteration,
    # and only after `_BALANCE_RETRY_LIMIT` failures in a row do we halt
    # cleanly with `BROKER_NETWORK`. Reset to 0 on any successful call.
    consecutive_balance_failures = 0

    # Wilson 2026-08-24: subprocess heartbeat. Persisted to
    # `run.parameters['last_heartbeat_at']` every ~5 minutes of
    # healthy iteration (measured in iterations, not wall time, so
    # tests that inject a pre-baked now_fn iterator don't run out).
    # The supervisor uses this + `process_pid` to detect zombie
    # RUNNING rows (subprocess dead but DB status still RUNNING) and
    # restart them. Also grep-able signature in Railway logs — the
    # absence of "paper runner heartbeat" lines after N minutes is
    # proof of silent death. Loop iterates every ~5s so 60 = ~5 min.
    _HEARTBEAT_INTERVAL_ITERATIONS = 60

    iteration = 0
    while True:
        if max_iterations is not None and iteration >= max_iterations:
            logger.info(
                "paper runner: %s reached test iteration cap %d",
                run.id,
                max_iterations,
            )
            return
        iteration += 1

        # ─── Health tick ─────────────────────────────────────────────
        # Reload the row so we see kill flags set by the router in
        # another process.
        db.refresh(run)
        if run.kill_requested_at is not None:
            logger.info("paper runner: kill flag observed on %s; closing", run.id)
            _flatten_and_halt(db, run, client, reason="USER_KILL")
            return

        # Fetch account state. A broker circuit-open here means the
        # OANDA client is refusing to hit the network — treat as a
        # terminal halt with the specific reason instead of letting
        # the exception escalate to a process crash. We still call
        # `_flatten_and_halt` (best-effort close) even though the
        # first request will bounce; the runner logs the failure and
        # the run row still transitions to ERROR.
        #
        # 401 handling (Wilson UAT 2026-07-15): the previous
        # subprocess crashed on 401 after 3.5h — a diagnostic run
        # against the same code path with the same stored token
        # returned 200 for all calls. Most-likely cause was either
        # transient OANDA-side 401 or a long-lived Session state
        # issue. Recovery: try one credential refresh (in case the
        # user re-verified the token in the UI while the run was
        # live) with a fresh Session; if the second call still 401s,
        # halt cleanly with a specific stop_reason so the frontend
        # can prompt the user to re-verify.
        from agentfx.adapters.oanda import OandaError

        try:
            balance = client.get_account_balance()
        except OandaCircuitOpen:
            logger.warning("paper runner: %s got OandaCircuitOpen; halting", run.id)
            _flatten_and_halt(db, run, client, reason="BROKER_CIRCUIT")
            return
        except OandaError as exc:
            if _is_auth_error(exc):
                logger.warning(
                    "paper runner: %s got 401 on balance; attempting cred refresh",
                    run.id,
                )
                new_client = _recover_from_auth_loss(db, run, exc)
                if new_client is None:
                    logger.error(
                        "paper runner: %s auth-refresh failed; halting oanda_auth_lost",
                        run.id,
                    )
                    _flatten_and_halt(db, run, client, reason="OANDA_AUTH_LOST")
                    return
                client = new_client
                balance = client.get_account_balance()
            else:
                # Non-auth OANDA failure — most commonly a read timeout
                # after the adapter's own 5-retry chain gave up
                # (`connection_error:` prefix). Wilson 2026-08-14: this
                # used to `raise` and kill the subprocess; the runner
                # now skips the tick and retries next iteration. If it
                # keeps failing we halt cleanly via BROKER_NETWORK
                # instead of leaving the position stranded on OANDA.
                consecutive_balance_failures += 1
                if consecutive_balance_failures >= _BALANCE_RETRY_LIMIT:
                    logger.error(
                        "paper runner: %s balance refresh failed %d times "
                        "in a row (%s); halting broker_network",
                        run.id,
                        consecutive_balance_failures,
                        exc,
                    )
                    _flatten_and_halt(db, run, client, reason="BROKER_NETWORK")
                    return
                sleep_seconds = _balance_retry_sleep_seconds(
                    consecutive_balance_failures
                )
                logger.warning(
                    "paper runner: %s balance refresh failed (%s); "
                    "skipping tick (%d/%d), backing off %.0fs",
                    run.id,
                    exc,
                    consecutive_balance_failures,
                    _BALANCE_RETRY_LIMIT,
                    sleep_seconds,
                )
                sleep_fn(sleep_seconds)
                continue
        consecutive_balance_failures = 0
        run.current_balance = balance["balance"]
        run.pnl = balance["balance"] - (run.snapshot_balance or 0.0)
        db.commit()

        # Heartbeat: log INFO + persist `last_heartbeat_at` every
        # ~5 min (60 iterations at 5s cadence) of healthy iteration.
        # Supervisor uses this to detect zombie RUNNING rows and
        # restart them. Emitted on iteration 1 too so a fresh
        # subprocess registers a heartbeat immediately.
        if iteration == 1 or iteration % _HEARTBEAT_INTERVAL_ITERATIONS == 0:
            heartbeat_iso = datetime.now(UTC).isoformat()
            logger.info(
                "paper runner heartbeat: run=%s iter=%d balance=%.2f pnl=%.2f",
                run.id,
                iteration,
                run.current_balance or 0.0,
                run.pnl or 0.0,
            )
            try:
                params = dict(run.parameters or {})
                params["last_heartbeat_at"] = heartbeat_iso
                run.parameters = params
                from sqlalchemy.orm.attributes import flag_modified as _flag_hb

                _flag_hb(run, "parameters")
                db.commit()
            except Exception:  # noqa: BLE001 — never let heartbeat crash the loop
                logger.exception(
                    "paper runner: heartbeat persist failed on %s (loop continues)",
                    run.id,
                )

        # Evaluate risk gates against the current balance. The health
        # tick only checks halt-gates (kill_switch, loss cap);
        # position-size gating happens at order-placement time inside
        # `_signal_tick` where the real proposed units are known.
        # v0.6.2 (Wilson UAT 2026-07-19): previously called
        # `evaluate_all(state, proposed_units=state.max_position_size_units)`
        # which compared max against itself — a no-op check that
        # forced legacy runs to carry a stale `max_position_size_units`
        # value even though sizing is fully dynamic.
        risk_cfg = run.risk_config or {}
        state = RiskState(
            snapshot_balance=run.snapshot_balance or 0.0,
            current_balance=run.current_balance,
            max_position_size_units=int(
                risk_cfg.get("max_position_size_units") or 10_000_000
            ),
            loss_cap_pct=float(risk_cfg.get("loss_cap_pct", 2.0)),
            kill_switch_dd_pct=float(risk_cfg.get("kill_switch_dd_pct", 5.0)),
        )
        for check in (check_kill_switch_dd(state), check_loss_cap(state)):
            if not check.ok:
                logger.info(
                    "paper runner: gate refused on %s (%s); halting",
                    run.id,
                    check.reason,
                )
                _flatten_and_halt(db, run, client, reason=check.reason)
                return

        # ─── Broker transaction reconciliation (T1 — every 12 ticks) ─
        # Every ~60s, poll OANDA's authoritative transaction log for
        # any events we missed between polls (broker-side SL/TP fires,
        # margin closeouts, etc.). Idempotent — safe on every tick,
        # cheap enough at 60s cadence. Best-effort: any error here
        # doesn't stop the runner; the next iteration re-tries.
        if iteration % 12 == 0:
            try:
                from agentfx.services.paper_reconciler import (
                    reconcile_broker_transactions,
                )

                result = reconcile_broker_transactions(db=db, run=run, client=client)
                if result.trades_updated > 0:
                    logger.info(
                        "reconciler: run %s updated %d trade(s) from %d txn(s)",
                        run.id,
                        result.trades_updated,
                        result.transactions_seen,
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "reconciler: failed on run %s (will retry next tick)", run.id
                )

        # ─── Signal tick (only when past the boundary) ───────────────
        now = now_fn()
        if signal_enabled and now >= next_signal_at:
            try:
                _signal_tick(
                    db=db,
                    run=run,
                    client=client,
                    strategy=strategy,
                    candle_fetch_fn=candle_fetch_fn,
                    instrument=instrument,
                    granularity_seconds=granularity_seconds,
                    now_utc=datetime.fromtimestamp(now, tz=UTC),
                )
            except Exception:  # noqa: BLE001
                # Signal work is best-effort — a compilation failure /
                # arithmetic overflow / OANDA network blip mustn't take
                # the whole runner down. Log and continue; the health
                # tick keeps the kill switch responsive.
                logger.exception(
                    "paper runner: signal tick failed on %s; skipping this bar",
                    run.id,
                )
            # Advance to the NEXT boundary strictly after `now`.
            # `align_to_granularity` handles both cases: if `now` is
            # exactly on a boundary it returns `now + granularity` (so
            # we don't re-fire the current tick); otherwise it rounds
            # up to the next boundary.
            #
            # BUG FIXED 2026-07-15 (Wilson UAT): previously we did
            # `align(now + granularity, granularity)` — that overshoots
            # by one bar. If tick fires at 12:47:00.230, `now + 60` is
            # 12:48:00.230 which rounds up to 12:49:00 — skipping the
            # 12:48 boundary. Result: every M1 tick was on 2-minute
            # cadence, missing half the signals. Wilson lost the 13:03
            # MACD cross_up because the 13:06 catch-up tick was skipped.
            next_signal_at = (
                align_to_granularity(now, granularity_seconds) + _tick_offset
            )

        # Cadence — health tick every 5s, or sooner if the next signal
        # tick is closer. Injectable `sleep_fn` so tests skip the wait
        # entirely.
        if signal_enabled:
            sleep_seconds = min(5.0, max(0.1, next_signal_at - now_fn()))
        else:
            sleep_seconds = 5.0
        sleep_fn(sleep_seconds)

        if max_iterations is None and run.status != RunStatus.RUNNING:
            # Something outside the loop terminated the run — respect
            # it and exit.
            return


# ─────────────────────────────────────────────────────────────────────
# Signal tick (US-024)
# ─────────────────────────────────────────────────────────────────────


def _emit_stale_transition(
    *,
    db,
    run,
    reason: str,
    outputs_extra: dict | None = None,
) -> None:
    """Write USER_SIGNAL_SKIPPED_STALE only on the entry-into-stale
    transition. Subsequent stale ticks are silent.

    State is tracked in `run.parameters['stale_active']`. `_clear_stale`
    (below) emits USER_SIGNAL_RESUMED and clears the flag when a tick
    finds fresh candles again. Wilson UAT 2026-07-19: without this the
    weekend produced 60h × 60 ticks/h = ~3600 identical STALE entries,
    burying every meaningful decision.
    """
    params = dict(run.parameters or {})
    if params.get("stale_active"):
        return
    params["stale_active"] = True
    # Re-assign a fresh dict so SQLAlchemy's ORM change detection picks
    # up the JSON column mutation without needing `flag_modified`.
    run.parameters = params
    outputs = {"paper_run_id": run.id}
    if outputs_extra:
        outputs.update(outputs_extra)
    _write_decision(
        db,
        run,
        action="USER_SIGNAL_SKIPPED_STALE",
        reason=reason,
        outputs=outputs,
    )


def _clear_stale_state(*, db, run, latest_candle_at: str) -> None:
    """Called from the successful-signal path to log the resume from a
    previous stale window. Mirror of `_emit_stale_transition`."""
    params = dict(run.parameters or {})
    if not params.get("stale_active"):
        return
    params["stale_active"] = False
    run.parameters = params
    _write_decision(
        db,
        run,
        action="USER_SIGNAL_RESUMED",
        reason=f"fresh candles arrived — latest {latest_candle_at}",
        outputs={
            "paper_run_id": run.id,
            "latest_candle_at": latest_candle_at,
        },
    )


def _count_bars_since(
    *,
    db,
    instrument: str,
    granularity: str,
    entry_ts: datetime,
    now_utc: datetime,
) -> int:
    """Count completed candles between `entry_ts` (exclusive) and
    `now_utc` (inclusive) for the given instrument+granularity.

    Backtest's `_apply_exits` measures `bars_held` as the integer
    index count within the OHLCV series (t - entry_bar). Paper's
    equivalent is the row count in the shared candles table over the
    same window. Weekend gaps and ingestion outages don't advance the
    counter — same semantics as backtest.

    Excludes the trade's entry bar itself so bars_held starts at 0
    at open and increments once per closed bar.

    Only counts `complete=True` rows so the currently-forming bar
    (partially aggregated by the ingester) isn't over-counted.
    """
    from agentfx.api.models.market_data import Candle

    return (
        db.query(Candle)
        .filter(
            Candle.instrument == instrument,
            Candle.timeframe == granularity,
            Candle.timestamp > entry_ts,
            Candle.timestamp <= now_utc,
            Candle.complete.is_(True),
        )
        .count()
    )


def _signal_tick(
    *,
    db,
    run,
    client,
    strategy,
    candle_fetch_fn: Callable[[str, str, int], pd.DataFrame],
    instrument: str,
    granularity_seconds: int,
    now_utc: datetime,
) -> None:
    """One granularity-boundary tick: fetch → walk → act.

    v0.7.0 restructure (Wilson UAT 2026-08-06 fidelity fix):

    The tick now delegates all state-machine logic — SL, TP, min_hold,
    max_hold, opposite_flip, fresh entry — to `paper_walk.walk()`,
    which is a straight port of backtest's `_apply_exits`. The tick's
    only job is:

        1. Guards that early-return without processing (kill / stale).
        2. Fetch candles + derive raw_entries.
        3. Build the walk's starting state from broker + open trade.
        4. Call walk() over `bars > bookmark`.
        5. Reconcile the walk's final state against the broker.
        6. Advance the bookmark unconditionally.
        7. Persist an equity snapshot.

    Fixes the previous bug where the runner queued signals during
    min_hold (bookmark stayed frozen while the shortcut early-returned)
    and then fired them AFTER min_hold expired — behaviour backtest
    never exhibits. See `services/paper_walk.py` docstring.
    """
    from agentfx.api.models import RunStatus, Trade
    from agentfx.services.paper_walk import WalkState, walk

    if run.status != RunStatus.RUNNING:
        return

    granularity = _seconds_to_granularity(granularity_seconds)

    # Spec + exit rules — needed for both the walk and for the tick's
    # own diagnostic logging. If the strategy isn't a DSL-compiled
    # spec (legacy tests may still pass classic strategies), degrade
    # to a no-op: this restructure only supports DSL specs.
    spec = getattr(strategy, "spec", None)
    exit_block = getattr(spec, "exit", None) if spec is not None else None

    # Min-hold gate: if the position has held fewer than
    # min_hold_bars completed bars, suppress every exit path this
    # tick — matches backtest exactly. Silent no-op (no decision log
    # spam) since it fires every tick during the hold window. Equity
    # snapshot is still recorded so the LivePnLChart stays continuous.
    # v0.7.0 restructure: the min_hold and max_hold gates that used to
    # early-return here are now applied per-bar INSIDE `walk()`, matching
    # backtest's `_apply_exits`. The previous approach of early-returning
    # without advancing the watermark caused signals fired during
    # min_hold to be queued and re-fired when min_hold expired — a
    # divergence from backtest never intended by the state machine.
    # See src/agentfx/services/paper_walk.py.

    # Wilson UAT 2026-07-29 Fix A: cumulative indicators (OBV,
    # ad_line) need to see the same window backtest cumulated over.
    # Compute the fold-anchored `from_time` and fetch the closed-
    # candle range from that anchor to now. Legacy runs without
    # `approved_at` (or fetch functions that don't accept from_time,
    # e.g. test doubles) fall back to the last-N-bars mode.
    from_time, _fold_idx = _fold_anchor_from_time(run=run, db=db, now_utc=now_utc)
    if from_time is not None:
        try:
            df = candle_fetch_fn(
                instrument,
                granularity,
                from_time=from_time,
                to_time=now_utc,
            )
        except TypeError:
            df = candle_fetch_fn(instrument, granularity, DEFAULT_LOOKBACK_BARS)
    else:
        df = candle_fetch_fn(instrument, granularity, DEFAULT_LOOKBACK_BARS)

    # Stale-candle guard. Ingestion outages (weekend, OANDA maintenance,
    # ingester falling behind) leave the shared `candles` table N bars
    # behind wall-clock. We skip signal evaluation rather than halt —
    # the runner resumes on next tick when fresh data lands.
    #
    # Wilson UAT 2026-07-19: over a 60h weekend the decision log
    # accumulated ~3600 identical STALE entries (one per tick), burying
    # every real event. Only log on state TRANSITIONS: enter-stale
    # once, exit-stale once, tracked via
    # `run.parameters['stale_state']`.
    if df.empty:
        _emit_stale_transition(
            db=db,
            run=run,
            reason=f"no candles returned for {instrument} {granularity}",
            outputs_extra={"reason_detail": "empty_candles"},
        )
        return
    latest_ts = df.index[-1]
    if hasattr(latest_ts, "to_pydatetime"):
        latest_dt = latest_ts.to_pydatetime()
    else:  # pragma: no cover — defensive
        latest_dt = latest_ts
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.replace(tzinfo=UTC)
    # OANDA labels bars by open-time; the latest CLOSED bar's open
    # timestamp is `now - granularity` at best (ingestion catches up
    # instantly) and `now - granularity - ingest_lag` at worst. Trip
    # only when the gap exceeds `granularity + ingest_lag_tolerance`
    # — that isolates real ingester outages from the normal 30-80s
    # OANDA/ingester lag. Uniform tolerance across granularities
    # since the lag is set by the ingester's poll cadence, not by bar
    # length. See UAT observation 2026-07-13 (Wilson).
    stale_threshold_seconds = granularity_seconds + INGEST_LAG_TOLERANCE_SECONDS
    if (now_utc - latest_dt).total_seconds() > stale_threshold_seconds:
        _emit_stale_transition(
            db=db,
            run=run,
            reason=(
                f"latest candle {latest_dt.isoformat()} is older than "
                f"{granularity} + {INGEST_LAG_TOLERANCE_SECONDS}s "
                f"(={stale_threshold_seconds}s) — ingestion may be behind"
            ),
            outputs_extra={
                "latest_candle_at": latest_dt.isoformat(),
                "now": now_utc.isoformat(),
                "threshold_seconds": stale_threshold_seconds,
            },
        )
        return

    # Fresh candles → clear any previous stale flag and log resume.
    _clear_stale_state(db=db, run=run, latest_candle_at=latest_dt.isoformat())

    # Raw entry events at bar-close: +1 = long cross fired at bar T,
    # -1 = short cross fired, 0 = no event. This replaces
    # `generate_signals` (which projected `_apply_exits` state across
    # the full lookback window). Wilson's UAT 2026-07-13 finding: the
    # projected-state model treated a hypothetical pre-deploy trade as
    # "we're currently long" and refused every subsequent cross while
    # `opposite_signal: false` prevented state re-transitions, so paper
    # deployments would idle for hours. Under the event model the runner
    # only reacts to entry crosses that fire post-deploy, and exits are
    # handled by (a) OANDA-side SL/TP orders placed at entry time and
    # (b) the max_hold check below.
    raw_entries = strategy.raw_entry_signals(df)
    if len(raw_entries) < 1:
        return

    # ── Load state ────────────────────────────────────────────────
    # Open trade (for entry price + entry timestamp).
    open_trade = (
        db.query(Trade)
        .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
        .order_by(Trade.entry_timestamp.asc())
        .first()
    )
    # Broker position.
    position = client.get_position(instrument)
    long_units = int(position.get("long_units", 0) or 0)
    short_units = int(position.get("short_units", 0) or 0)
    net_units = long_units + short_units

    # Broker-side SL/TP reconciliation. OANDA's SL/TP orders (placed
    # at entry time) fire on any tick, not at bar close. If they
    # filled between our previous tick and this one, OANDA position
    # went from open to 0 without the runner initiating close, and
    # our open Trade row is stranded with exit_timestamp=None forever.
    # Detect and reconcile BEFORE the walk so the walk sees the
    # correct starting state.
    if net_units == 0 and open_trade is not None:
        _reconcile_broker_side_close(
            db=db,
            run=run,
            strategy=strategy,
            latest_close=float(df["close"].iloc[-1]),
            latest_high=float(df["high"].iloc[-1]),
            latest_low=float(df["low"].iloc[-1]),
            now_utc=now_utc,
        )
        # Reconciler may have closed the open_trade row — re-query so
        # the walk starts from the correct state.
        open_trade = (
            db.query(Trade)
            .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
            .order_by(Trade.entry_timestamp.asc())
            .first()
        )

    # ── Build walk state ─────────────────────────────────────────
    params = getattr(run, "parameters", None) or {}
    last_eval_iso = params.get("last_evaluated_bar_ts")
    bookmark_ts: datetime | None = (
        datetime.fromisoformat(last_eval_iso) if last_eval_iso else None
    )
    if bookmark_ts is not None and bookmark_ts.tzinfo is None:
        bookmark_ts = bookmark_ts.replace(tzinfo=UTC)

    in_position_start = 1 if net_units > 0 else (-1 if net_units < 0 else 0)
    entry_close_start: float | None = None
    bars_held_start = 0
    if in_position_start != 0:
        if open_trade is not None:
            entry_close_start = float(open_trade.entry_price)
            # Bars held so far uses the DB row-count semantics
            # `_count_bars_since` provides — matches the pre-refactor
            # `bars_held_early` computation exactly (weekend gaps
            # correctly don't advance the counter).
            entry_ts_ts = open_trade.entry_timestamp
            if entry_ts_ts.tzinfo is None:
                entry_ts_ts = entry_ts_ts.replace(tzinfo=UTC)
            bars_held_start = _count_bars_since(
                db=db,
                instrument=instrument,
                granularity=granularity,
                entry_ts=entry_ts_ts,
                now_utc=now_utc,
            )
        else:
            # Broker has position but no open_trade row (data-integrity
            # edge case; tests exercise it as a broker-view-only setup).
            # Fall back to latest bar close as the walk's entry_close so
            # the state machine can still make same/opposite dispatch
            # decisions. SL/TP evaluated against this synthesized entry
            # will be approximate but the mismatch only matters until
            # the next real entry gives us a real entry_close.
            entry_close_start = float(df["close"].iloc[-1])
            bars_held_start = 0

    start_state = WalkState(
        in_position=in_position_start,  # type: ignore[arg-type]
        entry_close=entry_close_start,
        bars_held=bars_held_start,
    )

    pip_size = float(getattr(strategy, "pip_size", 0.0001) or 0.0001)
    close_series = df["close"].astype(float)
    # High/low are only used when exit_block.eval_mode == "wick";
    # pass unconditionally when available so the walk decides.
    high_series = df["high"].astype(float) if "high" in df.columns else None
    low_series = df["low"].astype(float) if "low" in df.columns else None

    # ── Walk ─────────────────────────────────────────────────────
    result = walk(
        raw_entries=raw_entries,
        close_series=close_series,
        start_state=start_state,
        bookmark_ts=bookmark_ts,
        exit_rules=exit_block,
        pip_size=pip_size,
        high_series=high_series,
        low_series=low_series,
    )

    # ── Advance bookmark UNCONDITIONALLY on any successful tick ──
    # This is the fix. Previously the min_hold gate returned early
    # without touching the bookmark, so signals from bars during the
    # hold accumulated and fired en masse when the gate opened. Now
    # walk() decides per-bar (respecting min_hold, matching backtest)
    # and the bookmark advances every tick, so nothing accumulates.
    if result.last_bar_ts is not None:
        latest_iso = (
            result.last_bar_ts.isoformat()
            if isinstance(result.last_bar_ts, datetime)
            else str(result.last_bar_ts)
        )
        if latest_iso != last_eval_iso:
            run.parameters = {**params, "last_evaluated_bar_ts": latest_iso}
            db.commit()

    # ── Execute the walk's decision(s) ───────────────────────────
    exit_rules_full = spec  # for _open_position_and_persist's sig
    risk_cfg = run.risk_config or {}
    _execute_walk_transitions(
        db=db,
        run=run,
        client=client,
        strategy=strategy,
        instrument=instrument,
        df=df,
        now_utc=now_utc,
        transitions=result.transitions,
        broker_net_units=net_units,
        broker_position=position,
        exit_rules=exit_rules_full,
        risk_cfg=risk_cfg,
    )


def _open_position_and_persist(
    *,
    db,
    run,
    client,
    strategy,
    instrument: str,
    signal_direction: Direction,
    df,
    now_utc: datetime,
    exit_rules,  # spec.exit chain; the caller has already resolved to spec
    risk_cfg: dict,
    position_snapshot,
) -> None:
    """Size + place a market order + persist the trade + write the
    decision log. Extracted from `_signal_tick` verbatim so the walk-
    driven restructure has a single callsite for "open a new
    position." No behaviour change from the pre-extract inline code.

    - `signal_direction` is "long" or "short" — determines the sign of
      the order.
    - `df` is the OHLC dataframe passed to the DSL evaluator; used by
      `_signal_reason` for the audit-log indicator values.
    - `exit_rules` is the strategy's `spec` (whole spec, not just the
      exit block — matches the pre-extract naming).
    - `position_snapshot` is the broker position dict at the start of
      the tick; only used for the final equity snapshot.

    Returns None. On sizing failure, broker rejection, or other
    recoverable errors, writes an appropriate decision log entry and
    returns silently.
    """
    # Wilson 2026-08-15 audit: the strategy's DSL `stop_pips` is
    # enforced walk-side against bar CLOSE. Registering it as a
    # broker-side `stopLossOnFill` order turned it into a TOUCH-based
    # stop — an intra-bar wick fires it, but the walk (and backtest's
    # `_apply_exits`) only trigger on the bar's close. That silently
    # changes the strategy's payoff distribution vs its OOS-validated
    # backtest — every wick that would have recovered becomes a
    # locked loss.
    #
    # The broker order now carries only a WIDE fail-safe SL sized
    # from `kill_switch_dd_pct` (matches the run's hard-halt loss
    # cap), and NO broker-side TP. The walk enforces the strategy's
    # real SL + TP on close each tick; the broker SL only fires if
    # the runner is dead. See `compute_failsafe_sl_pips` for the
    # sizing formula.
    #
    # `risk_config.stop_loss_pips` / `take_profit_pips` are legacy
    # deploy-modal overrides — retained in the schema for back-compat
    # but ignored here.
    sl_pips_dsl = (
        int(exit_rules.exit.stop_pips) if exit_rules and exit_rules.exit else None
    )
    tp_pips_dsl = (
        int(exit_rules.exit.target_pips) if exit_rules and exit_rules.exit else None
    )

    # Dynamic position sizing (v0.6.1 UAT 2026-07-15 — Wilson):
    # backtest already sizes each trade off risk_pct × balance
    # (backtest.py::_dsl_position_size) with the running balance
    # compounding across the run. Paper was using a static
    # `max_position_size_units` (default 10k), completely ignoring
    # risk_pct and account balance — a 100k account was 23× under-
    # sized. Match backtest by computing units from risk_pct + current
    # balance + stop distance + account-currency-aware pip value.
    # `max_position_size_units` becomes a safety cap only.
    from agentfx.services.paper_sizing import (
        compute_failsafe_sl_pips,
        compute_position_units,
    )

    account_summary = client.get_account_balance()
    account_currency = str(account_summary.get("currency") or "USD")
    balance_account = float(
        account_summary.get("balance") or run.current_balance or 0.0
    )
    risk_pct_dsl = 0.005  # DSL grammar default (0.5%); backtest uses it too
    if exit_rules is not None and getattr(exit_rules, "size", None) is not None:
        risk_pct_dsl = float(exit_rules.size.risk_pct)
    # `max_position_size_units` semantic change (v0.6.1): now a
    # safety cap that never exceeds the DSL-driven size. Legacy runs
    # written before this shipped default to 10k which under-caps
    # any reasonably-sized account — treat 10k as "no cap set" so
    # legacy runs get the correct dynamic size on their next order.
    raw_cap = risk_cfg.get("max_position_size_units")
    safety_cap = int(raw_cap) if raw_cap and int(raw_cap) > 10_000 else None
    sizing = compute_position_units(
        instrument=instrument,
        balance=balance_account,
        account_currency=account_currency,
        risk_pct=risk_pct_dsl,
        stop_pips=float(sl_pips_dsl or 0),
        safety_cap_units=safety_cap,
        client=client,
    )
    if sizing.capped_units <= 0:
        _write_decision(
            db,
            run,
            action="USER_SIGNAL_SKIPPED_STALE",
            reason=(
                f"sizing produced 0 units — {sizing.notes} "
                f"(balance={sizing.balance:.2f} {sizing.account_currency}, "
                f"risk_pct={sizing.risk_pct}, stop_pips={sizing.stop_pips}, "
                f"pip_value={sizing.pip_value_account:.6f})"
            ),
            outputs={
                "paper_run_id": run.id,
                "sizing": sizing.__dict__,
            },
        )
        return
    signed_units = (
        sizing.capped_units if signal_direction == "long" else -sizing.capped_units
    )

    # Broker fail-safe SL, sized from the run's hard-halt loss cap
    # (kill_switch_dd_pct). Wide enough that normal price action never
    # touches it; only fires if the runner is dead. Walk enforces the
    # DSL's real SL + TP on bar close each tick.
    kill_switch_dd_pct = float(risk_cfg.get("kill_switch_dd_pct", 5.0))
    failsafe_sl_pips = compute_failsafe_sl_pips(
        balance=sizing.balance,
        units=sizing.capped_units,
        pip_value_account=sizing.pip_value_account,
        kill_switch_dd_pct=kill_switch_dd_pct,
    )

    from agentfx.adapters.oanda import OandaOrderRejected

    try:
        result = client.place_market_order(
            instrument,
            signed_units,
            stop_loss_pips=failsafe_sl_pips,
            take_profit_pips=None,
        )
    except OandaOrderRejected as exc:
        # Broker refused to fill (INSUFFICIENT_MARGIN, MARKET_HALTED,
        # POSITION_LOCKED, NO_FILL_TRANSACTION_RETURNED, etc.). Log
        # with the specific reject reason so the operator sees why —
        # don't persist a fake Trade row with a zero-fill.
        _write_decision(
            db,
            run,
            action="USER_ORDER_REJECTED",
            reason=(
                f"OANDA rejected {signal_direction.upper()} "
                f"{abs(signed_units)} {instrument}: {exc.reject_reason} "
                f"(txn={exc.transaction_id})"
            ),
            outputs={
                "paper_run_id": run.id,
                "instrument": instrument,
                "signed_units": signed_units,
                "reject_reason": exc.reject_reason,
                "reject_transaction_id": exc.transaction_id,
                "order_type": exc.order_type,
            },
        )
        _persist_equity_snapshot(
            db=db, run=run, position=position_snapshot, now_utc=now_utc
        )
        return
    fill_price = float(result.get("fill_price") or 0.0)
    filled_units = int(result.get("units") or signed_units)
    trade_id = result.get("trade_id")
    fill_transaction_id = result.get("fill_transaction_id")
    # Pass the candles DataFrame so `_signal_reason` can compute
    # actual indicator values at the trigger bar. Wilson's UAT
    # feedback (2026-07-13): "the indicator on the graph didn't seem
    # as the signal actually fired" — enriched form makes it
    # verifiable at a glance.
    signal_reason = _signal_reason(strategy, signal_direction, df)

    _persist_trade_entry(
        db=db,
        run=run,
        instrument=instrument,
        entry_price=fill_price,
        quantity=abs(filled_units),
        side="long" if filled_units > 0 else "short",
        entry_timestamp=now_utc,
        signal_reason=signal_reason,
        broker_trade_id=trade_id,
        broker_open_transaction_id=fill_transaction_id,
        entry_financing=float(result.get("financing") or 0.0),
        entry_commission=float(result.get("commission") or 0.0),
    )
    # Sizing trace so the operator can audit `why this many units` at
    # a glance — surface balance × risk_pct / (stop × pip_value) plus
    # the account currency and conversion rate used. Full breakdown
    # goes to outputs_json for programmatic inspection.
    sizing_note = (
        f"{sizing.balance:.2f} {sizing.account_currency} × {sizing.risk_pct} "
        f"/ ({sizing.stop_pips}pip × {sizing.pip_value_account:.6f}) = "
        f"{sizing.desired_units}"
    )
    if sizing.capped_units < sizing.desired_units:
        sizing_note += f" → capped at {sizing.capped_units}"
    _write_decision(
        db,
        run,
        action="USER_ORDER_PLACED",
        reason=(
            f"{signal_direction.upper()} {abs(filled_units)} {instrument} "
            f"@ {fill_price:.5f} ({signal_reason}) | sizing: {sizing_note} | "
            f"broker failsafe SL: {failsafe_sl_pips} pips "
            f"({kill_switch_dd_pct}% of balance)"
        ),
        outputs={
            "paper_run_id": run.id,
            "instrument": instrument,
            "signed_units": filled_units,
            "fill_price": fill_price,
            "broker_trade_id": trade_id,
            "signal_reason": signal_reason,
            "broker_failsafe_sl_pips": failsafe_sl_pips,
            "dsl_stop_pips": sl_pips_dsl,
            "dsl_target_pips": tp_pips_dsl,
            "sizing": {
                "balance": sizing.balance,
                "account_currency": sizing.account_currency,
                "risk_pct": sizing.risk_pct,
                "stop_pips": sizing.stop_pips,
                "pip_value_account": sizing.pip_value_account,
                "quote_to_account_rate": sizing.quote_to_account_rate,
                "desired_units": sizing.desired_units,
                "capped_units": sizing.capped_units,
                "safety_cap": sizing.safety_cap,
            },
        },
    )
    _persist_equity_snapshot(
        db=db, run=run, position=position_snapshot, now_utc=now_utc
    )


def _execute_walk_transitions(
    *,
    db,
    run,
    client,
    strategy,
    instrument: str,
    df,
    now_utc: datetime,
    transitions: list,
    broker_net_units: int,
    broker_position,
    exit_rules,  # StrategySpec (whole spec — passed through to _open_position_and_persist)
    risk_cfg: dict,
) -> None:
    """Execute the walk's final decision against the broker.

    Design (Wilson UAT 2026-08-06):
    - Zero transitions → walk decided nothing changed; equity snapshot
      and return.
    - One transition → execute it (open / close / flip).
    - Many transitions (multi-bar catch-up after outage) → log the
      intermediate transitions to the decision log for audit, then
      execute ONLY the last one. Paper cannot back-execute past bars
      on OANDA; the intermediate flips didn't touch the broker in
      real time, so we don't create Trade rows for them. This
      preserves broker/DB agreement.

    Callers persist the equity snapshot at the end of the tick — this
    function itself only snapshots when returning without executing
    anything (the 0-transition path).
    """
    if not transitions:
        _persist_equity_snapshot(
            db=db, run=run, position=broker_position, now_utc=now_utc
        )
        return

    if len(transitions) > 1:
        # Catch-up mode. Log everything but the last transition as a
        # notice; the runner cannot back-execute on OANDA.
        for t in transitions[:-1]:
            _write_decision(
                db,
                run,
                action="USER_INTERMEDIATE_TRANSITION_SKIPPED",
                reason=(
                    f"walk detected {t.kind} at bar {t.bar_ts.isoformat()} "
                    f"(reason={t.reason}); paper cannot back-execute past "
                    "bars on OANDA. Executing only the final walk state."
                ),
                outputs={
                    "paper_run_id": run.id,
                    "skipped_bar_ts": t.bar_ts.isoformat(),
                    "kind": t.kind,
                    "reason": t.reason,
                    "direction": t.direction,
                    "close_px": t.close_px,
                },
            )

    from agentfx.adapters.oanda import OandaOrderRejected

    last_t = transitions[-1]

    if last_t.kind == "close":
        # Walk decided to exit — SL, TP, or max_hold hit.
        try:
            _close_position_and_persist(
                db=db,
                run=run,
                client=client,
                instrument=instrument,
                now_utc=now_utc,
                reason=(
                    f"walk-driven exit at bar {last_t.bar_ts.isoformat()}: "
                    f"{last_t.reason}"
                ),
                exit_reason=last_t.reason,
            )
        except OandaOrderRejected as exc:
            _write_decision(
                db,
                run,
                action="USER_CLOSE_REJECTED",
                reason=(
                    f"walk-driven close rejected by OANDA: "
                    f"{exc.reject_reason} (txn={exc.transaction_id}) — "
                    "position stays open, next tick will re-evaluate"
                ),
                outputs={
                    "paper_run_id": run.id,
                    "reject_reason": exc.reject_reason,
                    "reject_transaction_id": exc.transaction_id,
                    "order_type": exc.order_type,
                    "close_context": last_t.reason,
                },
            )
        _persist_equity_snapshot(
            db=db, run=run, position=broker_position, now_utc=now_utc
        )
        return

    if last_t.kind == "flip":
        # Close current side, open opposite. Two broker calls (close +
        # market order) mirroring the pre-refactor opposite_flip
        # behaviour. If the close leg fails, don't open the new side —
        # the retry on the next tick will handle it.
        try:
            _close_position_and_persist(
                db=db,
                run=run,
                client=client,
                instrument=instrument,
                now_utc=now_utc,
                reason=(
                    f"walk-driven opposite_flip at bar {last_t.bar_ts.isoformat()}"
                ),
                exit_reason="opposite_flip",
            )
        except OandaOrderRejected as exc:
            _write_decision(
                db,
                run,
                action="USER_CLOSE_REJECTED",
                reason=(
                    f"opposite-flip close rejected by OANDA: "
                    f"{exc.reject_reason} (txn={exc.transaction_id}) — "
                    "position stays open, next tick will re-evaluate"
                ),
                outputs={
                    "paper_run_id": run.id,
                    "reject_reason": exc.reject_reason,
                    "reject_transaction_id": exc.transaction_id,
                    "order_type": exc.order_type,
                    "close_context": "opposite_flip",
                },
            )
            _persist_equity_snapshot(
                db=db, run=run, position=broker_position, now_utc=now_utc
            )
            return
        # Close succeeded — open the new side.
        signal_direction = "long" if last_t.direction > 0 else "short"
        _open_position_and_persist(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            instrument=instrument,
            signal_direction=signal_direction,  # type: ignore[arg-type]
            df=df,
            now_utc=now_utc,
            exit_rules=exit_rules,
            risk_cfg=risk_cfg,
            position_snapshot=broker_position,
        )
        return

    if last_t.kind == "open":
        # Fresh entry from flat.
        signal_direction = "long" if last_t.direction > 0 else "short"
        _open_position_and_persist(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            instrument=instrument,
            signal_direction=signal_direction,  # type: ignore[arg-type]
            df=df,
            now_utc=now_utc,
            exit_rules=exit_rules,
            risk_cfg=risk_cfg,
            position_snapshot=broker_position,
        )
        return

    # Unknown kind — defensive. Log and equity-snapshot.
    _write_decision(
        db,
        run,
        action="USER_SIGNAL_SKIPPED_STALE",
        reason=f"walk returned unknown transition kind={last_t.kind!r}; ignoring",
        outputs={"paper_run_id": run.id, "kind": last_t.kind},
    )
    _persist_equity_snapshot(db=db, run=run, position=broker_position, now_utc=now_utc)


# ─────────────────────────────────────────────────────────────────────
# Trade + decision persistence helpers
# ─────────────────────────────────────────────────────────────────────


def _persist_trade_entry(
    *,
    db,
    run,
    instrument: str,
    entry_price: float,
    quantity: float,
    side: str,
    entry_timestamp: datetime,
    signal_reason: str,
    broker_trade_id: str | None = None,
    broker_open_transaction_id: str | None = None,
    entry_financing: float = 0.0,
    entry_commission: float = 0.0,
) -> None:
    """Insert a new Trade row for a placed order.

    Side convention on the Trade table is 'buy' / 'sell' (legacy). Map
    'long' → 'buy', 'short' → 'sell'. `origin='live'` and
    `signal_reason` were added in migration b3e5c7a1d842. `broker_*_id`
    fields were added in 6d8a3c9e1f47 (v0.6.1 T1). `financing` and
    `commission` were added in 9e4b7f2a8c31 (v0.6.1) — usually 0 at
    entry, but we still capture them so a broker that charges on-fill
    commission doesn't silently disappear.
    """
    from agentfx.api.models import Trade

    ts = int(entry_timestamp.timestamp() * 1000)
    trade = Trade(
        id=f"tr_{ts}_{run.id}_{uuid.uuid4().hex[:8]}",
        run_id=run.id,
        entry_timestamp=entry_timestamp,
        entry_price=entry_price,
        quantity=quantity,
        side="buy" if side == "long" else "sell",
        origin="live",
        signal_reason=signal_reason,
        broker_trade_id=broker_trade_id,
        broker_open_transaction_id=broker_open_transaction_id,
        financing=entry_financing or None,
        commission=entry_commission or None,
    )
    db.add(trade)
    db.commit()


def _reconcile_broker_side_close(
    *,
    db,
    run,
    strategy,
    latest_close: float,
    latest_high: float,
    latest_low: float,
    now_utc: datetime,
) -> None:
    """Detect and reconcile a broker-side SL/TP fill.

    Called when the OANDA position is flat AND we might have an open
    Trade row (the runner didn't initiate the close, but the broker's
    SL/TP order fired between ticks). Updates the Trade row with an
    approximate exit price + pnl + a classified `exit_reason`, and
    writes a USER_POSITION_CLOSED decision log so the timeline shows
    the exit.

    Classification uses the latest bar's HIGH-LOW range: if the range
    contains the expected stop or target level, that level was
    touched intraday and OANDA's SL/TP would have fired. Much more
    robust than the earlier "close-price-with-2-pip-tolerance"
    approach — Wilson UAT 2026-07-15 hit a stop where the bar close
    was 21 pips below the stop level (fired mid-bar, price bounced),
    causing 'broker_other' misclassification.
    """
    from agentfx.api.models import Trade

    open_trade = (
        db.query(Trade)
        .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
        .order_by(Trade.entry_timestamp.asc())
        .first()
    )
    if open_trade is None:
        return  # nothing to reconcile

    exit_rules = getattr(strategy, "spec", None)
    if exit_rules is None or exit_rules.exit is None:
        return
    stop_pips = int(exit_rules.exit.stop_pips)
    target_pips = int(exit_rules.exit.target_pips)
    pip_size = float(getattr(strategy, "pip_size", 0.0001) or 0.0001)

    entry_price = float(open_trade.entry_price)
    is_long = open_trade.side == "buy"
    stop_price = (
        entry_price - stop_pips * pip_size
        if is_long
        else entry_price + stop_pips * pip_size
    )
    target_price = (
        entry_price + target_pips * pip_size
        if is_long
        else entry_price - target_pips * pip_size
    )

    # A level was "touched" in the latest bar if it falls inside the
    # bar's high-low range. That's the condition OANDA's SL/TP order
    # actually watches — it fires on any tick that crosses the level,
    # not on bar close.
    if is_long:
        stop_hit = latest_low <= stop_price  # long SL is below entry
        target_hit = latest_high >= target_price  # long TP is above entry
    else:
        stop_hit = latest_high >= stop_price  # short SL is above entry
        target_hit = latest_low <= target_price  # short TP is below entry

    if stop_hit and not target_hit:
        classified = "stop"
        exit_price_est = stop_price
    elif target_hit and not stop_hit:
        classified = "target"
        exit_price_est = target_price
    elif stop_hit and target_hit:
        # Both levels in the same bar (rare, high-vol). Tie-break by
        # whichever level is closer to the bar close — assumes the
        # fill happened before the price kept moving.
        dist_stop = abs(latest_close - stop_price)
        dist_target = abs(latest_close - target_price)
        if dist_stop < dist_target:
            classified = "stop"
            exit_price_est = stop_price
        else:
            classified = "target"
            exit_price_est = target_price
    else:
        classified = "broker_other"
        exit_price_est = latest_close

    # Wilson UAT 2026-07-28 (v0.6.5): DO NOT write a local pnl
    # estimate. The prior code computed `(exit - entry) × qty` in
    # the quote currency, then stored that as `pnl`. Two problems:
    #   1. For a non-USD account (Wilson's is AUD) this skips
    #      OANDA's homeConversionFactor — the stored value is off
    #      by roughly the AUD/USD ratio (~30% under-report).
    #   2. `exit_price_est` is a stop/target level or bar close, not
    #      the actual OANDA fill price. Real slippage compounds the
    #      error.
    # The reconciler picks up the corresponding ORDER_FILL txn
    # within one reconciler tick and writes the authoritative
    # OANDA `realizedPL` (in account currency, from the fill
    # response). Until then the trade renders as "pending
    # reconciliation" in the UI — honest and self-heals within
    # ~12 min. Never shows a wrong number.
    #
    # Kept: exit_timestamp + exit_price_est + exit_reason so the
    # chart's marker + trades table still show the trade as closed.
    # Pips derived from (entry_price, exit_price_est) are currency-
    # agnostic and stay approximately correct even without
    # conversion.
    open_trade.exit_timestamp = now_utc
    open_trade.exit_price = exit_price_est
    open_trade.exit_reason = classified
    # pnl / pnl_pct intentionally left NULL — see comment above.
    db.commit()

    _write_decision(
        db,
        run,
        action="USER_POSITION_CLOSED",
        reason=(
            f"broker-side {classified} fill detected — trade {open_trade.id} "
            f"closed at ~{exit_price_est:.5f} (entry {entry_price:.5f}), "
            f"pnl pending reconciler"
        ),
        outputs={
            "paper_run_id": run.id,
            "trade_id": open_trade.id,
            "exit_reason": classified,
            "exit_price": exit_price_est,
            "pnl": None,
        },
    )


def _close_position_and_persist(
    *,
    db,
    run,
    client,
    instrument: str,
    now_utc: datetime,
    reason: str,
    exit_reason: str | None = None,
) -> None:
    """Flatten the OANDA position, update matching open Trade rows,
    and write a `USER_POSITION_CLOSED` decision log.

    `exit_reason` is the structured taxonomy value written to
    `trades.exit_reason` (see the migration for the enum). Distinct
    from the human-readable `reason` string that goes into the
    decision log.

    Best-effort on the Trade update — if the trade row was written by
    a prior runner instance and lost, we still commit the OANDA close
    and log the decision so the audit trail is preserved.
    """
    from agentfx.api.models import Trade

    result = client.close_all_positions(instrument)
    long_pnl = float(result.get("long_realized_pnl", 0.0) or 0.0)
    short_pnl = float(result.get("short_realized_pnl", 0.0) or 0.0)
    total_pnl = long_pnl + short_pnl
    # v0.6.1: financing and commission accrued on this close — split
    # across open trades in proportion to quantity, same as pnl. If
    # the reconciler later runs a DAILY_FINANCING on any of these
    # trades it will accumulate on top.
    long_fin = float(result.get("long_financing", 0.0) or 0.0)
    short_fin = float(result.get("short_financing", 0.0) or 0.0)
    total_fin = long_fin + short_fin
    long_com = float(result.get("long_commission", 0.0) or 0.0)
    short_com = float(result.get("short_commission", 0.0) or 0.0)
    total_com = long_com + short_com
    # v0.6.4: capture the OANDA fill transaction IDs so the reconciler
    # can tell "runner already reconciled this exact close" apart from
    # "runner marked closed with a local estimate". Prefer whichever
    # side actually filled; both being present in the same close is
    # possible (hedged position) but rare.
    close_txn_id = result.get("long_fill_transaction_id") or result.get(
        "short_fill_transaction_id"
    )

    # Update every open Trade row for this run — a normal single-side
    # position will have exactly one; the multi-fill / partial-close
    # cases would have more.
    open_trades = (
        db.query(Trade)
        .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
        .all()
    )
    if open_trades:
        # Split the realized pnl proportionally to quantity. Rough
        # apportionment is fine — OANDA's per-trade close would give
        # us exact per-trade pnl but we're closing everything at once
        # here so the proportional split is the honest read of the
        # combined result.
        total_qty = sum(t.quantity for t in open_trades) or 1.0
        for t in open_trades:
            share = float(t.quantity) / total_qty
            t.pnl = total_pnl * share
            t.pnl_pct = (
                (t.pnl / (t.entry_price * t.quantity)) * 100 if t.entry_price else 0.0
            )
            t.exit_timestamp = now_utc
            # Exit price: derive from the pnl share + entry price. Not
            # exact for JPY vs major pip differences but close enough
            # for the UI. If this drifts noticeably we'll read the
            # actual fill price from the OANDA response in v0.6.2.
            t.exit_price = t.entry_price + (t.pnl / t.quantity if t.quantity else 0.0)
            if exit_reason is not None:
                t.exit_reason = exit_reason
            # v0.6.1: accumulate financing + commission on this close.
            # `or 0.0` handles legacy rows where the column is NULL
            # (pre-migration Trades).
            if total_fin:
                t.financing = float(t.financing or 0.0) + total_fin * share
            if total_com:
                t.commission = float(t.commission or 0.0) + total_com * share
            # v0.6.4: stamp the OANDA fill txn ID so the reconciler's
            # idempotency guard recognises this close is already fully
            # reconciled with authoritative OANDA numbers. Without
            # this, the reconciler's new txn-ID-based guard treats the
            # runner's close as unreconciled and would re-write pnl
            # from the ORDER_FILL — which is fine (same value) but
            # double-writes financing.
            if close_txn_id:
                t.broker_close_transaction_id = str(close_txn_id)
        db.commit()

    _write_decision(
        db,
        run,
        action="USER_POSITION_CLOSED",
        reason=(
            f"Closed {instrument} — long_pnl={long_pnl:.2f} "
            f"short_pnl={short_pnl:.2f} ({reason})"
        ),
        outputs={
            "paper_run_id": run.id,
            "instrument": instrument,
            "long_realized_pnl": long_pnl,
            "short_realized_pnl": short_pnl,
            "close_reason": reason,
            "closed_trade_count": len(open_trades),
        },
    )
    # Position is now flat — snapshot equity without unrealized_pnl.
    _persist_equity_snapshot(db=db, run=run, position=None, now_utc=now_utc)


def _persist_equity_snapshot(
    *,
    db,
    run,
    position: dict | None,
    now_utc: datetime,
) -> None:
    """Append a paper_equity_snapshot row for the reload-safe chart (US-026).

    Written on every signal tick — hold / place / close — but NEVER on
    the health tick, so the row count scales with trading cadence
    rather than loop cadence. `unrealized_pnl` is pulled from the
    OANDA position when the runner was holding a live side; passing
    `position=None` (post-close) records 0.
    """
    from agentfx.api.models import PaperEquitySnapshot

    unrealized = 0.0
    if position is not None:
        unrealized = float(position.get("unrealized_pnl", 0.0) or 0.0)

    snapshot = PaperEquitySnapshot(
        id=uuid.uuid4().hex,
        run_id=run.id,
        timestamp=now_utc,
        balance=float(run.current_balance or run.snapshot_balance or 0.0),
        unrealized_pnl=unrealized,
    )
    db.add(snapshot)
    db.commit()


def _write_decision(
    db,
    run,
    *,
    action: str,
    reason: str,
    outputs: dict,
) -> None:
    """Append a DecisionLog row scoped to this paper run.

    The `decision_logs.run_id` column FKs to `evolution_runs.id`, not
    paper runs — since v0.6.0 it's nullable to accommodate user
    actions that aren't attached to an evolution. We stash the paper
    run id inside `outputs_json` so `GET /paper-runs/{id}/decisions`
    (US-025) can filter cleanly. A future migration may promote it to
    its own column with an index.
    """
    from agentfx.api.models import DecisionLog

    log = DecisionLog(
        id=uuid.uuid4().hex,
        run_id=None,
        agent_type="PAPER_RUNNER",
        action=action,
        outputs_json=outputs,
        reasoning_text=reason,
        user_id=run.user_id,
    )
    db.add(log)
    db.commit()


def _signal_reason(strategy, direction: Direction, df=None) -> str:
    """Derive a debuggable reason string for the trade row.

    Wilson's UAT feedback (2026-07-13): "SHORT trade opened immediately
    but looking at the indicator in the graph, it didn't seem as the
    signal actually fired". Root problem: the previous version returned
    just "sma < sma" without values, so the operator couldn't verify
    what the runner saw vs what the chart shows.

    Now includes:
    - Indicator name + args ("sma[5]", not just "sma")
    - Actual computed value at the trigger bar
    - Comparison operator
    - RHS values (indicator OR literal)
    - Bar timestamp (so the chart can be scrolled to the exact bar)

    Example output:
        "sma[5]=1.08452 < sma[20]=1.08517 @ 2026-07-13T05:30:00+00:00"

    Falls back to the previous "sma < sma" form (or generic "SIDE
    signal") when the DSL uses AND/OR compound conditions the walker
    can't summarize in one line, or when indicator eval fails. Failure
    never breaks the trade write.
    """
    from agentfx.core.dsl.indicators import evaluate_indicator
    from agentfx.core.dsl.grammar import IndicatorCall

    def _side_label_value(side, cache: dict) -> tuple[str, str | None]:
        """(display label, numeric string) for one side of a Comparison.

        Numeric string is None when the value can't be resolved (e.g.
        indicator eval raised); the caller renders just the label.

        Handles both real `IndicatorCall` instances (production) and
        duck-typed objects with `.ind` / `.args` attrs (test mocks).
        """
        ind = getattr(side, "ind", None)
        if ind is not None:
            args = list(getattr(side, "args", []) or [])
            args_str = "[" + ",".join(_fmt_num(a) for a in args) + "]" if args else ""
            label = f"{ind}{args_str}"
            if df is None or not isinstance(side, IndicatorCall):
                # Duck-typed mock or no candles — return label without
                # attempting eval. `evaluate_indicator` requires a real
                # IndicatorCall + real DataFrame.
                return label, None
            key = (ind, tuple(args))
            if key not in cache:
                try:
                    cache[key] = evaluate_indicator(df, ind, args)
                except Exception:  # noqa: BLE001
                    cache[key] = None
            series = cache[key]
            if series is None or len(series) == 0:
                return label, None
            val = series.iloc[-1]
            if val is None or (isinstance(val, float) and (val != val)):
                return label, None
            return label, _fmt_num(float(val))
        # Numeric literal on this side.
        try:
            val = float(side)
            return _fmt_num(val), _fmt_num(val)
        except (TypeError, ValueError):
            return "expr", None

    try:
        spec = getattr(strategy, "spec", None)
        if spec is None:
            return f"{direction} signal"
        cond = spec.entry.long if direction == "long" else spec.entry.short
        if cond is None:
            return f"{direction} signal"
        # Compound (AND/OR) or nested — bail to the previous simple form.
        if not (hasattr(cond, "lhs") and hasattr(cond, "rhs") and hasattr(cond, "cmp")):
            return f"{direction} compound signal"

        cache: dict = {}
        lhs_label, lhs_val = _side_label_value(cond.lhs, cache)
        rhs_label, rhs_val = _side_label_value(cond.rhs, cache)
        cmp = cond.cmp
        lhs_render = f"{lhs_label}={lhs_val}" if lhs_val is not None else lhs_label
        rhs_render = f"{rhs_label}={rhs_val}" if rhs_val is not None else rhs_label
        core = f"{lhs_render} {cmp} {rhs_render}"

        if df is not None and len(df) > 0:
            ts = df.index[-1]
            if hasattr(ts, "isoformat"):
                core += f" @ {ts.isoformat()}"
        return core
    except Exception:  # noqa: BLE001
        return f"{direction} signal"


def _fmt_num(x) -> str:
    """Compact number formatting: 5 decimals for prices, integer otherwise."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if f == int(f) and abs(f) < 1e6:
        return str(int(f))
    return f"{f:.5f}"


def _seconds_to_granularity(seconds: int) -> str:
    """Inverse of `granularity_to_seconds` — needed by the candle
    fetcher which takes granularity string, not seconds.

    Falls back to 'M1' on unknown values (matches the forward map's
    fallback behaviour so a misconfigured run still gets bars, just
    at the wrong cadence — the stale-candle guard then triggers).
    """
    for name, value in _GRANULARITY_SECONDS.items():
        if value == seconds:
            return name
    return "M1"


# ─────────────────────────────────────────────────────────────────────
# Balance snapshot
# ─────────────────────────────────────────────────────────────────────


def _snapshot_balance_if_needed(*, db, run, client) -> None:
    """First-tick capture of the account's balance.

    Wilson's UX answer #4: the user never types an initial balance —
    the runner reads it from the sub-account on the first tick and
    stores it as `runs.snapshot_balance`. Every P&L % on the run
    detail page is `(balance - snapshot) / snapshot`.

    Idempotent — if the run already has a snapshot (e.g. after a
    crash + watchdog recovery) we do NOT re-snapshot; the original
    denominator is more useful than a mid-run re-anchor.
    """
    if run.snapshot_balance is not None and run.snapshot_balance > 0:
        # Even on resume, seed last_transaction_id if it's missing —
        # the reconciler needs a starting point. Best-effort: any
        # OANDA error here means the reconciler stays inert until the
        # health tick sets it via a subsequent get_account_balance.
        if run.last_transaction_id is None:
            try:
                run.last_transaction_id = _safe_txn_id(client.get_last_transaction_id())
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
        return

    balance = client.get_account_balance()
    run.snapshot_balance = balance["balance"]
    run.initial_balance = balance["balance"]  # keep legacy col in sync
    run.current_balance = balance["balance"]
    run.pnl = 0.0
    # Seed the reconciler high-water mark on first tick so subsequent
    # `/transactions/sinceid` calls know where to start.
    try:
        run.last_transaction_id = _safe_txn_id(client.get_last_transaction_id())
    except Exception:  # noqa: BLE001
        run.last_transaction_id = None
    db.commit()


def _safe_txn_id(raw) -> str | None:
    """Coerce an OANDA transaction ID response to `str | None`.

    OANDA v20 returns transaction IDs as strings (or numeric strings).
    Defensive: if the adapter is mocked in a test and returns a
    MagicMock — or the OANDA API surface changes shape unexpectedly —
    we store None rather than letting a non-string blow up the DB
    commit (`can't adapt type 'MagicMock'`) and poison the session.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int):
        return str(raw)
    return None


# ─────────────────────────────────────────────────────────────────────
# Credential recovery (T0.2 — Wilson UAT 2026-07-15)
# ─────────────────────────────────────────────────────────────────────


def _is_auth_error(exc: Exception) -> bool:
    """True iff `exc` is an OANDA auth-loss error (401 or 403).

    We check the message string because `_request` wraps status codes
    in `OandaError(f"{code} {text}")`; there's no structured status
    field on the exception. Both 401 and 403 map to auth-loss for
    our purposes — the user needs to intervene.
    """
    s = str(exc)
    return s.startswith("401 ") or s.startswith("403 ")


def _recover_from_auth_loss(db, run, original_exc: Exception):
    """Reload creds from DB and build a fresh OandaClient.

    Returns the new client if the refresh yields something that can
    complete a `get_account_balance` call, or None if the fresh
    client also 401s (meaning the credentials are truly bad, not
    just stale in the subprocess).

    This handles two operational scenarios:
    - User re-verified credentials in Settings while the run was
      running → we pick up the fresh token.
    - The subprocess's long-lived requests.Session got into a bad
      state → a brand new session might work with the same token.
    """
    from agentfx.adapters.oanda import OandaClient, OandaError
    from agentfx.api.core.secrets import decrypt_secret
    from agentfx.api.models import UserOandaCredentials
    from agentfx.api.models.user_credentials import (
        UserOandaAccount,
        UserOandaAccountPoolStatus,
    )

    # AFX-7: if the run was pool-allocated, re-decrypt the pool
    # sub-account's parent credentials — the owner's own creds don't
    # apply, the run trades on a different account entirely.
    pool_row = (
        db.query(UserOandaAccount)
        .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
        .filter(UserOandaAccount.current_run_id == str(run.id))
        .first()
    )
    if pool_row is not None:
        pool_creds = pool_row.credentials
        try:
            api_key = decrypt_secret(bytes(pool_creds.oanda_api_key_encrypted))
        except Exception:  # noqa: BLE001
            logger.exception(
                "auth-refresh: pool decrypt failed for run %s pool_row=%s",
                run.id,
                pool_row.id,
            )
            return None
        env_value = (
            pool_creds.oanda_environment.value
            if hasattr(pool_creds.oanda_environment, "value")
            else str(pool_creds.oanda_environment)
        )
        new_client = OandaClient(
            api_key=api_key,
            environment=env_value,
            account_id=pool_row.oanda_account_id,
        )
        try:
            new_client.get_account_balance()
        except OandaError as exc:
            if _is_auth_error(exc):
                logger.warning(
                    "auth-refresh: pool-sourced client still 401 for run %s "
                    "(pool_row=%s, account=%s): %s",
                    run.id,
                    pool_row.id,
                    pool_row.oanda_account_id,
                    original_exc,
                )
                return None
            raise
        logger.info(
            "auth-refresh: pool key refresh succeeded for run %s (original: %s)",
            run.id,
            original_exc,
        )
        return new_client

    creds = (
        db.query(UserOandaCredentials)
        .filter(UserOandaCredentials.user_id == run.user_id)
        .one_or_none()
    )
    if creds is None:
        logger.error(
            "auth-refresh: no credentials in DB for run %s user %s",
            run.id,
            run.user_id,
        )
        return None

    try:
        api_key = decrypt_secret(creds.oanda_api_key_encrypted)
    except Exception:  # noqa: BLE001
        logger.exception("auth-refresh: decrypt failed for run %s", run.id)
        return None

    env_value = (
        creds.oanda_environment.value
        if hasattr(creds.oanda_environment, "value")
        else str(creds.oanda_environment)
    )
    new_client = OandaClient(
        api_key=api_key,
        environment=env_value,
        account_id=run.oanda_account_id,
    )
    try:
        new_client.get_account_balance()
    except OandaError as exc:
        if _is_auth_error(exc):
            logger.warning(
                "auth-refresh: fresh client still 401 for run %s (%s)",
                run.id,
                original_exc,
            )
            return None
        # Some other error on the health check — surface it, don't
        # pretend recovery worked. Caller re-tries once.
        raise
    logger.info(
        "auth-refresh: succeeded for run %s (original error: %s)",
        run.id,
        original_exc,
    )
    return new_client


# ─────────────────────────────────────────────────────────────────────
# Terminal state helpers
# ─────────────────────────────────────────────────────────────────────


_FLATTEN_RETRY_LIMIT = 3
_FLATTEN_RETRY_BACKOFF_SECONDS = (10.0, 30.0, 60.0)


def _flatten_and_halt(db, run, client, *, reason: str, sleep_fn=time.sleep) -> None:
    """Close all OANDA positions on the run's instrument and mark the
    run terminal. Best-effort on the OANDA close — the run row is
    always transitioned even if the broker call raises. Also flags
    any open Trade row with a classified `exit_reason` so the chart
    marker shows the halt cause instead of a stranded open trade.

    Wilson 2026-08-23 audit: the Aug 21 outage halted both runs while
    OANDA was still down, so `close_all_positions` failed and both live
    positions stranded on the broker for 37h. Retry the close with
    exponential backoff before giving up — a common flatten-during-outage
    scenario is that OANDA recovers within seconds.
    """
    from agentfx.api.models import RunStatus, Trade

    close_ok = False
    close_result: dict | None = None
    for attempt in range(1, _FLATTEN_RETRY_LIMIT + 1):
        try:
            close_result = client.close_all_positions(run.pair)
            close_ok = True
            if attempt > 1:
                logger.info(
                    "paper runner: close_all_positions succeeded on %s "
                    "at attempt %d/%d",
                    run.id,
                    attempt,
                    _FLATTEN_RETRY_LIMIT,
                )
            break
        except Exception:  # noqa: BLE001
            logger.warning(
                "paper runner: close_all_positions failed on %s "
                "(attempt %d/%d); will retry",
                run.id,
                attempt,
                _FLATTEN_RETRY_LIMIT,
            )
            if attempt < _FLATTEN_RETRY_LIMIT:
                backoff = _FLATTEN_RETRY_BACKOFF_SECONDS[attempt - 1]
                sleep_fn(backoff)
    if not close_ok:
        logger.exception(
            "paper runner: close_all_positions failed on %s "
            "after %d attempts; halting anyway (position may be stranded)",
            run.id,
            _FLATTEN_RETRY_LIMIT,
        )

    # Wilson 2026-08-31: `close_all_positions` returns a "success"
    # dict without actually closing anything in two cases:
    #   * `get_position` returned 0/0 units → short-circuit no-op
    #   * OANDA rejected with CLOSEOUT_POSITION_DOESNT_EXIST → benign
    # During an OANDA maintenance window (like Aug 28 23:35 UTC), the
    # position endpoint can return stale 0/0 even when the position is
    # still open. Under the old code, my `if close_ok:` branch marked
    # DB trades closed → phantom-flat state.
    #
    # Cross-check: if close_ok but the OANDA response says ZERO units
    # were actually closed AND we had open DB trades, VERIFY with a
    # fresh get_position before trusting the close. If OANDA still
    # holds units, the API was lying — flip to stranded semantics.
    close_actually_did_something = True
    if close_ok and close_result is not None:
        units_reported_closed = abs(
            int(close_result.get("long_units_closed", 0) or 0)
        ) + abs(int(close_result.get("short_units_closed", 0) or 0))
        if units_reported_closed == 0:
            # No fill returned. Verify OANDA state before trusting.
            try:
                verify_pos = client.get_position(run.pair)
                verify_long = int(verify_pos.get("long_units", 0) or 0)
                verify_short = int(verify_pos.get("short_units", 0) or 0)
                if verify_long != 0 or verify_short != 0:
                    logger.error(
                        "paper runner: %s close_all_positions returned "
                        "success but 0 units closed; get_position "
                        "verified position STILL OPEN "
                        "(long=%d short=%d). Treating as stranded — DB "
                        "trades stay OPEN for reconciler to reconcile.",
                        run.id,
                        verify_long,
                        verify_short,
                    )
                    close_ok = False
                    close_actually_did_something = False
            except Exception:  # noqa: BLE001
                # Verification call failed too — be conservative and
                # treat as stranded rather than mark closed on
                # unverified state.
                logger.warning(
                    "paper runner: %s close_all_positions verification "
                    "get_position failed; treating as stranded to be safe",
                    run.id,
                )
                close_ok = False
                close_actually_did_something = False

    # Wilson 2026-08-24: previously we marked open Trade rows CLOSED
    # even when `close_all_positions` failed — the DB then lied that
    # positions were flat while OANDA still held them (Aug 21 event
    # stranded both live positions for 37h with paper DB showing
    # `broker_other` exits at pnl=0). Fix: only mark trades closed
    # when the broker actually confirmed the close. On failure, leave
    # the DB rows OPEN, persist a `stranded_positions` marker on the
    # run so the operator / reconciler can find them, and write a
    # loud decision-log entry per stranded trade.
    now = datetime.now(UTC)
    open_trades = (
        db.query(Trade)
        .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
        .all()
    )

    if close_ok:
        # Broker confirmed close — safe to mark DB rows closed.
        exit_reason_map = {
            "USER_KILL": "user_kill",
            "BROKER_CIRCUIT": "broker_other",
        }
        for t in open_trades:
            t.exit_timestamp = now
            # Exit price/pnl are best-effort — the broker's exact fill
            # isn't queried here. Chart marker still renders with the
            # entry-price fallback so the halt is visible.
            if t.exit_price is None:
                t.exit_price = t.entry_price
            if t.pnl is None:
                t.pnl = 0.0
            t.exit_reason = exit_reason_map.get(reason, "broker_other")
        if open_trades:
            db.commit()
    elif open_trades:
        # Broker close FAILED. Leave trades OPEN in the DB so the
        # reconciler sees the mismatch and Wilson can act. Persist a
        # marker on the run for the supervisor + Slack alert.
        stranded = [
            {
                "trade_id": t.id,
                "side": t.side,
                "units": t.quantity,
                "entry_price": t.entry_price,
                "entry_timestamp": (
                    t.entry_timestamp.isoformat() if t.entry_timestamp else None
                ),
            }
            for t in open_trades
        ]
        params = dict(run.parameters or {})
        params["stranded_positions"] = {
            "recorded_at": now.isoformat(),
            "halt_reason": reason,
            "trades": stranded,
        }
        run.parameters = params
        from sqlalchemy.orm.attributes import flag_modified as _flag

        _flag(run, "parameters")
        db.commit()

        for t in open_trades:
            _write_decision(
                db,
                run,
                action="USER_POSITION_STRANDED",
                reason=(
                    f"BROKER CLOSE FAILED on halt ({reason}) — "
                    f"{t.side} {t.quantity} {run.pair} @ {t.entry_price} "
                    f"is STILL OPEN AT BROKER but DB kept trade open. "
                    f"Manual close required in the OANDA UI."
                ),
                outputs={
                    "paper_run_id": run.id,
                    "trade_id": t.id,
                    "halt_reason": reason,
                    "side": t.side,
                    "units": t.quantity,
                    "entry_price": t.entry_price,
                },
            )

    terminal_status = (
        RunStatus.HALTED_MANUAL if reason == "USER_KILL" else RunStatus.ERROR
    )
    _mark_terminal(db, run, terminal_status, reason)


def _mark_terminal(db, run, status, reason: str) -> None:
    run.status = status
    run.stop_reason = reason
    run.completed_at = datetime.now(UTC)
    db.commit()
    # AFX-7: release the fleet pool slot (if any) so the sub-account
    # can cool down + rotate back to FREE. Benign noop when the run
    # wasn't pool-allocated. Never re-raises — a release failure must
    # not un-commit the terminal state above.
    try:
        from agentfx.agents.fleet.account_pool import release_slot

        released = release_slot(db, run_id=str(run.id))
        if released:
            db.commit()
    except Exception:  # noqa: BLE001 — belt-and-braces
        logger.exception(
            "paper runner: pool release errored on %s — "
            "lease TTL will reclaim the slot",
            run.id,
        )
    # v0.6.2 (Wilson UAT 2026-07-19): Slack alert on every terminal
    # event. Idempotent per (run_id, reason) via
    # `run.parameters['notified']` so re-entrant halt paths + the
    # watchdog-vs-subprocess race can't spam the channel. Never
    # re-raises — a notification failure must not affect the trade.
    try:
        from agentfx.notifications import post_paper_run_alert

        post_paper_run_alert(run=run, reason=reason, db=db)
    except Exception:  # noqa: BLE001 — outermost belt-and-braces
        logger.exception(
            "paper runner: notification hook errored on %s (%s) — "
            "ignoring so the terminal state stays committed",
            run.id,
            reason,
        )


__all__ = [
    "DEFAULT_LOOKBACK_BARS",
    "TickAction",
    "align_to_granularity",
    "decide_action",
    "granularity_to_seconds",
    "pip_size_for_pair",
    "run_paper_run",
]
