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

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Iterable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .. import models
from ..schemas.paper import (
    ApprovalQueueMeta,
    ClosestGenome,
    DeployPaperRequest,
)

logger = logging.getLogger(__name__)


@dataclass
class ApprovalQueueResult:
    """What `list_approval_queue_for_user` now returns.

    Split into the deployable list (`items`) and the diagnostic counts
    (`meta`) so the router can pass both through to the response schema
    without recomputing anything.
    """

    items: list[models.StrategyGenome]
    meta: ApprovalQueueMeta


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
) -> ApprovalQueueResult:
    """Return deployable genomes + diagnostic meta counts for the user.

    A genome is on the queue when:
    - `validator_passed = True` on the genome row, AND
    - its parent evolution run's `created_by == user_id`
      (the user only sees the queue their own runs produced), AND
    - there is no `runs` row for that genome with the user as owner
      whose status is APPROVED / RUNNING / AWAITING_APPROVAL / HALTED_MANUAL /
      COMPLETED.

    The last predicate implements "one paper deployment per genome per
    user" — approving twice would duplicate broker orders.

    US-027: the meta counts drive the empty-state diagnostic banner. A
    generic "queue empty" hides four very different situations (no
    evolutions yet / evolutions ran but 0 validated / validated-none-
    pass / all-passing-touched); the counts let the frontend render an
    accurate explanation. Wilson's 2026-07-08 smoke-test mis-diagnosed
    the empty queue as a write-back bug because there was no diagnostic
    on the page.
    """
    # 1. Genomes with a written verdict (any value) under this user's
    #    evolution runs — the pool the meta counts sum over.
    verdicts = (
        db.query(models.StrategyGenome)
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .join(models.EvolutionRun, models.Generation.run_id == models.EvolutionRun.id)
        .filter(
            models.EvolutionRun.created_by == user_id,
            models.StrategyGenome.validator_passed.isnot(None),
        )
        .all()
    )
    passing = [g for g in verdicts if g.validator_passed is True]
    failed = [g for g in verdicts if g.validator_passed is False]

    # 2. Filter out passing genomes the user has already acted on.
    touched = {
        r.strategy_genome_id
        for r in db.query(models.Run.strategy_genome_id)
        .filter(
            models.Run.user_id == user_id,
            models.Run.strategy_genome_id.isnot(None),
        )
        .all()
    }
    deployable = [g for g in passing if g.id not in touched]

    # 3. Meta rollups. Kept as separate queries so the row-fetch above
    #    only pulls verdicted genomes — count queries stay cheap even
    #    when the user has 10k+ genomes.
    evolution_run_count = (
        db.query(func.count(models.EvolutionRun.id))
        .filter(models.EvolutionRun.created_by == user_id)
        .scalar()
        or 0
    )
    total_genomes = (
        db.query(func.count(models.StrategyGenome.id))
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .join(models.EvolutionRun, models.Generation.run_id == models.EvolutionRun.id)
        .filter(models.EvolutionRun.created_by == user_id)
        .scalar()
        or 0
    )

    meta = ApprovalQueueMeta(
        evolution_run_count=int(evolution_run_count),
        total_genomes=int(total_genomes),
        validated_count=len(verdicts),
        passed_count=len(passing),
        touched_count=sum(1 for g in passing if g.id in touched),
        top_close_to_passing=(
            _top_close_to_passing(db, failed, limit=5)
            if failed and not deployable
            else []
        ),
    )
    return ApprovalQueueResult(items=deployable, meta=meta)


# ─────────────────────────────────────────────────────────────────────
# US-027: closest-to-passing ranking
#
# Gate B thresholds — kept in sync with `agentfx.core.gates` (which is
# the authoritative source in the backend) and the frontend Vitest
# constants in `portal/web/src/types/paper.ts::GATE_B_THRESHOLDS`. If
# these ever drift the empty-state banner will silently mis-rank
# genomes; changing thresholds should be a coordinated 3-place edit.
# ─────────────────────────────────────────────────────────────────────
_GATE_B_SHARPE_MIN = 1.0
_GATE_B_MAX_DRAWDOWN_MAX = 0.10  # absolute value; storage is negative
_GATE_B_HIT_RATE_MIN = 0.48
_GATE_B_TRADES_MIN = 300


def _top_close_to_passing(
    db: Session,
    failed_genomes: list[models.StrategyGenome],
    *,
    limit: int = 5,
) -> list[ClosestGenome]:
    """Return up to `limit` failed genomes ordered by distance to passing.

    Ranking: sum of normalized deficits across the four Gate B gates
    (Sharpe / |MDD| / Hit rate / Trades). Smaller total deficit = closer
    to passing. Ties broken by `gates_cleared` desc, then by ID for a
    deterministic order.

    Rationale (v0.6.1 UAT feedback, 2026-07-13): the previous "max
    median OOS Sharpe" heuristic could rank a genome with 1 fluky
    high-Sharpe fold above a genome with 5 solid folds averaging lower.
    Normalized-distance is noise-resistant because low trade counts
    incur their own deficit rather than being ignored.
    """
    if not failed_genomes:
        return []

    ids = [g.id for g in failed_genomes]
    oos_rows = (
        db.query(models.ValidationFold)
        .filter(
            models.ValidationFold.genome_id.in_(ids),
            models.ValidationFold.is_oos.is_(True),
        )
        .all()
    )
    if not oos_rows:
        return []

    by_genome: dict[str, list[models.ValidationFold]] = {}
    for row in oos_rows:
        by_genome.setdefault(row.genome_id, []).append(row)

    def _median(values: list[float]) -> float:
        s = sorted(values)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    # Batch-load the generation → run_id mapping so a 5-row rank
    # doesn't fire 5 separate SELECTs.
    gen_ids = {g.generation_id for g in failed_genomes if g.generation_id}
    gen_to_run: dict[str, str] = {}
    if gen_ids:
        gen_to_run = {
            gid: rid
            for gid, rid in db.query(models.Generation.id, models.Generation.run_id)
            .filter(models.Generation.id.in_(gen_ids))
            .all()
        }

    genome_lookup = {g.id: g for g in failed_genomes}
    scored: list[ClosestGenome] = []
    for gid, folds in by_genome.items():
        sharpes = [f.sharpe for f in folds if f.sharpe is not None]
        mdds = [f.max_drawdown for f in folds if f.max_drawdown is not None]
        hits = [f.hit_rate for f in folds if f.hit_rate is not None]
        if not sharpes:
            continue
        med_sharpe = _median(sharpes)
        # Storage: max_drawdown is stored as a NEGATIVE fraction
        # (e.g. -0.05 = 5% drawdown). "Worst case" for ranking is the
        # smallest (most negative) — that's the one Gate B compares
        # against its 10% ceiling on abs value.
        worst_mdd = min(mdds) if mdds else 0.0
        med_hit = _median(hits) if hits else 0.0
        total_trades = sum((f.n_trades or 0) for f in folds)

        # Per-gate deficits, normalized by the threshold so gates are
        # commensurable. Zero when the gate is already cleared.
        d_sharpe = max(0.0, (_GATE_B_SHARPE_MIN - med_sharpe) / _GATE_B_SHARPE_MIN)
        d_mdd = max(
            0.0,
            (abs(worst_mdd) - _GATE_B_MAX_DRAWDOWN_MAX) / _GATE_B_MAX_DRAWDOWN_MAX,
        )
        d_hit = max(0.0, (_GATE_B_HIT_RATE_MIN - med_hit) / _GATE_B_HIT_RATE_MIN)
        d_trades = max(0.0, (_GATE_B_TRADES_MIN - total_trades) / _GATE_B_TRADES_MIN)
        distance = d_sharpe + d_mdd + d_hit + d_trades
        gates_cleared = (
            (0 if d_sharpe > 0 else 1)
            + (0 if d_mdd > 0 else 1)
            + (0 if d_hit > 0 else 1)
            + (0 if d_trades > 0 else 1)
        )

        genome = genome_lookup[gid]
        failure_summary = (
            " · ".join(str(f) for f in (genome.validation_failures or [])[:3])
            or "Failed Gate B"
        )
        scored.append(
            ClosestGenome(
                genome_id=gid,
                evolution_run_id=gen_to_run.get(genome.generation_id or ""),
                oos_sharpe=med_sharpe,
                oos_max_drawdown=worst_mdd,
                oos_hit_rate=med_hit,
                oos_n_trades=total_trades,
                gates_cleared=gates_cleared,
                distance_score=distance,
                failure_summary=failure_summary,
            )
        )

    # Ascending by distance, tie-break by more-gates-cleared desc,
    # tie-break by genome_id for determinism.
    scored.sort(key=lambda c: (c.distance_score, -c.gates_cleared, c.genome_id))
    return scored[:limit]


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

    # Wilson UAT 2026-07-29 Fix A: stash the genome's OOS fold length
    # on the run so the paper runner can anchor cumulative-indicator
    # windows (OBV, ad_line) to the same span validation used. Without
    # this, live OBV cumulates over an arbitrary rolling window and
    # produces signals on completely different bars than backtest.
    # Median of OOS folds' durations is used to be robust against
    # oddly-sized folds (walk-forward can end early on data
    # availability); fall back to 30d if the genome has no OOS folds.
    oos_folds = [
        f for f in (genome.validation_folds or []) if getattr(f, "is_oos", False)
    ]
    if oos_folds:
        fold_days_list = [
            (f.fold_end - f.fold_start).total_seconds() / 86400.0 for f in oos_folds
        ]
        fold_days_list.sort()
        n = len(fold_days_list)
        median = (
            fold_days_list[n // 2]
            if n % 2 == 1
            else ((fold_days_list[n // 2 - 1] + fold_days_list[n // 2]) / 2.0)
        )
        fold_length_days = max(1, int(round(median)))
    else:
        fold_length_days = 30

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
            # Wilson UAT 2026-07-29 Fix A: fold-anchored candle fetch.
            # See `paper_runner._fold_anchor_from_time` for the semantic.
            "fold_length_days": fold_length_days,
            "current_fold_index": 0,
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

    # Audit trail (US-018) — a real user did this. `paper_run_id`
    # lives in `outputs_json` so `GET /paper-runs/{id}/decisions`
    # (US-025) surfaces this row — the endpoint filters on the
    # outputs_json key. `action` uses the enum's uppercase NAME to
    # match the runner-emitted decisions' casing (v0.6.0 originally
    # used `.value` here, but that ships lowercase strings and made
    # every router-side user action invisible to the endpoint).
    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=_link_to_evolution_run_id(genome),
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_APPROVE_GENOME.name,
            user_id=current_user.id,
            reasoning_text=(
                f"{current_user.email} approved genome {genome.id} for paper "
                f"trading on {body.instrument} {body.granularity}."
            ),
            outputs_json={
                "paper_run_id": run.id,
                "strategy_genome_id": genome.id,
                "instrument": body.instrument,
                "granularity": body.granularity,
                "oanda_account_id": body.oanda_account_id,
                "risk_config": body.risk_config.model_dump(),
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
            action=models.UserAction.USER_REJECT_GENOME.name,
            user_id=current_user.id,
            reasoning_text=(
                f"{current_user.email} rejected genome {genome.id} from the "
                f"approval queue. Reason: {reason}"
            ),
            outputs_json={
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
            action=models.UserAction.USER_KILL_PAPER.name,
            user_id=current_user.id,
            reasoning_text=(
                f"{current_user.email} requested kill on paper run {run.id}. "
                "The runner halts on next loop tick (<5s SLA)."
            ),
            outputs_json={"paper_run_id": run.id},
        )
    )
    db.commit()
    db.refresh(run)
    return run


_RESUMABLE_STATUSES = {
    models.RunStatus.HALTED_MANUAL,
    models.RunStatus.ERROR,
}


def resume_paper_run(
    db: Session,
    run: models.Run,
    *,
    current_user: models.User,
) -> models.Run:
    """Reverse a halted or crashed run so the runner picks it up again.

    Clears `kill_requested_at`, `stop_reason`, `completed_at`, and any
    prior `crash_detail`; transitions status to APPROVED so
    `schedule_paper_run` treats it the same way as a fresh approval.
    `snapshot_balance` is preserved intentionally — the P&L %
    denominator stays anchored to the original run start so the metric
    is coherent across the halt boundary. If the user wanted a fresh
    snapshot they'd redeploy from the queue, not resume.

    Accepts both HALTED_MANUAL (user pressed kill) and ERROR (crash /
    hibernation / broker circuit). Wilson's UAT feedback 2026-07-13
    surfaced that laptop hibernation ERRORs are often transient and
    should be resumable without re-approving from scratch. If the
    underlying cause is non-recoverable (credentials revoked, DSL
    invalid), the next run will crash the same way and the user can
    read the fresh `crash_detail` to make a call.

    Refuses (raises ValueError) if the run isn't in a resumable
    status — the router translates that into 409.
    """
    if run.status not in _RESUMABLE_STATUSES:
        raise ValueError(
            f"Run is {run.status.value if hasattr(run.status, 'value') else run.status}; "
            "only HALTED_MANUAL or ERROR runs can be resumed"
        )

    was_error = run.status == models.RunStatus.ERROR
    run.status = models.RunStatus.APPROVED
    run.kill_requested_at = None
    run.stop_reason = None
    run.completed_at = None
    run.process_pid = None
    # Fold the params-dict mutations into one block so ERROR-only
    # crash_detail history rotation and always-on notification-dedup
    # reset share the same fresh-dict + flag_modified path.
    #
    # v0.6.2 (Wilson 2026-07-24): `parameters['notified']` was
    # written by the Slack notifier at halt time to dedup a single
    # halt event across re-entrant call sites. If we left it in
    # place across a resume, the next occurrence of the SAME reason
    # (e.g. a BROKER_CIRCUIT a week later after transient OANDA
    # instability) would be silently swallowed. Clearing on resume
    # makes each halt-lifecycle its own notification budget: one
    # ping per reason per halt, with resume opening a fresh window.
    if run.parameters:
        from sqlalchemy.orm.attributes import flag_modified

        params = dict(run.parameters)
        changed = params.pop("notified", None) is not None
        if was_error:
            prior_detail = params.pop("crash_detail", None)
            if prior_detail is not None:
                history = list(params.get("crash_history") or [])
                history.append(prior_detail)
                params["crash_history"] = history[-5:]  # keep last 5
                changed = True
        if changed:
            run.parameters = params
            flag_modified(run, "parameters")

    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=None,
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_RESUME_PAPER.name,
            user_id=current_user.id,
            reasoning_text=(
                f"{current_user.email} resumed "
                f"{'crashed' if was_error else 'halted'} paper run {run.id}. "
                "Snapshot balance preserved so P&L % stays anchored to the "
                "original run start."
                + (" Prior crash_detail moved to crash_history." if was_error else "")
            ),
            outputs_json={
                "paper_run_id": run.id,
                "resumed_from": ("ERROR" if was_error else "HALTED_MANUAL"),
            },
        )
    )
    db.commit()
    db.refresh(run)
    return run


def delete_paper_run(db: Session, run: models.Run) -> None:
    """Delete a paper run row + its orphaned decision logs.

    Router-level guard refuses on non-terminal states so this
    function trusts its input.

    Trades, orders, and paper_equity_snapshots cascade via the ORM /
    FK cascade on `runs.id`. Decision logs don't — the runner writes
    them with `run_id=None` and stashes the paper run id inside
    `outputs_json.paper_run_id` (see `paper_runner._write_decision`).
    Sweep those explicitly so redeploying doesn't leave a growing
    tail of unreferenced audit rows per delete cycle.
    """
    db.query(models.DecisionLog).filter(
        # Postgres JSON ->> operator extracts the value as text; works
        # for both JSON and JSONB columns. `outputs_json.astext` isn't
        # available on the generic JSON type used by the column.
        models.DecisionLog.outputs_json.op("->>")("paper_run_id") == run.id
    ).delete(synchronize_session=False)
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


# ─────────────────────────────────────────────────────────────────────
# US-025 — read endpoints (trades / decisions / equity)
# ─────────────────────────────────────────────────────────────────────


# DecisionLog action types the runner + user actions emit for a paper
# run. Any row outside this set (agent-authored evolution decisions,
# unrelated user actions) is filtered out so the /paper/:id log stream
# stays focused on the run's own timeline.
_PAPER_DECISION_ACTIONS: tuple[str, ...] = (
    "USER_ORDER_PLACED",
    "USER_ORDER_REJECTED",
    "USER_POSITION_CLOSED",
    "USER_CLOSE_REJECTED",
    "USER_SIGNAL_SKIPPED_STALE",
    "USER_ENTRY_MISSED",
    "USER_DEPLOY_PAPER",
    "USER_APPROVE_GENOME",
    "USER_REJECT_GENOME",
    "USER_KILL_PAPER",
    "USER_RESUME_PAPER",
    "USER_VALIDATOR_OVERRIDE",
)


def list_trades_for_run(
    db: Session,
    run_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[models.Trade], int]:
    """Trades belonging to `run_id`, newest-first.

    Ordering: `COALESCE(exit_timestamp, entry_timestamp) DESC` so open
    trades (no exit) sort ahead of the closed history.
    """
    from sqlalchemy import desc, func as sa_func

    q = db.query(models.Trade).filter(models.Trade.run_id == run_id)
    total = q.count()
    items = (
        q.order_by(
            desc(
                sa_func.coalesce(
                    models.Trade.exit_timestamp, models.Trade.entry_timestamp
                )
            )
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def list_decisions_for_run(
    db: Session,
    run_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    since: datetime | None = None,
) -> tuple[list[models.DecisionLog], int]:
    """Paper-runner-relevant DecisionLog rows for `run_id`.

    The runner writes rows with `run_id=None` (evolution-runs FK doesn't
    fit paper runs) and stashes `paper_run_id` inside `outputs_json`.
    Router-side user actions (kill / resume / deploy / reject) write
    rows with `run_id` pointing at the associated *evolution* run —
    which isn't what we want here — but ALSO include `paper_run_id`
    in `outputs_json`. So we filter by JSON-key membership across both
    row shapes, then narrow to the paper-specific action set.

    Ordered newest-first. `since` filters to rows strictly after the
    timestamp so the frontend can incrementally poll for new entries.
    """
    from sqlalchemy import cast, desc, String as SAString

    # Postgres has JSONB `->` / `->>` operators — the column is JSON
    # (not JSONB) so we cast to text and compare. Slower than a native
    # JSONB index but volume here is small (order of hundreds per run).
    paper_id_matches = (
        cast(models.DecisionLog.outputs_json["paper_run_id"], SAString) == f'"{run_id}"'
    )

    q = db.query(models.DecisionLog).filter(
        paper_id_matches,
        models.DecisionLog.action.in_(_PAPER_DECISION_ACTIONS),
    )
    if since is not None:
        q = q.filter(models.DecisionLog.created_at > since)
    total = q.count()
    items = (
        q.order_by(desc(models.DecisionLog.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def list_equity_for_run(
    db: Session,
    run_id: str,
    *,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: int = 20000,
) -> list[models.PaperEquitySnapshot]:
    """Persisted equity snapshots for `run_id`, chart-friendly (oldest
    first). `from_ts` / `to_ts` narrow to a visible window on the chart.

    Wilson UAT 2026-07-26: fixed a silent truncation bug. The old code
    used `ORDER BY timestamp ASC LIMIT 5000` which, for any run with
    >5000 samples, would silently return the OLDEST 5000 samples and
    drop everything after that. Wilson's 9-day EUR_USD run had 6860
    samples; the chart never saw anything past Jul 23 even though the
    run was still writing samples on Jul 24. All downstream
    calculations (financing detection, balance sum reconciliation,
    tooltip totals) were off by whatever happened in the truncated
    tail.

    Fix: query `ORDER BY timestamp DESC LIMIT N` to grab the NEWEST N
    samples, then reverse to hand the chart the oldest-first ordering
    it expects. Raised the default limit to 20000 (~14 days of M1
    signal-tick cadence) so a longer paper run still fits without
    truncation. Callers passing an explicit `from_ts`/`to_ts` window
    still get an ordered slice within that window.
    """
    q = db.query(models.PaperEquitySnapshot).filter(
        models.PaperEquitySnapshot.run_id == run_id
    )
    if from_ts is not None:
        q = q.filter(models.PaperEquitySnapshot.timestamp >= from_ts)
    if to_ts is not None:
        q = q.filter(models.PaperEquitySnapshot.timestamp <= to_ts)
    rows = q.order_by(models.PaperEquitySnapshot.timestamp.desc()).limit(limit).all()
    rows.reverse()
    return rows


# ─────────────────────────────────────────────────────────────────────
# US-026 — backtest OOS overlay for LivePnLChart
# ─────────────────────────────────────────────────────────────────────


def get_backtest_overlay(
    db: Session,
    run: models.Run,
) -> dict:
    """Resolve the paper run's underlying evolution → OOS equity series.

    Returns a dict shaped like the `BacktestOverlayResponse` schema
    (`items` list + `source` + `reason`). Falls through to
    `unavailable` when the genome has no parent evolution or the OOS
    equity series was never persisted (older evolutions predate the
    v0.4.0 fold-consistency work that started storing it).

    The overlay curve x-axis is `timestamp_relative_seconds` — seconds
    from the OOS window's first bar — so the frontend can render it
    against `LivePnLChart`'s hours-since-run-start axis directly
    without knowing anything about the backtest's calendar.
    """
    if run.strategy_genome_id is None:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "no_associated_evolution_run",
        }

    genome = (
        db.query(models.StrategyGenome)
        .filter(models.StrategyGenome.id == run.strategy_genome_id)
        .first()
    )
    if genome is None:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "no_associated_evolution_run",
        }

    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .first()
    )
    if generation is None:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "no_associated_evolution_run",
        }

    # Pick the champion OOS fold — walk-forward OOS metrics are per-fold
    # rows, not a single curve. v0.4.0's persistence stored the fold's
    # equity series as a JSON list; older rows have None.
    oos_folds = (
        db.query(models.ValidationFold)
        .filter(
            models.ValidationFold.genome_id == genome.id,
            models.ValidationFold.is_oos.is_(True),
        )
        .order_by(models.ValidationFold.fold_index)
        .all()
    )
    if not oos_folds:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "no_oos_equity_persisted",
        }

    # Look for a fold that has a persisted equity series. The v0.4.0
    # plan document proposed an `oos_equity_series` JSON blob on
    # ValidationFold to store the walk-forward OOS equity curve for
    # each fold, but that column was never actually landed — no
    # migration adds it. Every genome (fresh or historical) hits this
    # branch. The reason label reflects the ACTUAL situation ("column
    # not in schema") rather than the misleading v0.4.0-predates
    # framing Wilson's UAT walkthrough (2026-07-13) surfaced.
    series_attr = None
    for candidate in ("oos_equity_series", "equity_series"):
        if any(getattr(f, candidate, None) for f in oos_folds):
            series_attr = candidate
            break
    if series_attr is None:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "oos_equity_series_not_persisted",
        }

    # Concatenate the persisted per-fold series into one relative-time
    # curve so LivePnLChart renders a single continuous overlay.
    items: list[dict] = []
    offset_seconds = 0.0
    for fold in oos_folds:
        raw = getattr(fold, series_attr, None) or []
        for entry in raw:
            ts_seconds = float(entry.get("t", 0.0))
            balance = float(entry.get("balance", 0.0))
            items.append(
                {
                    "timestamp_relative_seconds": offset_seconds + ts_seconds,
                    "balance": balance,
                }
            )
        # Advance the offset by the fold's duration so the next fold
        # continues along the same x-axis rather than restarting at 0.
        if raw:
            offset_seconds += float(raw[-1].get("t", 0.0))

    return {
        "items": items,
        "source": "walk_forward_oos",
        "reason": None,
    }


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


# ─────────────────────────────────────────────────────────────────────
# LLM-suggested deployment name (v0.6.2)
# ─────────────────────────────────────────────────────────────────────

_SUGGEST_NAME_MODEL = "claude-haiku-4-5"
_SUGGEST_NAME_MAX_TOKENS = 48
_SUGGEST_NAME_MAX_LEN = 60

_SUGGEST_NAME_PROMPT_TEMPLATE = """You name forex paper-trading strategies for a solo trader's dashboard.

Given this Strategy DSL (indicators, entry, exit), propose ONE short memorable name that reflects the strategy's character (2-4 words, Title Case, no quotes, no punctuation, no instrument names).

Examples of the vibe (do NOT reuse verbatim):
- MACD Momentum Rider
- RSI Reversal Scalper
- OBV Trend Follower
- SMA Breakout Chaser

DSL:
{dsl_json}

Reply with ONLY the name. Nothing else."""


def _sanitize_suggested_name(raw: str) -> str:
    """Trim quotes, punctuation, whitespace, and cap length. LLM
    occasionally wraps the response in quotes or adds a trailing period
    despite the instruction; strip both without complaint."""
    # Take only the first non-empty line — belt against the LLM adding
    # an explanation on the next line.
    first_line = next((line for line in raw.splitlines() if line.strip()), "")
    stripped = first_line.strip().strip('"').strip("'").strip("`").strip()
    # Drop trailing punctuation.
    stripped = re.sub(r"[.!?,;:]+$", "", stripped).strip()
    return stripped[:_SUGGEST_NAME_MAX_LEN]


def suggest_deployment_name(
    db: Session,
    strategy_genome_id: str,
    current_user: models.User,
) -> str:
    """LLM-suggested short name summarising the genome's DSL character.

    Purely a suggestion — the caller still submits `POST /paper-runs/approve`
    with whatever name they want. On the way in the caller must own the
    genome (same authorization as approve). On the way out, an empty
    string is treated as failure and surfaced to the router as a 502.
    """
    genome = _load_owned_validated_genome(db, strategy_genome_id, current_user.id)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured on this environment")

    # Lazy import so environments without the anthropic dep (unit tests
    # that don't hit this path) don't pay the import cost.
    import anthropic

    dsl_snippet = json.dumps(genome.dsl_json, sort_keys=True)[:4000]
    prompt = _SUGGEST_NAME_PROMPT_TEMPLATE.format(dsl_json=dsl_snippet)

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=_SUGGEST_NAME_MODEL,
        max_tokens=_SUGGEST_NAME_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = ""
    for block in resp.content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            raw = text
            break
    name = _sanitize_suggested_name(raw)
    if not name:
        raise RuntimeError("LLM returned an empty name suggestion")
    logger.info(
        "suggest_deployment_name genome=%s user=%s → %r",
        strategy_genome_id,
        current_user.id,
        name,
    )
    return name


__all__ = [
    "approve_and_deploy",
    "delete_paper_run",
    "find_orphaned_running_paper_runs",
    "get_paper_run",
    "list_approval_queue_for_user",
    "list_paper_runs",
    "reject_genome",
    "request_kill",
    "resume_paper_run",
    "suggest_deployment_name",
]
