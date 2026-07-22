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
