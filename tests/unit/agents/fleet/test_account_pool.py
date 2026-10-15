"""Unit tests for `agents.fleet.account_pool`.

v0.8.1 C.3 — covers:
- Successful allocation flips FREE → IN_USE with lease.
- No-free-account path returns rejection, never raises.
- Concurrency cap enforcement.
- Release flips IN_USE → COOLING_DOWN with cooling_until.
- Release of a run not holding anything returns False (benign).
- Cooldown sweep auto-flips COOLING_DOWN → FREE when expired.
- Expired lease sweep reclaims IN_USE back to FREE.
- pool_summary shape.
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
from agentfx.api.models import (
    OandaAccountPool,
    OandaAccountPoolEnvironment,
    OandaAccountPoolStatus,
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


def _seed(
    db: Session,
    *,
    account_id: str = "101-004-pool-001",
    env: OandaAccountPoolEnvironment = OandaAccountPoolEnvironment.PRACTICE,
    status: OandaAccountPoolStatus = OandaAccountPoolStatus.FREE,
    api_key: str = "pool-token-abc",
    nickname: str | None = None,
    cooling_until: datetime | None = None,
    lease_expires_at: datetime | None = None,
    current_run_id: str | None = None,
) -> OandaAccountPool:
    row = OandaAccountPool(
        account_id=account_id,
        api_key_encrypted=encrypt_secret(api_key),
        environment=env,
        status=status,
        nickname=nickname,
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
    def test_free_account_is_allocated(self, db):
        row = _seed(db, api_key="hello-world-token")
        result = account_pool.allocate_slot(db, run_id="run_123")
        assert result.accepted is True
        assert result.account_id == row.account_id
        assert result.environment == "practice"
        assert result.api_key_plaintext == "hello-world-token"
        assert result.pool_row_id == row.id

        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.IN_USE
        assert row.current_run_id == "run_123"
        assert row.in_use_since is not None
        assert row.lease_expires_at is not None

    def test_no_free_account_returns_rejection(self, db):
        _seed(db, status=OandaAccountPoolStatus.DISABLED)
        result = account_pool.allocate_slot(db, run_id="run_x")
        assert result.accepted is False
        assert "no_free_account" in result.reason

    def test_environment_filter_rejects_mismatched_pool(self, db):
        _seed(db, env=OandaAccountPoolEnvironment.LIVE)
        result = account_pool.allocate_slot(
            db,
            run_id="run_x",
            environment=OandaAccountPoolEnvironment.PRACTICE,
        )
        assert result.accepted is False

    def test_fifo_picks_oldest_free(self, db):
        a = _seed(db, account_id="A", api_key="ta")
        # Nudge created_at so B is strictly newer.
        b_created = datetime.now(UTC) + timedelta(seconds=10)
        b = OandaAccountPool(
            account_id="B",
            api_key_encrypted=encrypt_secret("tb"),
            environment=OandaAccountPoolEnvironment.PRACTICE,
            status=OandaAccountPoolStatus.FREE,
            created_at=b_created,
        )
        db.add(b)
        db.commit()
        result = account_pool.allocate_slot(db, run_id="run_fifo")
        assert result.accepted is True
        assert result.account_id == a.account_id

    def test_concurrent_cap_blocks_further_allocation(self, db, monkeypatch):
        monkeypatch.setattr(account_pool, "_MAX_CONCURRENT_PAPER_RUNS", 1)
        a = _seed(db, account_id="A", api_key="ta")
        b = _seed(db, account_id="B", api_key="tb")
        first = account_pool.allocate_slot(db, run_id="run_1")
        assert first.accepted is True
        second = account_pool.allocate_slot(db, run_id="run_2")
        assert second.accepted is False
        assert "concurrent_cap_hit" in second.reason
        db.refresh(a)
        db.refresh(b)
        # First row was consumed; second stays FREE.
        statuses = {a.status, b.status}
        assert OandaAccountPoolStatus.IN_USE in statuses
        assert OandaAccountPoolStatus.FREE in statuses


# ─────────────────────────────────────────────────────────────────────
# release_slot
# ─────────────────────────────────────────────────────────────────────


class TestRelease:
    def test_release_flips_to_cooling_down(self, db):
        row = _seed(db)
        account_pool.allocate_slot(db, run_id="run_r1")
        released = account_pool.release_slot(db, run_id="run_r1")
        assert released is True
        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.COOLING_DOWN
        assert row.cooling_until is not None
        assert row.current_run_id is None
        assert row.last_run_id == "run_r1"
        assert row.last_run_ended_at is not None

    def test_release_of_unknown_run_returns_false(self, db):
        _seed(db)
        assert account_pool.release_slot(db, run_id="nothing_held") is False


# ─────────────────────────────────────────────────────────────────────
# Sweepers
# ─────────────────────────────────────────────────────────────────────


class TestSweepers:
    def test_expired_cooldown_sweeps_back_to_free_on_allocate(self, db):
        past = datetime.now(UTC) - timedelta(minutes=5)
        row = _seed(
            db,
            status=OandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=past,
        )
        # Allocation sweep should flip it back to FREE then claim it.
        result = account_pool.allocate_slot(db, run_id="run_sweep")
        assert result.accepted is True
        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.IN_USE

    def test_active_cooldown_is_not_swept(self, db):
        future = datetime.now(UTC) + timedelta(hours=1)
        row = _seed(
            db,
            status=OandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=future,
        )
        result = account_pool.allocate_slot(db, run_id="run_wait")
        assert result.accepted is False
        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.COOLING_DOWN

    def test_expired_lease_is_reclaimed(self, db):
        past = datetime.now(UTC) - timedelta(hours=1)
        row = _seed(
            db,
            status=OandaAccountPoolStatus.IN_USE,
            current_run_id="orphaned_run",
            lease_expires_at=past,
        )
        # Allocating a new run should reclaim the orphan + claim it.
        result = account_pool.allocate_slot(db, run_id="run_new")
        assert result.accepted is True
        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.IN_USE
        assert row.current_run_id == "run_new"
        assert row.last_run_id == "orphaned_run"


# ─────────────────────────────────────────────────────────────────────
# pool_summary
# ─────────────────────────────────────────────────────────────────────


class TestPoolSummary:
    def test_counts_shape(self, db):
        _seed(db, account_id="A", api_key="k1")
        _seed(
            db,
            account_id="B",
            api_key="k2",
            status=OandaAccountPoolStatus.DISABLED,
        )
        _seed(
            db,
            account_id="C",
            api_key="k3",
            status=OandaAccountPoolStatus.COOLING_DOWN,
            cooling_until=datetime.now(UTC) + timedelta(minutes=30),
        )
        summary = account_pool.pool_summary(db)
        assert summary["total_accounts"] == 3
        assert summary["status_counts"]["free"] == 1
        assert summary["status_counts"]["disabled"] == 1
        assert summary["status_counts"]["cooling_down"] == 1
        assert summary["concurrent_in_use"] == 0
        assert summary["max_concurrent_runs"] >= 1
        assert len(summary["free_accounts"]) == 1
        assert summary["free_accounts"][0]["account_id"] == "A"
        assert len(summary["cooling_down_accounts"]) == 1
        assert summary["cooling_down_accounts"][0]["seconds_until_free"] > 0
