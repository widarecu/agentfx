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
from datetime import UTC, datetime
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
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.core.secrets import decrypt_secret
    from agentfx.api.models import Run, RunStatus, UserOandaCredentials
    from agentfx.core.dsl import StrategySpec, compile_spec

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).one_or_none()
        if run is None:
            logger.error("paper runner: run %s not found; exiting", run_id)
            return

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
        client = OandaClient(
            api_key=api_key,
            environment=env_value,
            account_id=run.oanda_account_id,
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
        run.started_at = datetime.now(UTC)
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
    [str, str, int], pd.DataFrame
]:  # pragma: no cover — thin adapter, exercised by integration test
    """Return a `(instrument, granularity, bars) -> DataFrame` closure
    that pulls the last N candles from the shared `candles` table.

    Extracted as a factory so `_execute_loop` doesn't take a DB
    dependency directly and unit tests inject their own arrays.
    """
    from agentfx.api.services.market_data import market_data_service

    def _fetch(instrument: str, granularity: str, bars: int):
        import pandas as pd

        candles, _total = market_data_service.get_candles(
            db=db,
            exchange="oanda",
            instrument=instrument,
            timeframe=granularity,
            limit=bars,
        )
        # market_data_service.get_candles returns newest-first when no
        # time range is specified — reverse to chronological order for
        # `generate_signals`.
        candles = list(reversed(candles))
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
    from agentfx.services.paper_policy import RiskState, evaluate_all

    if now_fn is None:  # pragma: no cover — trivial

        def _wall_clock() -> float:
            return datetime.now(UTC).timestamp()

        now_fn = _wall_clock

    signal_enabled = (
        strategy is not None and candle_fetch_fn is not None and instrument is not None
    )
    next_signal_at = (
        align_to_granularity(now_fn(), granularity_seconds)
        if signal_enabled
        else float("inf")
    )

    _snapshot_balance_if_needed(db=db, run=run, client=client)

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
        try:
            balance = client.get_account_balance()
        except OandaCircuitOpen:
            logger.warning("paper runner: %s got OandaCircuitOpen; halting", run.id)
            _flatten_and_halt(db, run, client, reason="BROKER_CIRCUIT")
            return
        run.current_balance = balance["balance"]
        run.pnl = balance["balance"] - (run.snapshot_balance or 0.0)
        db.commit()

        # Evaluate risk gates against the current balance.
        risk_cfg = run.risk_config or {}
        state = RiskState(
            snapshot_balance=run.snapshot_balance or 0.0,
            current_balance=run.current_balance,
            max_position_size_units=int(
                risk_cfg.get("max_position_size_units", 10_000)
            ),
            daily_loss_pct=float(risk_cfg.get("daily_loss_pct", 2.0)),
            kill_switch_dd_pct=float(risk_cfg.get("kill_switch_dd_pct", 5.0)),
        )
        gate = evaluate_all(state, proposed_units=state.max_position_size_units)
        if not gate.ok and gate.reason in {
            "DAILY_LOSS_LIMIT",
            "BROKER_CIRCUIT",
        }:
            logger.info(
                "paper runner: gate refused on %s (%s); halting",
                run.id,
                gate.reason,
            )
            _flatten_and_halt(db, run, client, reason=gate.reason)
            return

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
            next_signal_at = align_to_granularity(
                now + granularity_seconds, granularity_seconds
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
    """One granularity-boundary tick: fetch → compile → decide → act.

    Extracted so unit tests can drive one tick in isolation with
    mocked candles + mocked OandaClient. Errors bubble to
    `_execute_loop` which logs and continues.
    """
    from agentfx.api.models import RunStatus

    if run.status != RunStatus.RUNNING:
        return

    granularity = _seconds_to_granularity(granularity_seconds)
    df = candle_fetch_fn(instrument, granularity, DEFAULT_LOOKBACK_BARS)

    # Stale-candle guard. Ingestion outages (weekend, OANDA maintenance,
    # ingester falling behind) leave the shared `candles` table N bars
    # behind wall-clock. We skip signal evaluation + log rather than
    # halt — the runner resumes on next tick when fresh data lands.
    if df.empty:
        _write_decision(
            db,
            run,
            action="USER_SIGNAL_SKIPPED_STALE",
            reason=f"no candles returned for {instrument} {granularity}",
            outputs={
                "paper_run_id": run.id,
                "reason_detail": "empty_candles",
            },
        )
        return
    latest_ts = df.index[-1]
    if hasattr(latest_ts, "to_pydatetime"):
        latest_dt = latest_ts.to_pydatetime()
    else:  # pragma: no cover — defensive
        latest_dt = latest_ts
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.replace(tzinfo=UTC)
    if (now_utc - latest_dt).total_seconds() > 2 * granularity_seconds:
        _write_decision(
            db,
            run,
            action="USER_SIGNAL_SKIPPED_STALE",
            reason=(
                f"latest candle {latest_dt.isoformat()} is older than "
                f"2x {granularity} bars — skipping signal"
            ),
            outputs={
                "paper_run_id": run.id,
                "latest_candle_at": latest_dt.isoformat(),
                "now": now_utc.isoformat(),
            },
        )
        return

    signals = strategy.generate_signals(df)
    if len(signals) < 2:
        return
    last_signal = int(signals.iloc[-1])
    prev_signal = int(signals.iloc[-2])
    signal_direction: Direction = (
        "long" if last_signal > 0 else "short" if last_signal < 0 else "flat"
    )

    position = client.get_position(instrument)
    long_units = int(position.get("long_units", 0) or 0)
    short_units = int(position.get("short_units", 0) or 0)
    # OANDA returns short_units as a negative integer; treat the sum as
    # signed net exposure so `decide_action`'s arithmetic works
    # consistently regardless of sign convention.
    net_units = long_units + short_units

    # Missed-entry guard. `generate_signals` returns a HELD-STATE series:
    # +1 on every bar while long, -1 while short, 0 while flat. So a
    # non-zero value at the latest bar can mean either "fresh cross this
    # bar" (prev == 0) or "in a held position since a cross that fired
    # some earlier bar" (prev == last). If we cold-deployed mid-hold
    # (or the operator manually closed the OANDA position while backtest
    # state persisted), the runner would otherwise chase a stale entry
    # hours late — Wilson's UAT observation 2026-07-13. Policy: skip and
    # wait for the next fresh cross.
    is_fresh_entry_or_flip = prev_signal != last_signal
    if last_signal != 0 and net_units == 0 and not is_fresh_entry_or_flip:
        _write_decision(
            db,
            run,
            action="USER_ENTRY_MISSED",
            reason=(
                f"backtest state is {signal_direction} but no cross at latest "
                f"bar — entry was before we started watching. Waiting for next "
                f"cross."
            ),
            outputs={
                "paper_run_id": run.id,
                "last_signal": last_signal,
                "prev_signal": prev_signal,
            },
        )
        _persist_equity_snapshot(db=db, run=run, position=position, now_utc=now_utc)
        return

    risk_cfg = run.risk_config or {}
    max_units = int(risk_cfg.get("max_position_size_units", 10_000))
    action = decide_action(
        signal_direction=signal_direction,
        signal_units=max_units if signal_direction != "flat" else 0,
        current_net_units=net_units,
    )

    if action.action == "hold":
        # Deliberately no decision log here — a busy strategy would
        # produce one row per health-tick × signal-tick, blowing up
        # the audit table. `USER_ORDER_PLACED` / `USER_POSITION_CLOSED`
        # are the load-bearing entries.
        # Still snapshot equity so the reload-safe LivePnLChart curve
        # doesn't skip bars just because the strategy held.
        _persist_equity_snapshot(db=db, run=run, position=position, now_utc=now_utc)
        return

    if action.action == "close":
        _close_position_and_persist(
            db=db,
            run=run,
            client=client,
            instrument=instrument,
            now_utc=now_utc,
            reason="flat_signal",
        )
        return

    # action.action == "place"
    # Two-step position flip: if we have an open position in the
    # opposite direction, close first + return. Next signal tick opens
    # the new side. Explicit in the audit trail; matches how the
    # backtest engine records flips.
    if net_units != 0 and (
        (net_units > 0 and signal_direction == "short")
        or (net_units < 0 and signal_direction == "long")
    ):
        _close_position_and_persist(
            db=db,
            run=run,
            client=client,
            instrument=instrument,
            now_utc=now_utc,
            reason="direction_change",
        )
        return

    signed_units = max_units if signal_direction == "long" else -max_units
    sl_pips = risk_cfg.get("stop_loss_pips")
    tp_pips = risk_cfg.get("take_profit_pips")

    result = client.place_market_order(
        instrument,
        signed_units,
        stop_loss_pips=sl_pips,
        take_profit_pips=tp_pips,
    )
    fill_price = float(result.get("fill_price") or 0.0)
    filled_units = int(result.get("units") or signed_units)
    trade_id = result.get("trade_id")
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
    )
    _write_decision(
        db,
        run,
        action="USER_ORDER_PLACED",
        reason=(
            f"{signal_direction.upper()} {abs(filled_units)} {instrument} "
            f"@ {fill_price:.5f} ({signal_reason})"
        ),
        outputs={
            "paper_run_id": run.id,
            "instrument": instrument,
            "signed_units": filled_units,
            "fill_price": fill_price,
            "broker_trade_id": trade_id,
            "signal_reason": signal_reason,
        },
    )
    _persist_equity_snapshot(db=db, run=run, position=position, now_utc=now_utc)


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
) -> None:
    """Insert a new Trade row for a placed order.

    Side convention on the Trade table is 'buy' / 'sell' (legacy). Map
    'long' → 'buy', 'short' → 'sell'. Both `origin='live'` and
    `signal_reason` were added in the b3e5c7a1d842 migration.
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
    )
    db.add(trade)
    db.commit()


def _close_position_and_persist(
    *,
    db,
    run,
    client,
    instrument: str,
    now_utc: datetime,
    reason: str,
) -> None:
    """Flatten the OANDA position, update matching open Trade rows,
    and write a `USER_POSITION_CLOSED` decision log.

    Best-effort on the Trade update — if the trade row was written by
    a prior runner instance and lost, we still commit the OANDA close
    and log the decision so the audit trail is preserved.
    """
    from agentfx.api.models import Trade

    result = client.close_all_positions(instrument)
    long_pnl = float(result.get("long_realized_pnl", 0.0) or 0.0)
    short_pnl = float(result.get("short_realized_pnl", 0.0) or 0.0)
    total_pnl = long_pnl + short_pnl

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
        return

    balance = client.get_account_balance()
    run.snapshot_balance = balance["balance"]
    run.initial_balance = balance["balance"]  # keep legacy col in sync
    run.current_balance = balance["balance"]
    run.pnl = 0.0
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# Terminal state helpers
# ─────────────────────────────────────────────────────────────────────


def _flatten_and_halt(db, run, client, *, reason: str) -> None:
    """Close all OANDA positions on the run's instrument and mark the
    run terminal. Best-effort on the OANDA close — the run row is
    always transitioned even if the broker call raises."""
    try:
        client.close_all_positions(run.pair)
    except Exception:  # noqa: BLE001
        logger.exception(
            "paper runner: close_all_positions failed on %s; halting anyway",
            run.id,
        )

    from agentfx.api.models import RunStatus

    terminal_status = (
        RunStatus.HALTED_MANUAL if reason == "USER_KILL" else RunStatus.ERROR
    )
    _mark_terminal(db, run, terminal_status, reason)


def _mark_terminal(db, run, status, reason: str) -> None:
    run.status = status
    run.stop_reason = reason
    run.completed_at = datetime.now(UTC)
    db.commit()


__all__ = [
    "DEFAULT_LOOKBACK_BARS",
    "TickAction",
    "align_to_granularity",
    "decide_action",
    "granularity_to_seconds",
    "pip_size_for_pair",
    "run_paper_run",
]
