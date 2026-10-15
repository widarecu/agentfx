"""SQLAlchemy model for PE Managed Agents workspace dispatches (v0.8.1 C.2 Phase 2).

One row per attempt to hand a PE proposal to a code-authoring
workspace. The row captures:

- Which PE proposal the dispatch targets (back-reference by
  `pe_proposal_decision_key` into `agent_decisions`).
- The sub-branch and PR URL the workspace produced (or null if the
  dispatch failed before reaching PR stage).
- Lifecycle status and timing — PENDING → RUNNING → (PR_OPEN |
  TESTS_FAILED | WORKSPACE_FAILED) and later → MERGED | ABANDONED.
- The container-time + LLM $ cost so monthly spend is auditable.

This is a separate table from `agent_decisions` on purpose: a PE
decision ("I propose this change") is append-only, but a dispatch
has an active lifecycle Wilson needs to see (is the workspace still
running? did the PR open? did tests pass?). Mixing those into one
table would conflate the Reflexion log with operational state.
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
    String,
    Text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.sql import func

from .user import Base


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class AgentWorkspaceRunStatus(str, Enum):
    """Lifecycle state of one workspace dispatch.

    - PENDING: row created, dispatch not yet started (never persists
      long; the adapter transitions it to RUNNING immediately).
    - RUNNING: the workspace is live. `provider_run_id` references
      the Anthropic Managed Agents session.
    - PR_OPEN: tests passed inside the workspace and a PR landed on
      `feat/agent-fleet`. `pr_url` is set. Wilson reviews from here.
    - TESTS_FAILED: workspace ran but internal `make test-unit`
      failed after N retries. No PR opened. `summary` carries the
      failing test output tail.
    - WORKSPACE_FAILED: workspace couldn't start / crashed / hit
      TTL. `summary` carries the provider error.
    - MERGED: Wilson merged the PR → `feat/agent-fleet`. Set by a
      follow-up poll of GitHub, not by the workspace itself.
    - ABANDONED: Wilson closed the PR without merging, or Wilson
      explicitly aborted a RUNNING dispatch. Terminal.
    """

    PENDING = "pending"
    RUNNING = "running"
    PR_OPEN = "pr_open"
    TESTS_FAILED = "tests_failed"
    WORKSPACE_FAILED = "workspace_failed"
    MERGED = "merged"
    ABANDONED = "abandoned"


class AgentWorkspaceRun(Base):
    """One PE workspace dispatch — one row per attempt."""

    __tablename__ = "agent_workspace_runs"

    id = Column(String(36), primary_key=True, default=_uuid_hex)

    # Back-reference to the PE proposal that triggered this dispatch.
    # Not a formal FK — `agent_decisions.decision_key` isn't a unique
    # column (append-only log), and a soft reference is operationally
    # safer here.
    pe_proposal_decision_key = Column(String(200), nullable=False, index=True)

    # Target branch for the PR. v0.8.1 pins this to `feat/agent-fleet`
    # per Wilson's approval model; recorded here so we can tell at a
    # glance which branch a historical PR targeted.
    target_branch = Column(String(80), nullable=False, default="feat/agent-fleet")

    # Agent's working sub-branch, pattern `agent/<yyyymmdd-slug>`.
    # Populated by the adapter once the workspace is created.
    agent_branch = Column(String(120), nullable=True)

    # Which adapter did the dispatch. "stub" during dark rollout,
    # "managed_agents" once the real SDK integration is wired.
    adapter = Column(String(32), nullable=False, default="stub")

    # Provider-side session identifier (Anthropic Managed Agents run
    # id). Null when adapter == "stub".
    provider_run_id = Column(String(120), nullable=True, index=True)

    status = Column(
        SQLEnum(
            AgentWorkspaceRunStatus,
            values_callable=lambda x: [e.value for e in x],
            name="agentworkspacerunstatus",
        ),
        nullable=False,
        default=AgentWorkspaceRunStatus.PENDING,
        index=True,
    )

    # Freeform workspace output tail. On TESTS_FAILED, the pytest
    # summary. On WORKSPACE_FAILED, the provider error. On PR_OPEN,
    # the PR body preview.
    summary = Column(Text, nullable=True)

    # GitHub PR URL once opened. Null before PR_OPEN.
    pr_url = Column(String(400), nullable=True)
    pr_number = Column(Integer, nullable=True)

    # Container-time cost (Managed Agents charges by minute) + LLM
    # cost inside the workspace. Null when unknown or stubbed.
    container_minutes = Column(Float, nullable=True)
    container_usd = Column(Float, nullable=True)
    llm_usd = Column(Float, nullable=True)

    # Lifecycle timestamps.
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)

    # Daily-cap query hits this heavily; indexed accordingly.
    __table_args__ = (
        Index("idx_agent_workspace_runs_created_status", "created_at", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentWorkspaceRun id={self.id[:8]} status={self.status} "
            f"adapter={self.adapter} pr={self.pr_url or '(none)'}>"
        )
