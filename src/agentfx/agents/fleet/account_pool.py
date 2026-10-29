"""OANDA account pool helpers (v0.8.1 C.3).

Fleet-side allocation + release logic for the `oanda_account_pool`
table. v0.8.1 ships the data model + admin surface + these helpers;
v0.8.2 wires PM to actually call `allocate_slot` when it approves a
paper deployment.

Design philosophy:
- The pool is append-only from the operator's side (Wilson POSTs new
  accounts via `/oanda-pool/accounts`). Deletes are supported but
  rare — status flips to DISABLED cover the usual "take this out of
  rotation temporarily" case.
- Allocation is simple-first: FIFO over the FREE set, oldest-created
  first so cooled-down accounts cycle in evenly. v0.8.2 will swap
  this for a scoring function (prefer accounts with positive 14d
  P&L, etc.) once there's enough history to grade.
- Release is DB-authoritative: when a paper run terminates, the
  paper runner calls `release_slot(run_id)` which flips the pool
  row to COOLING_DOWN with a 1h gate. No background sweeper needed
  — allocation reads automatically re-flip COOLING_DOWN rows to
  FREE once `cooling_until` has passed.
- Lease TTL protects against stuck IN_USE rows if a paper runner
  crashes without releasing. Default 14 days; allocation reads
  reclaim expired leases automatically.

Why not an in-memory lock? Multiple backend processes (if we ever
scale horizontally) would race. DB row-level status is the only
consistent authority.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from agentfx.api.models.oanda_account_pool import (
    OandaAccountPool,
    OandaAccountPoolEnvironment,
    OandaAccountPoolStatus,
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
# runs. Prevents runaway even if Wilson adds a huge number of pool
# accounts. Can be raised via settings later.
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

    `pool_row_id` is the opaque pool-row id to pass back to
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
        db.query(OandaAccountPool)
        .filter(OandaAccountPool.status == OandaAccountPoolStatus.COOLING_DOWN)
        .filter(OandaAccountPool.cooling_until.isnot(None))
        .filter(OandaAccountPool.cooling_until <= now)
        .all()
    )
    for row in rows:
        row.status = OandaAccountPoolStatus.FREE
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
        db.query(OandaAccountPool)
        .filter(OandaAccountPool.status == OandaAccountPoolStatus.IN_USE)
        .filter(OandaAccountPool.lease_expires_at.isnot(None))
        .filter(OandaAccountPool.lease_expires_at <= now)
        .all()
    )
    for row in rows:
        logger.warning(
            "account_pool: reclaiming expired lease on account_id=%s "
            "run_id=%s (lease was until %s, now %s)",
            row.account_id,
            row.current_run_id,
            row.lease_expires_at,
            now,
        )
        row.last_run_id = row.current_run_id
        row.last_run_ended_at = now
        row.current_run_id = None
        row.in_use_since = None
        row.lease_expires_at = None
        row.status = OandaAccountPoolStatus.FREE
    if rows:
        db.flush()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def allocate_slot(
    db: Session,
    *,
    run_id: str,
    environment: OandaAccountPoolEnvironment | None = None,
    lease_days: int = _DEFAULT_LEASE_DAYS,
    now: datetime | None = None,
) -> SlotAllocation:
    """Reserve a FREE pool account for `run_id`.

    - Sweeps expired leases + cooled-down rows first so the FREE set
      is current.
    - Enforces the fleet-wide concurrency cap.
    - Allocates the oldest-FREE row matching `environment` (or any
      environment if None).
    - Flips the row to IN_USE with lease, decrypts the API key, and
      returns the triple the paper runner needs.

    Never raises. On any error / no eligible row, returns
    `SlotAllocation(accepted=False, reason=...)`.
    """
    now = now or datetime.now(UTC)
    try:
        # Idempotency on restart: if this run_id already holds an
        # IN_USE row (crash + re-schedule within the lease window),
        # reuse it. Allocating a NEW slot would strand positions on
        # the old account. Safe to run before the sweeps since this
        # path only touches rows explicitly keyed to `run_id`.
        existing = (
            db.query(OandaAccountPool)
            .filter(OandaAccountPool.status == OandaAccountPoolStatus.IN_USE)
            .filter(OandaAccountPool.current_run_id == run_id)
            .first()
        )
        if existing is not None:
            try:
                from agentfx.api.core.secrets import decrypt_secret

                plaintext = decrypt_secret(bytes(existing.api_key_encrypted))
            except Exception as exc:
                logger.exception(
                    "account_pool: decrypt failed on restart-reuse "
                    "pool row=%s account_id=%s",
                    existing.id,
                    existing.account_id,
                )
                return SlotAllocation(
                    accepted=False,
                    reason=f"decrypt_failed_on_reuse:{type(exc).__name__}",
                )
            logger.info(
                "account_pool: reusing existing allocation for run_id=%s "
                "(pool_row=%s, account_id=%s, lease_until=%s)",
                run_id,
                existing.id,
                existing.account_id,
                existing.lease_expires_at,
            )
            return SlotAllocation(
                accepted=True,
                pool_row_id=existing.id,
                account_id=existing.account_id,
                environment=(
                    existing.environment.value
                    if hasattr(existing.environment, "value")
                    else str(existing.environment)
                ),
                api_key_plaintext=plaintext,
            )

        _sweep_cooldowns(db, now=now)
        _sweep_expired_leases(db, now=now)

        in_use_count = (
            db.query(OandaAccountPool)
            .filter(OandaAccountPool.status == OandaAccountPoolStatus.IN_USE)
            .count()
        )
        if in_use_count >= _MAX_CONCURRENT_PAPER_RUNS:
            return SlotAllocation(
                accepted=False,
                reason=f"concurrent_cap_hit:{in_use_count}/{_MAX_CONCURRENT_PAPER_RUNS}",
            )

        q = (
            db.query(OandaAccountPool)
            .filter(OandaAccountPool.status == OandaAccountPoolStatus.FREE)
            .order_by(OandaAccountPool.created_at.asc())
        )
        if environment is not None:
            q = q.filter(OandaAccountPool.environment == environment)
        row = q.first()
        if row is None:
            return SlotAllocation(
                accepted=False,
                reason=f"no_free_account:env={environment or 'any'}",
            )

        # Decrypt the API key — fail allocation (don't commit the
        # transition) if the Fernet key can't open the ciphertext.
        try:
            from agentfx.api.core.secrets import decrypt_secret

            plaintext = decrypt_secret(bytes(row.api_key_encrypted))
        except Exception as exc:
            logger.exception(
                "account_pool: decrypt failed for pool row=%s account_id=%s",
                row.id,
                row.account_id,
            )
            return SlotAllocation(
                accepted=False, reason=f"decrypt_failed:{type(exc).__name__}"
            )

        row.status = OandaAccountPoolStatus.IN_USE
        row.current_run_id = run_id
        row.in_use_since = now
        row.lease_expires_at = now + timedelta(days=lease_days)
        db.flush()

        logger.info(
            "account_pool: allocated account_id=%s to run_id=%s "
            "(pool_row=%s, lease=%d days)",
            row.account_id,
            run_id,
            row.id,
            lease_days,
        )
        return SlotAllocation(
            accepted=True,
            pool_row_id=row.id,
            account_id=row.account_id,
            environment=(
                row.environment.value
                if hasattr(row.environment, "value")
                else str(row.environment)
            ),
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
    """Release the pool row currently held by `run_id`, transitioning
    it to COOLING_DOWN with the specified cooling-down window.

    Returns True iff a row was found and released. False means the
    run wasn't holding any pool slot (benign — paper runs created
    before the pool existed, or a double-release).

    Never raises.
    """
    now = now or datetime.now(UTC)
    try:
        row = (
            db.query(OandaAccountPool)
            .filter(OandaAccountPool.status == OandaAccountPoolStatus.IN_USE)
            .filter(OandaAccountPool.current_run_id == run_id)
            .first()
        )
        if row is None:
            return False

        row.last_run_id = row.current_run_id
        row.last_run_ended_at = now
        row.current_run_id = None
        row.in_use_since = None
        row.lease_expires_at = None
        row.status = OandaAccountPoolStatus.COOLING_DOWN
        row.cooling_until = now + timedelta(hours=cooling_hours)
        db.flush()
        logger.info(
            "account_pool: released account_id=%s from run_id=%s (cooling until %s)",
            row.account_id,
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

    Returns counts by status, concurrent-run usage against the cap,
    and the oldest FREE/COOLING_DOWN rows so PM can anticipate
    allocations.
    """
    now = now or datetime.now(UTC)
    rows = db.query(OandaAccountPool).all()
    counts: dict[str, int] = {}
    free_accounts: list[dict] = []
    cooling_accounts: list[dict] = []
    for r in rows:
        key = r.status.value if hasattr(r.status, "value") else str(r.status)
        counts[key] = counts.get(key, 0) + 1
        if r.status == OandaAccountPoolStatus.FREE:
            free_accounts.append(
                {
                    "account_id": r.account_id,
                    "nickname": r.nickname,
                    "environment": (
                        r.environment.value
                        if hasattr(r.environment, "value")
                        else str(r.environment)
                    ),
                }
            )
        elif r.status == OandaAccountPoolStatus.COOLING_DOWN:
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
                    "account_id": r.account_id,
                    "seconds_until_free": max(0, seconds_until_free),
                }
            )
    in_use_count = counts.get(OandaAccountPoolStatus.IN_USE.value, 0)
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
