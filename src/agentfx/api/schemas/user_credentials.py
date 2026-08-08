"""Pydantic schemas for the /users/me/credentials/* endpoints (US-019).

The public read view NEVER echoes the plaintext API key. Every field
that leaves the API is either a mask, a booleanised status, or a
timestamp — the ciphertext lives in `LargeBinary` on the ORM model
and is decrypted only inside the paper-runner subprocess.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseSchema

# The public API only accepts the practice endpoint for v0.6.0. LIVE is
# gated behind v1.0.0 and blocked by the router; typing it here as a
# `Literal` gives us a 422 at parse time instead of a runtime branch.
CredentialsEnvironment = Literal["practice"]


class UserOandaCredentialsPublic(BaseSchema):
    """Read view. Callers see the environment + verified state but never
    the plaintext key or any part of the ciphertext."""

    environment: str
    verified: bool
    verified_at: datetime | None = None
    verify_failure_reason: str | None = None
    key_version: int
    updated_at: datetime | None = None


class UserOandaCredentialsUpsert(BaseSchema):
    """PUT body — full replacement of the caller's credentials."""

    oanda_api_key: str = Field(
        ...,
        min_length=16,
        max_length=500,
        description=(
            "The OANDA v20 REST API token generated in the OANDA "
            "dashboard. Stored encrypted; never echoed back."
        ),
    )
    environment: CredentialsEnvironment = Field(
        default="practice",
        description="Only `practice` is accepted in v0.6.0.",
    )


class UserOandaAccountPublic(BaseSchema):
    """One row in the cached accounts list."""

    oanda_account_id: str
    alias: str | None = None
    currency: str
    last_seen_at: datetime
    # v0.7.x — set to the run_id of the caller's APPROVED or RUNNING
    # paper run on this account, if one exists; NULL otherwise.
    # DeployPaperModal shows a warning strip when the picked account
    # has a non-null value here so the operator sees the collision
    # before submit. See `find_active_paper_run_on_account` in
    # services/paper.py for the exact query.
    active_paper_run_id: str | None = None


class UserOandaAccountsList(BaseSchema):
    """Response body for `/credentials/accounts`."""

    items: list[UserOandaAccountPublic]
    verified_at: datetime | None = None


class VerifyCredentialsResponse(BaseSchema):
    """Response from `POST /credentials/verify`.

    Success returns the fresh account list AgentFX just fetched from
    OANDA and cached; failure returns a sanitised human-readable reason
    plus HTTP 400. This shape lets the frontend re-render the
    detected-accounts panel from the response body directly rather than
    round-tripping the GET.
    """

    verified: bool
    verified_at: datetime | None = None
    verify_failure_reason: str | None = None
    accounts: list[UserOandaAccountPublic] = Field(default_factory=list)


__all__ = [
    "UserOandaAccountPublic",
    "UserOandaAccountsList",
    "UserOandaCredentialsPublic",
    "UserOandaCredentialsUpsert",
    "VerifyCredentialsResponse",
]
