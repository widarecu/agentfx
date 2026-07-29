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
            id="run_x",
            parameters={},
        )
        db = MagicMock()

        _mark_terminal(db, run, RunStatus.HALTED_MANUAL, "USER_KILL")

        assert run.status == RunStatus.HALTED_MANUAL
        assert run.stop_reason == "USER_KILL"
        assert isinstance(run.completed_at, datetime)
        db.commit.assert_called()

    def test_invokes_notification_hook_once_per_unique_reason(self, monkeypatch):
        """Wilson UAT 2026-07-19 — every terminal event should fire
        exactly one Slack alert. Re-entrant halt paths must not spam
        the channel (see `_should_send` dedup)."""
        from agentfx.api.models import RunStatus
        from agentfx.notifications import slack as slack_module

        calls: list[tuple[str, str]] = []

        def spy_post(**kwargs):
            calls.append((kwargs["run"].id, kwargs["reason"]))
            # Also mutate parameters like the real helper does, so
            # dedup semantics are exercised end-to-end.
            slack_module._mark_notified(kwargs["run"], kwargs["reason"])

        monkeypatch.setattr("agentfx.notifications.post_paper_run_alert", spy_post)

        run = SimpleNamespace(
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
            id="run_x",
            parameters={},
        )
        db = MagicMock()

        # First terminal call fires the alert.
        _mark_terminal(db, run, RunStatus.HALTED_MANUAL, "USER_KILL")
        assert calls == [("run_x", "USER_KILL")]

        # Idempotent — if some future code path re-runs _mark_terminal
        # with the same reason (e.g. a watchdog picking up an already-
        # terminated run), NO extra alert should fire.
        _mark_terminal(db, run, RunStatus.HALTED_MANUAL, "USER_KILL")
        # The spy IS called again because our monkeypatch bypasses
        # the real dedup — but the actual `post_paper_run_alert` DOES
        # dedup via `run.parameters['notified']`, verified in
        # tests/unit/notifications/test_slack.py::
        # test_second_call_same_reason_does_not_post_again. Here we
        # just assert the hook wiring itself works.
        assert len(calls) == 2  # hook fires; dedup lives inside the hook

    def test_hook_exception_does_not_break_terminal_write(self, monkeypatch):
        """If the notification hook itself raises, the terminal state
        must still be committed — the trade is more important than
        the alert."""
        from agentfx.api.models import RunStatus

        def boom(**kwargs):
            raise RuntimeError("simulated notification implosion")

        monkeypatch.setattr("agentfx.notifications.post_paper_run_alert", boom)

        run = SimpleNamespace(
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
            id="run_x",
            parameters={},
        )
        db = MagicMock()

        # Must not raise.
        _mark_terminal(db, run, RunStatus.ERROR, "OANDA_AUTH_LOST")

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "OANDA_AUTH_LOST"
        db.commit.assert_called()


class TestEnsureStartedAt:
    """Wilson UAT 2026-07-24: `started_at` was being rewritten on every
    subprocess boot (including lifespan orphan-recovery after a Railway
    container swap). That collapsed the LivePnLChart X-axis because
    all historical equity samples predated the "restart" and got
    clamped to `frac=0`. `_ensure_started_at` is the guard: set once
    when None, never overwrite. Tests call the real helper so a
    regression in the guard is actually caught (not shadowed by a
    duplicated test-local implementation)."""

    def test_first_boot_sets_started_at(self):
        from agentfx.services.paper_runner import _ensure_started_at

        run = SimpleNamespace(started_at=None)
        _ensure_started_at(run)
        assert run.started_at is not None

    def test_restart_does_not_overwrite_started_at(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.services.paper_runner import _ensure_started_at

        original_start = datetime.now(UTC) - timedelta(days=7)
        run = SimpleNamespace(started_at=original_start)
        _ensure_started_at(run)
        assert run.started_at == original_start

    def test_repeated_calls_are_idempotent(self):
        """Even if run_paper_run were to call the guard twice in one
        boot for any reason, the value should still equal the FIRST
        call's timestamp — never advance."""
        from agentfx.services.paper_runner import _ensure_started_at

        run = SimpleNamespace(started_at=None)
        _ensure_started_at(run)
        first = run.started_at
        # Second call must not advance.
        _ensure_started_at(run)
        assert run.started_at == first


# ─────────────────────────────────────────────────────────────────────
# _fold_anchor_from_time (Wilson UAT 2026-07-29 Fix A)
# ─────────────────────────────────────────────────────────────────────


class TestFoldAnchorFromTime:
    """The paper runner must anchor cumulative indicators (OBV,
    ad_line) to fold-length windows that match validation semantics,
    not a fixed rolling window. Full details in the docstring on
    `_fold_anchor_from_time` and the Wilson UAT 2026-07-29 audit."""

    def _make(
        self,
        *,
        fold_days: int = 22,
        current_fold_index: int = 0,
        approved_days_ago: float = 5.0,
        has_open_position: bool = False,
    ):
        from datetime import UTC, datetime, timedelta

        now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
        approved = now - timedelta(days=approved_days_ago)
        run = SimpleNamespace(
            id="run_test",
            approved_at=approved,
            parameters={
                "fold_length_days": fold_days,
                "current_fold_index": current_fold_index,
            },
        )
        db = MagicMock()
        # Trades query — count() returns 1 if open position, else 0.
        db.query.return_value.filter.return_value.count.return_value = (
            1 if has_open_position else 0
        )
        return db, run, now

    def test_within_first_fold_anchor_is_approved_at(self):
        from agentfx.services.paper_runner import _fold_anchor_from_time

        db, run, now = self._make(approved_days_ago=5.0, fold_days=22)
        from_time, idx = _fold_anchor_from_time(run=run, db=db, now_utc=now)
        assert from_time == run.approved_at
        assert idx == 0

    def test_rolls_to_next_fold_when_boundary_crossed_and_flat(self):
        """22-day fold + 25 days elapsed + flat position → advance to
        fold_index=1, anchor becomes approved + 22 days."""
        from datetime import timedelta

        from agentfx.services.paper_runner import _fold_anchor_from_time

        db, run, now = self._make(
            approved_days_ago=25.0, fold_days=22, has_open_position=False
        )
        from_time, idx = _fold_anchor_from_time(run=run, db=db, now_utc=now)
        assert idx == 1
        assert from_time == run.approved_at + timedelta(days=22)
        # And the run.parameters got updated.
        assert run.parameters["current_fold_index"] == 1
        db.commit.assert_called_once()

    def test_does_not_roll_when_position_open(self):
        """Same 25-day elapsed but a position is open → hold at old
        anchor. Walk-forward training never carried positions across
        fold boundaries, so we mirror that: wait for the position to
        close naturally before advancing."""
        from agentfx.services.paper_runner import _fold_anchor_from_time

        db, run, now = self._make(
            approved_days_ago=25.0, fold_days=22, has_open_position=True
        )
        from_time, idx = _fold_anchor_from_time(run=run, db=db, now_utc=now)
        assert idx == 0
        assert from_time == run.approved_at
        assert run.parameters["current_fold_index"] == 0
        db.commit.assert_not_called()

    def test_defaults_to_30d_when_fold_length_missing(self):
        """Legacy runs deployed before Fix A won't have fold_length_days
        in parameters. Fall back to a sensible 30-day default rather
        than crash."""
        from datetime import UTC, datetime, timedelta

        from agentfx.services.paper_runner import _fold_anchor_from_time

        now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
        approved = now - timedelta(days=10.0)
        run = SimpleNamespace(
            id="run_legacy",
            approved_at=approved,
            parameters={},  # empty
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        from_time, idx = _fold_anchor_from_time(run=run, db=db, now_utc=now)
        # 10 days < 30 default → still fold 0.
        assert idx == 0
        assert from_time == approved

    def test_multi_fold_boundary_jumps_directly_to_current(self):
        """If a run was quiet for a long time and multiple fold
        boundaries elapsed, jump to the CURRENT fold (not just +1)."""
        from datetime import timedelta

        from agentfx.services.paper_runner import _fold_anchor_from_time

        db, run, now = self._make(
            approved_days_ago=70.0,
            fold_days=22,
            current_fold_index=0,
            has_open_position=False,
        )
        # 70/22 = 3.18 → target_fold_index = 3
        from_time, idx = _fold_anchor_from_time(run=run, db=db, now_utc=now)
        assert idx == 3
        assert from_time == run.approved_at + timedelta(days=66)


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
