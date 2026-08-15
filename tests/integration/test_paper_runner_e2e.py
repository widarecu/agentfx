"""End-to-end integration for the paper runner loop (PR-G).

Drives `_execute_loop` inline against real Postgres with a mocked
OandaClient. Skips the subprocess spawn — that path is exercised
in PR-F's router integration tests. Locks the four terminal
state transitions the plan calls out:

- USER_KILL — router sets kill flag, runner observes, halts <5s tick
- LOSS_CAP_HIT — declining balance trips the cumulative loss cap
- BROKER_CIRCUIT — OandaCircuitOpen raised by the client halts the run
- Snapshot capture — first-tick balance persists as `snapshot_balance`

`unittest.mock` is used instead of respx / requests_mock because the
runner talks to `OandaClient` directly, not to raw HTTP, so mocking
the client method is the tighter test surface.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _mk_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{str(uuid4())[:8]}"


@pytest.fixture
def alice_user(db_session: Session):
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    unique = str(uuid4())[:8]
    u = User(
        email=f"e2e_alice_{unique}@example.com",
        hashed_password=get_password_hash("alicepass"),
        role="trader",
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def alice_paper_run(db_session: Session, alice_user):
    """A paper run in RUNNING state (bypasses the router — this test
    drives the loop, not the approval flow)."""
    from src.agentfx.api.models import Run, RunEnvironment, RunStatus

    run = Run(
        id=_mk_id("run"),
        strategy_id=None,
        pair="EUR_USD",
        environment=RunEnvironment.PAPER,
        status=RunStatus.RUNNING,
        parameters={"granularity": "H1", "dsl_json": {"stub": True}},
        initial_balance=0.0,
        current_balance=0.0,
        pnl=0.0,
        user_id=alice_user.id,
        oanda_account_id="101-004-99999-001",
        risk_config={
            "max_position_size_units": 10_000,
            "loss_cap_pct": 2.0,
            "kill_switch_dd_pct": 5.0,
        },
        started_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


class TestSnapshotCapture:
    def test_first_tick_captures_snapshot(self, db_session, alice_paper_run):
        """PR-F loop must snapshot the OANDA balance on first tick and
        persist it as the P&L denominator."""
        from agentfx.services.paper_runner import _execute_loop

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}

        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.snapshot_balance == 10_000.0
        # initial_balance is kept in sync for legacy code paths.
        assert alice_paper_run.initial_balance == 10_000.0
        assert alice_paper_run.current_balance == 10_000.0
        assert alice_paper_run.pnl == 0.0


class TestKillSwitch:
    def test_kill_flag_causes_halt_next_tick(self, db_session, alice_paper_run):
        """The router flow: PR-F's POST /kill sets `kill_requested_at`.
        The runner observes it on the next loop refresh and transitions
        to HALTED_MANUAL with close_all_positions called."""
        from src.agentfx.api.models import RunStatus
        from agentfx.services.paper_runner import _execute_loop

        # Simulate the router having flipped the flag before the loop
        # runs its first refresh.
        alice_paper_run.kill_requested_at = datetime.now(UTC)
        # Snapshot pre-captured so the loop enters the polling branch
        # immediately rather than snapshotting first (mimics a
        # mid-flight kill, not a kill during boot).
        alice_paper_run.snapshot_balance = 10_000.0
        alice_paper_run.current_balance = 10_000.0
        db_session.commit()

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}

        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.status == RunStatus.HALTED_MANUAL
        assert alice_paper_run.stop_reason == "USER_KILL"
        assert alice_paper_run.completed_at is not None
        client.close_all_positions.assert_called_once_with("EUR_USD")


class TestLossCapHalt:
    def test_declining_balance_trips_loss_cap(self, db_session, alice_paper_run):
        """A 5% balance drop hits both the DD gate and the loss-cap
        gate. `evaluate_all` orders DD first, so the observed reason
        is BROKER_CIRCUIT (kill switch). The loss-cap-only scenario
        is a smaller drop that hits ONLY the 2% cap without exceeding
        the 5% DD cap — that's the case this test locks."""
        from src.agentfx.api.models import RunStatus
        from agentfx.services.paper_runner import _execute_loop

        # Snapshot at 10_000, balance drops to 9_800 = exactly 2% loss.
        # DD gate at 5% is fine; loss-cap gate at 2% trips.
        alice_paper_run.snapshot_balance = 10_000.0
        alice_paper_run.current_balance = 10_000.0
        db_session.commit()

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 9_800.0}

        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.status == RunStatus.ERROR
        assert alice_paper_run.stop_reason == "LOSS_CAP_HIT"
        client.close_all_positions.assert_called_once_with("EUR_USD")


class TestBrokerCircuit:
    def test_dd_gate_trips_broker_circuit(self, db_session, alice_paper_run):
        """5% balance drop trips the kill-switch DD gate before the
        loss-cap gate (per evaluate_all precedence)."""
        from src.agentfx.api.models import RunStatus
        from agentfx.services.paper_runner import _execute_loop

        alice_paper_run.snapshot_balance = 10_000.0
        alice_paper_run.current_balance = 10_000.0
        db_session.commit()

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 9_500.0}

        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.status == RunStatus.ERROR
        assert alice_paper_run.stop_reason == "BROKER_CIRCUIT"

    def test_client_circuit_open_halts_with_broker_reason(
        self, db_session, alice_paper_run
    ):
        """When OandaClient itself raises OandaCircuitOpen — 5 failures
        in 60s from PR-E — the loop catches it, halts, and records
        BROKER_CIRCUIT as the reason (not a generic process_crashed).

        NOTE the import — `agentfx.` (not `src.agentfx.`). The runner
        imports the un-prefixed form, and Python treats `src.agentfx`
        and `agentfx` as two different module objects; if the test
        raises the `src.agentfx` variant of `OandaCircuitOpen`, the
        runner's `except` clause won't catch it and the exception
        escalates as a process crash instead of a clean halt.
        """
        from agentfx.adapters.oanda import OandaCircuitOpen
        from agentfx.api.models import RunStatus
        from agentfx.services.paper_runner import _execute_loop

        alice_paper_run.snapshot_balance = 10_000.0
        alice_paper_run.current_balance = 10_000.0
        db_session.commit()

        # First call snapshots; second call raises. We advance past
        # snapshot by pre-setting snapshot_balance, so the first
        # in-loop call to get_account_balance is what raises.
        client = MagicMock()
        client.get_account_balance.side_effect = OandaCircuitOpen(
            "OANDA circuit breaker open — 5 failures within 60s"
        )

        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.status == RunStatus.ERROR
        assert alice_paper_run.stop_reason == "BROKER_CIRCUIT"
        # Close was best-effort — it doesn't matter whether it fired
        # or itself raised; the run state is the invariant.


class TestRouterKillFlow:
    """Router endpoint → DB flag → runner tick. Locks the full user
    story from the wireframes: user hits kill switch in the browser,
    within one tick the run transitions."""

    def test_post_kill_then_runner_tick_halts(
        self, test_client_with_db, db_session, alice_user, alice_paper_run
    ):
        from src.agentfx.api.models import RunStatus
        from agentfx.services.paper_runner import _execute_loop

        # Authenticate as Alice.
        login = test_client_with_db.post(
            "/api/v1/login",
            data={"username": alice_user.email, "password": "alicepass"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        # 1. Router flips the flag.
        kill = test_client_with_db.post(
            f"/api/v1/paper-runs/{alice_paper_run.id}/kill",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert kill.status_code == 200

        # 2. Runner's next iteration observes it.
        db_session.expire_all()
        alice_paper_run = (
            db_session.query(alice_paper_run.__class__)
            .filter_by(id=alice_paper_run.id)
            .one()
        )
        # Pre-snapshot so the loop skips capture and hits the poll
        # branch immediately.
        alice_paper_run.snapshot_balance = 10_000.0
        alice_paper_run.current_balance = 10_000.0
        db_session.commit()

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}
        _execute_loop(
            db=db_session,
            run=alice_paper_run,
            client=client,
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        db_session.refresh(alice_paper_run)
        assert alice_paper_run.status == RunStatus.HALTED_MANUAL
        assert alice_paper_run.stop_reason == "USER_KILL"
