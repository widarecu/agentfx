"""User OANDA credentials + accounts router (US-019).

Endpoints — all mounted under `/api/v1/users/me/credentials`:

- `GET  /`             → masked view of the caller's credentials
- `PUT  /`             → upsert (encrypts + persists; clears verified)
- `DELETE /`           → 204
- `POST /verify`       → calls OANDA `/v3/accounts` with the caller's
                         key; on success refreshes the cache table and
                         returns the fresh account list
- `GET  /accounts`     → returns the cached account list

Authorization is caller-scoped: every endpoint operates on the current
user's own row. There is no by-id path — a user can only ever touch
their own credentials.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..core.secrets import SecretEncryptionError, decrypt_secret, encrypt_secret
from ..services import user as user_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _load_credentials(db: Session, user_id: int) -> models.UserOandaCredentials | None:
    return (
        db.query(models.UserOandaCredentials)
        .filter(models.UserOandaCredentials.user_id == user_id)
        .one_or_none()
    )


def _public_view(
    row: models.UserOandaCredentials,
) -> schemas.UserOandaCredentialsPublic:
    return schemas.UserOandaCredentialsPublic(
        environment=(
            row.oanda_environment.value
            if hasattr(row.oanda_environment, "value")
            else str(row.oanda_environment)
        ),
        verified=row.verified_at is not None,
        verified_at=row.verified_at,
        verify_failure_reason=row.verify_failure_reason,
        key_version=row.key_version,
        updated_at=row.updated_at,
    )


def _account_view(row: models.UserOandaAccount) -> schemas.UserOandaAccountPublic:
    return schemas.UserOandaAccountPublic(
        oanda_account_id=row.oanda_account_id,
        alias=row.alias,
        currency=row.currency,
        last_seen_at=row.last_seen_at,
    )


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.get(
    "/users/me/credentials",
    response_model=schemas.UserOandaCredentialsPublic,
)
def get_my_credentials(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.UserOandaCredentialsPublic:
    row = _load_credentials(db, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No OANDA credentials set for this user",
        )
    return _public_view(row)


@router.put(
    "/users/me/credentials",
    response_model=schemas.UserOandaCredentialsPublic,
)
def upsert_my_credentials(
    body: schemas.UserOandaCredentialsUpsert,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.UserOandaCredentialsPublic:
    """Create or replace the caller's credentials.

    Any prior verified state is cleared — the caller must POST /verify
    again after any edit so a stale `verified_at` never survives a key
    change. Cached accounts are dropped for the same reason.
    """
    try:
        encrypted = encrypt_secret(body.oanda_api_key)
    except SecretEncryptionError as e:
        # The exception message is safe to surface (it never contains
        # the key), but we still 500 rather than 400 — encryption
        # failure means the process is mis-provisioned.
        logger.error("credentials upsert: encryption failed (%s)", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server-side encryption is not available",
        ) from e

    row = _load_credentials(db, current_user.id)
    if row is None:
        row = models.UserOandaCredentials(
            user_id=current_user.id,
            oanda_api_key_encrypted=encrypted,
            oanda_environment=models.OandaEnvironment.PRACTICE,
            key_version=1,
        )
        db.add(row)
    else:
        row.oanda_api_key_encrypted = encrypted
        row.oanda_environment = models.OandaEnvironment.PRACTICE
        # Clear verified state so a stale badge doesn't imply that
        # this new key was tested.
        row.verified_at = None
        row.verify_failure_reason = None
        # Cached accounts are stale by definition now — drop.
        for acct in list(row.accounts):
            db.delete(acct)

    db.commit()
    db.refresh(row)
    return _public_view(row)


@router.delete(
    "/users/me/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_my_credentials(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> Response:
    row = _load_credentials(db, current_user.id)
    if row is None:
        # Idempotent — deleting when there's nothing to delete is not
        # an error state. The wireframes call the "Delete" button a
        # danger action; the frontend renders 204 the same as success.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/me/credentials/verify",
    response_model=schemas.VerifyCredentialsResponse,
)
def verify_my_credentials(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.VerifyCredentialsResponse:
    """Call OANDA `/v3/accounts` with the caller's decrypted key.

    On success:
      - `verified_at` set to now
      - `verify_failure_reason` cleared
      - the returned accounts are upserted into `user_oanda_accounts`;
        rows not seen in this response are pruned so the cache reflects
        exactly what OANDA now says.

    On failure:
      - `verified_at` cleared
      - `verify_failure_reason` set to a sanitised message
      - HTTP 400 with the same message
    """
    row = _load_credentials(db, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set credentials before calling /verify",
        )

    # Lazy import so importing this router doesn't drag `requests` /
    # `httpx` in at app-startup time on the code path that doesn't use
    # OANDA (e.g. isolated unit test collection).
    from agentfx.adapters.oanda import OandaClient, OandaError

    try:
        plaintext = decrypt_secret(row.oanda_api_key_encrypted)
    except SecretEncryptionError as e:
        logger.error("credentials verify: decrypt failed (%s)", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored credentials cannot be decrypted",
        ) from e

    env_value = (
        row.oanda_environment.value
        if hasattr(row.oanda_environment, "value")
        else str(row.oanda_environment)
    )
    client = OandaClient(api_key=plaintext, environment=env_value)

    try:
        accounts = client.list_accounts()
    except OandaError as e:
        # We deliberately store the error string — but only the class
        # and status-line portion, never a raw response body which
        # could echo back a stray part of the key.
        sanitised = str(e)[:400]
        row.verified_at = None
        row.verify_failure_reason = sanitised
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=sanitised,
        ) from e

    # Success — refresh the cache table.
    now = datetime.now(UTC)
    existing = {a.oanda_account_id: a for a in row.accounts}
    seen: set[str] = set()
    for acct in accounts:
        aid = acct["account_id"]
        seen.add(aid)
        if aid in existing:
            existing[aid].alias = acct.get("alias")
            existing[aid].currency = acct.get("currency", "USD")
            existing[aid].last_seen_at = now
        else:
            db.add(
                models.UserOandaAccount(
                    user_credentials_id=row.id,
                    oanda_account_id=aid,
                    alias=acct.get("alias"),
                    currency=acct.get("currency", "USD"),
                    last_seen_at=now,
                )
            )
    # Prune anything OANDA no longer reports.
    for aid, cached in existing.items():
        if aid not in seen:
            db.delete(cached)

    row.verified_at = now
    row.verify_failure_reason = None
    db.commit()
    db.refresh(row)

    return schemas.VerifyCredentialsResponse(
        verified=True,
        verified_at=row.verified_at,
        verify_failure_reason=None,
        accounts=[_account_view(a) for a in row.accounts],
    )


@router.get(
    "/users/me/credentials/accounts",
    response_model=schemas.UserOandaAccountsList,
)
def list_my_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.UserOandaAccountsList:
    """Return the caller's cached OANDA accounts (last-verified snapshot).

    This is a cheap read used by the deploy modal to render the
    account picker. Live balances are queried separately (US-020 /
    US-022) — they're never cached in AgentFX.
    """
    row = _load_credentials(db, current_user.id)
    if row is None:
        return schemas.UserOandaAccountsList(items=[], verified_at=None)
    return schemas.UserOandaAccountsList(
        items=[_account_view(a) for a in row.accounts],
        verified_at=row.verified_at,
    )


@router.get("/users/me/credentials/diagnose")
def diagnose_my_credentials(
    account_id: str,
    instrument: str = "EUR_USD",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> dict:
    """Read-only investigation: reproduce the paper runner's OANDA calls.

    Wilson UAT 2026-07-16: staging paper run crashed with 401 on
    `get_account_balance` after 3.5h uptime, but re-verify with the
    same stored token succeeded. To rule out (a) OandaClient
    construction bugs, (b) stateful Session corruption over a
    long-lived instance, (c) intermittent OANDA 401s — hit the same
    three endpoints the runner uses on every tick, 3× each, and
    return timing + status + first bytes of body. If all 9 calls
    return 200, the runner path works right now and the earlier 401
    was either transient at OANDA or a state issue our resilience
    layer must handle. If any 401s, we've reproduced the bug.

    Nothing persisted — pure investigation.
    """
    import time

    from agentfx.adapters.oanda import OandaClient, OandaError

    row = _load_credentials(db, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set credentials before diagnosing",
        )
    try:
        plaintext = decrypt_secret(row.oanda_api_key_encrypted)
    except SecretEncryptionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"decrypt failed: {e}",
        ) from e

    env_value = (
        row.oanda_environment.value
        if hasattr(row.oanda_environment, "value")
        else str(row.oanda_environment)
    )
    # Construct exactly like paper_runner.run_paper_run does.
    client = OandaClient(
        api_key=plaintext,
        environment=env_value,
        account_id=account_id,
    )

    def _hit(name: str, fn) -> dict:
        started = time.time()
        try:
            fn()
            return {
                "name": name,
                "ok": True,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except OandaError as exc:
            return {
                "name": name,
                "ok": False,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": str(exc)[:400],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "name": name,
                "ok": False,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            }

    calls = []
    for i in range(3):
        calls.append(_hit(f"get_account_balance[{i}]", client.get_account_balance))
        calls.append(
            _hit(f"get_position[{i}]", lambda: client.get_position(instrument))
        )
        calls.append(_hit(f"get_pricing[{i}]", lambda: client.get_pricing(instrument)))

    return {
        "account_id": account_id,
        "instrument": instrument,
        "environment": env_value,
        "session_reuse": "single OandaClient across all calls (matches runner)",
        "calls": calls,
        "summary": {
            "total": len(calls),
            "ok": sum(1 for c in calls if c["ok"]),
            "failed": sum(1 for c in calls if not c["ok"]),
        },
    }
