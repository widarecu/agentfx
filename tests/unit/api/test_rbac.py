"""Unit tests for src/agentfx/api/core/rbac.py (US-018).

Pure — no DB, no HTTP, no Fastapi TestClient. The router-integration
side is exercised in tests/integration/test_rbac_isolation.py; here we
lock the primitives down so a subtle change in the helper doesn't
silently widen access.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentfx.api.core.rbac import (
    require_admin_for_scope_all,
    require_owner_or_admin,
    scope_query_to_user,
)
from agentfx.api.models.user import UserRole


def _user(id: int, role: UserRole) -> SimpleNamespace:
    return SimpleNamespace(id=id, role=role, email=f"u{id}@example.com")


class _FakeModel:
    """SQLAlchemy-like model shim exposing a `created_by` attribute."""

    class created_by:  # noqa: N801  — SQLA column-style class attribute
        pass


class _ModelWithoutOwner:
    pass


# ─────────────────────────────────────────────────────────────────────────
# scope_query_to_user
# ─────────────────────────────────────────────────────────────────────────


class TestScopeQueryToUser:
    def test_admin_scope_all_returns_query_unfiltered(self):
        q = MagicMock(name="unfiltered_query")
        user = _user(1, UserRole.ADMIN)

        result = scope_query_to_user(q, _FakeModel, user, scope="all")

        assert result is q
        q.filter.assert_not_called()

    def test_admin_scope_my_is_filtered_to_own_rows(self):
        q = MagicMock(name="raw_query")
        filtered = q.filter.return_value
        user = _user(1, UserRole.ADMIN)

        result = scope_query_to_user(q, _FakeModel, user, scope="my")

        # A fresh ADMIN session defaults to their own rows so they don't
        # accidentally see the whole system.
        assert result is filtered
        q.filter.assert_called_once()

    def test_trader_scope_all_still_filtered(self):
        # scope_query_to_user must not silently widen access for a
        # non-admin caller. The router-side gate
        # (require_admin_for_scope_all) is what raises 403, but the helper
        # is defense-in-depth: if the router forgets, the query stays
        # scoped.
        q = MagicMock()
        filtered = q.filter.return_value
        user = _user(2, UserRole.TRADER)

        result = scope_query_to_user(q, _FakeModel, user, scope="all")

        assert result is filtered
        q.filter.assert_called_once()

    def test_viewer_is_always_scoped_to_own(self):
        q = MagicMock()
        filtered = q.filter.return_value
        user = _user(3, UserRole.VIEWER)

        result = scope_query_to_user(q, _FakeModel, user, scope="my")

        assert result is filtered
        q.filter.assert_called_once()

    def test_scope_raises_typeerror_for_model_without_created_by(self):
        q = MagicMock()
        user = _user(1, UserRole.TRADER)

        with pytest.raises(TypeError, match="created_by"):
            scope_query_to_user(q, _ModelWithoutOwner, user)


# ─────────────────────────────────────────────────────────────────────────
# require_admin_for_scope_all
# ─────────────────────────────────────────────────────────────────────────


class TestRequireAdminForScopeAll:
    def test_admin_scope_all_ok(self):
        require_admin_for_scope_all(_user(1, UserRole.ADMIN), scope="all")

    def test_admin_scope_my_ok(self):
        require_admin_for_scope_all(_user(1, UserRole.ADMIN), scope="my")

    def test_trader_scope_my_ok(self):
        require_admin_for_scope_all(_user(2, UserRole.TRADER), scope="my")

    def test_viewer_scope_my_ok(self):
        require_admin_for_scope_all(_user(3, UserRole.VIEWER), scope="my")

    def test_trader_scope_all_403(self):
        with pytest.raises(HTTPException) as exc:
            require_admin_for_scope_all(_user(2, UserRole.TRADER), scope="all")
        assert exc.value.status_code == 403
        assert "ADMIN" in exc.value.detail

    def test_viewer_scope_all_403(self):
        with pytest.raises(HTTPException) as exc:
            require_admin_for_scope_all(_user(3, UserRole.VIEWER), scope="all")
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────
# require_owner_or_admin
# ─────────────────────────────────────────────────────────────────────────


class TestRequireOwnerOrAdmin:
    def test_owner_ok(self):
        row = SimpleNamespace(created_by=42)
        require_owner_or_admin(row, _user(42, UserRole.TRADER))

    def test_owner_viewer_ok(self):
        row = SimpleNamespace(created_by=42)
        require_owner_or_admin(row, _user(42, UserRole.VIEWER))

    def test_admin_can_access_others_row(self):
        row = SimpleNamespace(created_by=99)
        require_owner_or_admin(row, _user(1, UserRole.ADMIN))

    def test_non_owner_403(self):
        row = SimpleNamespace(created_by=99)
        with pytest.raises(HTTPException) as exc:
            require_owner_or_admin(row, _user(42, UserRole.TRADER))
        assert exc.value.status_code == 403
        assert "authorized" in exc.value.detail.lower()

    def test_non_owner_viewer_403(self):
        row = SimpleNamespace(created_by=99)
        with pytest.raises(HTTPException) as exc:
            require_owner_or_admin(row, _user(42, UserRole.VIEWER))
        assert exc.value.status_code == 403

    def test_none_owner_fails_closed_for_non_admin(self):
        # Rows with a NULL created_by predate the ownership migration.
        # Non-admin callers must NOT be able to reach them via direct-ID —
        # a real fix would migrate the rows to a system-user, but until
        # that happens we fail closed (403) so nothing leaks.
        row = SimpleNamespace(created_by=None)
        with pytest.raises(HTTPException) as exc:
            require_owner_or_admin(row, _user(1, UserRole.TRADER))
        assert exc.value.status_code == 403

    def test_none_owner_admin_still_allowed(self):
        row = SimpleNamespace(created_by=None)
        require_owner_or_admin(row, _user(1, UserRole.ADMIN))

    def test_resource_name_appears_in_error(self):
        row = SimpleNamespace(created_by=99)
        with pytest.raises(HTTPException) as exc:
            require_owner_or_admin(
                row, _user(42, UserRole.TRADER), resource="evolution run"
            )
        assert "evolution run" in exc.value.detail
