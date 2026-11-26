"""AFX-99: the liveness heartbeat comes from a dedicated thread, so it
stays fresh whatever the trading loop is blocked on.

History: AFX-92 found a 401 retry ladder starving the inline heartbeat
past the supervisor's 900s zombie window (a second runner was started
on a live one), and the first AFX-93 draft re-broke it with a 905s gap.
Now the runner's heartbeat thread writes `runs.last_heartbeat_at` on its
own session, and the loop only stamps `runs.last_loop_progress_at`, so
the supervisor can tell dead (stale heartbeat) from stuck (fresh
heartbeat, stale progress).

These tests run the real thread against a fake clock: the thread polls
every few milliseconds and stamps whatever the fake clock says, so
"blocked for 20 minutes" takes milliseconds of real time.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentfx.adapters.oanda import OandaError
from agentfx.api.models import RunStatus
from agentfx.api.workers import paper_runner as worker
from agentfx.api.workers.paper_runner import (
    _HEARTBEAT_STALE_SECONDS,
    _LOOP_PROGRESS_STALE_SECONDS,
)
from agentfx.services import paper_runner as runner
from agentfx.services.paper_runner import _execute_loop

pytestmark = pytest.mark.unit

_START = datetime(2026, 11, 13, 22, 16, 11, tzinfo=UTC)
_BALANCE = {"balance": 10_000.0}
_WAIT_TIMEOUT_SECONDS = 5.0


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


class _HeartbeatSessions:
    """Session factory for the heartbeat thread. Records every
    `last_heartbeat_at` value written and every session handed out."""

    def __init__(self) -> None:
        self.beats: list[datetime] = []
        self.sessions: list[MagicMock] = []
        self.threads: set[str] = set()

    def __call__(self):
        session = MagicMock(name=f"heartbeat_session_{len(self.sessions)}")

        def _update(values, synchronize_session=None):  # noqa: ARG001
            self.threads.add(threading.current_thread().name)
            self.beats.extend(values.values())
            return 1

        session.query.return_value.filter.return_value.update.side_effect = _update
        self.sessions.append(session)
        return session

    def wait_for_beat_at(self, instant: datetime) -> None:
        deadline = time.monotonic() + _WAIT_TIMEOUT_SECONDS
        while not (self.beats and self.beats[-1] >= instant):
            assert time.monotonic() < deadline, f"no heartbeat at {instant}"
            time.sleep(0.001)


def _run():
    return SimpleNamespace(
        id="run_afx99",
        user_id=7,
        pair="AUD_USD",
        snapshot_balance=10_000.0,
        last_transaction_id="1",
        current_balance=10_000.0,
        pnl=0.0,
        kill_requested_at=None,
        risk_config={},
        parameters={},
        status=RunStatus.RUNNING,
        stop_reason=None,
        completed_at=None,
        last_loop_progress_at=None,
    )


def _drive(run, client, clock, sessions, *, db=None, max_iterations=3):
    db = db if db is not None else MagicMock(name="loop_session")
    with (
        patch("agentfx.services.paper_runner.datetime", _make_fake_datetime(clock)),
        patch.object(runner, "_HEARTBEAT_INTERVAL_SECONDS", 0.002),
    ):
        _execute_loop(
            db=db,
            run=run,
            client=client,
            sleep_fn=clock.advance,
            now_fn=lambda: clock.now.timestamp(),
            max_iterations=max_iterations,
            heartbeat_session_factory=sessions,
        )
    return db


def _heartbeat_threads_alive() -> list[threading.Thread]:
    return [
        t
        for t in threading.enumerate()
        if t.name.startswith("paperrun-heartbeat-") and t.is_alive()
    ]


def _max_gap(points: list[datetime]) -> float:
    return max((b - a).total_seconds() for a, b in zip(points, points[1:]))


class TestHeartbeatThread:
    def test_heartbeat_stays_fresh_while_loop_is_blocked_in_a_broker_call(self):
        """Done-when #1: the loop blocks inside one broker call for longer
        than the zombie window; the heartbeat never goes stale."""
        clock = _FakeClock(_START)
        sessions = _HeartbeatSessions()
        run = _run()
        blocked_for = _HEARTBEAT_STALE_SECONDS + 300
        seen_while_blocked: list[float] = []

        calls = {"n": 0}

        def _balance():
            calls["n"] += 1
            if calls["n"] == 2:
                # One call that never returns for 20 fake minutes (three
                # chained ~300s 401 ladders and then some).
                for _ in range(int(blocked_for // 60)):
                    clock.advance(60)
                    sessions.wait_for_beat_at(clock.now)
                    age = (clock.now - sessions.beats[-1]).total_seconds()
                    seen_while_blocked.append(age)
            return _BALANCE

        client = MagicMock()
        client.get_account_balance.side_effect = _balance

        _drive(run, client, clock, sessions)

        assert run.status == RunStatus.RUNNING
        assert len(seen_while_blocked) == blocked_for // 60
        assert max(seen_while_blocked) < _HEARTBEAT_STALE_SECONDS
        gap = _max_gap([_START, *sessions.beats])
        assert gap < _HEARTBEAT_STALE_SECONDS, (
            f"heartbeat gap {gap:.0f}s reached the {_HEARTBEAT_STALE_SECONDS:.0f}s "
            "zombie window while the loop was blocked"
        )

    def test_weekend_persistent_401_keeps_heartbeat_fresh_without_forced_beats(self):
        """The AFX-93 worst case: every balance call walks the full 401
        ladder and every refresh fails, all weekend. Nothing in the
        auth path beats any more; the thread alone keeps it fresh."""
        clock = _FakeClock(_START)  # Friday 22:16Z, market closed
        sessions = _HeartbeatSessions()
        run = _run()

        def _ladder():
            clock.advance(300)
            sessions.wait_for_beat_at(clock.now)
            raise OandaError("401 Insufficient authorization (attempt 4/4)")

        client = MagicMock()
        client.get_account_balance.side_effect = _ladder

        def _recover(db, run, exc):  # noqa: ARG001
            clock.advance(300)
            sessions.wait_for_beat_at(clock.now)
            return None

        with patch(
            "agentfx.services.paper_runner._recover_from_auth_loss",
            side_effect=_recover,
        ):
            _drive(run, client, clock, sessions, max_iterations=10)

        assert run.status == RunStatus.RUNNING
        client.close_all_positions.assert_not_called()
        assert _max_gap([_START, *sessions.beats, clock.now]) < _HEARTBEAT_STALE_SECONDS

    def test_heartbeat_written_before_first_broker_call(self):
        clock = _FakeClock(_START)
        sessions = _HeartbeatSessions()
        seen_at_first_call: list[list[datetime]] = []

        def _balance():
            if not seen_at_first_call:
                seen_at_first_call.append(list(sessions.beats))
            return _BALANCE

        client = MagicMock()
        client.get_account_balance.side_effect = _balance

        _drive(_run(), client, clock, sessions, max_iterations=1)

        assert seen_at_first_call and seen_at_first_call[0][:1] == [_START]

    def test_heartbeat_never_uses_the_loop_session(self):
        clock = _FakeClock(_START)
        sessions = _HeartbeatSessions()
        run = _run()
        client = MagicMock()
        client.get_account_balance.return_value = _BALANCE

        db = _drive(run, client, clock, sessions)

        assert sessions.beats
        assert all(s is not db for s in sessions.sessions)
        # Each beat opens and closes its own session.
        assert all(s.close.call_count == 1 for s in sessions.sessions)
        # The loop session never writes the heartbeat; the loop only
        # stamps its own progress on the run.
        db.query.assert_not_called()
        assert not hasattr(run, "last_heartbeat_at")
        assert run.last_loop_progress_at == _START

    def test_beat_is_scoped_to_this_runner_while_running(self):
        """A superseded or halted runner's thread must not keep a run
        looking alive: the UPDATE is filtered on id, RUNNING and pid."""
        sessions = _HeartbeatSessions()
        beat = runner._HeartbeatThread(
            run_id="run_afx99", expected_pid=4242, session_factory=sessions
        )
        beat._beat()

        (session,) = sessions.sessions
        conditions = session.query.return_value.filter.call_args.args
        rendered = " ".join(
            str(c.compile(compile_kwargs={"literal_binds": True})) for c in conditions
        )
        assert "runs.id = 'run_afx99'" in rendered
        assert "runs.status = 'RUNNING'" in rendered
        assert "runs.process_pid = 4242" in rendered

    def test_failed_beat_is_logged_and_the_next_one_still_runs(self):
        sessions = _HeartbeatSessions()
        calls = {"n": 0}

        def _flaky_factory():
            calls["n"] += 1
            session = sessions()
            if calls["n"] == 1:
                session.commit.side_effect = RuntimeError("db blip")
            return session

        beat = runner._HeartbeatThread(
            run_id="run_afx99", expected_pid=None, session_factory=_flaky_factory
        )
        beat._beat()
        beat._beat()

        assert calls["n"] == 2
        assert all(s.close.call_count == 1 for s in sessions.sessions)


class TestHeartbeatThreadStops:
    @pytest.mark.parametrize(
        ("kill", "balance_error", "expected_status", "expected_reason"),
        [
            (True, None, RunStatus.HALTED_MANUAL, "USER_KILL"),
            (False, OandaError("401 bad token"), RunStatus.ERROR, "OANDA_AUTH_LOST"),
        ],
        ids=["kill", "halt"],
    )
    def test_thread_stops_when_the_loop_exits(
        self, kill, balance_error, expected_status, expected_reason
    ):
        clock = _FakeClock(datetime(2026, 11, 12, 14, 0, tzinfo=UTC))  # market open
        sessions = _HeartbeatSessions()
        run = _run()
        if kill:
            run.kill_requested_at = clock.now
        client = MagicMock()
        client.get_account_balance.side_effect = balance_error
        client.get_account_balance.return_value = _BALANCE
        client.close_all_positions.return_value = {"long_units_closed": 0}
        client.get_position.return_value = {"long_units": 0, "short_units": 0}

        with (
            patch(
                "agentfx.services.paper_runner._recover_from_auth_loss",
                return_value=None,
            ),
            patch("agentfx.notifications.post_paper_run_alert", lambda **_: None),
        ):
            _drive(run, client, clock, sessions, max_iterations=None)

        assert run.status == expected_status
        assert run.stop_reason == expected_reason
        assert _heartbeat_threads_alive() == []
        beats = len(sessions.beats)
        time.sleep(0.02)
        assert len(sessions.beats) == beats, "heartbeat kept beating after exit"

    def test_thread_stops_when_the_loop_crashes(self):
        clock = _FakeClock(_START)
        sessions = _HeartbeatSessions()
        client = MagicMock()
        client.get_account_balance.side_effect = RuntimeError("bug in the loop")

        with pytest.raises(RuntimeError, match="bug in the loop"):
            _drive(_run(), client, clock, sessions)

        assert _heartbeat_threads_alive() == []


class TestWedgedLoopIsStuckNotDead:
    def test_wedged_loop_goes_stale_and_supervisor_classifies_it_stuck(self):
        """Done-when #2: the loop hangs inside a broker call (socket with
        no timeout). The heartbeat stays fresh, the progress stamp goes
        stale, and the supervisor records the run as stuck, stops the
        live runner, and restarts only once its exit is confirmed."""
        clock = _FakeClock(_START)
        sessions = _HeartbeatSessions()
        run = _run()
        entered = threading.Event()
        release = threading.Event()

        calls = {"n": 0}

        def _balance():
            calls["n"] += 1
            if calls["n"] == 2:
                entered.set()
                release.wait(_WAIT_TIMEOUT_SECONDS)
            return _BALANCE

        client = MagicMock()
        client.get_account_balance.side_effect = _balance
        loop = threading.Thread(
            target=_drive, args=(run, client, clock, sessions), daemon=True
        )
        loop.start()
        try:
            assert entered.wait(_WAIT_TIMEOUT_SECONDS)
            progress_at_wedge = run.last_loop_progress_at
            clock.advance(_LOOP_PROGRESS_STALE_SECONDS + 60)
            sessions.wait_for_beat_at(clock.now)

            heartbeat = sessions.beats[-1]
            assert run.last_loop_progress_at == progress_at_wedge
            assert (clock.now - heartbeat).total_seconds() < _HEARTBEAT_STALE_SECONDS
            progress_age = (clock.now - progress_at_wedge).total_seconds()
            assert progress_age >= _LOOP_PROGRESS_STALE_SECONDS

            # What the supervisor reads from the row at this moment.
            row = SimpleNamespace(
                id=run.id,
                status=RunStatus.RUNNING,
                stop_reason=None,
                completed_at=None,
                approved_at=_START - timedelta(days=3),
                parameters={},
                process_pid=37,
                pair=run.pair,
                user_id=run.user_id,
                last_heartbeat_at=heartbeat,
                last_loop_progress_at=progress_at_wedge,
            )
            events: list[str] = []
            exited = threading.Event()
            proc = MagicMock()
            proc.pid = 37
            proc.terminate.side_effect = lambda: (
                events.append("terminate"),
                exited.set(),
            )
            handle = worker._ChildHandle(proc=proc, exited=exited, run_id=run.id)
            db = MagicMock()
            db.query.return_value.filter.return_value.all.return_value = [row]
            with (
                patch.dict(worker._CHILDREN, {37: handle}, clear=True),
                patch.object(
                    worker,
                    "schedule_paper_run",
                    side_effect=lambda rid: events.append(f"schedule:{rid}"),
                ),
                patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()),
            ):
                restarted = worker.scan_and_restart_zombie_running(db=db, now=clock.now)
        finally:
            release.set()
            loop.join(_WAIT_TIMEOUT_SECONDS)

        assert restarted == 1
        assert (
            row.parameters["auto_restart_prior_reason"] == "stuck:loop progress stale"
        )
        assert events == ["terminate", f"schedule:{run.id}"]
        assert handle.superseded is True
        assert row.last_heartbeat_at is None
        assert row.last_loop_progress_at is None
