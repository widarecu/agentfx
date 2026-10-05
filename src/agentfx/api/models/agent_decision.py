"""SQLAlchemy model for the v0.8.x Agent Fleet decision log.

Separate from `decision_logs` (which captures evolution + user actions
tied to an evolution run) because fleet agents operate outside the
per-run scope: a PM morning brief has no `run_id`, a QA divergence
report spans multiple paper runs, a PE PR targets a code repo not a
genome.

Design intent:

- **Append-only.** Every decision an agent makes is written on
  creation and immutable thereafter — `outcome_json` and
  `outcome_recorded_at` are populated LATER by the Reflexion loop
  when the real-world result is known. We never overwrite the
  original reasoning.
- **Grades the fleet, not individual trades.** This table is the
  substrate the Portfolio Manager + Quant Analyst re-read to
  reflect on past decisions and avoid repeating mistakes.
- **Human-readable.** Agents dump their reasoning as plain text so
  Wilson can audit `agent_memory/scoreboard.md` or re-query the DB
  without parsing structured blobs.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import JSON, Column, DateTime, Float, Index, String, Text
from sqlalchemy.sql import func

from .user import Base


class AgentRole(str, Enum):
    """Which specialist produced the decision."""

    PM = "PM"  # Portfolio Manager
    QA = "QA"  # Quant Analyst
    PE = "PE"  # Platform Engineer
    RA = "RA"  # Research Agent


class AgentDecision(Base):
    """One row per decision a fleet agent made.

    Rows are written at decision time by the orchestrator; outcome
    fields are updated later (Reflexion loop) when the real outcome
    is observed. Nothing else about the row mutates.
    """

    __tablename__ = "agent_decisions"

    id = Column(String(36), primary_key=True, index=True)  # uuid hex

    # Which agent + what kind of decision.
    agent_role = Column(String(4), nullable=False, index=True)  # AgentRole
    action = Column(String(60), nullable=False, index=True)
    # Freeform short identifier so Reflexion can group related decisions
    # (e.g. "pm_morning_brief_2026-10-05", "qa_divergence_run_abc").
    decision_key = Column(String(200), nullable=False, index=True)

    # Context the agent was reading at decision time (platform state
    # snapshot, prior memory, prompt). Stored verbatim so Reflexion can
    # ask "would a human / another model make the same call given
    # exactly this input?" Not meant for the UI — too noisy.
    inputs_json = Column(JSON, nullable=True)

    # What the agent decided, as structured JSON — e.g. the morning
    # brief's recommendations, the divergence report classification,
    # the PE work order spec.
    outputs_json = Column(JSON, nullable=True)

    # Plain-text reasoning the agent produced. Separate from
    # outputs_json because reasoning is the single thing the next
    # Reflexion call re-reads most, and keeping it typed as Text
    # avoids JSON escaping noise.
    reasoning_text = Column(Text, nullable=True)

    # LLM call accounting — the orchestrator populates these so
    # cost dashboards + per-day caps work. input + output tokens
    # (post-cache) and the dollar estimate at pricing time.
    model = Column(String(60), nullable=True)
    input_tokens = Column(Float, nullable=True)
    output_tokens = Column(Float, nullable=True)
    cache_read_tokens = Column(Float, nullable=True)
    usd_cost = Column(Float, nullable=True)

    # Reflexion fields — filled in LATER when the real outcome of this
    # decision is known. Null until then.
    outcome_json = Column(JSON, nullable=True)
    outcome_recorded_at = Column(DateTime(timezone=True), nullable=True)
    # Short tag for how the decision turned out: "good", "bad",
    # "mixed", "no_signal", "pending". Agents read this when sampling
    # past decisions for prompt context.
    outcome_grade = Column(String(12), nullable=True, index=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_agent_decision_role_created", "agent_role", "created_at"),
        Index("idx_agent_decision_grade_role", "outcome_grade", "agent_role"),
    )
