"""Unit tests for the small pure helpers inside paper.py router.

The heavy end-to-end path (auth + DB + FastAPI TestClient) is covered
by `tests/integration/test_paper_router.py`; here we lock the tiny
serialisation + guard helpers that DO make sense to test in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentfx.api import models
from agentfx.api.routers.paper import (
    _ensure_trader,
    _require_paper_run_owner,
    _to_list_item,
    _to_public,
)


def _user(id: int, role: models.UserRole) -> SimpleNamespace:
    return SimpleNamespace(id=id, role=role, email=f"u{id}@example.com")


def _run(user_id: int, status=models.RunStatus.RUNNING) -> SimpleNamespace:
    return SimpleNamespace(
        id="run_test",
        status=status,
        strategy_genome_id=None,
        pair="EUR_USD",
        parameters={"granularity": "H1"},
        oanda_account_id="101-004-99999-001",
        oanda_environment=None,
        snapshot_balance=10_000.0,
        current_balance=10_150.0,
        pnl=150.0,
        risk_config={"max_position_size_units": 10_000},
        stop_reason=None,
        kill_requested_at=None,
        approved_at=datetime.now(UTC),
        approved_by_user_id=user_id,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=None,
        user_id=user_id,
    )


class TestEnsureTrader:
    def test_trader_ok(self):
        _ensure_trader(_user(1, models.UserRole.TRADER))

    def test_admin_ok(self):
        _ensure_trader(_user(1, models.UserRole.ADMIN))

    def test_viewer_403(self):
        with pytest.raises(HTTPException) as exc:
            _ensure_trader(_user(1, models.UserRole.VIEWER))
        assert exc.value.status_code == 403
        assert "TRADER" in exc.value.detail


class TestRequirePaperRunOwner:
    def test_owner_ok(self):
        run = _run(user_id=42)
        _require_paper_run_owner(run, _user(42, models.UserRole.TRADER))

    def test_admin_can_access_others_run(self):
        run = _run(user_id=42)
        _require_paper_run_owner(run, _user(99, models.UserRole.ADMIN))

    def test_non_owner_403(self):
        run = _run(user_id=42)
        with pytest.raises(HTTPException) as exc:
            _require_paper_run_owner(run, _user(99, models.UserRole.TRADER))
        assert exc.value.status_code == 403

    def test_none_owner_fails_closed_for_non_admin(self):
        run = _run(user_id=42)
        run.user_id = None
        with pytest.raises(HTTPException) as exc:
            _require_paper_run_owner(run, _user(42, models.UserRole.TRADER))
        assert exc.value.status_code == 403

    def test_none_owner_admin_still_allowed(self):
        run = _run(user_id=42)
        run.user_id = None
        _require_paper_run_owner(run, _user(99, models.UserRole.ADMIN))


class TestSerialisers:
    def test_to_public_shape(self):
        r = _run(user_id=42)
        out = _to_public(r)
        assert out.id == "run_test"
        assert out.status == "RUNNING"
        assert out.instrument == "EUR_USD"
        assert out.granularity == "H1"
        assert out.snapshot_balance == 10_000.0
        assert out.current_balance == 10_150.0
        assert out.pnl == 150.0

    def test_to_public_handles_string_status(self):
        # When SQLAlchemy has just refreshed and status comes back as a
        # bare string (rare, but observed), the serialiser should not
        # crash.
        r = _run(user_id=42)
        r.status = "COMPLETED"
        out = _to_public(r)
        assert out.status == "COMPLETED"

    def test_to_public_handles_missing_granularity(self):
        r = _run(user_id=42)
        r.parameters = {}
        out = _to_public(r)
        assert out.granularity == ""

    def test_to_list_item_shape(self):
        r = _run(user_id=42)
        out = _to_list_item(r)
        assert out.id == "run_test"
        assert out.instrument == "EUR_USD"
        assert out.snapshot_balance == 10_000.0
