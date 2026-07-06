"""Row-level access control for user-owned resources (US-018).

Two primitives, used by every list / read / delete endpoint that
returns rows tagged with `created_by`:

- `scope_query_to_user(query, model, current_user, scope)` — folds a
  `created_by == current_user.id` filter into an in-flight SQLAlchemy
  query when the caller isn't ADMIN. ADMIN callers passing `scope="all"`
  get the unfiltered query. VIEWER / TRADER are always filtered to their
  own rows regardless of the requested scope.
- `require_owner_or_admin(row, current_user, resource)` — 403s a
  router when a caller with a valid session tries to touch a row they
  don't own. Callers that hit rows they can't see get a 404 via the
  list-level filter; callers that reach a row directly (e.g. a
  guessed ID via GET/DELETE by id) get a 403 here.

Both are pure — no DB access, no HTTP status codes leak into the
non-router layers. Router imports both; services take the filter
values as plain kwargs and don't know about roles.

See planning/requirements/US-018-per-user-rbac-filter.md.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Query

from ..models.user import User, UserRole

# Public scope values accepted from the API. Kept explicit so a typo in a
# query string reaches the caller as a 400 instead of silently widening.
Scope = Literal["my", "all"]


def scope_query_to_user(
    query: Query,
    model: Any,
    current_user: User,
    *,
    scope: Scope = "my",
) -> Query:
    """Fold a `created_by = current_user.id` filter into `query`.

    Semantics:

    - ADMIN + scope="all"  → unfiltered (sees every row)
    - ADMIN + scope="my"   → own rows only (default; a fresh ADMIN
      session shouldn't accidentally show the whole system's data)
    - VIEWER / TRADER      → own rows only, regardless of `scope`.
      The router MUST enforce a 403 on `scope="all"` for non-admins
      before calling this (see `require_admin_for_scope_all`) — this
      function is defense-in-depth and won't leak, but a 403 gives the
      caller a clear signal that their request was rejected rather
      than silently narrowed.

    `model` is the SQLAlchemy declarative class the query is anchored
    on — it must expose a `created_by` column. Passing a model without
    that column is a programming error; caught by unit tests.
    """
    if not hasattr(model, "created_by"):
        raise TypeError(
            f"scope_query_to_user: {model.__name__} has no `created_by` column"
        )

    if current_user.role == UserRole.ADMIN and scope == "all":
        return query

    return query.filter(model.created_by == current_user.id)


def require_admin_for_scope_all(current_user: User, scope: Scope) -> None:
    """Router-side gate: `scope=all` is ADMIN-only.

    Returns cleanly for allowed combinations. Raises 403 when a
    non-ADMIN caller asks for the widened scope. Called before
    `scope_query_to_user` so the caller learns why their request was
    rejected instead of getting an empty list.
    """
    if scope == "all" and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="scope=all requires ADMIN role",
        )


def require_owner_or_admin(
    row: Any,
    current_user: User,
    *,
    resource: str = "resource",
) -> None:
    """Router-side gate for direct-ID access.

    Call after fetching a row by primary key. If the row's `created_by`
    doesn't match the caller and the caller isn't ADMIN, raise 403.

    Passing a row that has no `created_by` (or has one that is None) is
    treated as a bug — such rows predate the ownership migration and
    should be filtered out or migrated before this helper sees them.
    We fail closed (403) rather than open in that case.
    """
    if current_user.role == UserRole.ADMIN:
        return

    owner_id = getattr(row, "created_by", None)
    if owner_id is None or owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access this {resource}",
        )


__all__ = [
    "Scope",
    "require_admin_for_scope_all",
    "require_owner_or_admin",
    "scope_query_to_user",
]
