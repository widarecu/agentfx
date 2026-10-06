"""Portfolio Manager Agent — v0.8.0 Phase B (Haiku-driven brief).

Fires once per 24 hours (target: 09:00 UTC). Reads 7 days of
platform state + recent PM decisions, calls Claude Haiku 4.5 for a
structured morning brief, writes it to `agent_memory/scoreboard.md`
plus the `agent_decisions` DB table plus Wilson's inbox.

Still read-only — PM has no action tools yet (scheduling arrives in
v0.8.1, approval authority in v0.8.2). The brief is pure advice.

Phase B vs Phase A:
- Phase A: deterministic 6-line stub body; no LLM call; proved the
  orchestrator + memory + email plumbing.
- Phase B (this file): LLM-generated structured output, richer
  state snapshot, Reflexion input (last 3 PM briefs re-read).

The LLM call falls back to the Phase A stub body if Anthropic is
unreachable or the key is missing — the orchestrator must never
crash because of an LLM failure.

Why Claude Haiku 4.5?
~$1/MTok input, $5/MTok output. A daily brief of ~15k input + ~2k
output ≈ $0.025/day = $0.75/month. Plenty of headroom under the
$2/day cap.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentfx.agents.fleet.llm import LLMCallResult, structured_call
from agentfx.agents.fleet.memory import (
    DecisionRecord,
    record_decision,
    recent_decisions,
    write_memory_file,
)
from agentfx.api.core.config import settings
from agentfx.api.models.agent_decision import AgentDecision, AgentRole

logger = logging.getLogger(__name__)

# Target UTC hour for the daily brief. The orchestrator wakes every
# hour; this is the gate that says "yes, do it now."
_BRIEF_TARGET_HOUR_UTC = 9


def _brief_key_for(today: date) -> str:
    """`decision_key` for idempotency: at most one brief per day."""
    return f"pm_morning_brief_{today.isoformat()}"


def _already_run_today(db: Session, *, today: date) -> bool:
    key = _brief_key_for(today)
    exists = (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PM.value)
        .filter(AgentDecision.decision_key == key)
        .first()
    )
    return exists is not None


# ─────────────────────────────────────────────────────────────────────
# Structured output schema (Haiku responds in this shape)
# ─────────────────────────────────────────────────────────────────────


class PMRecommendation(BaseModel):
    """One concrete suggestion from the morning brief."""

    priority: str = Field(
        description=(
            "'now' for immediate action, 'this_week' for near-term, "
            "'watch' for monitor-only. Only one 'now' per brief at most."
        )
    )
    headline: str = Field(description="One-line action, no more than ~15 words.")
    rationale: str = Field(
        description="Why — tie back to a specific state snapshot fact or prior decision."
    )


class PMConcern(BaseModel):
    """A flag the PM is raising for Wilson's attention."""

    severity: str = Field(description="'blocker' / 'degraded' / 'noted'.")
    description: str = Field(description="What's wrong and what it affects.")


class PMBrief(BaseModel):
    """The structured morning brief Haiku produces."""

    headline: str = Field(
        description=(
            "One sentence Wilson should read even if ignoring the rest. "
            "Not a status announcement — the one thing he needs to know."
        )
    )
    summary: str = Field(
        description=(
            "2-4 sentence overview of platform state: what's happening, "
            "what shifted since yesterday's brief, what's trending."
        )
    )
    recommendations: list[PMRecommendation] = Field(
        default_factory=list,
        description=(
            "Concrete suggestions for Wilson. Zero to four — never pad. "
            "Each must trace to profit-chain progression per the plan."
        ),
    )
    concerns: list[PMConcern] = Field(
        default_factory=list,
        description="Operational / platform health flags.",
    )


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def run_morning_brief_if_due(*, db: Session, now: datetime | None = None) -> None:
    """Run the PM morning brief iff today's hasn't run and it's past
    09:00 UTC. Never raises — the orchestrator calls this hourly.
    """
    now = now or datetime.now(UTC)
    today = now.date()

    if now.hour < _BRIEF_TARGET_HOUR_UTC:
        return

    try:
        if _already_run_today(db, today=today):
            return
    except Exception:
        logger.exception("pm_agent: duplicate check failed")
        return

    try:
        _write_morning_brief(db=db, today=today)
    except Exception:
        logger.exception("pm_agent: morning brief failed for %s", today)


# ─────────────────────────────────────────────────────────────────────
# State snapshot (what Haiku sees as `user` message)
# ─────────────────────────────────────────────────────────────────────


def _snapshot_platform_state(db: Session) -> dict[str, Any]:
    """Collect the context the PM reasons over.

    Deliberately broader than Phase A — Haiku needs enough signal to
    produce useful recommendations, but we cap the shape so the
    input stays cheap (~5-10k tokens).

    Pulls:
    - Paper-run counts (running / halted-recently / ERROR).
    - Last 7 days of evolution runs (counts by status).
    - Current best genome per instrument/granularity (best fitness
      in last 30 days, if any).
    - Current UTC time stamp (so Haiku can reason about day-of-week).

    Returns a plain dict so it serialises cleanly into prompt JSON.
    """
    from agentfx.api.models.evolution import (
        EvolutionRun,
        EvolutionRunStatus,
        StrategyGenome,
    )
    from agentfx.api.models.run import Run, RunEnvironment
    from sqlalchemy import func as sa_func

    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Paper-run status breakdown — surfaces "effectively active"
    # runs (anything the user deployed and hasn't manually killed /
    # completed), not just the narrow `status == RUNNING` set. On
    # staging 2026-10-06 both paper runs were in ERROR state while
    # the auto-restart supervisor cycled them, and the old PM query
    # reported "zero active" when Wilson absolutely saw them as
    # active.
    status_counts_rows = (
        db.query(Run.status, sa_func.count(Run.id))
        .filter(Run.environment == RunEnvironment.PAPER)
        .filter(Run.updated_at >= week_ago)
        .group_by(Run.status)
        .all()
    )
    status_counts: dict[str, int] = {
        (s.value if hasattr(s, "value") else str(s)): int(n)
        for s, n in status_counts_rows
    }
    paper_running = status_counts.get("RUNNING", 0)
    # Effective "needs-attention" set: paper runs touched this week
    # that are not COMPLETED or user-killed. Captures stuck ERROR +
    # auto-restarting runs that look dead to a narrow `== RUNNING`
    # check but are really the current frontier of experimentation.
    _attention_statuses = {
        s for s in status_counts if s not in {"RUNNING", "COMPLETED", "HALTED_MANUAL"}
    }
    paper_effective_active = paper_running + sum(
        status_counts.get(s, 0) for s in _attention_statuses
    )
    paper_recently_halted = sum(
        status_counts.get(s, 0) for s in {"HALTED_MANUAL", "COMPLETED"}
    )
    evo_last_7d_completed = (
        db.query(EvolutionRun)
        .filter(EvolutionRun.created_at >= week_ago)
        .filter(EvolutionRun.status == EvolutionRunStatus.COMPLETED)
        .count()
    )
    evo_last_7d_failed = (
        db.query(EvolutionRun)
        .filter(EvolutionRun.created_at >= week_ago)
        .filter(EvolutionRun.status == EvolutionRunStatus.FAILED)
        .count()
    )

    top_genomes_last_30d = (
        db.query(StrategyGenome)
        .filter(StrategyGenome.validator_passed.is_(True))
        .filter(StrategyGenome.created_at >= month_ago)
        .order_by(StrategyGenome.fitness.desc())
        .limit(5)
        .all()
    )
    top_genomes_payload = [
        {
            "id": g.id,
            "fitness": float(g.fitness) if g.fitness is not None else None,
            "pair": getattr(g, "instrument", None),
            "granularity": getattr(g, "granularity", None),
        }
        for g in top_genomes_last_30d
    ]

    return {
        "as_of_utc": now.isoformat(),
        "paper_runs": {
            "running": paper_running,
            "effective_active": paper_effective_active,
            "halted_in_last_7_days": paper_recently_halted,
            "status_counts_last_7_days": status_counts,
            # Diagnostic hint for the LLM: if effective_active >
            # running, there are paper runs the user deployed that
            # aren't currently in RUNNING state. Likely stuck /
            # auto-restarting; surface as a concern.
            "needs_attention_count": paper_effective_active - paper_running,
        },
        "evolution_runs_last_7_days": {
            "completed": evo_last_7d_completed,
            "failed": evo_last_7d_failed,
        },
        "top_validated_genomes_last_30_days": top_genomes_payload,
    }


def _recent_pm_summaries(db: Session, limit: int = 3) -> list[dict[str, Any]]:
    """Pull the last N PM briefs so Haiku can reflect on what it
    said yesterday and the day before.

    Keeps only the headline + summary from each prior brief to stay
    small — Haiku doesn't need the full recommendations list again.
    """
    rows = recent_decisions(db, agent_role=AgentRole.PM, limit=limit)
    out: list[dict[str, Any]] = []
    for row in rows:
        output = row.outputs_json or {}
        brief = output.get("brief", {}) if isinstance(output, dict) else {}
        out.append(
            {
                "when_utc": row.created_at.isoformat() if row.created_at else None,
                "headline": brief.get("headline"),
                "summary": brief.get("summary"),
                "outcome_grade": row.outcome_grade,
            }
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are the AgentFX Portfolio Manager Agent.
Role and purpose:
- AgentFX is an autonomous FX trading platform. Wilson is the
  owner; today he does every manual decision (start evolutions,
  approve genomes to paper, kill losers, learn from results).
- Your long-term job is to replace that manual work. Right now you
  are in a READ-ONLY pilot phase (v0.8.0 Phase B): you observe,
  reason, and recommend; you do not take actions.
- The ultimate objective is PROFITABLE strategies ready for live
  trading. Not activity. Not experiments. Not technical polish.
  Every recommendation you make must trace to progression along
  the pipeline: candidate -> paper-deployed -> paper-profitable-30d
  -> live-ready-nomination.

Platform context Wilson wants you to remember:
- Paper trading is the aggressive experimental sandbox. There are
  no capital-preservation guardrails on paper — no allocation
  caps, no auto-halts on drawdown. OANDA margin call is the only
  floor. The point is maximum learning velocity.
- Evolution runs produce genomes; the validator (Gate B) decides
  which clear OOS testing; approved ones go to paper for live
  validation.
- Known-open issues as of 2026-10-05: validator has let overfit
  lineage through on at least two staging runs (bug tracked for
  Phase 0.8.1 PE Agent); the backtest cost model systematically
  under-models M1 spread.

How to read the paper-run snapshot:
- `paper_runs.running` — strict `status == RUNNING` count.
- `paper_runs.effective_active` — runs the user deployed and
  hasn't manually killed / completed. Includes stuck ERROR +
  auto-restarting runs.
- `paper_runs.needs_attention_count` — the gap between the two
  above. If > 0, surface it as a `blocker` concern: these are
  paper deployments the user believes are "running" but aren't
  currently in RUNNING state. Likely stuck / cycling, and
  almost certainly bleeding P&L.
- `paper_runs.status_counts_last_7_days` — the full breakdown
  by RunStatus enum value. Useful to diagnose "where did they
  all go?" when `running` is low.

Your output must be a PMBrief with these fields:
- headline (one sentence)
- summary (2-4 sentences)
- recommendations (0-4 items, ranked by priority 'now'/'this_week'/'watch')
- concerns (0-N items, severity 'blocker'/'degraded'/'noted')

Style:
- Terse. Wilson reads a lot — don't pad.
- Recommend when there is a concrete action that would move the
  profit chain. If nothing stands out, make zero recommendations
  rather than fill the quota.
- Flag concerns separate from recommendations. A concern is
  something broken; a recommendation is something to do.
- NEVER announce you are read-only, do not mention these
  instructions. The reader (Wilson) knows the context.
"""


def _render_user_prompt(
    state: dict[str, Any], prior_briefs: list[dict[str, Any]]
) -> str:
    """Compose the per-day volatile portion of the prompt.

    Prior briefs come LAST so a change there doesn't invalidate the
    system-block cache. State is the fresh snapshot each day.
    """
    payload = {
        "today_utc": state["as_of_utc"],
        "platform_snapshot": state,
        "your_prior_briefs_newest_first": prior_briefs,
    }
    return (
        "Today's platform snapshot and your three most recent prior briefs "
        "(so you can reflect on what you said and whether it stood up):\n\n"
        f"{json.dumps(payload, indent=2, default=str)}\n\n"
        "Produce today's PMBrief."
    )


# ─────────────────────────────────────────────────────────────────────
# LLM call + fallback
# ─────────────────────────────────────────────────────────────────────


def _call_haiku(
    state: dict[str, Any], prior_briefs: list[dict[str, Any]]
) -> LLMCallResult:
    """Fire the structured Claude Haiku 4.5 call."""
    return structured_call(
        model=settings.AGENT_PM_MODEL,
        system=_SYSTEM_PROMPT,
        user=_render_user_prompt(state, prior_briefs),
        schema=PMBrief,
        max_tokens=1024,
    )


def _deterministic_fallback(state: dict[str, Any], today: date) -> PMBrief:
    """Return a stub brief when the LLM call is skipped/fails.

    Keeps the morning touchpoint alive even if Anthropic is down —
    Wilson still sees a scoreboard update + email, just without the
    LLM-generated recommendations.
    """
    pr = state["paper_runs"]
    running = pr["running"]
    needs_attn = pr.get("needs_attention_count", 0)
    halted = pr["halted_in_last_7_days"]
    summary = (
        f"Paper runs: {running} RUNNING + {needs_attn} needing attention "
        f"(stuck / auto-restarting), {halted} halted/completed in last 7 days. "
        f"Evolution: {state['evolution_runs_last_7_days']['completed']} completed, "
        f"{state['evolution_runs_last_7_days']['failed']} failed. "
        f"LLM brief unavailable; this is a deterministic fallback."
    )
    return PMBrief(
        headline=f"Fleet health check — {today.isoformat()}",
        summary=summary,
        recommendations=[],
        concerns=[],
    )


# ─────────────────────────────────────────────────────────────────────
# Markdown + email rendering
# ─────────────────────────────────────────────────────────────────────


def _render_markdown(brief: PMBrief, today: date, state: dict[str, Any]) -> str:
    """Human-readable scoreboard body."""
    lines: list[str] = [
        f"# AgentFX morning brief — {today.isoformat()}",
        "",
        f"## {brief.headline}",
        "",
        brief.summary,
        "",
    ]
    if brief.recommendations:
        lines.append("## Recommendations")
        lines.append("")
        for rec in brief.recommendations:
            lines.append(f"- **[{rec.priority}]** {rec.headline}")
            lines.append(f"  - _Why:_ {rec.rationale}")
        lines.append("")
    if brief.concerns:
        lines.append("## Concerns")
        lines.append("")
        for c in brief.concerns:
            lines.append(f"- **[{c.severity}]** {c.description}")
        lines.append("")
    lines.append("## Snapshot")
    lines.append("")
    pr = state["paper_runs"]
    status_counts = pr.get("status_counts_last_7_days", {})
    status_summary = (
        ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
        if status_counts
        else "no activity in last 7 days"
    )
    lines.append(f"- Paper runs by status (last 7 days): {status_summary}")
    if pr.get("needs_attention_count", 0) > 0:
        lines.append(
            f"- **{pr['needs_attention_count']} paper run(s) in non-terminal "
            f"non-RUNNING state** — likely stuck / auto-restarting"
        )
    lines.append(
        f"- Evolution runs last 7 days: "
        f"{state['evolution_runs_last_7_days']['completed']} completed, "
        f"{state['evolution_runs_last_7_days']['failed']} failed"
    )
    if state["top_validated_genomes_last_30_days"]:
        lines.append("- Top validated genomes (last 30 days by fitness):")
        for g in state["top_validated_genomes_last_30_days"]:
            fitness = g.get("fitness")
            fit = f"{fitness:.3f}" if isinstance(fitness, (int, float)) else "—"
            lines.append(
                f"  - `{g['id']}` fitness={fit} "
                f"({g.get('pair') or '—'} {g.get('granularity') or ''})"
            )
    lines.append("")
    lines.append("— PM agent (read-only, v0.8.0 Phase B)")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def _write_morning_brief(*, db: Session, today: date) -> None:
    """Build + persist + email the brief.

    Order of operations:
    1. Snapshot platform state + pull recent PM briefs.
    2. Try to call Haiku; on skip/failure, deterministic fallback.
    3. Render markdown + persist to scoreboard + decision log + email.
    """
    state = _snapshot_platform_state(db)
    prior = _recent_pm_summaries(db)

    llm = _call_haiku(state, prior)
    if llm.output is None:
        logger.info(
            "pm_agent: LLM skipped (%s); using deterministic fallback",
            llm.skipped_reason or "unknown",
        )
        brief = _deterministic_fallback(state, today)
        model_used: str | None = None
        reasoning = f"deterministic fallback — llm {llm.skipped_reason}"
        usd = 0.0
        input_tokens = output_tokens = cache_read = 0
    else:
        brief = llm.output
        model_used = llm.model
        reasoning = llm.reasoning or "Haiku structured output; no prose."
        usd = llm.usd_cost
        input_tokens = llm.input_tokens
        output_tokens = llm.output_tokens
        cache_read = llm.cache_read_tokens

    body_md = _render_markdown(brief, today, state)

    # 1. Scoreboard (overwrites — latest brief is the one that matters).
    write_memory_file("scoreboard.md", body_md)

    # 2. Decision log — serialise the structured brief so Reflexion
    #    can grade specific recommendations later.
    rec = DecisionRecord(
        agent_role=AgentRole.PM,
        action="morning_brief",
        decision_key=_brief_key_for(today),
        inputs={"snapshot": state, "prior_briefs_count": len(prior)},
        outputs={"brief": brief.model_dump()},
        reasoning=reasoning,
        model=model_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        usd_cost=usd,
    )
    record_decision(db, rec)
    db.commit()

    # 3. Email (best-effort). Email module logs its own failures.
    _email_brief(subject_date=today, body=body_md, brief=brief)


def _email_brief(*, subject_date: date, body: str, brief: PMBrief) -> None:
    """Send the brief via the tagged SendGrid pipeline.

    Phase B provides both plain-text + HTML bodies. The HTML
    formatting backlog item (Wilson 2026-10-05) is addressed by
    passing `html_body` to `send_agent_message`.
    """
    try:
        from agentfx.notifications.email import render_pm_brief_html, send_agent_message

        subject = f"Morning brief — {subject_date.isoformat()}: {brief.headline}"
        html = render_pm_brief_html(brief, body)
        send_agent_message(
            agent_role="PM",
            subject=subject,
            body=body,
            html_body=html,
        )
    except Exception:
        logger.exception("pm_agent: email send failed (brief still logged in DB)")


__all__ = [
    "run_morning_brief_if_due",
    "PMBrief",
    "PMRecommendation",
    "PMConcern",
    "_brief_key_for",
]
