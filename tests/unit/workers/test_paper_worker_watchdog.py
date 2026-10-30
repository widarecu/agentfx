"""Unit tests for the paper-runner watchdog notification wiring
(v0.6.2 — Wilson UAT 2026-07-19).

`_mark_run_failed_after_crash` in `src/agentfx/api/workers/paper_runner.py`
runs in the parent uvicorn process when the subprocess is reaped with
a non-zero exit code. It writes the ERROR state to the DB and fires a
Slack alert with the exit code in `extra`.

We lock two invariants here:
1. A crash while the run was in RUNNING or APPROVED fires exactly one
   notification with the recorded stop_reason.
2. If the notification hook itself raises, the watchdog still keeps
   the ERROR state committed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.unit


def _stub_run(status):
    """A minimal Run row the watchdog will mutate."""
    return SimpleNamespace(
        id="run_watchdog_x",
        status=status,
        stop_reason=None,
        completed_at=None,
        parameters={},
    )


class TestWatchdogNotificationWiring:
    def test_running_crash_fires_notification_with_exit_code(self):
        """A subprocess reaped from RUNNING with a non-zero exit fires
        one notification tagged `process_crashed`."""
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _mark_run_failed_after_crash,
        )

        run = _stub_run(RunStatus.RUNNING)
        db = MagicMock()

        calls: list[dict] = []

        with (
            patch(
                "agentfx.api.core.database.SessionLocal",
                return_value=db,
            ),
            patch(
                "agentfx.api.services.paper.get_paper_run",
                return_value=run,
            ),
            patch(
                "agentfx.notifications.post_paper_run_alert",
                side_effect=lambda **kwargs: calls.append(kwargs),
            ),
        ):
            _mark_run_failed_after_crash("run_watchdog_x", exit_code=137)

        assert run.status == RunStatus.ERROR
        assert "process_crashed" in run.stop_reason
        assert "137" in run.stop_reason
        assert len(calls) == 1
        assert calls[0]["reason"] == run.stop_reason
        assert calls[0]["extra"] == {"exit_code": 137}

    def test_approved_pre_init_crash_fires_notification(self):
        """Subprocess dies before flipping the run to RUNNING (e.g.
        credentials-decrypt failure). Reason becomes
        `crashed_pre_init` and the watchdog still notifies."""
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _mark_run_failed_after_crash,
        )

        run = _stub_run(RunStatus.APPROVED)
        db = MagicMock()

        calls: list[dict] = []

        with (
            patch(
                "agentfx.api.core.database.SessionLocal",
                return_value=db,
            ),
            patch(
                "agentfx.api.services.paper.get_paper_run",
                return_value=run,
            ),
            patch(
                "agentfx.notifications.post_paper_run_alert",
                side_effect=lambda **kwargs: calls.append(kwargs),
            ),
        ):
            _mark_run_failed_after_crash("run_watchdog_x", exit_code=1)

        assert run.status == RunStatus.ERROR
        assert "crashed_pre_init" in run.stop_reason
        assert len(calls) == 1

    def test_crash_release_slot_called(self):
        """AFX-7: watchdog must release the pool slot when flipping a
        crashed run to ERROR, otherwise a hard-killed subprocess
        leaks its slot until the 14-day lease TTL expires."""
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import _mark_run_failed_after_crash

        run = _stub_run(RunStatus.RUNNING)
        db = MagicMock()

        release_calls: list[dict] = []

        def spy_release(session, *, run_id):
            release_calls.append({"run_id": run_id})
            return True

        with (
            patch("agentfx.api.core.database.SessionLocal", return_value=db),
            patch("agentfx.api.services.paper.get_paper_run", return_value=run),
            patch("agentfx.notifications.post_paper_run_alert", lambda **_: None),
            patch(
                "agentfx.agents.fleet.account_pool.release_slot",
                side_effect=spy_release,
            ),
        ):
            _mark_run_failed_after_crash("run_watchdog_crash", exit_code=137)

        assert release_calls == [{"run_id": "run_watchdog_crash"}]
        assert run.status == RunStatus.ERROR

    def test_crash_release_slot_failure_does_not_break_watchdog(self):
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import _mark_run_failed_after_crash

        run = _stub_run(RunStatus.RUNNING)
        db = MagicMock()

        def boom(session, *, run_id):
            raise RuntimeError("pool DB down")

        with (
            patch("agentfx.api.core.database.SessionLocal", return_value=db),
            patch("agentfx.api.services.paper.get_paper_run", return_value=run),
            patch("agentfx.notifications.post_paper_run_alert", lambda **_: None),
            patch("agentfx.agents.fleet.account_pool.release_slot", side_effect=boom),
        ):
            _mark_run_failed_after_crash("run_watchdog_crash", exit_code=1)
        assert run.status == RunStatus.ERROR

    def test_hook_exception_does_not_break_watchdog(self):
        """If the notification hook explodes, the ERROR state must
        still be persisted. The watchdog is the last line of defence
        against a runaway subprocess; nothing above it can catch
        this."""
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _mark_run_failed_after_crash,
        )

        run = _stub_run(RunStatus.RUNNING)
        db = MagicMock()

        def boom(**kwargs):
            raise RuntimeError("notification daemon on fire")

        with (
            patch(
                "agentfx.api.core.database.SessionLocal",
                return_value=db,
            ),
            patch(
                "agentfx.api.services.paper.get_paper_run",
                return_value=run,
            ),
            patch("agentfx.notifications.post_paper_run_alert", side_effect=boom),
        ):
            # Must not raise.
            _mark_run_failed_after_crash("run_watchdog_x", exit_code=-9)

        assert run.status == RunStatus.ERROR
        db.commit.assert_called()

    def test_terminal_run_is_left_alone(self):
        """Watchdog on a run already in a terminal state (e.g. the
        subprocess crashed AFTER calling `_mark_terminal` itself with
        USER_KILL) must NOT overwrite the reason or double-notify."""
        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _mark_run_failed_after_crash,
        )

        run = SimpleNamespace(
            id="run_watchdog_x",
            status=RunStatus.HALTED_MANUAL,
            stop_reason="USER_KILL",
            completed_at="already_set",
            parameters={"notified": {"USER_KILL": "2026-07-22T09:00:00+00:00"}},
        )
        db = MagicMock()
        calls: list[dict] = []

        with (
            patch(
                "agentfx.api.core.database.SessionLocal",
                return_value=db,
            ),
            patch(
                "agentfx.api.services.paper.get_paper_run",
                return_value=run,
            ),
            patch(
                "agentfx.notifications.post_paper_run_alert",
                side_effect=lambda **kwargs: calls.append(kwargs),
            ),
        ):
            _mark_run_failed_after_crash("run_watchdog_x", exit_code=0)

        # Terminal state preserved — watchdog only overwrites RUNNING
        # or APPROVED (see the `if run.status in {...}` guard).
        assert run.status == RunStatus.HALTED_MANUAL
        assert run.stop_reason == "USER_KILL"
        # No extra notification — the subprocess's own `_mark_terminal`
        # was the notification-fanout, not the watchdog.
        assert calls == []


# ─────────────────────────────────────────────────────────────────────
# Auto-restart supervisor (Wilson 2026-08-23)
# ─────────────────────────────────────────────────────────────────────


class TestIsTransientStopReason:
    """Only `BROKER_NETWORK` and the `process_crashed*` /
    `crashed_pre_init*` prefixes qualify for auto-restart. Everything
    else stays dead until a human intervenes."""

    def test_broker_network_is_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        assert _is_transient_stop_reason("BROKER_NETWORK") is True

    def test_process_crashed_with_exit_code_is_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        assert _is_transient_stop_reason("process_crashed (exit_code=1)") is True
        assert _is_transient_stop_reason("crashed_pre_init (exit_code=1)") is True

    def test_loss_cap_hit_is_not_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        # LOSS_CAP_HIT means a risk gate tripped — operator should
        # review the strategy, not blind-restart.
        assert _is_transient_stop_reason("LOSS_CAP_HIT") is False

    def test_broker_circuit_is_not_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        # Kill-switch DD — same as LOSS_CAP_HIT, needs review.
        assert _is_transient_stop_reason("BROKER_CIRCUIT") is False

    def test_oanda_auth_lost_is_not_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        # Credential problem — auto-restart would just re-fail.
        assert _is_transient_stop_reason("OANDA_AUTH_LOST") is False

    def test_user_kill_is_not_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        assert _is_transient_stop_reason("USER_KILL") is False

    def test_none_is_not_transient(self):
        from agentfx.api.workers.paper_runner import _is_transient_stop_reason

        assert _is_transient_stop_reason(None) is False
        assert _is_transient_stop_reason("") is False


class TestScanAndRestart:
    """Drive the supervisor tick with a fake DB session + candidate
    Run rows. Verify eligibility gates."""

    def _stub_run(
        self,
        *,
        status,
        stop_reason: str | None,
        completed_at,
        approved_at=None,
        parameters: dict | None = None,
        run_id: str = "run_supervisor_x",
    ):
        return SimpleNamespace(
            id=run_id,
            status=status,
            stop_reason=stop_reason,
            completed_at=completed_at,
            approved_at=approved_at,
            parameters=parameters or {},
            process_pid=1234,
            pair="EUR_USD",
            user_id=42,
        )

    def _fake_db(self, candidates):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        db.add = MagicMock()
        db.commit = MagicMock()
        return db

    def test_restarts_broker_network_after_backoff(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _RESTART_BACKOFF_SECONDS,
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        # First-attempt backoff = 60s. Halt was 5 min ago → past backoff.
        run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="BROKER_NETWORK",
            completed_at=now - timedelta(minutes=5),
            approved_at=now - timedelta(days=2),
        )
        db = self._fake_db([run])

        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        assert restarted == 1
        assert scheduled == [run.id]
        assert run.status == RunStatus.APPROVED
        assert run.stop_reason is None
        assert run.completed_at is None
        assert run.parameters["auto_restart_attempts"] == 1
        assert run.parameters["auto_restart_prior_reason"] == "BROKER_NETWORK"

    def test_respects_backoff_window(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        # Halt was 30s ago — still inside the 60s first-attempt backoff.
        run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="BROKER_NETWORK",
            completed_at=now - timedelta(seconds=30),
            approved_at=now - timedelta(days=2),
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        assert restarted == 0
        assert scheduled == []
        assert run.status == RunStatus.ERROR  # unchanged

    def test_stops_after_max_attempts(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _MAX_AUTO_RESTART_ATTEMPTS,
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        # Already hit max attempts. Halt was 1 hour ago (plenty past
        # the last backoff). Should NOT restart.
        run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="BROKER_NETWORK",
            completed_at=now - timedelta(hours=1),
            # approved_at recent enough that lifetime < reset window
            # (24h), so counter stays at max.
            approved_at=now - timedelta(hours=6),
            parameters={"auto_restart_attempts": _MAX_AUTO_RESTART_ATTEMPTS},
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        assert restarted == 0
        assert scheduled == []
        assert run.status == RunStatus.ERROR

    def test_skips_non_transient_reasons(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        loss_cap_run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="LOSS_CAP_HIT",
            completed_at=now - timedelta(hours=6),
            approved_at=now - timedelta(days=2),
            run_id="run_losscap",
        )
        auth_lost_run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="OANDA_AUTH_LOST",
            completed_at=now - timedelta(hours=6),
            approved_at=now - timedelta(days=2),
            run_id="run_authlost",
        )
        db = self._fake_db([loss_cap_run, auth_lost_run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        assert restarted == 0
        assert scheduled == []

    def test_process_crashed_with_exit_code_restarts(self):
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="process_crashed (exit_code=1)",
            completed_at=now - timedelta(minutes=5),
            approved_at=now - timedelta(days=2),
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        assert restarted == 1
        assert scheduled == [run.id]
        assert (
            run.parameters["auto_restart_prior_reason"]
            == "process_crashed (exit_code=1)"
        )

    def test_attempt_counter_resets_after_healthy_lifetime(self):
        """A run that survived past the 24h reset window before crashing
        again gets a fresh 3-attempt budget."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _MAX_AUTO_RESTART_ATTEMPTS,
            scan_and_restart_transient_errors,
        )

        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
        # Ran healthy for 3 days (> 24h reset window) then crashed 5min
        # ago. Attempts counter was already at max but should reset.
        run = self._stub_run(
            status=RunStatus.ERROR,
            stop_reason="BROKER_NETWORK",
            completed_at=now - timedelta(minutes=5),
            approved_at=now - timedelta(days=3),
            parameters={"auto_restart_attempts": _MAX_AUTO_RESTART_ATTEMPTS},
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            # SimpleNamespace stub isn't a real SQLAlchemy instance;
            # skip the change-tracking flag rather than mocking the
            # whole ORM machinery.
            patch(
                "sqlalchemy.orm.attributes.flag_modified",
                MagicMock(),
            ),
        ):
            restarted = scan_and_restart_transient_errors(db=db, now=now)

        # Fresh budget → restart happened, counter reset then bumped to 1.
        assert restarted == 1
        assert run.parameters["auto_restart_attempts"] == 1


class TestScanZombieRunning:
    """Wilson 2026-08-24: second supervisor scan catches paper runs
    that are RUNNING in the DB but whose subprocess is dead."""

    def _stub_run(
        self,
        *,
        process_pid,
        approved_at,
        parameters: dict | None = None,
        run_id: str = "run_zombie_x",
    ):
        from agentfx.api.models import RunStatus

        return SimpleNamespace(
            id=run_id,
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
            approved_at=approved_at,
            parameters=parameters or {},
            process_pid=process_pid,
            pair="EUR_USD",
            user_id=42,
        )

    def _fake_db(self, candidates):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        db.add = MagicMock()
        db.commit = MagicMock()
        return db

    def test_null_pid_running_run_gets_restarted(self):
        """The Aug 23 failure mode: status=RUNNING but process_pid=None.
        This is a zombie."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            scan_and_restart_zombie_running,
        )

        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        run = self._stub_run(
            process_pid=None,
            approved_at=now - timedelta(days=2),
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
        ):
            restarted = scan_and_restart_zombie_running(db=db, now=now)

        assert restarted == 1
        assert scheduled == [run.id]
        assert run.status == RunStatus.APPROVED
        assert run.process_pid is None
        assert run.parameters["auto_restart_attempts"] == 1
        assert run.parameters["auto_restart_prior_reason"].startswith("zombie:")

    def test_stale_heartbeat_running_run_gets_restarted(self):
        """A RUNNING run with a pid but no heartbeat for 20 min is
        still zombie (the subprocess may be alive but is not doing
        any work — the loop is stuck or crashed silently)."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.models import RunStatus
        from agentfx.api.workers.paper_runner import (
            _HEARTBEAT_STALE_SECONDS,
            scan_and_restart_zombie_running,
        )

        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        stale_hb = (now - timedelta(seconds=_HEARTBEAT_STALE_SECONDS + 60)).isoformat()
        run = self._stub_run(
            process_pid=12345,
            approved_at=now - timedelta(days=2),
            parameters={"last_heartbeat_at": stale_hb},
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
        ):
            restarted = scan_and_restart_zombie_running(db=db, now=now)

        assert restarted == 1
        assert scheduled == [run.id]
        assert run.status == RunStatus.APPROVED

    def test_fresh_heartbeat_running_run_is_not_touched(self):
        """A healthy RUNNING run with a fresh heartbeat and a live
        pid must NOT be restarted."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.workers.paper_runner import (
            scan_and_restart_zombie_running,
        )

        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        fresh_hb = (now - timedelta(seconds=60)).isoformat()
        run = self._stub_run(
            process_pid=12345,
            approved_at=now - timedelta(days=2),
            parameters={"last_heartbeat_at": fresh_hb},
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
        ):
            restarted = scan_and_restart_zombie_running(db=db, now=now)

        assert restarted == 0
        assert scheduled == []

    def test_freshly_scheduled_run_gets_grace_period(self):
        """A run approved 60s ago that hasn't emitted a heartbeat yet
        must not be classified zombie — the subprocess may still be
        booting."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.workers.paper_runner import (
            scan_and_restart_zombie_running,
        )

        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        run = self._stub_run(
            process_pid=12345,  # live pid, no heartbeat yet
            approved_at=now - timedelta(seconds=60),  # 60s ago
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
        ):
            restarted = scan_and_restart_zombie_running(db=db, now=now)

        assert restarted == 0

    def test_zombie_respects_max_attempts(self):
        """After the max restart budget is exhausted, zombie runs are
        left alone (Slack + manual intervention)."""
        from datetime import UTC, datetime, timedelta

        from agentfx.api.workers.paper_runner import (
            _MAX_AUTO_RESTART_ATTEMPTS,
            scan_and_restart_zombie_running,
        )

        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        run = self._stub_run(
            process_pid=None,
            approved_at=now - timedelta(hours=6),
            parameters={"auto_restart_attempts": _MAX_AUTO_RESTART_ATTEMPTS},
        )
        db = self._fake_db([run])
        scheduled: list[str] = []
        with (
            patch(
                "agentfx.api.workers.paper_runner.schedule_paper_run",
                side_effect=lambda rid: scheduled.append(rid),
            ),
            patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
        ):
            restarted = scan_and_restart_zombie_running(db=db, now=now)

        assert restarted == 0
        assert scheduled == []
