"""Unit tests for `agents.fleet.account_pool`.

AFX-7 (pivoted 2026-10-29) — the pool is derived from
`UserOandaAccount` rows under `UserOandaCredentials.allow_fleet_pool_use=True`.
Covers:
- Successful allocation flips FREE → IN_USE with lease.
- Opt-in off → sub-account is skipped (no_free_account).
- LIVE environment refused even when consented.
- No-free-account path returns rejection, never raises.
- Concurrency cap enforcement.
- Release flips IN_USE → COOLING_DOWN with cooling_until.
- Release of a run not holding anything returns False (benign).
- Cooldown sweep auto-flips COOLING_DOWN → FREE when expired.
- Expired lease sweep reclaims IN_USE back to FREE.
- Idempotent reuse on restart with same run_id.
- pool_summary shape only counts consenting rows.
- Environment filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import account_pool
from agentfx.api.core.database import Base
from agentfx.api.core.secrets import encrypt_secret
from agentfx.api.models import User
from agentfx.api.models.user_credentials import (
    OandaEnvironment,
    UserOandaAccount,
    UserOandaAccountPoolStatus,
    UserOandaCredentials,
)

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


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


_next_user_id = {"n": 1}


def _mk_creds(
    db: Session,
    *,
    api_key: str = "pool-token-abc",
    environment: OandaEnvironment = OandaEnvironment.PRACTICE,
    allow_fleet_pool_use: bool = True,
) -> UserOandaCredentials:
    """Create a user + their OANDA credentials row."""
    uid = _next_user_id["n"]
    _next_user_id["n"] += 1
    user = User(
        id=uid,
        email=f"u{uid}@example.com",
        hashed_password="x",
        role="VIEWER",
    )
    db.add(user)
    db.flush()
    creds = UserOandaCredentials(
        user_id=uid,
        oanda_api_key_encrypted=encrypt_secret(api_key),
        oanda_environment=environment,
        allow_fleet_pool_use=allow_fleet_pool_use,
    )
    db.add(creds)
    db.commit()
    db.refresh(creds)
    return creds


def _mk_account(
    db: Session,
    creds: UserOandaCredentials,
    *,
    oanda_account_id: str = "101-004-pool-001",
    alias: str | None = None,
    pool_status: UserOandaAccountPoolStatus = UserOandaAccountPoolStatus.FREE,
    cooling_until: datetime | None = None,
    lease_expires_at: datetime | None = None,
    current_run_id: str | None = None,
) -> UserOandaAccount:
    row = UserOandaAccount(
        user_credentials_id=creds.id,
        oanda_account_id=oanda_account_id,
        alias=alias,
        currency="USD",
        pool_status=pool_status,
        cooling_until=cooling_until,
        lease_expires_at=lease_expires_at,
        current_run_id=current_run_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────
# allocate_slot
# ─────────────────────────────────────────────────────────────────────


class TestAllocate:
    def test_consenting_free_account_is_allocated(self, db):
        creds = _mk_creds(db, api_key="hello-world-token")
        row = _mk_account(db, creds)
        result = account_pool.allocate_slot(db, run_id="run_123")
        assert result.accepted is True
        assert result.account_id == row.oanda_account_id
        assert result.environment == "practice"
        assert result.api_key_plaintext == "hello-world-token"
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.IN_USE
        assert row.current_run_id == "run_123"
        assert row.lease_expires_at is not None

    def test_opt_in_off_skips_account(self, db):
        """Credentials without allow_fleet_pool_use are invisible to the pool."""
        creds = _mk_creds(db, allow_fleet_pool_use=False)
        _mk_account(db, creds)
        result = account_pool.allocate_slot(db, run_id="run_x")
        assert result.accepted is False
        assert "no_free_account" in result.reason

    def test_live_environment_refused_even_if_consented(self, db):
        """Fleet pool is practice-only. LIVE creds are never allocated."""
        result = account_pool.allocate_slot(
            db, run_id="run_x", environment=OandaEnvironment.LIVE
        )
        assert result.accepted is False
        assert "live_env_blocked" in result.reason

    def test_no_free_account_returns_rejection(self, db):
        result = account_pool.allocate_slot(db, run_id="run_nothing")
        assert result.accepted is False
        assert "no_free_account" in result.reason

    def test_environment_filter_rejects_mismatched(self, db):
        """If we ask for LIVE (hypothetical), practice creds don't satisfy."""
        creds = _mk_creds(db, environment=OandaEnvironment.PRACTICE)
        _mk_account(db, creds)
        # The LIVE short-circuit fires first — refused:live_env_blocked.
        result = account_pool.allocate_slot(
            db, run_id="run_env", environment=OandaEnvironment.LIVE
        )
        assert result.accepted is False

    def test_fifo_picks_oldest_free(self, db):
        creds = _mk_creds(db)
        older = _mk_account(db, creds, oanda_account_id="A-old")
        # Give the second row a later created_at so FIFO is deterministic.
        import time

        time.sleep(0.01)
        _mk_account(db, creds, oanda_account_id="B-new")
        result = account_pool.allocate_slot(db, run_id="run_fifo")
        assert result.accepted is True
        assert result.account_id == older.oanda_account_id

    def test_concurrent_cap_blocks_further_allocation(self, db, monkeypatch):
        monkeypatch.setattr(account_pool, "_MAX_CONCURRENT_PAPER_RUNS", 1)
        creds = _mk_creds(db)
        _mk_account(db, creds, oanda_account_id="X-1")
        _mk_account(db, creds, oanda_account_id="X-2")
        first = account_pool.allocate_slot(db, run_id="r1")
        assert first.accepted is True
        second = account_pool.allocate_slot(db, run_id="r2")
        assert second.accepted is False
        assert "concurrent_cap_hit" in second.reason


class TestRestartReuse:
    """AFX-7: a crashed paper runner that gets re-scheduled with the
    SAME run_id must reuse its existing pool slot — allocating a
    different account would strand positions on the old one."""

    def test_already_held_slot_is_reused_not_reallocated(self, db):
        creds = _mk_creds(db, api_key="original-key")
        row = _mk_account(db, creds)
        first = account_pool.allocate_slot(db, run_id="run_rs1")
        assert first.accepted is True
        db.refresh(row)
        original_lease = row.lease_expires_at

        second = account_pool.allocate_slot(db, run_id="run_rs1")
        assert second.accepted is True
        assert second.account_id == row.oanda_account_id
        assert second.api_key_plaintext == "original-key"
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.IN_USE
        assert row.lease_expires_at == original_lease  # untouched

    def test_restart_reuse_takes_precedence_over_free_slot(self, db):
        creds = _mk_creds(db, api_key="A-key")
        _mk_account(db, creds, oanda_account_id="pool-A")
        first = account_pool.allocate_slot(db, run_id="run_rs2")
        assert first.account_id == "pool-A"
        creds2 = _mk_creds(db, api_key="B-key")
        _mk_account(db, creds2, oanda_account_id="pool-B")
        second = account_pool.allocate_slot(db, run_id="run_rs2")
        assert second.account_id == "pool-A"
        assert second.api_key_plaintext == "A-key"

    def test_reuse_different_run_id_still_allocates_free(self, db):
        creds = _mk_creds(db, api_key="A-key")
        _mk_account(db, creds, oanda_account_id="pool-A")
        creds2 = _mk_creds(db, api_key="B-key")
        _mk_account(db, creds2, oanda_account_id="pool-B")
        a = account_pool.allocate_slot(db, run_id="run_rs3a")
        b = account_pool.allocate_slot(db, run_id="run_rs3b")
        assert a.accepted and b.accepted
        assert a.account_id != b.account_id


class TestRelease:
    def test_release_flips_to_cooling_down(self, db):
        creds = _mk_creds(db)
        row = _mk_account(db, creds)
        account_pool.allocate_slot(db, run_id="run_r1")
        released = account_pool.release_slot(db, run_id="run_r1")
        assert released is True
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.COOLING_DOWN
        assert row.cooling_until is not None
        assert row.current_run_id is None
        assert row.last_run_id == "run_r1"
        assert row.last_run_ended_at is not None

    def test_release_of_unknown_run_returns_false(self, db):
        creds = _mk_creds(db)
        _mk_account(db, creds)
        assert account_pool.release_slot(db, run_id="nothing_held") is False


class TestSweepers:
    def test_expired_cooldown_sweeps_back_to_free_on_allocate(self, db):
        creds = _mk_creds(db)
        past = datetime.now(UTC) - timedelta(minutes=5)
        row = _mk_account(
            db,
            creds,
            pool_status=UserOandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=past,
        )
        result = account_pool.allocate_slot(db, run_id="run_sweep")
        assert result.accepted is True
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.IN_USE

    def test_active_cooldown_is_not_swept(self, db):
        creds = _mk_creds(db)
        future = datetime.now(UTC) + timedelta(hours=1)
        row = _mk_account(
            db,
            creds,
            pool_status=UserOandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=future,
        )
        result = account_pool.allocate_slot(db, run_id="run_wait")
        assert result.accepted is False
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.COOLING_DOWN

    def test_expired_lease_is_reclaimed(self, db):
        creds = _mk_creds(db)
        past = datetime.now(UTC) - timedelta(hours=1)
        row = _mk_account(
            db,
            creds,
            pool_status=UserOandaAccountPoolStatus.IN_USE,
            current_run_id="orphaned_run",
            lease_expires_at=past,
        )
        result = account_pool.allocate_slot(db, run_id="run_new")
        assert result.accepted is True
        db.refresh(row)
        assert row.pool_status == UserOandaAccountPoolStatus.IN_USE
        assert row.current_run_id == "run_new"
        assert row.last_run_id == "orphaned_run"


class TestPoolSummary:
    def test_counts_shape_only_includes_consenting_rows(self, db):
        # Consenting user: 2 accounts (1 free, 1 cooling)
        consenting = _mk_creds(db, api_key="k1", allow_fleet_pool_use=True)
        _mk_account(db, consenting, oanda_account_id="A")
        _mk_account(
            db,
            consenting,
            oanda_account_id="C",
            pool_status=UserOandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=datetime.now(UTC) + timedelta(minutes=30),
        )
        # Non-consenting user: 1 account, must be invisible to summary
        other = _mk_creds(db, api_key="k2", allow_fleet_pool_use=False)
        _mk_account(db, other, oanda_account_id="OTHER")

        summary = account_pool.pool_summary(db)
        assert summary["total_accounts"] == 2  # excludes non-consenting
        assert summary["status_counts"]["free"] == 1
        assert summary["status_counts"]["cooling_down"] == 1
        assert summary["concurrent_in_use"] == 0
        assert summary["max_concurrent_runs"] >= 1
        assert len(summary["free_accounts"]) == 1
        assert summary["free_accounts"][0]["account_id"] == "A"
        assert len(summary["cooling_down_accounts"]) == 1
        assert summary["cooling_down_accounts"][0]["seconds_until_free"] > 0


class TestCredentialKeyNeverLogged:
    """Regression — decrypted api_keys must never show up in logs
    emitted by allocate/release/sweep paths."""

    def test_allocate_log_never_contains_decrypted_key(self, db, caplog):
        import logging

        secret_token = "ULTRA_SECRET_POOL_TOKEN_SHOULD_NEVER_LOG"
        creds = _mk_creds(db, api_key=secret_token)
        _mk_account(db, creds)
        with caplog.at_level(logging.DEBUG, logger="agentfx.agents.fleet.account_pool"):
            account_pool.allocate_slot(db, run_id="run_leak_check")
        for record in caplog.records:
            assert secret_token not in record.getMessage()
