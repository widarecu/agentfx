"""SQLAlchemy model for the OANDA account pool (v0.8.1 C.3).

Fleet-managed pool of OANDA accounts that PM can allocate from for
parallel paper experiments. Separate from `UserOandaCredentials` /
`UserOandaAccount` (which are per-portal-user credential + account
discovery) — pool accounts belong to the fleet, not to any single
portal user.

Operational flow:
1. Wilson creates OANDA practice accounts out-of-band (via OANDA's
   website).
2. For each one, Wilson POSTs to `/oanda-pool/accounts` with the
   account_id + API key + environment. The API key is encrypted at
   rest via the existing Fernet infra from US-019.
3. PM Agent reads the pool on each scheduling tick; allocates free
   accounts for new paper experiments.
4. The paper runner subprocess decrypts the API key in memory just
   before the first OANDA call — plaintext never touches disk.

Status lifecycle:
- `free` — available for allocation. Default on insert.
- `in_use` — a paper run is currently trading against this account.
  `current_run_id` + `in_use_since` record which + when.
- `cooling_down` — the previous paper run has ended; a short wait
  (default 1 hour) prevents OANDA rate-limit + position-
  reconciliation edge cases between back-to-back runs on the same
  account. `cooling_until` records the gate time.
- `disabled` — operator-marked unusable (e.g. OANDA closed the
  account, credential rotation needed). PM skips these.

RBAC: the admin endpoints live under ADMIN role. Pool rows are
fleet-owned and not visible to regular TRADER users.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.sql import func
from sqlalchemy import Enum as SQLEnum

from .user import Base


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class OandaAccountPoolStatus(str, Enum):
    """Lifecycle state of one pool account."""

    FREE = "free"
    IN_USE = "in_use"
    COOLING_DOWN = "cooling_down"
    DISABLED = "disabled"


class OandaAccountPoolEnvironment(str, Enum):
    """Which OANDA environment this account targets."""

    PRACTICE = "practice"
    LIVE = "live"


class OandaAccountPool(Base):
    """One row per OANDA account in the fleet pool."""

    __tablename__ = "oanda_account_pool"

    id = Column(String(36), primary_key=True, default=_uuid_hex)

    # Human-readable label for the operator's dashboard view.
    # Deliberately nullable — a bare account_id is fine.
    nickname = Column(String(60), nullable=True)

    # The OANDA v20 account identifier, e.g. `101-004-12345678-001`.
    # Unique in the pool — the same OANDA account can't appear twice
    # (would break the exclusivity guard in paper deploy).
    account_id = Column(String(50), nullable=False, unique=True, index=True)

    environment = Column(
        SQLEnum(
            OandaAccountPoolEnvironment,
            values_callable=lambda x: [e.value for e in x],
            name="oandaaccountpoolenvironment",
        ),
        nullable=False,
        default=OandaAccountPoolEnvironment.PRACTICE,
    )

    # Fernet ciphertext of the OANDA API token. NEVER stored or
    # logged in plaintext — the paper runner subprocess decrypts
    # in-memory just before making a request.
    api_key_encrypted = Column(LargeBinary, nullable=False)
    # Version tag for a future key-rotation flow. All rows written
    # in v0.8.1 use version 1.
    key_version = Column(Integer, nullable=False, default=1)

    status = Column(
        SQLEnum(
            OandaAccountPoolStatus,
            values_callable=lambda x: [e.value for e in x],
            name="oandaaccountpoolstatus",
        ),
        nullable=False,
        default=OandaAccountPoolStatus.FREE,
        index=True,
    )

    # When status == IN_USE, which run holds the lease. FK to runs
    # with ondelete=SET NULL so a run deletion doesn't cascade here.
    # No formal FK constraint for operational flexibility — a stuck
    # row with a dangling current_run_id is recoverable, a cascaded
    # pool deletion is not.
    current_run_id = Column(String(120), nullable=True, index=True)
    in_use_since = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Transition to COOLING_DOWN after a run ends; stays until
    # `cooling_until`, then auto-flips to FREE on the next pool read.
    cooling_until = Column(DateTime(timezone=True), nullable=True)

    # Last run that used this account — reference even after status
    # moves on, for operator traceability.
    last_run_id = Column(String(120), nullable=True)
    last_run_ended_at = Column(DateTime(timezone=True), nullable=True)

    # Rolling 14-day P&L across runs on this account. Updated when
    # a run terminates. Used by PM's allocation scoring (future
    # v0.8.2) to prefer accounts that have been earning vs bleeding.
    cumulative_pnl_14d = Column(Float, nullable=False, default=0.0)

    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Primary allocation query — "give me a FREE account."
        Index("idx_oanda_pool_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        # Deliberately omits the ciphertext + any identifying details
        # beyond the account_id (which is already visible in OANDA's
        # own URLs).
        return (
            f"<OandaAccountPool account_id={self.account_id} "
            f"status={self.status} env={self.environment}>"
        )
