"""Portfolio Manager Agent (v0.8.0 Phase A — read-only).

Fires once per 24 hours (target: 09:00 UTC). Reads the last 7 days
of platform state, writes a morning brief, and emails it to Wilson.

What this agent does NOT do yet (deferred to later milestones):
- Any `POST` to `/evolution-runs` / `/paper-runs/approve` /
  `/paper-runs/kill`. This is the READ-ONLY pilot. Earn Wilson's
  trust before granting authority.
- Call out to the LLM in anything but the simplest possible shape.
  v0.8.0's purpose is to prove the plumbing — orchestrator cron,
  memory layer, email channel, cost accounting. The brief content
  gets smarter in Phase B once QA has data to summarise.

Why Haiku 4.5?
Cost. ~$1/MTok input, $5/MTok output. A daily brief of ~20k input
+ ~5k output tokens runs ~$0.045/day = $1.35/month — plenty of
budget headroom.

Why one check per hour instead of true 09:00 UTC cron?
Zero new deps and the "run at most once a day" invariant lives in
the DB (we check whether today's brief row exists) rather than in
scheduler state. Simpler to reason about.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentfx.api.models.agent_decision import AgentDecision, AgentRole
from agentfx.agents.fleet.memory import (
    DecisionRecord,
    record_decision,
    write_memory_file,
)

logger = logging.getLogger(__name__)

# Target UTC hour for the daily brief. The orchestrator wakes every
# hour; this is the gate that says "yes, do it now."
_BRIEF_TARGET_HOUR_UTC = 9


# `decision_key` identifier used for idempotency: at most one PM
# morning brief row per calendar day.
def _brief_key_for(today: date) -> str:
    return f"pm_morning_brief_{today.isoformat()}"


def _already_run_today(db: Session, *, today: date) -> bool:
    """Return True if a PM morning brief has been recorded for `today`.

    The check is a straight read on `agent_decisions.decision_key` so
    we don't double-send when the hourly tick lands twice in the
    09:00 UTC window.
    """
    key = _brief_key_for(today)
    exists = (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PM.value)
        .filter(AgentDecision.decision_key == key)
        .first()
    )
    return exists is not None


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def run_morning_brief_if_due(*, db: Session, now: datetime | None = None) -> None:
    """Run the PM morning brief iff today's hasn't run yet and it's
    at or past the target UTC hour.

    Never raises — the orchestrator depends on this being safe to
    call every hour of every day without any effect outside the
    intended window.

    `now` is injected for tests; defaults to real wall-clock UTC.
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
# Brief generation (v0.8.0 Phase A — read-only, stub body)
# ─────────────────────────────────────────────────────────────────────


def _snapshot_platform_state(db: Session) -> dict[str, Any]:
    """Read the minimal platform state the Phase-A brief summarises.

    Deliberately shallow in Phase A — just enough to prove the
    pipeline. Phase B expands this to the full 7-day paper P&L +
    evolution + regime read.
    """
    from agentfx.api.models.run import Run, RunEnvironment, RunStatus

    running = (
        db.query(Run)
        .filter(Run.environment == RunEnvironment.PAPER)
        .filter(Run.status == RunStatus.RUNNING)
        .count()
    )
    halted_last_24h = (
        db.query(Run)
        .filter(Run.environment == RunEnvironment.PAPER)
        .filter(Run.status != RunStatus.RUNNING)
        .count()
    )
    return {
        "paper_runs_running": running,
        "paper_runs_not_running": halted_last_24h,
    }


def _render_brief(state: dict[str, Any], today: date) -> str:
    """Produce the plain-text brief for `agent_memory/scoreboard.md`
    and the email body.

    v0.8.0 Phase A: deterministic text from the state snapshot. No
    LLM call yet — once the orchestration + email + memory
    plumbing is proven live, Phase B swaps this out for a Haiku 4.5
    call (same inputs, richer output).
    """
    return (
        f"AgentFX morning brief — {today.isoformat()}\n"
        f"==============================================\n\n"
        f"Paper runs RUNNING:     {state['paper_runs_running']}\n"
        f"Paper runs not running: {state['paper_runs_not_running']}\n\n"
        f"v0.8.0 Phase A pilot: the fleet is observing. No actions "
        f"are taken on this run — Wilson approves authority upgrades "
        f"at each milestone (see planning/ROADMAP.md § v0.8.1+).\n\n"
        f"— PM agent (read-only)\n"
    )


def _write_morning_brief(*, db: Session, today: date) -> None:
    """Build + persist + email the brief.

    Writes three places:
    - `agent_memory/scoreboard.md` — Wilson's at-a-glance file.
    - `agent_decisions` DB row — Reflexion dataset.
    - Wilson's email inbox — the daily touch via SendGrid.

    All three are best-effort. Failure in one doesn't block the
    others; the orchestrator loop catches any uncaught exception.
    """
    state = _snapshot_platform_state(db)
    body = _render_brief(state, today)

    # 1. Scoreboard (overwrite each day — the file always reflects
    #    the latest brief; prior versions live in agent_decisions).
    write_memory_file("scoreboard.md", body)

    # 2. Decision log row.
    rec = DecisionRecord(
        agent_role=AgentRole.PM,
        action="morning_brief",
        decision_key=_brief_key_for(today),
        inputs={"snapshot": state},
        outputs={"brief_markdown": body},
        reasoning=(
            "Phase A pilot: deterministic snapshot summary; no LLM call. "
            "Confirms orchestration + memory + email plumbing is live."
        ),
        model=None,  # populated once Phase B wires the Haiku call
        usd_cost=0.0,
    )
    record_decision(db, rec)
    db.commit()

    # 3. Email to Wilson. Best-effort; a send failure is logged by
    #    the email module and does not re-raise.
    _email_brief(subject_date=today, body=body)


def _email_brief(*, subject_date: date, body: str) -> None:
    """Send the brief via the existing SendGrid pipeline, tagged
    with the agent prefix so Wilson's inbox stays sortable.
    """
    try:
        from agentfx.notifications.email import send_agent_message

        send_agent_message(
            agent_role="PM",
            subject=f"Morning brief — {subject_date.isoformat()}",
            body=body,
        )
    except Exception:
        logger.exception("pm_agent: email send failed (brief still logged in DB)")


__all__ = [
    "run_morning_brief_if_due",
    "_brief_key_for",  # exported for tests
]
