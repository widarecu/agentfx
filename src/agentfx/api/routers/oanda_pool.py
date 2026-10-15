"""OANDA account pool admin router (v0.8.1 C.3).

ADMIN-only endpoints for managing the fleet's pool of OANDA accounts.
The pool is separate from per-portal-user credentials; pool accounts
belong to the fleet, not any specific portal user.

Endpoints — all mounted under `/api/v1/oanda-pool`:

- `POST   /accounts`         → register a new account into the pool
- `GET    /accounts`         → list all pool accounts (never leaks keys)
- `PATCH  /accounts/{id}`    → update nickname / notes / status / rotate key
- `DELETE /accounts/{id}`    → remove a pool account (refuses if IN_USE)
- `GET    /summary`          → pool health snapshot

RBAC: every endpoint depends on `get_current_admin_user`. TRADER /
VIEWER get 403; the pool is operator infrastructure.

Secret handling: the API key arrives in the POST / PATCH body in
plaintext (HTTPS only — operator sends from their machine), is
immediately encrypted via the existing Fernet helper, and never
appears in a response. The GET endpoints return the account_id and
status but never the ciphertext or any derivative of it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import models
from ..core.database import get_db
from ..core.secrets import SecretEncryptionError, encrypt_secret
from ..services import user as user_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Request / response schemas (kept local to the router — these are
# admin-plumbing shapes, not domain contracts worth promoting to the
# shared schemas package).
# ─────────────────────────────────────────────────────────────────────


class _AccountCreate(BaseModel):
    """Body for `POST /oanda-pool/accounts`."""

    account_id: str = Field(..., min_length=3, max_length=50)
    api_key: str = Field(..., min_length=16, max_length=256)
    environment: str = Field(default="practice", pattern="^(practice|live)$")
    nickname: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class _AccountPatch(BaseModel):
    """Body for `PATCH /oanda-pool/accounts/{id}`.

    All fields optional — only provided fields are updated.
    `api_key` rotation bumps `key_version` by 1.
    """

    nickname: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(
        default=None, pattern="^(free|disabled)$"
    )  # Only operator-controllable transitions.
    api_key: str | None = Field(default=None, min_length=16, max_length=256)


class _AccountPublic(BaseModel):
    """Public view of a pool row. Never includes the ciphertext."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    nickname: str | None
    environment: str
    status: str
    current_run_id: str | None
    in_use_since: datetime | None
    lease_expires_at: datetime | None
    cooling_until: datetime | None
    last_run_id: str | None
    last_run_ended_at: datetime | None
    cumulative_pnl_14d: float
    key_version: int
    notes: str | None
    created_at: datetime
    updated_at: datetime | None


class _AccountList(BaseModel):
    items: list[_AccountPublic]
    total: int


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _row_to_public(row: models.OandaAccountPool) -> _AccountPublic:
    def _val(x):
        return x.value if hasattr(x, "value") else str(x) if x is not None else x

    return _AccountPublic(
        id=row.id,
        account_id=row.account_id,
        nickname=row.nickname,
        environment=_val(row.environment),
        status=_val(row.status),
        current_run_id=row.current_run_id,
        in_use_since=row.in_use_since,
        lease_expires_at=row.lease_expires_at,
        cooling_until=row.cooling_until,
        last_run_id=row.last_run_id,
        last_run_ended_at=row.last_run_ended_at,
        cumulative_pnl_14d=row.cumulative_pnl_14d,
        key_version=row.key_version,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_row(db: Session, row_id: str) -> models.OandaAccountPool:
    row = (
        db.query(models.OandaAccountPool)
        .filter(models.OandaAccountPool.id == row_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool account {row_id} not found",
        )
    return row


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.post(
    "/accounts",
    response_model=_AccountPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_pool_account(
    body: _AccountCreate,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> _AccountPublic:
    """Register a new OANDA account into the fleet pool.

    The API key is encrypted at rest with Fernet before persistence.
    Duplicate `account_id` returns 409 — the pool enforces uniqueness
    so the exclusivity guard in paper deploy stays valid.
    """
    existing = (
        db.query(models.OandaAccountPool)
        .filter(models.OandaAccountPool.account_id == body.account_id)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Account {body.account_id} already in pool",
        )

    try:
        ciphertext = encrypt_secret(body.api_key)
    except SecretEncryptionError as exc:
        logger.error("oanda_pool: encryption failed on create (%s)", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server-side encryption is not available",
        ) from exc

    env_enum = models.OandaAccountPoolEnvironment(body.environment)

    row = models.OandaAccountPool(
        id=uuid.uuid4().hex,
        account_id=body.account_id,
        api_key_encrypted=ciphertext,
        environment=env_enum,
        status=models.OandaAccountPoolStatus.FREE,
        key_version=1,
        nickname=body.nickname,
        notes=body.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "oanda_pool: account_id=%s added to pool (env=%s, nickname=%s)",
        row.account_id,
        body.environment,
        body.nickname or "<unnamed>",
    )
    return _row_to_public(row)


@router.get("/accounts", response_model=_AccountList)
def list_pool_accounts(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> _AccountList:
    """List every pool account. Keys never leak — only metadata."""
    rows = (
        db.query(models.OandaAccountPool)
        .order_by(models.OandaAccountPool.created_at.asc())
        .all()
    )
    return _AccountList(items=[_row_to_public(r) for r in rows], total=len(rows))


@router.get("/accounts/{row_id}", response_model=_AccountPublic)
def get_pool_account(
    row_id: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> _AccountPublic:
    return _row_to_public(_get_row(db, row_id))


@router.patch("/accounts/{row_id}", response_model=_AccountPublic)
def update_pool_account(
    row_id: str,
    body: _AccountPatch,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> _AccountPublic:
    """Update nickname / notes / status, or rotate the API key.

    Only `free` ⇄ `disabled` transitions are operator-controllable.
    `in_use` / `cooling_down` are fleet-managed and the endpoint
    refuses to touch them. Rotating `api_key` bumps `key_version`.
    """
    row = _get_row(db, row_id)
    touched = False

    if body.nickname is not None:
        row.nickname = body.nickname
        touched = True
    if body.notes is not None:
        row.notes = body.notes
        touched = True
    if body.status is not None:
        current = row.status.value if hasattr(row.status, "value") else str(row.status)
        if current not in ("free", "disabled"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot change status while account is {current}. "
                    "Wait for the paper run to release the slot."
                ),
            )
        row.status = models.OandaAccountPoolStatus(body.status)
        touched = True
    if body.api_key is not None:
        try:
            row.api_key_encrypted = encrypt_secret(body.api_key)
        except SecretEncryptionError as exc:
            logger.error("oanda_pool: encryption failed on key rotation (%s)", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server-side encryption is not available",
            ) from exc
        row.key_version = (row.key_version or 1) + 1
        touched = True

    if touched:
        row.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(row)
    return _row_to_public(row)


@router.delete(
    "/accounts/{row_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_pool_account(
    row_id: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> Response:
    """Remove a pool account.

    Refuses while status is IN_USE — a running paper run depends on
    it. Operator should DISABLE first, wait for the run to end, then
    DELETE. COOLING_DOWN and DISABLED are deletable.
    """
    row = _get_row(db, row_id)
    current = row.status.value if hasattr(row.status, "value") else str(row.status)
    if current == "in_use":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Pool account is IN_USE by run {row.current_run_id}. "
                "Disable it and wait for the run to release the slot."
            ),
        )
    db.delete(row)
    db.commit()
    logger.info("oanda_pool: account_id=%s removed from pool", row.account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/summary")
def pool_summary_endpoint(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(user_service.get_current_admin_user),
) -> dict:
    """Pool health snapshot — counts by status, concurrency usage,
    oldest FREE / COOLING_DOWN accounts. Same shape the PM reads
    when it builds its scheduling context, exposed here so Wilson
    can sanity-check it from a browser."""
    from agentfx.agents.fleet.account_pool import pool_summary

    return pool_summary(db)
