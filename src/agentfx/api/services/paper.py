"""Service layer for the /paper-runs router (US-021 / US-023).

Bridges the router (which handles HTTP + RBAC) and the paper runner
worker (which lives in a subprocess). Every mutation here writes to
`runs` and, where relevant, to `decision_logs` — that gives the
`AgentType.USER` audit trail the plan calls for (US-018).

The functions are DB-only. Scheduling the runner subprocess is the
router's job (via `BackgroundTasks`) so tests can call the service
without spawning processes.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from collections.abc import Iterable

from sqlalchemy import and_
from sqlalchemy.orm import Session

from .. import models
from ..schemas.paper import DeployPaperRequest


def _new_paper_run_id(instrument: str, granularity: str) -> str:
    """Human-legible run id — matches the legacy `run_` format so the
    UI's monospace column stays consistent across dev / paper rows.
    """
    short = uuid.uuid4().hex[:10]
    ts = int(time.time() * 1000)
    return f"run_{ts}_paper_{instrument}_{granularity}_{short}"


# ─────────────────────────────────────────────────────────────────────
# Approval queue
# ─────────────────────────────────────────────────────────────────────


def list_approval_queue_for_user(
    db: Session,
    user_id: int,
) -> list[models.StrategyGenome]:
    """Return validated genomes not yet approved or rejected by this user.

    A genome is on the queue when:
    - `validator_passed = True` on the genome row, AND
    - its parent evolution run's `created_by == user_id`
      (the user only sees the queue their own runs produced), AND
    - there is no `runs` row for that genome with the user as owner
      whose status is APPROVED / RUNNING / AWAITING_APPROVAL / HALTED_MANUAL /
      COMPLETED.

    The last predicate implements "one paper deployment per genome per
    user" — approving twice would duplicate broker orders.
    """
    # 1. Grab every genome that passed Gate B whose run is the user's.
    passing = (
        db.query(models.StrategyGenome)
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .join(models.EvolutionRun, models.Generation.run_id == models.EvolutionRun.id)
        .filter(
            models.StrategyGenome.validator_passed.is_(True),
            models.EvolutionRun.created_by == user_id,
        )
        .all()
    )

    # 2. Filter out ones the user has already acted on. We fetch the
    #    set of "already-touched" genome ids in one query rather than
    #    per-row.
    touched = {
        r.strategy_genome_id
        for r in db.query(models.Run.strategy_genome_id)
        .filter(
            models.Run.user_id == user_id,
            models.Run.strategy_genome_id.isnot(None),
        )
        .all()
    }

    return [g for g in passing if g.id not in touched]


# ─────────────────────────────────────────────────────────────────────
# Approve — create a paper run row + return it
# ─────────────────────────────────────────────────────────────────────


def approve_and_deploy(
    db: Session,
    body: DeployPaperRequest,
    *,
    current_user: models.User,
) -> models.Run:
    """Create the AWAITING_APPROVAL → APPROVED transition and persist
    the paper run row. The router calls `schedule_paper_run(run.id)`
    afterward.

    Refuses (raises `ValueError`) when any of the pre-conditions the
    UX enforces at the deploy modal are missing at the DB layer:
    - genome doesn't exist / doesn't belong to the caller
    - genome hasn't passed Gate B
    - user has no verified OANDA credentials
    - the chosen `oanda_account_id` isn't one of the user's detected
      accounts

    The router translates these into 400s.
    """
    genome = _load_owned_validated_genome(db, body.strategy_genome_id, current_user.id)
    creds = _load_verified_credentials(db, current_user.id)
    _validate_account_choice(creds, body.oanda_account_id)

    name = body.name or (
        f"{body.instrument} {body.granularity} — {datetime.now(UTC):%Y-%m-%d}"
    )
    now = datetime.now(UTC)

    run = models.Run(
        id=_new_paper_run_id(body.instrument, body.granularity),
        # `strategy_id` remains nullable for paper runs; the genome
        # link is the source of truth.
        strategy_id=None,
        strategy_genome_id=genome.id,
        pair=body.instrument,
        environment=models.RunEnvironment.PAPER,
        status=models.RunStatus.APPROVED,
        parameters={
            "name": name,
            "instrument": body.instrument,
            "granularity": body.granularity,
            "dsl_json": genome.dsl_json,
        },
        # Historical initial_balance column stays populated with the
        # snapshot's future landing value once the runner ticks; keep
        # 0 here so a mid-flight query never sees a stale 10000
        # default.
        initial_balance=0.0,
        current_balance=0.0,
        pnl=0.0,
        user_id=current_user.id,
        oanda_account_id=body.oanda_account_id,
        oanda_environment=(
            creds.oanda_environment.value
            if hasattr(creds.oanda_environment, "value")
            else str(creds.oanda_environment)
        ),
        risk_config=body.risk_config.model_dump(),
        approved_at=now,
        approved_by_user_id=current_user.id,
    )
    db.add(run)

    # Audit trail (US-018) — a real user did this.
    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=_link_to_evolution_run_id(genome),
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_APPROVE_GENOME.value,
            user_id=current_user.id,
            inputs_json={
                "strategy_genome_id": genome.id,
                "instrument": body.instrument,
                "granularity": body.granularity,
                "oanda_account_id": body.oanda_account_id,
                "risk_config": body.risk_config.model_dump(),
                "paper_run_id": run.id,
            },
        )
    )

    db.commit()
    db.refresh(run)
    return run


# ─────────────────────────────────────────────────────────────────────
# Reject
# ─────────────────────────────────────────────────────────────────────


def reject_genome(
    db: Session,
    *,
    strategy_genome_id: str,
    reason: str,
    current_user: models.User,
) -> None:
    """Write a rejection row to the decision log.

    We don't create a `runs` row on reject — the approval queue query
    treats the log row as the sentinel. Same-genome, same-user pairs
    are unique in the audit trail because the queue filters out
    already-touched genomes.
    """
    genome = _load_owned_validated_genome(db, strategy_genome_id, current_user.id)
    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=_link_to_evolution_run_id(genome),
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_REJECT_GENOME.value,
            user_id=current_user.id,
            inputs_json={
                "strategy_genome_id": genome.id,
                "reason": reason,
            },
        )
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# List / detail / kill / delete
# ─────────────────────────────────────────────────────────────────────


def list_paper_runs(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 10,
    status: str | None = None,
    instrument: str | None = None,
    created_by: int | None = None,
) -> tuple[list[models.Run], int]:
    """Paginated listing of paper runs.

    `created_by` is set by the router from `current_user.id` unless
    the caller is an ADMIN passing `?scope=all` (US-018).
    """
    q = db.query(models.Run).filter(
        models.Run.environment == models.RunEnvironment.PAPER
    )
    if status:
        q = q.filter(models.Run.status == status)
    if instrument:
        q = q.filter(models.Run.pair == instrument)
    if created_by is not None:
        q = q.filter(models.Run.user_id == created_by)
    total = q.count()
    items = q.order_by(models.Run.created_at.desc()).offset(skip).limit(limit).all()
    return items, total


def get_paper_run(db: Session, run_id: str) -> models.Run | None:
    return (
        db.query(models.Run)
        .filter(
            models.Run.id == run_id,
            models.Run.environment == models.RunEnvironment.PAPER,
        )
        .one_or_none()
    )


def request_kill(
    db: Session,
    run: models.Run,
    *,
    current_user: models.User,
) -> models.Run:
    """Set `kill_requested_at` to now.

    Idempotent — if the flag is already set the timestamp stays put.
    The runner polls this on every loop tick; the router doesn't
    signal the process directly.
    """
    now = datetime.now(UTC)
    if run.kill_requested_at is None:
        run.kill_requested_at = now
    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=None,  # paper-run kills aren't tied to an evolution run
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_KILL_PAPER.value,
            user_id=current_user.id,
            inputs_json={"paper_run_id": run.id},
        )
    )
    db.commit()
    db.refresh(run)
    return run


def delete_paper_run(db: Session, run: models.Run) -> None:
    """Delete a paper run row.

    Router-level guard refuses on non-terminal states so this
    function trusts its input.
    """
    db.delete(run)
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _load_owned_validated_genome(
    db: Session,
    genome_id: str,
    user_id: int,
) -> models.StrategyGenome:
    genome = (
        db.query(models.StrategyGenome)
        .filter(models.StrategyGenome.id == genome_id)
        .one_or_none()
    )
    if genome is None:
        raise ValueError("genome not found")
    if not genome.validator_passed:
        raise ValueError("genome has not passed Gate B validation")

    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .one_or_none()
    )
    if generation is None:
        raise ValueError("genome's generation is missing")

    evolution_run = (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.id == generation.run_id)
        .one_or_none()
    )
    if evolution_run is None or evolution_run.created_by != user_id:
        raise ValueError("genome not owned by caller")

    return genome


def _load_verified_credentials(
    db: Session,
    user_id: int,
) -> models.UserOandaCredentials:
    creds = (
        db.query(models.UserOandaCredentials)
        .filter(models.UserOandaCredentials.user_id == user_id)
        .one_or_none()
    )
    if creds is None or creds.verified_at is None:
        raise ValueError(
            "OANDA credentials not set or not verified — hit /users/me/credentials"
        )
    return creds


def _validate_account_choice(
    creds: models.UserOandaCredentials,
    oanda_account_id: str,
) -> None:
    account_ids = {a.oanda_account_id for a in creds.accounts}
    if oanda_account_id not in account_ids:
        raise ValueError(
            f"OANDA account '{oanda_account_id}' is not one of the "
            "user's detected sub-accounts"
        )


def _link_to_evolution_run_id(genome: models.StrategyGenome) -> str | None:
    """Best-effort backlink to the genome's evolution run — used as
    `decision_logs.run_id`. Nullable in case the parent evolution run
    was deleted between validation and approval.
    """
    generation = getattr(genome, "generation", None)
    if generation is not None:
        return generation.run_id
    return None


def find_orphaned_running_paper_runs(db: Session) -> Iterable[models.Run]:
    """Lifespan startup: paper runs left as RUNNING when the app
    crashed. The caller resets them to APPROVED so the scheduler picks
    them back up.
    """
    return (
        db.query(models.Run)
        .filter(
            and_(
                models.Run.environment == models.RunEnvironment.PAPER,
                models.Run.status == models.RunStatus.RUNNING,
            )
        )
        .all()
    )


__all__ = [
    "approve_and_deploy",
    "delete_paper_run",
    "find_orphaned_running_paper_runs",
    "get_paper_run",
    "list_approval_queue_for_user",
    "list_paper_runs",
    "reject_genome",
    "request_kill",
]
