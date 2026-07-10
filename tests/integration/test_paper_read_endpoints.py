"""Integration tests for the 4 paper-run read endpoints (US-025 + US-026).

Locks:
- Owner can fetch trades / decisions / equity / backtest-overlay for
  their own run.
- Non-owner (different TRADER) gets 403 on the same run.
- ADMIN can fetch any run's data.
- Non-existent run returns 404.
- Backtest overlay resolves the unavailable-reason paths correctly.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _mk_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{str(uuid4())[:8]}"


def _auth_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/login", data={"username": email, "password": password})
    if resp.status_code != 200:  # pragma: no cover
        pytest.skip(f"Auth failed for {email}: {resp.json()}")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def read_users(db_session: Session):
    """One owner TRADER, one other TRADER, one ADMIN."""
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    unique = str(uuid4())[:8]
    creds = {
        "owner": (f"read_owner_{unique}@example.com", "ownerpass"),
        "other": (f"read_other_{unique}@example.com", "otherpass"),
        "admin": (f"read_admin_{unique}@example.com", "adminpass"),
    }
    roles = {"owner": "trader", "other": "trader", "admin": "admin"}
    users: dict[str, dict] = {}
    for key, (email, password) in creds.items():
        u = User(
            email=email,
            hashed_password=get_password_hash(password),
            role=roles[key],
            is_active=True,
        )
        db_session.add(u)
        users[key] = {"email": email, "password": password, "user": u}
    db_session.commit()
    for v in users.values():
        db_session.refresh(v["user"])
    return users


@pytest.fixture
def owner_paper_run_with_data(db_session: Session, read_users):
    """Owner's paper run with 2 trades + 3 decision-log entries + 5
    equity snapshots. Enough to exercise ordering, RBAC, and the empty
    edge cases for backtest overlay (no genome link)."""
    from src.agentfx.api.models import (
        DecisionLog,
        PaperEquitySnapshot,
        Run,
        RunEnvironment,
        RunStatus,
        Trade,
    )

    owner = read_users["owner"]["user"]
    run_id = _mk_id("run_paper_read")
    run = Run(
        id=run_id,
        strategy_id=None,
        strategy_genome_id=None,
        pair="EUR_USD",
        environment=RunEnvironment.PAPER,
        status=RunStatus.RUNNING,
        parameters={},
        initial_balance=10_000.0,
        snapshot_balance=10_000.0,
        current_balance=10_000.0,
        pnl=0.0,
        risk_config={},
        user_id=owner.id,
    )
    db_session.add(run)
    db_session.commit()

    base = datetime.now(UTC).replace(microsecond=0)
    # Two trades — one open, one closed.
    open_trade = Trade(
        id="tr_open",
        run_id=run_id,
        entry_timestamp=base,
        entry_price=1.10,
        quantity=10_000,
        side="buy",
        origin="live",
        signal_reason="sma > sma",
    )
    closed_trade = Trade(
        id="tr_closed",
        run_id=run_id,
        entry_timestamp=base - timedelta(hours=1),
        exit_timestamp=base - timedelta(minutes=30),
        entry_price=1.09,
        exit_price=1.10,
        quantity=10_000,
        side="buy",
        pnl=100.0,
        pnl_pct=0.9,
        origin="live",
        signal_reason="macd_line crosses_above macd_signal",
    )
    db_session.add_all([open_trade, closed_trade])

    # Three decisions — 2 for this run, 1 unrelated.
    d1 = DecisionLog(
        id="dl_1",
        run_id=None,
        agent_type="PAPER_RUNNER",
        action="USER_ORDER_PLACED",
        outputs_json={"paper_run_id": run_id, "instrument": "EUR_USD"},
        reasoning_text="LONG 10000 EUR_USD @ 1.10000",
        user_id=owner.id,
        created_at=base - timedelta(seconds=30),
    )
    d2 = DecisionLog(
        id="dl_2",
        run_id=None,
        agent_type="PAPER_RUNNER",
        action="USER_SIGNAL_SKIPPED_STALE",
        outputs_json={"paper_run_id": run_id},
        reasoning_text="latest candle stale",
        user_id=owner.id,
        created_at=base - timedelta(seconds=10),
    )
    d3 = DecisionLog(
        id="dl_3_unrelated",
        run_id=None,
        agent_type="PAPER_RUNNER",
        action="USER_ORDER_PLACED",
        outputs_json={"paper_run_id": "run_other_unrelated"},
        reasoning_text="not for this run",
        user_id=owner.id,
        created_at=base - timedelta(seconds=5),
    )
    db_session.add_all([d1, d2, d3])

    # Five equity snapshots, chronological.
    for i in range(5):
        db_session.add(
            PaperEquitySnapshot(
                id=f"snap_{i}",
                run_id=run_id,
                timestamp=base + timedelta(minutes=i),
                balance=10_000.0 + i * 5,
                unrealized_pnl=i * 1.0,
            )
        )
    db_session.commit()

    return {"run": run, "owner": owner}


class TestListTrades:
    def test_owner_gets_trades(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/trades", headers=headers
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["total"] == 2
        # Open trade sorts ahead of closed.
        assert body["items"][0]["id"] == "tr_open"
        assert body["items"][0]["origin"] == "live"
        assert body["items"][0]["signal_reason"] == "sma > sma"

    def test_other_trader_forbidden(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["other"]["email"],
            read_users["other"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/trades", headers=headers
        )
        assert resp.status_code == 403

    def test_admin_can_read_any_run(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["admin"]["email"],
            read_users["admin"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/trades", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_missing_run_404(self, test_client_with_db, read_users):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        resp = test_client_with_db.get(
            "/api/v1/paper-runs/run_does_not_exist/trades", headers=headers
        )
        assert resp.status_code == 404


class TestListDecisions:
    def test_owner_gets_decisions_filtered_to_run(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/decisions", headers=headers
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["total"] == 2  # The unrelated one is filtered out.
        actions = {d["action"] for d in body["items"]}
        assert actions == {"USER_ORDER_PLACED", "USER_SIGNAL_SKIPPED_STALE"}

    def test_since_cursor(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        # 20s cutoff — d1 is 30s old (should drop), d2 is 10s old (kept).
        since = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/decisions",
            params={"since": since},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "USER_SIGNAL_SKIPPED_STALE"


class TestListEquity:
    def test_owner_gets_equity_curve(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/equity", headers=headers
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert len(body["items"]) == 5
        assert body["snapshot_balance"] == 10_000.0
        # Chronological order.
        balances = [p["balance"] for p in body["items"]]
        assert balances == sorted(balances)


class TestBacktestOverlay:
    def test_no_genome_returns_unavailable(
        self, test_client_with_db, read_users, owner_paper_run_with_data
    ):
        headers = _auth_headers(
            test_client_with_db,
            read_users["owner"]["email"],
            read_users["owner"]["password"],
        )
        run_id = owner_paper_run_with_data["run"].id
        resp = test_client_with_db.get(
            f"/api/v1/paper-runs/{run_id}/backtest-overlay", headers=headers
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["source"] == "unavailable"
        assert body["reason"] == "no_associated_evolution_run"
        assert body["items"] == []
