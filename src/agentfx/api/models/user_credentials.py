"""Per-user OANDA credentials + detected sub-accounts (US-019).

Two tables. The v0.6.0 UX split (see `portal/web/wireframes/v0.6.0/`)
took Wilson's answer #4 — AgentFX never asks the user to type an
account ID or "initial balance". The API key alone identifies the
OANDA login, and the sub-accounts under it are discovered from OANDA
on verify.

- `user_oanda_credentials` — one row per user. Encrypted API key (Fernet
  ciphertext in a `BYTEA` column), environment (PRACTICE for MVP, LIVE
  gated behind v1.0.0), verified state.
- `user_oanda_accounts` — cache of the accounts returned by OANDA's
  `/v3/accounts` at last verify. Live balances are always re-queried
  from OANDA on demand; only the identity + alias + currency are
  persisted so the deploy modal can render a picker without a network
  round-trip.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class OandaEnvironment(str, Enum):
    """OANDA API endpoints."""

    PRACTICE = "practice"
    LIVE = "live"


class UserOandaAccountPoolStatus(str, Enum):
    """Fleet-pool lifecycle for a single UserOandaAccount row.

    - FREE: available to the fleet's `allocate_slot` (default).
    - IN_USE: a paper run is currently trading on this account.
    - COOLING_DOWN: a run just released it; allocator re-flips to
      FREE once `cooling_until` passes.
    - DISABLED: operator took it out of rotation (future flag — not
      editable from the current Settings UI).
    """

    FREE = "free"
    IN_USE = "in_use"
    COOLING_DOWN = "cooling_down"
    DISABLED = "disabled"


def _uuid_hex() -> str:
    """Generate a hex-encoded UUID4 for use as a portable primary key.

    We store as `String(36)` rather than the Postgres native UUID type so
    the same models work under SQLite for unit tests. The trade-off is
    a slightly larger column and no automatic uniqueness guarantee at
    the DB layer beyond the PK constraint.
    """
    return uuid.uuid4().hex


class UserOandaCredentials(Base):
    """One per user — encrypted API key + verification state."""

    __tablename__ = "user_oanda_credentials"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Fernet ciphertext of the OANDA v20 REST API token. NEVER stored
    # or logged in plaintext — the paper runner subprocess decrypts
    # in-memory just before making a request.
    oanda_api_key_encrypted = Column(LargeBinary, nullable=False)

    oanda_environment = Column(
        SQLEnum(
            OandaEnvironment,
            values_callable=lambda x: [e.value for e in x],
            name="oandaenvironment",
        ),
        nullable=False,
        default=OandaEnvironment.PRACTICE,
    )

    # Populated by the /verify endpoint. NULL means "not yet verified"
    # or "verification failed" — combined with `verify_failure_reason`
    # for the UI to disambiguate.
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verify_failure_reason = Column(String(500), nullable=True)

    # Version tag for a future key-rotation flow (v0.7.0). All rows
    # written in v0.6.0 use version 1; rotation will re-encrypt in place
    # and bump the version.
    key_version = Column(Integer, nullable=False, default=1)

    # AFX-7 opt-in: when True, the fleet's `account_pool.allocate_slot`
    # may lease sub-accounts under these credentials to paper runs it
    # spawns. Default False — opt-in is explicit so a user who adds
    # OANDA creds for their own portal use never has their accounts
    # silently conscripted into fleet experiments. Flipped via the
    # Settings page.
    allow_fleet_pool_use = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", foreign_keys=[user_id])
    accounts = relationship(
        "UserOandaAccount",
        back_populates="credentials",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        # Deliberately does NOT include any part of the ciphertext, key
        # version, or user email — a stray log line stays useless to
        # an attacker who scrapes stdout.
        return f"<UserOandaCredentials user_id={self.user_id} env={self.oanda_environment}>"


class UserOandaAccount(Base):
    """Cached OANDA sub-account discovered via `/v3/accounts`.

    Balance is intentionally NOT stored — the deploy modal and run
    detail page always fetch live from OANDA on 5s polling. What lives
    here is the stable identity metadata: account ID, alias, currency.
    """

    __tablename__ = "user_oanda_accounts"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    user_credentials_id = Column(
        String(36),
        ForeignKey("user_oanda_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The OANDA account identifier like "101-004-12345678-001".
    oanda_account_id = Column(String(64), nullable=False)
    alias = Column(String(128), nullable=True)
    currency = Column(String(8), nullable=False)

    # When we last saw this account in an OANDA /v3/accounts response.
    # Rows older than the current verify are pruned by the router; kept
    # for a short window so a transient OANDA outage doesn't
    # immediately erase the picker's options.
    last_seen_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ─── Fleet pool state (AFX-7) ───
    # Only consulted when the parent credentials row has
    # `allow_fleet_pool_use=True`. All fields are server-managed by
    # `agents/fleet/account_pool.py`; the portal is read-only on them.
    pool_status = Column(
        SQLEnum(
            UserOandaAccountPoolStatus,
            values_callable=lambda x: [e.value for e in x],
            name="useroandaaccountpoolstatus",
        ),
        nullable=False,
        default=UserOandaAccountPoolStatus.FREE,
        server_default=UserOandaAccountPoolStatus.FREE.value,
    )
    current_run_id = Column(String(120), nullable=True)
    in_use_since = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    cooling_until = Column(DateTime(timezone=True), nullable=True)
    last_run_id = Column(String(120), nullable=True)
    last_run_ended_at = Column(DateTime(timezone=True), nullable=True)
    cumulative_pnl_14d = Column(
        Float, nullable=False, default=0.0, server_default="0.0"
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    credentials = relationship("UserOandaCredentials", back_populates="accounts")

    __table_args__ = (
        UniqueConstraint(
            "user_credentials_id",
            "oanda_account_id",
            name="uq_user_oanda_account_per_credentials",
        ),
        Index(
            "ix_user_oanda_account_pool_status",
            "pool_status",
        ),
        Index(
            "ix_user_oanda_account_current_run_id",
            "current_run_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserOandaAccount {self.oanda_account_id} "
            f"({self.currency}) alias={self.alias!r}>"
        )


__all__ = [
    "OandaEnvironment",
    "UserOandaAccount",
    "UserOandaAccountPoolStatus",
    "UserOandaCredentials",
]
