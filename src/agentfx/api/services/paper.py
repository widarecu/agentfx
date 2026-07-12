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
        closest_to_passing=(
            _closest_to_passing(db, failed) if failed and not deployable else None
        ),
    )
    return ApprovalQueueResult(items=deployable, meta=meta)


def _closest_to_passing(
    db: Session,
    failed_genomes: list[models.StrategyGenome],
) -> ClosestGenome | None:
    """Pick the failed genome with the highest median OOS Sharpe.

    Tiebreaker is fewest failing gates. Returns None when there are no
    OOS folds to compare against (would happen only on legacy data).
    """
    if not failed_genomes:
        return None

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
        return None

    # Group by genome, compute median Sharpe / drawdown / n_trades per
    # genome across its OOS folds.
    by_genome: dict[str, list[models.ValidationFold]] = {}
    for row in oos_rows:
        by_genome.setdefault(row.genome_id, []).append(row)

    def _median(values: list[float]) -> float:
        s = sorted(values)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    genome_lookup = {g.id: g for g in failed_genomes}
    best_id: str | None = None
    best_sharpe: float = float("-inf")
    best_fewest_fails: int = 1_000
    for gid, folds in by_genome.items():
        sharpes = [f.sharpe for f in folds if f.sharpe is not None]
        if not sharpes:
            continue
        med_sharpe = _median(sharpes)
        fails = len(genome_lookup[gid].validation_failures or [])
        if med_sharpe > best_sharpe or (
            med_sharpe == best_sharpe and fails < best_fewest_fails
        ):
            best_sharpe = med_sharpe
            best_fewest_fails = fails
            best_id = gid

    if best_id is None:
        return None

    folds = by_genome[best_id]
    genome = genome_lookup[best_id]
    med_mdd = _median([f.max_drawdown for f in folds if f.max_drawdown is not None])
    total_trades = sum((f.n_trades or 0) for f in folds)
    failure_summary = (
        " · ".join(str(f) for f in (genome.validation_failures or [])[:3])
        or "Failed Gate B"
    )
    # `evolution_run_id` lets the frontend build a valid /evolution/:runId/
    # genomes/:genomeId link — the only route pattern GenomeDetail
    # matches. Without this the "View genome detail →" link on the
    # closest-to-passing card 404s (v0.6.1 UAT bug caught 2026-07-12).
    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .first()
    )
    evolution_run_id = generation.run_id if generation is not None else None
    return ClosestGenome(
        genome_id=best_id,
        evolution_run_id=evolution_run_id,
        oos_sharpe=best_sharpe,
        oos_max_drawdown=med_mdd,
        oos_n_trades=total_trades,
        failure_summary=failure_summary,
    )


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


def resume_paper_run(
    db: Session,
    run: models.Run,
    *,
    current_user: models.User,
) -> models.Run:
    """Reverse a HALTED_MANUAL run so the runner picks it up again.

    Clears `kill_requested_at`, `stop_reason`, and `completed_at`;
    transitions status to APPROVED so `schedule_paper_run` treats it
    the same way as a fresh approval. `snapshot_balance` is
    preserved intentionally — the P&L % denominator stays anchored
    to the original run start so the metric is coherent across the
    halt boundary. If the user wanted a fresh snapshot they'd
    redeploy from the queue, not resume.

    Refuses (raises ValueError) if the run isn't in HALTED_MANUAL —
    the router translates that into 409. Resuming an ERRORed run is
    a separate feature (v0.6.1): those halts encode real problems
    (broker circuit, decrypt failure) that resume alone can't fix.
    """
    if run.status != models.RunStatus.HALTED_MANUAL:
        raise ValueError(
            f"Run is {run.status.value if hasattr(run.status, 'value') else run.status}; "
            "only HALTED_MANUAL runs can be resumed"
        )

    run.status = models.RunStatus.APPROVED
    run.kill_requested_at = None
    run.stop_reason = None
    run.completed_at = None
    run.process_pid = None

    db.add(
        models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=None,
            agent_type=models.AgentType.USER.value,
            action=models.UserAction.USER_RESUME_PAPER.name,
            user_id=current_user.id,
            reasoning_text=(
                f"{current_user.email} resumed halted paper run {run.id}. "
                "Snapshot balance preserved so P&L % stays anchored to the "
                "original run start."
            ),
            outputs_json={"paper_run_id": run.id},
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


# ─────────────────────────────────────────────────────────────────────
# US-025 — read endpoints (trades / decisions / equity)
# ─────────────────────────────────────────────────────────────────────


# DecisionLog action types the runner + user actions emit for a paper
# run. Any row outside this set (agent-authored evolution decisions,
# unrelated user actions) is filtered out so the /paper/:id log stream
# stays focused on the run's own timeline.
_PAPER_DECISION_ACTIONS: tuple[str, ...] = (
    "USER_ORDER_PLACED",
    "USER_POSITION_CLOSED",
    "USER_SIGNAL_SKIPPED_STALE",
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
    limit: int = 5000,
) -> list[models.PaperEquitySnapshot]:
    """Persisted equity snapshots for `run_id`, chart-friendly (oldest
    first). `from_ts` / `to_ts` narrow to a visible window on the chart.
    """
    q = db.query(models.PaperEquitySnapshot).filter(
        models.PaperEquitySnapshot.run_id == run_id
    )
    if from_ts is not None:
        q = q.filter(models.PaperEquitySnapshot.timestamp >= from_ts)
    if to_ts is not None:
        q = q.filter(models.PaperEquitySnapshot.timestamp <= to_ts)
    return q.order_by(models.PaperEquitySnapshot.timestamp).limit(limit).all()


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

    # Look for a fold that has a persisted equity series. The column
    # isn't on the current model; v0.4.0 ADDED a `oos_equity_series`
    # JSON blob but that column may not exist yet on this schema. We
    # try both attribute names and gracefully return unavailable when
    # neither is present.
    series_attr = None
    for candidate in ("oos_equity_series", "equity_series"):
        if any(getattr(f, candidate, None) for f in oos_folds):
            series_attr = candidate
            break
    if series_attr is None:
        return {
            "items": [],
            "source": "unavailable",
            "reason": "genome_predates_v0_4_0",
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
]
