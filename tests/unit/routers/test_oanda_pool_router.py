"""Unit tests for the OANDA account pool admin router (v0.8.1 C.3).

Each endpoint is exercised against a real in-memory SQLite session so
the Fernet encrypt/decrypt + SQLAlchemy row behaviour are honest. Auth
is checked by calling the admin dependency directly — a non-ADMIN role
must raise 403.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.api import models
from agentfx.api.core.database import Base
from agentfx.api.core.secrets import decrypt_secret, encrypt_secret
from agentfx.api.routers import oanda_pool as oanda_pool_router
from agentfx.api.services import user as user_service

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess
    Base.metadata.drop_all(eng)


@pytest.fixture
def admin_user():
    u = Mock(spec=models.User)
    u.id = 1
    u.email = "admin@example.com"
    u.role = models.UserRole.ADMIN
    u.is_active = True
    return u


@pytest.fixture
def trader_user():
    u = Mock(spec=models.User)
    u.id = 2
    u.email = "trader@example.com"
    u.role = models.UserRole.TRADER
    u.is_active = True
    return u


def _seed_row(db, **overrides) -> models.OandaAccountPool:
    row = models.OandaAccountPool(
        account_id=overrides.get("account_id", "101-004-pool-001"),
        api_key_encrypted=encrypt_secret(overrides.get("api_key", "tok")),
        environment=overrides.get(
            "environment", models.OandaAccountPoolEnvironment.PRACTICE
        ),
        status=overrides.get("status", models.OandaAccountPoolStatus.FREE),
        nickname=overrides.get("nickname"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────
# RBAC
# ─────────────────────────────────────────────────────────────────────


class TestRBAC:
    def test_non_admin_is_rejected(self, trader_user):
        with pytest.raises(HTTPException) as exc:
            user_service.get_current_admin_user(current_user=trader_user)
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# POST /accounts
# ─────────────────────────────────────────────────────────────────────


class TestCreate:
    def test_create_persists_encrypted(self, db, admin_user):
        body = oanda_pool_router._AccountCreate(
            account_id="101-004-new-001",
            api_key="plaintext-token-abc-123",
            environment="practice",
            nickname="first",
        )
        result = oanda_pool_router.create_pool_account(
            body=body, db=db, _admin=admin_user
        )
        assert result.account_id == "101-004-new-001"
        assert result.status == "free"
        assert result.nickname == "first"
        # Round-trip: the ciphertext must decrypt back to the input.
        row = (
            db.query(models.OandaAccountPool)
            .filter_by(account_id="101-004-new-001")
            .one()
        )
        assert decrypt_secret(row.api_key_encrypted) == "plaintext-token-abc-123"

    def test_duplicate_account_id_409(self, db, admin_user):
        _seed_row(db, account_id="dup-001")
        body = oanda_pool_router._AccountCreate(
            account_id="dup-001",
            api_key="k" * 20,
            environment="practice",
        )
        with pytest.raises(HTTPException) as exc:
            oanda_pool_router.create_pool_account(body=body, db=db, _admin=admin_user)
        assert exc.value.status_code == 409


# ─────────────────────────────────────────────────────────────────────
# GET /accounts + /accounts/{id}
# ─────────────────────────────────────────────────────────────────────


class TestList:
    def test_list_never_leaks_ciphertext(self, db, admin_user):
        _seed_row(db, account_id="A", api_key="secret-literal-aaa")
        _seed_row(db, account_id="B", api_key="secret-literal-bbb")
        result = oanda_pool_router.list_pool_accounts(db=db, _admin=admin_user)
        assert result.total == 2
        payload = result.model_dump_json()
        assert "secret-literal-aaa" not in payload
        assert "secret-literal-bbb" not in payload

    def test_get_single_404_when_missing(self, db, admin_user):
        with pytest.raises(HTTPException) as exc:
            oanda_pool_router.get_pool_account(
                row_id="does-not-exist", db=db, _admin=admin_user
            )
        assert exc.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# PATCH /accounts/{id}
# ─────────────────────────────────────────────────────────────────────


class TestPatch:
    def test_update_nickname_only(self, db, admin_user):
        row = _seed_row(db, nickname="old")
        body = oanda_pool_router._AccountPatch(nickname="new-label")
        result = oanda_pool_router.update_pool_account(
            row_id=row.id, body=body, db=db, _admin=admin_user
        )
        assert result.nickname == "new-label"
        db.refresh(row)
        assert row.nickname == "new-label"

    def test_rotate_api_key_bumps_version(self, db, admin_user):
        row = _seed_row(db, api_key="old-key")
        body = oanda_pool_router._AccountPatch(api_key="brand-new-key-xyz-99")
        before_version = row.key_version
        oanda_pool_router.update_pool_account(
            row_id=row.id, body=body, db=db, _admin=admin_user
        )
        db.refresh(row)
        assert row.key_version == before_version + 1
        assert decrypt_secret(row.api_key_encrypted) == "brand-new-key-xyz-99"

    def test_status_change_rejected_when_in_use(self, db, admin_user):
        row = _seed_row(db, status=models.OandaAccountPoolStatus.IN_USE)
        body = oanda_pool_router._AccountPatch(status="disabled")
        with pytest.raises(HTTPException) as exc:
            oanda_pool_router.update_pool_account(
                row_id=row.id, body=body, db=db, _admin=admin_user
            )
        assert exc.value.status_code == 409

    def test_status_free_to_disabled_allowed(self, db, admin_user):
        row = _seed_row(db)
        body = oanda_pool_router._AccountPatch(status="disabled")
        result = oanda_pool_router.update_pool_account(
            row_id=row.id, body=body, db=db, _admin=admin_user
        )
        assert result.status == "disabled"


# ─────────────────────────────────────────────────────────────────────
# DELETE /accounts/{id}
# ─────────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_free_account(self, db, admin_user):
        row = _seed_row(db)
        oanda_pool_router.delete_pool_account(row_id=row.id, db=db, _admin=admin_user)
        assert db.query(models.OandaAccountPool).count() == 0

    def test_delete_in_use_is_blocked_409(self, db, admin_user):
        row = _seed_row(
            db,
            status=models.OandaAccountPoolStatus.IN_USE,
        )
        row.current_run_id = "run_in_flight"
        db.commit()
        with pytest.raises(HTTPException) as exc:
            oanda_pool_router.delete_pool_account(
                row_id=row.id, db=db, _admin=admin_user
            )
        assert exc.value.status_code == 409
        # Row is still there.
        assert db.query(models.OandaAccountPool).count() == 1


# ─────────────────────────────────────────────────────────────────────
# GET /summary
# ─────────────────────────────────────────────────────────────────────


class TestSummary:
    def test_summary_shape(self, db, admin_user):
        _seed_row(db, account_id="A")
        _seed_row(db, account_id="B", status=models.OandaAccountPoolStatus.DISABLED)
        summary = oanda_pool_router.pool_summary_endpoint(db=db, _admin=admin_user)
        assert summary["total_accounts"] == 2
        assert summary["status_counts"]["free"] == 1
        assert summary["status_counts"]["disabled"] == 1
