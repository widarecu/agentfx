"""AFX-93: an OANDA 401/403 while the FX market is closed must not
halt the paper run with OANDA_AUTH_LOST.

OANDA's weekend maintenance returns transient 401s on the account
summary. Off-hours the runner logs a decision and keeps retrying with
backoff (heartbeat still persisted, no flatten). In market hours the
existing refresh-once-then-halt behaviour is unchanged, and a 401 that
outlives the reopen halts on the first market-open tick.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentfx.adapters.oanda import OandaError
from agentfx.api.models import RunStatus
from agentfx.api.workers.market_calendar import market_closure_at, reset_calendar
from agentfx.services.paper_runner import (
    _AUTH_DEFERRED_ACTION,
    _balance_retry_sleep_seconds,
    _execute_loop,
)

pytestmark = pytest.mark.unit

# 2026-11-14 is the Saturday of the incident; 2026-11-15 22:00Z is the
# Sunday reopen (17:00 New York, EST).
SATURDAY = datetime(2026, 11, 14, 9, 35, 15, tzinfo=UTC)
THURSDAY_MARKET_HOURS = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
SUNDAY_AFTER_REOPEN = datetime(2026, 11, 15, 22, 30, tzinfo=UTC)

_RECOVER = "agentfx.services.paper_runner._recover_from_auth_loss"


@pytest.fixture(autouse=True)
def _reset_calendar_cache():
    reset_calendar()
    yield
    reset_calendar()


def _run():
    return SimpleNamespace(
        id="run_afx93",
        user_id=7,
        pair="AUD_USD",
        snapshot_balance=10_000.0,
        last_transaction_id="123",
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


def _auth_client():
    client = MagicMock()
    client.get_account_balance.side_effect = OandaError(
        '401 {"errorMessage":"Insufficient authorization to perform request."}'
    )
    return client


def _clock(*instants: datetime):
    """now_fn returning each instant in turn, then repeating the last."""
    stamps = [t.timestamp() for t in instants]
    calls = {"n": 0}

    def now_fn() -> float:
        i = min(calls["n"], len(stamps) - 1)
        calls["n"] += 1
        return stamps[i]

    return now_fn


def _decisions(db, action):
    return [
        c.args[0]
        for c in db.add.call_args_list
        if getattr(c.args[0], "action", None) == action
    ]


class TestAuthErrorWhileMarketClosed:
    def test_persistent_401_on_weekend_keeps_run_running(self):
        run = _run()
        client = _auth_client()
        db = MagicMock()
        sleep_fn = MagicMock()

        # Record a heartbeat only when it is actually committed:
        # `flag_modified(run, "parameters")` followed by `db.commit()`.
        flagged = {"pending": False}
        committed_heartbeats: list[str] = []

        def _flag(obj, key):
            assert obj is run and key == "parameters"
            flagged["pending"] = True

        def _commit():
            if flagged["pending"]:
                committed_heartbeats.append(run.parameters["last_heartbeat_at"])
                flagged["pending"] = False

        db.commit.side_effect = _commit

        with (
            patch(_RECOVER, return_value=None) as recover,
            patch("sqlalchemy.orm.attributes.flag_modified", _flag),
        ):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=sleep_fn,
                now_fn=_clock(SATURDAY),
                max_iterations=3,
            )

        assert run.status == RunStatus.RUNNING
        assert run.stop_reason is None
        client.close_all_positions.assert_not_called()
        # Kept retrying instead of halting; creds are refreshed once per
        # closed-market streak, not on every retry.
        assert client.get_account_balance.call_count == 3
        assert recover.call_count == 1
        # One decision per closed-market streak, not one per retry.
        logged = _decisions(db, _AUTH_DEFERRED_ACTION)
        assert len(logged) == 1
        assert logged[0].outputs_json["paper_run_id"] == run.id
        assert logged[0].outputs_json["closure"] == "weekend"
        # Exponential backoff between retries.
        assert [c.args[0] for c in sleep_fn.call_args_list] == [
            _balance_retry_sleep_seconds(1),
            _balance_retry_sleep_seconds(2),
            _balance_retry_sleep_seconds(3),
        ]
        # Heartbeat keeps flowing so the supervisor doesn't zombie-restart:
        # startup, then at least one committed beat before each deferred
        # sleep.
        assert len(committed_heartbeats) >= 1 + sleep_fn.call_count

    def test_weekend_refresh_success_resumes_with_fresh_client(self):
        run = _run()
        client = _auth_client()
        fresh = MagicMock()
        fresh.get_account_balance.return_value = {"balance": 10_000.0}
        db = MagicMock()

        with patch(_RECOVER, return_value=fresh):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(SATURDAY),
                max_iterations=3,
            )

        assert run.status == RunStatus.RUNNING
        # Subsequent ticks use the fresh client.
        assert client.get_account_balance.call_count == 1
        assert fresh.get_account_balance.call_count == 3
        client.close_all_positions.assert_not_called()

    def test_weekend_refresh_non_auth_error_keeps_retrying(self):
        run = _run()
        client = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, side_effect=OandaError("503 Service unavailable")):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(SATURDAY),
                max_iterations=2,
            )

        assert run.status == RunStatus.RUNNING
        client.close_all_positions.assert_not_called()

    def test_weekend_flapping_auth_on_recheck_defers_not_crashes(self):
        """Refresh succeeds but the re-read on the fresh client 401s
        again (the 11-13 flapping pattern). Must defer, not escape the
        loop as an uncaught OandaError (process_crashed -> restart)."""
        run = _run()
        client = _auth_client()
        fresh = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, return_value=fresh) as recover:
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(SATURDAY),
                max_iterations=3,
            )

        assert run.status == RunStatus.RUNNING
        assert run.stop_reason is None
        assert recover.call_count == 1
        assert fresh.get_account_balance.call_count == 1
        client.close_all_positions.assert_not_called()
        fresh.close_all_positions.assert_not_called()
        assert len(_decisions(db, _AUTH_DEFERRED_ACTION)) == 1

    def test_new_streak_after_recovery_refreshes_again(self):
        run = _run()
        client = MagicMock()
        client.get_account_balance.side_effect = [
            OandaError("401 Insufficient authorization (attempt 4/4)"),
            {"balance": 10_000.0},
            OandaError("401 Insufficient authorization (attempt 4/4)"),
        ]
        db = MagicMock()

        with patch(_RECOVER, return_value=None) as recover:
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(SATURDAY),
                max_iterations=3,
            )

        assert run.status == RunStatus.RUNNING
        assert recover.call_count == 2
        assert len(_decisions(db, _AUTH_DEFERRED_ACTION)) == 2


class TestAuthErrorWhileMarketOpen:
    def test_persistent_401_in_market_hours_halts_oanda_auth_lost(self):
        run = _run()
        client = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, return_value=None):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(THURSDAY_MARKET_HOURS),
                max_iterations=3,
            )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "OANDA_AUTH_LOST"
        assert client.get_account_balance.call_count == 1
        client.close_all_positions.assert_called_once_with("AUD_USD")
        assert _decisions(db, _AUTH_DEFERRED_ACTION) == []

    def test_flapping_auth_on_recheck_in_market_hours_halts_not_crashes(self):
        run = _run()
        client = _auth_client()
        fresh = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, return_value=fresh):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(THURSDAY_MARKET_HOURS),
                max_iterations=3,
            )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "OANDA_AUTH_LOST"
        client.close_all_positions.assert_called_once_with("AUD_USD")

    def test_refresh_broker_error_in_market_hours_uses_retry_budget(self):
        """A non-auth error during the refresh is a broker problem: it
        counts toward BROKER_NETWORK instead of crashing the process."""
        run = _run()
        client = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, side_effect=OandaError("503 Service unavailable")):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(THURSDAY_MARKET_HOURS),
                max_iterations=10,
            )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "BROKER_NETWORK"

    def test_401_persisting_past_reopen_halts_on_first_open_tick(self):
        run = _run()
        client = _auth_client()
        db = MagicMock()

        with patch(_RECOVER, return_value=None):
            _execute_loop(
                db=db,
                run=run,
                client=client,
                sleep_fn=MagicMock(),
                now_fn=_clock(SATURDAY, SATURDAY, SUNDAY_AFTER_REOPEN),
                max_iterations=5,
            )

        assert run.status == RunStatus.ERROR
        assert run.stop_reason == "OANDA_AUTH_LOST"
        # Two weekend ticks deferred, the first open tick halted.
        assert client.get_account_balance.call_count == 3
        client.close_all_positions.assert_called_once_with("AUD_USD")
        assert len(_decisions(db, _AUTH_DEFERRED_ACTION)) == 1


class TestMarketClosureAt:
    @pytest.mark.parametrize(
        ("instant", "expected"),
        [
            # Winter (EST): weekly close/open at 22:00Z.
            (datetime(2026, 11, 13, 21, 59, tzinfo=UTC), (False, "")),
            (datetime(2026, 11, 13, 22, 0, tzinfo=UTC), (True, "weekend")),
            (datetime(2026, 11, 13, 22, 24, 41, tzinfo=UTC), (True, "weekend")),
            (SATURDAY, (True, "weekend")),
            (datetime(2026, 11, 15, 21, 59, tzinfo=UTC), (True, "weekend")),
            (datetime(2026, 11, 15, 22, 0, tzinfo=UTC), (False, "")),
            (THURSDAY_MARKET_HOURS, (False, "")),
            # Summer (EDT): weekly close/open at 21:00Z.
            (datetime(2026, 7, 17, 20, 59, tzinfo=UTC), (False, "")),
            (datetime(2026, 7, 17, 21, 0, tzinfo=UTC), (True, "weekend")),
            (datetime(2026, 7, 19, 21, 0, tzinfo=UTC), (False, "")),
            # Full-closure holiday from config/forex_calendar.yaml.
            (datetime(2026, 12, 25, 12, 0, tzinfo=UTC), (True, "holiday:2026-12-25")),
            # Christmas Eve 2026 is a weekday trading day.
            (datetime(2026, 12, 24, 12, 0, tzinfo=UTC), (False, "")),
        ],
    )
    def test_closure(self, instant, expected):
        assert market_closure_at(instant) == expected

    def test_naive_datetime_treated_as_utc(self):
        assert market_closure_at(datetime(2026, 11, 14, 9, 0)) == (True, "weekend")
