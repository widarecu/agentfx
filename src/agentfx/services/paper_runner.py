"""Paper runner decision loop (US-021).

Runs inside the subprocess spawned by
`agentfx.api.workers.paper_runner.schedule_paper_run`. The parent-side
plumbing (spawn / watchdog / kill-signal escape hatch) is intentionally
separate so this module can be exercised by unit tests without
touching multiprocessing.

Loop shape:
1. Load the run row + user credentials → build an OandaClient scoped
   to the user's chosen sub-account.
2. Snapshot the account balance on the first tick and persist it as
   `runs.snapshot_balance` (per Wilson's UX answer #4 the user never
   types this — it comes from OANDA).
3. Enter the decision loop. Each tick:
   - Poll `kill_requested_at`; if set, close positions and exit
     `HALTED_MANUAL`.
   - Fetch current OANDA position + balance.
   - Fetch fresh candles + compile a signal from the genome's DSL.
   - Evaluate risk gates (US-023). Kill-switch / daily-loss trip a
     terminal halt; position-size cap skips this tick.
   - Diff signal vs position → place / close / hold.
   - Write updated `current_balance` + `pnl` to the row.
4. Exit gracefully on `stop_conditions` (COMPLETED) or on an
   `OandaCircuitOpen` from the adapter (`stop_reason=BROKER_CIRCUIT`).

The full DSL → signal evaluation for live candles lands in PR-G with
the integration test; for now the loop is scaffolded so tests can
exercise the individual pieces (`decide_action`, gate integration,
kill polling) with mocked candles and mocked OandaClient.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


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
# Pure decision logic — tested in isolation
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
    injectables (OandaClient, sleep) so tests can exercise it.
    """
    # Lazy imports so this module can be imported without pulling
    # SQLAlchemy or the OANDA HTTP client (unit tests hit
    # `decide_action` and `_snapshot_balance_if_needed` directly).
    from agentfx.adapters.oanda import OandaClient
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.core.secrets import decrypt_secret
    from agentfx.api.models import Run, RunStatus, UserOandaCredentials

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

        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        db.commit()

        _execute_loop(db=db, run=run, client=client)
    finally:
        db.close()


def _execute_loop(
    *,
    db,
    run,
    client,
    sleep_fn=time.sleep,
    max_iterations: int | None = None,
) -> None:
    """Body of the paper-runner main loop.

    Extracted from `run_paper_run` so tests can drive it with a mocked
    `client` and a bounded `max_iterations`. Production callers pass
    `max_iterations=None` (unbounded) and rely on `kill_requested_at`
    or a terminal risk-gate refusal to exit.
    """
    from agentfx.adapters.oanda import OandaCircuitOpen
    from agentfx.api.models import RunStatus
    from agentfx.services.paper_policy import RiskState, evaluate_all

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

        # Cadence — polling matches the granularity roughly. Kept as
        # an injectable `sleep_fn` so tests skip the wait entirely.
        # Real cadence lands in PR-G; here we default to 5s to match
        # the run-detail page's polling.
        sleep_fn(5.0)

        if max_iterations is None and run.status != RunStatus.RUNNING:
            # Something outside the loop terminated the run — respect
            # it and exit.
            return


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
    "TickAction",
    "decide_action",
    "run_paper_run",
]
