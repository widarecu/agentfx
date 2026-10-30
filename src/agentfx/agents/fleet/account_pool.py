"""OANDA account pool helpers (AFX-7 — pivoted 2026-10-29).

The pool is derived from `UserOandaAccount` rows under
`UserOandaCredentials.allow_fleet_pool_use=True`. OANDA's
`/v3/accounts` already enumerates every sub-account under a user's
single API token (synced on credential verify), so a separate pool
table + admin UI was redundant architecture — the operator opts in
once via a Settings toggle and every sub-account they can see is
lendable to the fleet.

Design philosophy:
- Allocation reads: FIFO over FREE sub-accounts belonging to any
  consenting credentials row, oldest-created first so cooled-down
  accounts cycle in evenly. The parent credentials' api_key is
  decrypted per allocation (one shared key for all its sub-accounts).
- Release is DB-authoritative: when a paper run terminates, the
  paper runner calls `release_slot(run_id)` which flips the
  `UserOandaAccount` row to COOLING_DOWN with a 1h gate. No
  background sweeper needed — allocation reads automatically re-flip
  COOLING_DOWN rows to FREE once `cooling_until` has passed.
- Lease TTL protects against stuck IN_USE rows if a paper runner
  crashes without releasing. Default 14 days; allocation reads
  reclaim expired leases automatically.
- Idempotency on same `run_id`: a crashed subprocess that gets
  re-scheduled with the SAME run_id reuses its existing slot.
  Allocating a different account would strand positions on the old
  one.

Opt-in semantics: a user who adds OANDA creds for their own portal
use is NEVER opted in by default (`allow_fleet_pool_use=False`). The
Settings page is the only place that flips it.

Environment: fleet pool is practice-only. Live credentials, even
when consented, are refused — pool allocation returns
`refused:live_env_blocked`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from agentfx.api.models.user_credentials import (
    OandaEnvironment,
    UserOandaAccount,
    UserOandaAccountPoolStatus,
    UserOandaCredentials,
)

logger = logging.getLogger(__name__)


# Default lease length — a paper run grabs an account for this long
# before the allocator reclaims it (assuming the runner crashed
# without calling release). 14 days matches typical UAT paper-run
# windows; override per-call if a specific experiment needs longer.
_DEFAULT_LEASE_DAYS = 14

# Default cooling-down period after a run ends — gives OANDA a chance
# to reconcile position state + avoids rate-limit edge cases on
# back-to-back runs against the same account.
_DEFAULT_COOLING_HOURS = 1

# Soft cap: the fleet won't allocate past this many concurrent paper
# runs across the entire pool. Prevents runaway even if the operator
# consents a huge number of sub-accounts.
_MAX_CONCURRENT_PAPER_RUNS = 10


# ─────────────────────────────────────────────────────────────────────
# Allocation result types
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SlotAllocation:
    """Returned by `allocate_slot` — either success with a slot, or
    a reasoned refusal.

    `account_id` and `environment` are what the paper runner needs
    to actually trade; `api_key_plaintext` is decrypted at allocation
    time and should be consumed immediately (never logged, never
    persisted).

    `pool_row_id` is the opaque UserOandaAccount id to pass back to
    `release_slot(run_id)` when the paper run terminates.
    """

    accepted: bool
    pool_row_id: str | None = None
    account_id: str | None = None
    environment: str | None = None
    api_key_plaintext: str | None = None
    reason: str = ""  # Why rejected, if applicable


# ─────────────────────────────────────────────────────────────────────
# Internal state transitions (used by allocate/release + tests)
# ─────────────────────────────────────────────────────────────────────


def _sweep_cooldowns(db: Session, *, now: datetime) -> int:
    """Flip COOLING_DOWN rows whose `cooling_until` has passed back
    to FREE. Called at the top of each allocation attempt so pool
    reads don't miss recently-cooled accounts.

    Returns the number of rows transitioned."""
    rows = (
        db.query(UserOandaAccount)
        .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.COOLING_DOWN)
        .filter(UserOandaAccount.cooling_until.isnot(None))
        .filter(UserOandaAccount.cooling_until <= now)
        .all()
    )
    for row in rows:
        row.pool_status = UserOandaAccountPoolStatus.FREE
        row.cooling_until = None
    if rows:
        db.flush()
    return len(rows)


def _sweep_expired_leases(db: Session, *, now: datetime) -> int:
    """Flip IN_USE rows whose `lease_expires_at` has passed back to
    FREE. Protects against stuck rows when a paper runner crashed
    without calling `release_slot`.

    Returns the number of rows reclaimed."""
    rows = (
        db.query(UserOandaAccount)
        .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
        .filter(UserOandaAccount.lease_expires_at.isnot(None))
        .filter(UserOandaAccount.lease_expires_at <= now)
        .all()
    )
    for row in rows:
        logger.warning(
            "account_pool: reclaiming expired lease on account_id=%s "
            "run_id=%s (lease was until %s, now %s)",
            row.oanda_account_id,
            row.current_run_id,
            row.lease_expires_at,
            now,
        )
        row.last_run_id = row.current_run_id
        row.last_run_ended_at = now
        row.current_run_id = None
        row.in_use_since = None
        row.lease_expires_at = None
        row.pool_status = UserOandaAccountPoolStatus.FREE
    if rows:
        db.flush()
    return len(rows)


def _decrypt_credentials_key(creds: UserOandaCredentials) -> str | None:
    """Decrypt the parent credentials row's api_key. Returns None on
    failure (and logs); callers treat None as a refused allocation."""
    try:
        from agentfx.api.core.secrets import decrypt_secret

        return decrypt_secret(bytes(creds.oanda_api_key_encrypted))
    except Exception:
        logger.exception(
            "account_pool: decrypt failed for credentials id=%s user_id=%s",
            creds.id,
            creds.user_id,
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def allocate_slot(
    db: Session,
    *,
    run_id: str,
    environment: OandaEnvironment | None = None,
    lease_days: int = _DEFAULT_LEASE_DAYS,
    now: datetime | None = None,
) -> SlotAllocation:
    """Reserve a FREE sub-account for `run_id`.

    - Idempotent on `run_id`: a reschedule of the same run reuses its
      existing allocation (critical — allocating a different account
      would strand OANDA positions on the old one).
    - Only considers sub-accounts whose parent credentials have
      `allow_fleet_pool_use=True`.
    - LIVE environment is refused even when consented — fleet pool
      is practice-only per AFX-7's off-limits list.
    - Sweeps expired leases + cooled-down rows first so the FREE set
      is current.
    - Enforces the fleet-wide concurrency cap.
    - Allocates the oldest-created FREE sub-account matching
      `environment` (or any environment if None).
    - Flips the row to IN_USE with lease, decrypts the parent's
      api_key, and returns the triple the paper runner needs.

    Never raises. On any error / no eligible row, returns
    `SlotAllocation(accepted=False, reason=...)`.
    """
    if environment == OandaEnvironment.LIVE:
        return SlotAllocation(accepted=False, reason="refused:live_env_blocked")

    now = now or datetime.now(UTC)
    try:
        # Idempotency on restart: if this run_id already holds a row,
        # reuse it. Safe to run before the sweeps.
        existing = (
            db.query(UserOandaAccount)
            .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
            .filter(UserOandaAccount.current_run_id == run_id)
            .first()
        )
        if existing is not None:
            creds = existing.credentials
            plaintext = _decrypt_credentials_key(creds)
            if plaintext is None:
                return SlotAllocation(accepted=False, reason="decrypt_failed_on_reuse")
            env_value = (
                creds.oanda_environment.value
                if hasattr(creds.oanda_environment, "value")
                else str(creds.oanda_environment)
            )
            logger.info(
                "account_pool: reusing existing allocation for run_id=%s "
                "(account_id=%s, lease_until=%s)",
                run_id,
                existing.oanda_account_id,
                existing.lease_expires_at,
            )
            return SlotAllocation(
                accepted=True,
                pool_row_id=existing.id,
                account_id=existing.oanda_account_id,
                environment=env_value,
                api_key_plaintext=plaintext,
            )

        _sweep_cooldowns(db, now=now)
        _sweep_expired_leases(db, now=now)

        in_use_count = (
            db.query(UserOandaAccount)
            .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
            .count()
        )
        if in_use_count >= _MAX_CONCURRENT_PAPER_RUNS:
            return SlotAllocation(
                accepted=False,
                reason=f"concurrent_cap_hit:{in_use_count}/{_MAX_CONCURRENT_PAPER_RUNS}",
            )

        # Pick the oldest FREE sub-account whose parent credentials
        # (a) consent to fleet use and (b) match the requested env.
        q = (
            db.query(UserOandaAccount)
            .join(UserOandaCredentials, UserOandaAccount.credentials)
            .filter(UserOandaCredentials.allow_fleet_pool_use.is_(True))
            .filter(UserOandaCredentials.oanda_environment != OandaEnvironment.LIVE)
            .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.FREE)
            .order_by(UserOandaAccount.created_at.asc())
        )
        if environment is not None:
            q = q.filter(UserOandaCredentials.oanda_environment == environment)
        row = q.first()
        if row is None:
            env_label = environment.value if environment else "any"
            return SlotAllocation(
                accepted=False, reason=f"no_free_account:env={env_label}"
            )

        creds = row.credentials
        plaintext = _decrypt_credentials_key(creds)
        if plaintext is None:
            return SlotAllocation(accepted=False, reason="decrypt_failed")

        row.pool_status = UserOandaAccountPoolStatus.IN_USE
        row.current_run_id = run_id
        row.in_use_since = now
        row.lease_expires_at = now + timedelta(days=lease_days)
        db.flush()

        env_value = (
            creds.oanda_environment.value
            if hasattr(creds.oanda_environment, "value")
            else str(creds.oanda_environment)
        )
        logger.info(
            "account_pool: allocated account_id=%s to run_id=%s "
            "(row=%s, lease=%d days)",
            row.oanda_account_id,
            run_id,
            row.id,
            lease_days,
        )
        return SlotAllocation(
            accepted=True,
            pool_row_id=row.id,
            account_id=row.oanda_account_id,
            environment=env_value,
            api_key_plaintext=plaintext,
        )
    except Exception as exc:
        logger.exception(
            "account_pool: unexpected allocation failure for run_id=%s", run_id
        )
        return SlotAllocation(
            accepted=False, reason=f"allocator_error:{type(exc).__name__}"
        )


def release_slot(
    db: Session,
    *,
    run_id: str,
    cooling_hours: int = _DEFAULT_COOLING_HOURS,
    now: datetime | None = None,
) -> bool:
    """Release the sub-account currently held by `run_id`,
    transitioning it to COOLING_DOWN with the specified cooling
    window.

    Returns True iff a row was found and released. False means the
    run wasn't holding any pool slot (benign — paper runs that
    bypassed the pool, or a double-release).

    Never raises.
    """
    now = now or datetime.now(UTC)
    try:
        row = (
            db.query(UserOandaAccount)
            .filter(UserOandaAccount.pool_status == UserOandaAccountPoolStatus.IN_USE)
            .filter(UserOandaAccount.current_run_id == run_id)
            .first()
        )
        if row is None:
            return False

        row.last_run_id = row.current_run_id
        row.last_run_ended_at = now
        row.current_run_id = None
        row.in_use_since = None
        row.lease_expires_at = None
        row.pool_status = UserOandaAccountPoolStatus.COOLING_DOWN
        row.cooling_until = now + timedelta(hours=cooling_hours)
        db.flush()
        logger.info(
            "account_pool: released account_id=%s from run_id=%s (cooling until %s)",
            row.oanda_account_id,
            run_id,
            row.cooling_until,
        )
        return True
    except Exception:
        logger.exception("account_pool: release failed for run_id=%s", run_id)
        return False


def pool_summary(db: Session, *, now: datetime | None = None) -> dict:
    """Lightweight snapshot of pool health, shaped for the PM
    scheduling-context payload.

    Only counts sub-accounts whose parent credentials consent to
    fleet use (`allow_fleet_pool_use=True`).
    """
    now = now or datetime.now(UTC)
    rows = (
        db.query(UserOandaAccount)
        .join(UserOandaCredentials, UserOandaAccount.credentials)
        .filter(UserOandaCredentials.allow_fleet_pool_use.is_(True))
        .all()
    )
    counts: dict[str, int] = {}
    free_accounts: list[dict] = []
    cooling_accounts: list[dict] = []
    for r in rows:
        key = (
            r.pool_status.value
            if hasattr(r.pool_status, "value")
            else str(r.pool_status)
        )
        counts[key] = counts.get(key, 0) + 1
        if r.pool_status == UserOandaAccountPoolStatus.FREE:
            env = (
                r.credentials.oanda_environment.value
                if hasattr(r.credentials.oanda_environment, "value")
                else str(r.credentials.oanda_environment)
            )
            free_accounts.append(
                {
                    "account_id": r.oanda_account_id,
                    "alias": r.alias,
                    "environment": env,
                }
            )
        elif r.pool_status == UserOandaAccountPoolStatus.COOLING_DOWN:
            # SQLite drops tz-info on stored DateTime columns; Postgres
            # preserves it. Normalise both sides so the subtraction
            # works regardless of backend.
            seconds_until_free = 0
            if r.cooling_until is not None:
                until = r.cooling_until
                if until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                seconds_until_free = int((until - now).total_seconds())
            cooling_accounts.append(
                {
                    "account_id": r.oanda_account_id,
                    "seconds_until_free": max(0, seconds_until_free),
                }
            )
    in_use_count = counts.get(UserOandaAccountPoolStatus.IN_USE.value, 0)
    return {
        "total_accounts": len(rows),
        "status_counts": counts,
        "concurrent_in_use": in_use_count,
        "max_concurrent_runs": _MAX_CONCURRENT_PAPER_RUNS,
        "free_accounts": free_accounts,
        "cooling_down_accounts": cooling_accounts,
    }


__all__ = [
    "SlotAllocation",
    "allocate_slot",
    "release_slot",
    "pool_summary",
]
