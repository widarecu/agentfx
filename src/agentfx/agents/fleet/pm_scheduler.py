"""PM evolution scheduling tool (v0.8.1 Phase C.1).

Gives the Portfolio Manager Agent its FIRST write authority: the
ability to start evolution runs autonomously, with informed decisions
on when / which pair / which parameters.

Design intent:
- PM is read-only EVERYWHERE ELSE. Scheduling an evolution risks
  only LLM $ budget (the backtest + validation subprocess cost), not
  capital. OANDA practice accounts are unaffected until a resulting
  genome gets approved to paper — that's v0.8.2 PM authority.
- Hard rate limit: `_MAX_EVOLUTIONS_PER_DAY` runs/day across the
  entire fleet. Prevents runaway LLM spend if PM gets a bad run of
  rationales.
- Pair/granularity restricted to the pre-approved
  `settings.CANDLE_INSTRUMENTS` + `settings.CANDLE_GRANULARITIES`.
  PM can't invent new pairs — those are an operator decision.
- Each scheduled run is recorded as an `AgentDecision` with
  `action="started_evolution_run"` + its full rationale, so the
  Reflexion loop can later grade the decision when the evo run
  completes.

Why in a separate module rather than inside `portfolio_manager.py`?
- `portfolio_manager.py` is already large; keeping the scheduler
  self-contained makes testing + future PE handover easier.
- The same scheduling helpers are reused by any future "research
  agent proposes new sweep" flow (v0.8.2+).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentfx.api.core.config import settings
from agentfx.api.models.agent_decision import AgentDecision, AgentRole
from agentfx.agents.fleet.memory import DecisionRecord, record_decision

logger = logging.getLogger(__name__)


# Hard cap: this many PM-scheduled evolution runs per calendar day
# (UTC). Raised from 2/week on 2026-10-11 per Wilson — he wants PM
# to have real scheduling agency, not ultra-conservative throttling.
#
# IMPORTANT: this cap only limits how many evolutions PM may KICK
# OFF per day. Each evolution has its OWN internal LLM budget
# (set via `llm_budget_usd` at creation), which is counted against
# per-run budgets, NOT the fleet daily cap. 10 evolutions/day could
# realistically spend $50-200/day in evolution LLM subprocess costs
# if PM runs them to depth. Monitor via Railway cost dashboards.
_MAX_EVOLUTIONS_PER_DAY = 10


# ─────────────────────────────────────────────────────────────────────
# Structured output schema (PM proposes these; scheduler executes)
# ─────────────────────────────────────────────────────────────────────


class PMEvolutionAction(BaseModel):
    """One evolution run PM wants to start.

    Fields are deliberately bounded so Haiku can't produce something
    the scheduler would then reject at validation time (which would
    be a wasted LLM call). Rationale + expected impact are mandatory
    — a PM that can't say *why* it wants to run shouldn't run.
    """

    pair: str = Field(
        default="",
        description=(
            "Instrument symbol, e.g. 'EUR_USD'. Must be in the "
            "deployment's CANDLE_INSTRUMENTS list; otherwise the "
            "scheduler drops the action."
        ),
    )
    granularity: str = Field(
        default="M1",
        description=(
            "OANDA granularity: M1 / M5 / M10 / M30 / H1 / H4 / H12 / D. "
            "Must be in CANDLE_GRANULARITIES."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "WHY this run now, in plain prose. Must tie back to a "
            "specific state-snapshot fact (stale champion, prior "
            "outcome, regime shift) — not 'try something new'."
        ),
    )
    expected_profit_impact: str = Field(
        default="",
        description=(
            "What outcome would make this run worthwhile. Specific "
            "hypothesis — 'produce a genome with OOS Sharpe > current "
            "best (1.09) within 48h' is good; 'maybe find alpha' is "
            "not."
        ),
    )
    priority: str = Field(
        default="normal",
        description=(
            "'now' (fire this scan), 'this_week' (queue for later), "
            "'watch' (don't fire yet, just note). Only 'now' items "
            "trigger the scheduler. Propose as many 'now' actions as "
            "`evolution_scheduling_context.evolutions_remaining_today` "
            "allows when the state warrants it — the scheduler fires "
            "all of them subject to the daily cap."
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Rate limit + eligibility checks
# ─────────────────────────────────────────────────────────────────────


def _evolutions_started_today(db: Session) -> int:
    """Count PM-initiated evolution runs in the current UTC calendar
    day. Window is the current UTC date — a run started at 23:59 UTC
    doesn't count against tomorrow's budget.

    Reads the decision log — the DB is the authority on rate limits,
    not any in-memory counter (which would reset across restarts).
    """
    now = datetime.now(UTC)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    return (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PM.value)
        .filter(AgentDecision.action == "started_evolution_run")
        .filter(AgentDecision.created_at >= today_start)
        .count()
    )


def evolutions_remaining_today(db: Session) -> int:
    """Public helper for the PM snapshot — tells Haiku how many more
    evolutions it can propose before today's cap kicks in."""
    used = _evolutions_started_today(db)
    return max(0, _MAX_EVOLUTIONS_PER_DAY - used)


def _is_allowed_pair(pair: str) -> bool:
    allowed = {s.strip() for s in settings.CANDLE_INSTRUMENTS.split(",") if s.strip()}
    return pair in allowed


def _is_allowed_granularity(gran: str) -> bool:
    allowed = {s.strip() for s in settings.CANDLE_GRANULARITIES.split(",") if s.strip()}
    return gran in allowed


def _resolve_owner_user_id(db: Session) -> int | None:
    """Look up `settings.AGENT_FLEET_OWNER_EMAIL` → User.id.

    Returns None when:
    - the setting is empty (fleet-scoped mode — PM runs land with
      `created_by=NULL`), or
    - the email doesn't match any active user (misconfiguration).

    The not-found path logs as WARNING so Wilson sees the
    misconfiguration surface in the next QA tech-health scan.
    """
    email = (settings.AGENT_FLEET_OWNER_EMAIL or "").strip().lower()
    if not email:
        return None
    try:
        from agentfx.api.models.user import User

        row = (
            db.query(User)
            .filter(User.email == email)
            .filter(User.is_active.is_(True))
            .first()
        )
        if row is None:
            logger.warning(
                "pm_scheduler: AGENT_FLEET_OWNER_EMAIL=%s does not match any "
                "active user — PM-authored runs will land with created_by=NULL "
                "and stay invisible in the operator's scoped UI",
                email,
            )
            return None
        return int(row.id)
    except Exception:
        logger.exception(
            "pm_scheduler: owner-user resolution failed for email=%s", email
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Scheduler (the write action)
# ─────────────────────────────────────────────────────────────────────


class ScheduledEvolutionResult(BaseModel):
    """What `schedule_evolution_from_pm_action` returns per action."""

    accepted: bool
    evo_run_id: str | None = None
    reason: str = ""  # Why rejected, if applicable


def schedule_evolution_from_pm_action(
    *,
    db: Session,
    action: PMEvolutionAction,
    pm_decision_key: str,
) -> ScheduledEvolutionResult:
    """Validate + schedule one PM-proposed evolution run.

    Returns a ScheduledEvolutionResult indicating whether the run
    was accepted (and its new ID) or rejected (with reason).

    Rejection reasons (never raises):
    - "wrong_priority": priority != 'now'
    - "no_rationale" / "no_expected_impact": required text missing
    - "pair_not_allowed" / "granularity_not_allowed": not in config
    - "rate_limited": would exceed _MAX_EVOLUTIONS_PER_DAY
    - "scheduler_error": uncaught exception (logged)

    On success, writes an `AgentDecision` row with
    action="started_evolution_run" and `outputs_json.evo_run_id` so
    the Reflexion loop can later grade the outcome when the evo
    run completes.
    """
    try:
        # Priority gate — PM may propose 'watch' / 'this_week' items
        # for the user's eye without actually triggering them.
        if action.priority != "now":
            return ScheduledEvolutionResult(
                accepted=False, reason=f"wrong_priority:{action.priority}"
            )

        # Rationale + expected-impact are mandatory for an action to
        # be considered. A PM that can't say WHY shouldn't act.
        if not (action.rationale or "").strip():
            return ScheduledEvolutionResult(accepted=False, reason="no_rationale")
        if not (action.expected_profit_impact or "").strip():
            return ScheduledEvolutionResult(accepted=False, reason="no_expected_impact")

        if not _is_allowed_pair(action.pair):
            return ScheduledEvolutionResult(
                accepted=False, reason=f"pair_not_allowed:{action.pair}"
            )
        if not _is_allowed_granularity(action.granularity):
            return ScheduledEvolutionResult(
                accepted=False,
                reason=f"granularity_not_allowed:{action.granularity}",
            )

        # Rate limit — strictest check last so the easier rejections
        # happen first (cheaper + log more informative).
        if _evolutions_started_today(db) >= _MAX_EVOLUTIONS_PER_DAY:
            return ScheduledEvolutionResult(accepted=False, reason="rate_limited")

        # OK — fire the run via the existing service layer. Lazy
        # imports keep this module importable in test environments
        # that mock out the API layer.
        from agentfx.api import schemas
        from agentfx.api.services import evolution as evolution_service

        create = schemas.EvolutionRunCreate(
            instrument=action.pair,
            granularity=action.granularity,
        )
        # Attribute to the fleet operator so the run shows up in
        # their RBAC-scoped UI listing. Falls back to NULL (fleet-
        # scoped, ADMIN-only visible) if `AGENT_FLEET_OWNER_EMAIL`
        # is unset or doesn't match an active user. Wilson needed
        # this on 2026-10-15 — PM runs were landing with
        # created_by=NULL and he couldn't see them in /evolution-runs.
        owner_user_id = _resolve_owner_user_id(db)
        evo_run = evolution_service.create_run(db, create, created_by=owner_user_id)

        # Schedule the worker subprocess. Must happen OUTSIDE this
        # function's commit boundary — use a background task if the
        # caller is in an HTTP context, or call directly if not.
        from agentfx.api.workers.evolution_runner import schedule_run

        schedule_run(evo_run.id)

        # Record the decision — Reflexion will later re-read this
        # to grade whether PM's rationale panned out.
        record_decision(
            db,
            DecisionRecord(
                agent_role=AgentRole.PM,
                action="started_evolution_run",
                decision_key=f"pm_evo_{evo_run.id}",
                inputs={
                    "pm_brief_key": pm_decision_key,
                    "pair": action.pair,
                    "granularity": action.granularity,
                    "priority": action.priority,
                },
                outputs={
                    "evo_run_id": evo_run.id,
                    "expected_profit_impact": action.expected_profit_impact,
                    "attributed_user_id": owner_user_id,
                },
                reasoning=action.rationale,
            ),
        )
        db.commit()

        logger.info(
            "pm_scheduler: started evolution run %s pair=%s gran=%s via PM brief %s",
            evo_run.id,
            action.pair,
            action.granularity,
            pm_decision_key,
        )

        return ScheduledEvolutionResult(accepted=True, evo_run_id=evo_run.id)

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "pm_scheduler: unexpected failure scheduling evolution "
            "pair=%s gran=%s err=%s",
            action.pair,
            action.granularity,
            exc,
        )
        return ScheduledEvolutionResult(
            accepted=False, reason=f"scheduler_error:{type(exc).__name__}"
        )


# ─────────────────────────────────────────────────────────────────────
# Snapshot context the PM reads BEFORE deciding to schedule
# ─────────────────────────────────────────────────────────────────────


def build_scheduling_context(db: Session) -> dict[str, Any]:
    """Collect the signals PM needs to decide WHEN / WHICH / WHY.

    Returns a dict shaped for the PM prompt:
    - available pairs + granularities
    - per-pair: days since last COMPLETED evolution, current best
      genome's fitness
    - evolutions_remaining_today (rate-limit headroom)
    - prior PM scheduling decisions in the last 30 days + their
      current outcomes (Reflexion signal)
    """
    from agentfx.api.models.evolution import (
        EvolutionRun,
        EvolutionRunStatus,
        Generation,
        StrategyGenome,
    )
    from sqlalchemy import func as sa_func

    now = datetime.now(UTC)
    month_ago = now - timedelta(days=30)

    allowed_pairs = [
        s.strip() for s in settings.CANDLE_INSTRUMENTS.split(",") if s.strip()
    ]
    allowed_grans = [
        s.strip() for s in settings.CANDLE_GRANULARITIES.split(",") if s.strip()
    ]

    # Per-pair latest completed evolution timestamp.
    rows = (
        db.query(
            EvolutionRun.instrument,
            EvolutionRun.granularity,
            sa_func.max(EvolutionRun.completed_at).label("last_completed_at"),
        )
        .filter(EvolutionRun.status == EvolutionRunStatus.COMPLETED)
        .filter(EvolutionRun.completed_at.isnot(None))
        .group_by(EvolutionRun.instrument, EvolutionRun.granularity)
        .all()
    )
    last_completed: dict[tuple[str, str], datetime] = {
        (r.instrument, r.granularity): r.last_completed_at for r in rows
    }

    # Per-pair current best validated genome fitness (last 30 days).
    # StrategyGenome doesn't have instrument/granularity columns —
    # they live on the parent EvolutionRun. Join through Generation.
    champ_rows = (
        db.query(
            EvolutionRun.instrument,
            EvolutionRun.granularity,
            sa_func.max(StrategyGenome.fitness).label("best_fitness"),
        )
        .join(Generation, Generation.id == StrategyGenome.generation_id)
        .join(EvolutionRun, EvolutionRun.id == Generation.run_id)
        .filter(StrategyGenome.validator_passed.is_(True))
        .filter(StrategyGenome.created_at >= month_ago)
        .group_by(EvolutionRun.instrument, EvolutionRun.granularity)
        .all()
    )
    champions: dict[tuple[str, str], float] = {
        (r.instrument, r.granularity): float(r.best_fitness)
        for r in champ_rows
        if r.best_fitness is not None
    }

    per_pair = []
    for pair in allowed_pairs:
        for gran in allowed_grans:
            last_ts = last_completed.get((pair, gran))
            days_since = (now - last_ts).days if last_ts is not None else None
            per_pair.append(
                {
                    "pair": pair,
                    "granularity": gran,
                    "days_since_last_completed_evo": days_since,
                    "current_best_fitness": champions.get((pair, gran)),
                }
            )

    # Prior PM evolution-scheduling decisions + their current grades.
    prior = (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PM.value)
        .filter(AgentDecision.action == "started_evolution_run")
        .filter(AgentDecision.created_at >= month_ago)
        .order_by(AgentDecision.created_at.desc())
        .limit(10)
        .all()
    )
    prior_payload = [
        {
            "when_utc": row.created_at.isoformat() if row.created_at else None,
            "pair": (row.inputs_json or {}).get("pair"),
            "granularity": (row.inputs_json or {}).get("granularity"),
            "evo_run_id": (row.outputs_json or {}).get("evo_run_id"),
            "rationale": row.reasoning_text,
            "outcome_grade": row.outcome_grade,
        }
        for row in prior
    ]

    return {
        "allowed_pairs": allowed_pairs,
        "allowed_granularities": allowed_grans,
        "per_pair_coverage": per_pair,
        "evolutions_remaining_today": evolutions_remaining_today(db),
        "max_per_day": _MAX_EVOLUTIONS_PER_DAY,
        "prior_pm_evolution_decisions": prior_payload,
    }


__all__ = [
    "PMEvolutionAction",
    "ScheduledEvolutionResult",
    "build_scheduling_context",
    "evolutions_remaining_today",
    "schedule_evolution_from_pm_action",
]
