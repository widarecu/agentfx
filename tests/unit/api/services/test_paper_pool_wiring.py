"""Unit tests for the AFX-7 (2026-11-01 collapse) pool wiring in
`api.services.paper`:

- `_bind_pool_account_to_run` flips the operator-picked
  UserOandaAccount row to IN_USE at approve time.
- `delete_paper_run` releases that slot back to FREE at delete time.
- A terminated-but-not-deleted run keeps its slot bound (no release
  on terminal; that's covered in `tests/unit/services/test_paper_runner.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.api.core.database import Base
from agentfx.api.core.secrets import encrypt_secret
from agentfx.api.models import Run, RunEnvironment, RunStatus, User
from agentfx.api.models.user_credentials import (
    OandaEnvironment,
    UserOandaAccount,
    UserOandaAccountPoolStatus,
    UserOandaCredentials,
)
from agentfx.api.services.paper import _bind_pool_account_to_run, delete_paper_run

pytestmark = pytest.mark.unit


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine):
    with Session(engine) as sess:
        yield sess


def _mk_user_creds_account(
    db: Session,
    *,
    user_id: int = 1,
    oanda_account_id: str = "101-004-abc-001",
    pool_status: UserOandaAccountPoolStatus = UserOandaAccountPoolStatus.FREE,
) -> UserOandaAccount:
    db.add(
        User(
            id=user_id,
            email=f"u{user_id}@example.com",
            hashed_password="x",
            role="VIEWER",
        )
    )
    db.flush()
    creds = UserOandaCredentials(
        user_id=user_id,
        oanda_api_key_encrypted=encrypt_secret("token"),
        oanda_environment=OandaEnvironment.PRACTICE,
        allow_fleet_pool_use=False,
    )
    db.add(creds)
    db.flush()
    acct = UserOandaAccount(
        user_credentials_id=creds.id,
        oanda_account_id=oanda_account_id,
        alias=None,
        currency="USD",
        pool_status=pool_status,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def _mk_terminal_run(
    db: Session,
    *,
    run_id: str = "run_term_1",
    user_id: int = 1,
    oanda_account_id: str = "101-004-abc-001",
) -> Run:
    run = Run(
        id=run_id,
        strategy_id=None,
        pair="EUR_USD",
        environment=RunEnvironment.PAPER,
        status=RunStatus.HALTED_MANUAL,
        parameters={"instrument": "EUR_USD", "granularity": "M1"},
        initial_balance=0.0,
        current_balance=0.0,
        pnl=0.0,
        user_id=user_id,
        oanda_account_id=oanda_account_id,
        oanda_environment=OandaEnvironment.PRACTICE.value,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class TestBindPoolAccountToRun:
    def test_free_account_flips_to_in_use(self, db):
        """The core approve-time write: operator picks an account,
        we mark it IN_USE with the run id."""
        acct = _mk_user_creds_account(db)
        now = datetime.now(UTC)

        _bind_pool_account_to_run(
            db,
            user_id=1,
            oanda_account_id=acct.oanda_account_id,
            run_id="run_approve_1",
            now=now,
        )
        db.commit()
        db.refresh(acct)
        assert acct.pool_status == UserOandaAccountPoolStatus.IN_USE
        assert acct.current_run_id == "run_approve_1"
        # SQLite drops tz-info on DateTime columns; Postgres preserves
        # it. Compare the UTC-naive form so this works on both.
        assert acct.in_use_since.replace(tzinfo=None) == now.replace(tzinfo=None)
        assert acct.lease_expires_at is not None
        assert acct.cooling_until is None

    def test_idempotent_on_same_run_id(self, db):
        """A second bind call for the same (account, run) is a no-op —
        the operator can only approve once, but a retried request
        shouldn't throw."""
        acct = _mk_user_creds_account(db)
        now = datetime.now(UTC)

        _bind_pool_account_to_run(
            db,
            user_id=1,
            oanda_account_id=acct.oanda_account_id,
            run_id="run_x",
            now=now,
        )
        db.commit()
        first_lease = acct.lease_expires_at

        _bind_pool_account_to_run(
            db,
            user_id=1,
            oanda_account_id=acct.oanda_account_id,
            run_id="run_x",
            now=now,
        )
        db.commit()
        db.refresh(acct)
        assert acct.lease_expires_at == first_lease  # untouched

    def test_unknown_account_raises(self, db):
        _mk_user_creds_account(db, oanda_account_id="101-004-xxx-001")
        with pytest.raises(ValueError, match="not found"):
            _bind_pool_account_to_run(
                db,
                user_id=1,
                oanda_account_id="not-a-real-id",
                run_id="run_y",
                now=datetime.now(UTC),
            )


class TestDeletePaperRunReleasesPool:
    def test_delete_releases_bound_slot_to_free(self, db):
        """Deleting a terminal run must release its pool slot — the
        account is immediately available for the next run."""
        acct = _mk_user_creds_account(db)
        run = _mk_terminal_run(
            db, run_id="run_del_1", oanda_account_id=acct.oanda_account_id
        )
        _bind_pool_account_to_run(
            db,
            user_id=1,
            oanda_account_id=acct.oanda_account_id,
            run_id="run_del_1",
            now=datetime.now(UTC),
        )
        db.commit()

        delete_paper_run(db, run)

        db.refresh(acct)
        assert acct.pool_status == UserOandaAccountPoolStatus.FREE
        assert acct.current_run_id is None
        assert acct.last_run_id == "run_del_1"
        assert acct.last_run_ended_at is not None
        # Run row is gone.
        assert db.query(Run).filter(Run.id == "run_del_1").first() is None

    def test_delete_when_no_slot_held_is_benign(self, db):
        """Legacy runs created before pool wiring — delete still
        succeeds."""
        acct = _mk_user_creds_account(db)
        run = _mk_terminal_run(
            db, run_id="run_del_2", oanda_account_id=acct.oanda_account_id
        )
        # Deliberately skip the bind.

        delete_paper_run(db, run)

        db.refresh(acct)
        assert acct.pool_status == UserOandaAccountPoolStatus.FREE
        assert db.query(Run).filter(Run.id == "run_del_2").first() is None

    def test_delete_release_failure_still_deletes_run(self, db):
        """A broken pool helper must not block the delete — the
        operator asked to delete the run; failing silently and
        logging is better than keeping the row + a stuck slot."""
        acct = _mk_user_creds_account(db)
        run = _mk_terminal_run(
            db, run_id="run_del_3", oanda_account_id=acct.oanda_account_id
        )
        _bind_pool_account_to_run(
            db,
            user_id=1,
            oanda_account_id=acct.oanda_account_id,
            run_id="run_del_3",
            now=datetime.now(UTC),
        )
        db.commit()

        with patch(
            "agentfx.agents.fleet.account_pool.release_slot",
            side_effect=RuntimeError("pool DB down"),
        ):
            delete_paper_run(db, run)

        assert db.query(Run).filter(Run.id == "run_del_3").first() is None
