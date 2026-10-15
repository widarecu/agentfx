"""PE Managed Agents workspace adapter (v0.8.1 C.2 Phase 2).

What this module does, in plain terms:

PE Phase 1 (`platform_engineer.py`) produces fix proposals as emails.
A human (Wilson) reads the proposal and writes the fix.

Phase 2 is the step up: for HIGH-confidence proposals, instead of
emailing, the fleet hands the proposal to an Anthropic Managed Agents
workspace — a container Anthropic hosts for us — that reads the repo,
writes the fix, runs `make test-unit` inside the container, and opens
a GitHub PR against the `feat/agent-fleet` branch when tests pass.
Wilson only sees the result (a PR link in his inbox + GitHub
notifications), not the step-by-step reasoning.

The dispatch is tracked in the `agent_workspace_runs` DB table so
Wilson can audit cost + success rate over time.

Why an adapter abstraction (`WorkspaceAdapter`), not a direct SDK
call? Three reasons:

1. **Dark rollout.** This commit ships the orchestration + DB plumbing
   with a `StubWorkspaceAdapter` that doesn't call any external
   service. The real Managed Agents SDK integration lands in a
   follow-up once the SDK shape is verified against current docs
   (the Anthropic SDK's Managed Agents namespace moved between
   betas; writing it blind risks shipping broken code).
2. **Testability.** Tests pin a stub adapter and verify the
   orchestration logic (routing, caps, DB rows) without touching
   Anthropic.
3. **Fallback.** If Anthropic ever retires the Managed Agents beta,
   a self-hosted Docker + Claude Agent SDK adapter is a drop-in
   replacement for `ManagedAgentsAdapter` without touching PE or
   the orchestrator.

Guardrails baked into every adapter path:

- Hard daily dispatch cap (settings-driven, default 2).
- Daily $ cap on container-time + LLM cost (settings-driven,
  default $3; aborts dispatch if we'd exceed).
- Target branch pinned to `feat/agent-fleet` — the adapter never
  targets `main`. Wilson gates `feat/agent-fleet` → `main` manually.
- Workspace TTL (adapter-enforced): abort any run that takes longer
  than the budget allows.
- Only HIGH-confidence proposals dispatch; MEDIUM/LOW keep email
  path. Confidence comes straight from PE's structured output.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from agentfx.api.core.config import settings
from agentfx.api.models import AgentWorkspaceRun, AgentWorkspaceRunStatus

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Adapter contract
# ─────────────────────────────────────────────────────────────────────


@dataclass
class WorkspaceTaskSpec:
    """What PE hands to the adapter.

    Everything the workspace needs to know to attempt the fix in
    isolation — the human-readable task narrative (from the QA work
    order + PE proposal), the files PE expects to touch, and the
    tests PE wants added. The adapter is free to adjust these inside
    the workspace; the record keeps them for Reflexion.
    """

    task_slug: str  # "cost-model-eur-usd-m1-2026-10-15"
    pe_proposal_decision_key: str
    title: str
    narrative: str  # Full markdown — becomes the workspace's prompt.
    target_files: list[str]  # Repo-relative paths PE expects to edit.
    new_test_paths: list[str]
    estimated_profit_impact: str
    target_branch: str = "feat/agent-fleet"


@dataclass
class WorkspaceDispatchResult:
    """What the adapter returns to the caller.

    `accepted` tells PE whether the dispatch was accepted into the
    provider's queue (not whether the fix succeeded — that comes
    later via status polling). `workspace_run_id` is the DB row id,
    so PE can reference it in the decision log for Reflexion.
    """

    accepted: bool
    workspace_run_id: str | None = None
    provider_run_id: str | None = None
    agent_branch: str | None = None
    reason: str = ""  # Why rejected, if applicable.


class WorkspaceAdapter(Protocol):
    """Minimal surface every adapter must implement.

    Keep this surface small on purpose — adding methods here forces
    every adapter (including tests) to implement them, which slows
    rollout. New capabilities should live in adapter-specific helpers,
    not the Protocol.
    """

    def dispatch(
        self,
        db: Session,
        *,
        spec: WorkspaceTaskSpec,
        now: datetime | None = None,
    ) -> WorkspaceDispatchResult:
        """Hand the task to the workspace. Must not raise — any
        error returns `accepted=False` with a reason."""
        ...


# ─────────────────────────────────────────────────────────────────────
# Shared pre-dispatch logic — budget + cap + branch creation
# ─────────────────────────────────────────────────────────────────────


_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def build_task_slug(title: str, *, now: datetime | None = None) -> str:
    """`cost-model-eur-usd-m1-2026-10-15` style slug used for the
    agent sub-branch name. Deterministic given (title, date) so a
    re-dispatch for the same proposal reuses the branch."""
    now = now or datetime.now(UTC)
    cleaned = _SLUG_SAFE.sub("-", title.lower()).strip("-")[:60] or "pe-task"
    return f"{now.date().isoformat()}-{cleaned}"


def dispatches_today(db: Session, *, now: datetime | None = None) -> int:
    """Count how many workspace rows PE created today (any status).

    Caps run against this count — a tests-failed dispatch still
    counts against the daily cap, otherwise a looping proposal could
    chew the budget without Wilson noticing.
    """
    now = now or datetime.now(UTC)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    return (
        db.query(AgentWorkspaceRun)
        .filter(AgentWorkspaceRun.created_at >= today_start)
        .count()
    )


def spent_usd_today(db: Session, *, now: datetime | None = None) -> float:
    """Sum of (container_usd + llm_usd) for every workspace row
    created today. Used to check against the daily $ cap before
    dispatching. Rows with null costs count as zero (dispatch may
    still be in flight; cost is only known at completion)."""
    now = now or datetime.now(UTC)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    rows = (
        db.query(AgentWorkspaceRun)
        .filter(AgentWorkspaceRun.created_at >= today_start)
        .all()
    )
    total = 0.0
    for r in rows:
        total += r.container_usd or 0.0
        total += r.llm_usd or 0.0
    return total


def _max_dispatches_per_day() -> int:
    """Settings-backed daily cap. Read lazily so tests can monkey-patch
    `settings` without a restart.

    Note: `0` is a legitimate cap value (freeze all dispatches) — don't
    use `or` as a default-coalesce, it treats 0 as falsy.
    """
    val = getattr(settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 2)
    return int(val if val is not None else 2)


def _max_usd_per_day() -> float:
    val = getattr(settings, "AGENT_PE_WORKSPACE_DAILY_USD_CAP", 3.00)
    return float(val if val is not None else 3.00)


def check_budget_before_dispatch(
    db: Session, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Returns `(ok, reason)` — reason is empty when ok=True.

    Called by every adapter before any provider-side work so the
    stub and real adapters share the same guardrails.
    """
    count = dispatches_today(db, now=now)
    cap = _max_dispatches_per_day()
    if count >= cap:
        return False, f"daily_dispatch_cap_hit:{count}/{cap}"

    spent = spent_usd_today(db, now=now)
    usd_cap = _max_usd_per_day()
    if spent >= usd_cap:
        return False, f"daily_usd_cap_hit:${spent:.2f}/${usd_cap:.2f}"

    return True, ""


def _persist_dispatch_row(
    db: Session,
    *,
    spec: WorkspaceTaskSpec,
    adapter_name: str,
    now: datetime,
) -> AgentWorkspaceRun:
    """Create the PENDING row before the adapter touches the
    provider. Any downstream failure updates this row in-place so
    there's always a single source of truth for the dispatch."""
    row = AgentWorkspaceRun(
        id=uuid.uuid4().hex,
        pe_proposal_decision_key=spec.pe_proposal_decision_key,
        target_branch=spec.target_branch,
        agent_branch=f"agent/{spec.task_slug}",
        adapter=adapter_name,
        status=AgentWorkspaceRunStatus.PENDING,
        created_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────
# StubWorkspaceAdapter — the Phase-2 dark-rollout default
# ─────────────────────────────────────────────────────────────────────


class StubWorkspaceAdapter:
    """Non-dispatching adapter: records the task as "would dispatch"
    but never calls any external service. The row ends up in state
    WORKSPACE_FAILED with `summary="stub adapter — no provider call"`
    so Wilson can see what WOULD have been dispatched without any
    cost or side effects.

    This is the default while:
    - AGENT_PE_WORKSPACE_ENABLED is False (shipping dark).
    - OR the real `ManagedAgentsAdapter` fails its availability
      check at startup.

    Also what the test suite uses.
    """

    def dispatch(
        self,
        db: Session,
        *,
        spec: WorkspaceTaskSpec,
        now: datetime | None = None,
    ) -> WorkspaceDispatchResult:
        now = now or datetime.now(UTC)
        ok, reason = check_budget_before_dispatch(db, now=now)
        if not ok:
            logger.info("pe_workspace[stub]: budget refusal — %s", reason)
            return WorkspaceDispatchResult(accepted=False, reason=reason)

        row = _persist_dispatch_row(db, spec=spec, adapter_name="stub", now=now)
        row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
        row.summary = (
            "stub adapter — no provider call was made. "
            "AGENT_PE_WORKSPACE_ENABLED=False or no real adapter wired."
        )
        row.started_at = now
        row.ended_at = now
        db.commit()
        logger.info(
            "pe_workspace[stub]: recorded intended dispatch for proposal=%s "
            "(task_slug=%s). Set AGENT_PE_WORKSPACE_ENABLED=true + wire "
            "ManagedAgentsAdapter to dispatch for real.",
            spec.pe_proposal_decision_key,
            spec.task_slug,
        )
        return WorkspaceDispatchResult(
            accepted=True,
            workspace_run_id=row.id,
            agent_branch=row.agent_branch,
            reason="stub",
        )


# ─────────────────────────────────────────────────────────────────────
# ManagedAgentsAdapter — real Anthropic SDK integration
# ─────────────────────────────────────────────────────────────────────


class ManagedAgentsAdapter:
    """Dispatches to Anthropic Managed Agents (Beta).

    **This class is scaffolded but not wired to the SDK yet.** The
    first `dispatch` call falls back to the stub behaviour so a
    misconfigured enable-flag can't accidentally start burning
    container-hours. The SDK wire-up is a focused follow-up:

    1. Confirm current Managed Agents namespace on the pinned
       `anthropic` SDK version via that version's CHANGELOG (the
       namespace moved between betas in 2026).
    2. Set up the GitHub PAT in Anthropic's vault
       (`environment_variable` credential) so the workspace can
       push + open PRs without the token touching logs.
    3. Replace the `_dispatch_managed` TODO block with the SDK
       calls, re-run tests, flip the flag on in staging first.

    Everything AROUND the SDK call (budget check, DB row, task
    slug, result shape) is live and tested in this commit.
    """

    def __init__(self, *, timeout_minutes: int = 20) -> None:
        # Container TTL — abort any workspace that runs longer than
        # this. 20 min is enough for a small fix + test loop; a
        # proposal that needs more exploration should go back through
        # QA + PE first.
        self.timeout_minutes = timeout_minutes

    def dispatch(
        self,
        db: Session,
        *,
        spec: WorkspaceTaskSpec,
        now: datetime | None = None,
    ) -> WorkspaceDispatchResult:
        now = now or datetime.now(UTC)
        ok, reason = check_budget_before_dispatch(db, now=now)
        if not ok:
            return WorkspaceDispatchResult(accepted=False, reason=reason)

        row = _persist_dispatch_row(
            db, spec=spec, adapter_name="managed_agents", now=now
        )
        return self._dispatch_managed(db, row=row, spec=spec, now=now)

    def _dispatch_managed(
        self,
        db: Session,
        *,
        row: AgentWorkspaceRun,
        spec: WorkspaceTaskSpec,
        now: datetime,
    ) -> WorkspaceDispatchResult:
        """TODO (v0.8.1 C.2 Phase 2b): wire the Anthropic Managed
        Agents SDK call here.

        Expected shape (confirm against SDK docs before writing):
        - Create a session with the repo mounted + GitHub PAT vault
          credential.
        - Supply a system prompt describing the three-tier gate
          (sub-branch → feat/agent-fleet → main).
        - Supply the user prompt = `spec.narrative`.
        - Enable tool-use for file ops, bash (make test-unit),
          and git (branch/commit/push).
        - Return the provider's run id, poll for completion in a
          follow-up worker (not inline — this call must stay async-
          safe so the orchestrator's PE loop isn't blocked).

        Until that lands: downgrade to stub behaviour so the fleet
        is safe to run with AGENT_PE_WORKSPACE_ENABLED=true during
        rollout (the row still records the intent).
        """
        row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
        row.summary = (
            "ManagedAgentsAdapter: SDK wire-up pending. Row persisted so "
            "the orchestration path is exercised; no provider call made."
        )
        row.started_at = now
        row.ended_at = now
        db.commit()
        return WorkspaceDispatchResult(
            accepted=True,
            workspace_run_id=row.id,
            agent_branch=row.agent_branch,
            reason="managed_agents_sdk_pending",
        )


# ─────────────────────────────────────────────────────────────────────
# Adapter resolution — one place that picks the right adapter per env
# ─────────────────────────────────────────────────────────────────────


def resolve_adapter() -> WorkspaceAdapter:
    """Return the adapter the orchestrator should use.

    - Flag off → always `StubWorkspaceAdapter`.
    - Flag on + no GitHub PAT → `StubWorkspaceAdapter` (fail-safe —
      without a PAT, no PR can land, so dispatch would just burn
      cost).
    - Flag on + PAT set → `ManagedAgentsAdapter`.
    """
    enabled = bool(getattr(settings, "AGENT_PE_WORKSPACE_ENABLED", False))
    if not enabled:
        return StubWorkspaceAdapter()
    pat = getattr(settings, "GITHUB_TOKEN", "") or ""
    if not pat:
        logger.warning(
            "pe_workspace: AGENT_PE_WORKSPACE_ENABLED=true but GITHUB_TOKEN is "
            "empty; falling back to stub adapter so no cost is incurred."
        )
        return StubWorkspaceAdapter()
    return ManagedAgentsAdapter()


# ─────────────────────────────────────────────────────────────────────
# Operational helper: age-out stuck rows
# ─────────────────────────────────────────────────────────────────────


def sweep_stuck_runs(db: Session, *, now: datetime | None = None) -> int:
    """Mark RUNNING workspace rows that have aged past the global
    TTL as WORKSPACE_FAILED. Called by the orchestrator before any
    new dispatch so a crashed workspace doesn't pin the daily cap.

    Returns the number of rows reclaimed. Never raises.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=4)
    try:
        rows = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.status == AgentWorkspaceRunStatus.RUNNING)
            .filter(AgentWorkspaceRun.started_at.isnot(None))
            .filter(AgentWorkspaceRun.started_at <= cutoff)
            .all()
        )
        for row in rows:
            logger.warning(
                "pe_workspace: reclaiming stuck RUNNING row id=%s started_at=%s",
                row.id,
                row.started_at,
            )
            row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
            row.summary = (
                (row.summary or "")
                + "\n[sweeper] exceeded 4h TTL, marked WORKSPACE_FAILED"
            ).strip()
            row.ended_at = now
        if rows:
            db.commit()
        return len(rows)
    except Exception:
        logger.exception("pe_workspace: sweep_stuck_runs failed")
        return 0


__all__ = [
    "WorkspaceTaskSpec",
    "WorkspaceDispatchResult",
    "WorkspaceAdapter",
    "StubWorkspaceAdapter",
    "ManagedAgentsAdapter",
    "build_task_slug",
    "dispatches_today",
    "spent_usd_today",
    "check_budget_before_dispatch",
    "resolve_adapter",
    "sweep_stuck_runs",
]
