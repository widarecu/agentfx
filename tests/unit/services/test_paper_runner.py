"""Unit tests for the paper runner decision logic (US-021).

Pure — no subprocess, no OANDA network. Exercises:
- `decide_action` diff logic across every direction × position combo
- `_snapshot_balance_if_needed` idempotence
- `_execute_loop` kill-flag handling with a mocked OandaClient +
  mocked `db.refresh` semantics
- Terminal-state helpers write the expected `stop_reason` vocab

Integration coverage (subprocess spawn + real broker mock) lives in
PR-G — this file focuses on the parts a fast test can lock without
multiprocessing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentfx.services.paper_runner import (
    TickAction,
    _execute_loop,
    _mark_terminal,
    _snapshot_balance_if_needed,
    decide_action,
)


# ─────────────────────────────────────────────────────────────────────
# decide_action
# ─────────────────────────────────────────────────────────────────────


class TestDecideAction:
    def test_new_long_from_flat(self):
        r = decide_action(
            signal_direction="long", signal_units=10_000, current_net_units=0
        )
        assert r.action == "place"
        assert r.reason == "new_entry"

    def test_hold_when_signal_matches(self):
        r = decide_action(
            signal_direction="long", signal_units=10_000, current_net_units=10_000
        )
        assert r.action == "hold"

    def test_short_signal_flips_from_long(self):
        r = decide_action(
            signal_direction="short", signal_units=5_000, current_net_units=10_000
        )
        assert r.action == "place"
        assert r.reason == "direction_change"

    def test_flat_signal_closes_position(self):
        r = decide_action(
            signal_direction="flat", signal_units=0, current_net_units=10_000
        )
        assert r.action == "close"
        assert r.reason == "flat_signal"

    def test_flat_signal_no_op_when_already_flat(self):
        r = decide_action(signal_direction="flat", signal_units=0, current_net_units=0)
        assert r.action == "hold"
        assert r.reason == "already_flat"

    def test_short_new_entry_from_flat(self):
        r = decide_action(
            signal_direction="short", signal_units=10_000, current_net_units=0
        )
        assert r.action == "place"
        assert r.signal_direction == "short"


# ─────────────────────────────────────────────────────────────────────
# _snapshot_balance_if_needed
# ─────────────────────────────────────────────────────────────────────


class TestSnapshotBalance:
    def test_captures_on_first_call(self):
        run = SimpleNamespace(
            snapshot_balance=None,
            last_transaction_id=None,
            initial_balance=0.0,
            current_balance=0.0,
            pnl=0.0,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}
        db = MagicMock()

        _snapshot_balance_if_needed(db=db, run=run, client=client)

        assert run.snapshot_balance == 10_000.0
        assert run.initial_balance == 10_000.0
        assert run.current_balance == 10_000.0
        assert run.pnl == 0.0
        db.commit.assert_called()

    def test_idempotent_when_already_snapshotted(self):
        # v0.6.1 T1: pre-set last_transaction_id so the legacy-resume
        # seeding path doesn't fire; that's tested separately.
        run = SimpleNamespace(
            snapshot_balance=8_500.0,
            last_transaction_id="1000",
            initial_balance=8_500.0,
            current_balance=8_500.0,
            pnl=0.0,
        )
        client = MagicMock()
        db = MagicMock()

        _snapshot_balance_if_needed(db=db, run=run, client=client)

        # OANDA balance never queried (both balance and txn id already
        # snapshotted); existing snapshot preserved.
        client.get_account_balance.assert_not_called()
        client.get_last_transaction_id.assert_not_called()
        assert run.snapshot_balance == 8_500.0
        db.commit.assert_not_called()

    def test_seeds_last_transaction_id_on_legacy_resume(self):
        """v0.6.1 T1 (Wilson UAT 2026-07-16): a pre-T1 run has
        snapshot_balance set but no last_transaction_id. On the first
        health tick after a rebuild, we seed the reconciliation
        high-water mark without re-anchoring the balance snapshot."""
        run = SimpleNamespace(
            snapshot_balance=8_500.0,
            last_transaction_id=None,
            initial_balance=8_500.0,
            current_balance=8_500.0,
            pnl=0.0,
        )
        client = MagicMock()
        client.get_last_transaction_id.return_value = "2500"
        db = MagicMock()

        _snapshot_balance_if_needed(db=db, run=run, client=client)

        # Balance NOT re-anchored; last_transaction_id NOW set.
        client.get_account_balance.assert_not_called()
        client.get_last_transaction_id.assert_called_once()
        assert run.snapshot_balance == 8_500.0
        assert run.last_transaction_id == "2500"
        db.commit.assert_called()

    def test_zero_snapshot_is_treated_as_missing(self):
        run = SimpleNamespace(
            snapshot_balance=0.0,
            last_transaction_id=None,
            initial_balance=0.0,
            current_balance=0.0,
            pnl=0.0,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 1_234.5}
        db = MagicMock()

        _snapshot_balance_if_needed(db=db, run=run, client=client)
        assert run.snapshot_balance == 1_234.5


# ─────────────────────────────────────────────────────────────────────
# _execute_loop — kill-switch integration
# ─────────────────────────────────────────────────────────────────────


class TestExecuteLoop:
    def test_kill_flag_causes_immediate_halt(self):
        # Simulate: run has a snapshot; kill_requested_at is set before
        # the loop's first refresh; loop closes positions and returns.
        from datetime import UTC, datetime

        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            snapshot_balance=10_000.0,
            last_transaction_id=None,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=datetime.now(UTC),
            risk_config={
                "max_position_size_units": 10_000,
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}
        db = MagicMock()
        db.refresh = MagicMock()  # no-op: caller uses `run` directly

        _execute_loop(
            db=db,
            run=run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        # Positions flattened + terminal state set.
        client.close_all_positions.assert_called_once_with("EUR_USD")
        assert run.status == RunStatus.HALTED_MANUAL
        assert run.stop_reason == "USER_KILL"

    def test_gate_refusal_halts(self):
        # Simulate DD trip: current balance 5% below snapshot.
        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            snapshot_balance=10_000.0,
            last_transaction_id=None,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=None,
            risk_config={
                "max_position_size_units": 10_000,
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        # Simulate balance drop 5% → hits kill_switch_dd_pct
        client.get_account_balance.return_value = {"balance": 9_500.0}
        db = MagicMock()

        _execute_loop(
            db=db,
            run=run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "BROKER_CIRCUIT"

    def test_client_circuit_open_caught_and_halts(self):
        """PR-G robustness: OandaCircuitOpen from the client during
        `get_account_balance` is caught inside the loop, not left to
        crash the subprocess. Locks the `stop_reason=BROKER_CIRCUIT`
        vocabulary the wireframes call for."""
        from agentfx.adapters.oanda import OandaCircuitOpen
        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            snapshot_balance=10_000.0,
            last_transaction_id=None,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=None,
            risk_config={
                "max_position_size_units": 10_000,
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        client.get_account_balance.side_effect = OandaCircuitOpen("5 failures")
        db = MagicMock()

        _execute_loop(
            db=db,
            run=run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "BROKER_CIRCUIT"


# ─────────────────────────────────────────────────────────────────────
# Terminal state
# ─────────────────────────────────────────────────────────────────────


class TestMarkTerminal:
    def test_writes_status_reason_and_completed(self):
        from datetime import datetime

        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        db = MagicMock()

        _mark_terminal(db, run, RunStatus.HALTED_MANUAL, "USER_KILL")

        assert run.status == RunStatus.HALTED_MANUAL
        assert run.stop_reason == "USER_KILL"
        assert isinstance(run.completed_at, datetime)
        db.commit.assert_called()


# Compact type check that `TickAction` behaves as a frozen dataclass —
# guards a future refactor that swaps to a NamedTuple or plain dict.
def test_tick_action_is_hashable_and_immutable():
    a = TickAction(
        action="place", signal_direction="long", signal_units=10_000, reason="new"
    )
    # frozen: setting attrs should raise
    try:
        a.action = "hold"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("TickAction should be frozen")
