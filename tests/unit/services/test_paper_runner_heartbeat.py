"""AFX-92: the 401 retry ladder in the health tick must not starve the
heartbeat past the supervisor's zombie stale window.

On 2026-11-13 a run's adapter-level 401 ladder (~30+60+120s of sleeps
per call) plus the credential-refresh probe kept one loop iteration
busy for minutes. The heartbeat was iteration-counted (every 60th
iteration), so the supervisor saw a 16-minute-old heartbeat, declared
the live process a zombie and started a second runner.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentfx.adapters.oanda import OandaError
from agentfx.api.workers.paper_runner import _HEARTBEAT_STALE_SECONDS
from agentfx.services.paper_runner import _execute_loop

pytestmark = pytest.mark.unit

# Worst case for one adapter call that walks the full 401 ladder:
# 4 attempts x 15s timeout + ~30/60/120s (+15% jitter) of backoff.
_LADDER_CALL_SECONDS = 300.0


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _make_fake_datetime(clock: _FakeClock):
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 — mirrors datetime.now
            return clock.now

    return _FakeDateTime


def _run():
    from agentfx.api.models import RunStatus

    return SimpleNamespace(
        id="run_afx92",
        pair="AUD_USD",
        snapshot_balance=10_000.0,
        last_transaction_id="1",
        current_balance=10_000.0,
        pnl=0.0,
        kill_requested_at=None,
        risk_config={
            "max_position_size_units": 10_000,
            "loss_cap_pct": 2.0,
            "kill_switch_dd_pct": 5.0,
        },
        parameters={},
        status=RunStatus.RUNNING,
        stop_reason=None,
        completed_at=None,
    )


def _drive(run, client, clock, *, max_iterations, recover):
    heartbeats: list[datetime] = []

    def _on_commit():
        hb = (run.parameters or {}).get("last_heartbeat_at")
        if hb and (not heartbeats or heartbeats[-1].isoformat() != hb):
            heartbeats.append(datetime.fromisoformat(hb))

    db = MagicMock()
    db.commit.side_effect = _on_commit
    with (
        patch(
            "agentfx.services.paper_runner.datetime",
            _make_fake_datetime(clock),
        ),
        patch(
            "agentfx.services.paper_runner._recover_from_auth_loss",
            side_effect=recover,
        ),
        patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
    ):
        _execute_loop(
            db=db,
            run=run,
            client=client,
            sleep_fn=clock.advance,
            max_iterations=max_iterations,
        )
    return heartbeats


def _max_gap(start: datetime, heartbeats: list[datetime], end: datetime) -> float:
    points = [start, *heartbeats, end]
    return max((b - a).total_seconds() for a, b in zip(points, points[1:]))


class TestHeartbeatDuringAuthLadder:
    def test_401_ladder_keeps_heartbeat_inside_stale_window(self):
        start = datetime(2026, 11, 13, 22, 16, 11, tzinfo=UTC)
        clock = _FakeClock(start)
        run = _run()

        calls = {"n": 0}

        def _balance():
            calls["n"] += 1
            if calls["n"] == 2:
                # Original client walks the whole 401 ladder, then raises.
                clock.advance(_LADDER_CALL_SECONDS)
                raise OandaError("401 Insufficient authorization (attempt 4/4)")
            return {"balance": 10_000.0}

        client = MagicMock()
        client.get_account_balance.side_effect = _balance

        fresh_client = MagicMock()

        def _fresh_balance():
            # Re-read after refresh also hits a slow ladder before 200.
            clock.advance(_LADDER_CALL_SECONDS)
            return {"balance": 10_000.0}

        fresh_client.get_account_balance.side_effect = _fresh_balance

        def _recover(db, run, exc):  # noqa: ARG001
            # Refresh probe on the fresh client also ladders before 200.
            clock.advance(_LADDER_CALL_SECONDS)
            return fresh_client

        heartbeats = _drive(run, client, clock, max_iterations=3, recover=_recover)

        from agentfx.api.models import RunStatus

        assert heartbeats, "expected at least one heartbeat"
        assert run.status == RunStatus.RUNNING
        gap = _max_gap(start, heartbeats, clock.now)
        assert gap < _HEARTBEAT_STALE_SECONDS, (
            f"heartbeat gap {gap:.0f}s reached the {_HEARTBEAT_STALE_SECONDS:.0f}s "
            "zombie window during the 401 ladder"
        )

    def test_slow_iterations_still_heartbeat_on_wall_clock(self):
        """Iteration-counted heartbeats stretch when each iteration is
        slow; the heartbeat must also fire on elapsed wall time."""
        start = datetime(2026, 11, 13, 22, 0, 0, tzinfo=UTC)
        clock = _FakeClock(start)
        run = _run()

        def _slow_balance():
            clock.advance(60.0)
            return {"balance": 10_000.0}

        client = MagicMock()
        client.get_account_balance.side_effect = _slow_balance

        heartbeats = _drive(
            run, client, clock, max_iterations=30, recover=lambda *a: None
        )

        gap = _max_gap(start, heartbeats, clock.now)
        assert gap < _HEARTBEAT_STALE_SECONDS
