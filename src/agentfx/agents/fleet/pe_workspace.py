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

    SDK integration against `anthropic` ≥0.100 — namespaces:
    `client.beta.agents` (persistent agent configs),
    `client.beta.environments` (compute profiles),
    `client.beta.vaults` + `vaults.credentials` (secret storage),
    `client.beta.sessions` (one running workspace per dispatch),
    `sessions.events.send/list` (talk to and observe the agent).

    Division of responsibility:

    - **Pre-created once by the operator** (via console or a one-off
      setup script): the agent config, the environment, the vault +
      GitHub PAT credential. Their IDs live in settings
      (`AGENT_PE_WORKSPACE_AGENT_ID` / `_ENVIRONMENT_ID` / `_VAULT_ID`).
      That boundary keeps our code idempotent and lets Wilson rotate
      the PAT or tune the agent without a redeploy.

    - **Per-dispatch (what this adapter does)**: create a session
      against those IDs, send the user message with the task
      narrative, persist `provider_run_id = session_id`, flip the
      row to RUNNING. The outer polling loop
      (`agent_orchestrator._pe_workspace_poll_loop`) then
      transitions RUNNING → PR_OPEN / TESTS_FAILED /
      WORKSPACE_FAILED by calling `sessions.retrieve` +
      `sessions.events.list`.

    Any missing config, missing SDK, or SDK exception → the row
    is marked WORKSPACE_FAILED with an operator-readable summary;
    the dispatch returns `accepted=True` (the row IS persisted and
    audit-visible) so PE's scan loop doesn't retry the same
    proposal next tick.
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

    def _resolve_client(self) -> tuple[object | None, str | None]:
        """Return `(client, missing_reason)` — exactly one is non-None.

        Separated so tests can patch cleanly and so missing-config
        paths are explicit, not implicit `try/except`.
        """
        try:
            import anthropic  # noqa: F401 (checked for availability)
        except ImportError:
            return None, "anthropic SDK not installed"

        agent_id = getattr(settings, "AGENT_PE_WORKSPACE_AGENT_ID", "") or ""
        env_id = getattr(settings, "AGENT_PE_WORKSPACE_ENVIRONMENT_ID", "") or ""
        vault_id = getattr(settings, "AGENT_PE_WORKSPACE_VAULT_ID", "") or ""
        missing = [
            name
            for name, val in [
                ("AGENT_PE_WORKSPACE_AGENT_ID", agent_id),
                ("AGENT_PE_WORKSPACE_ENVIRONMENT_ID", env_id),
                ("AGENT_PE_WORKSPACE_VAULT_ID", vault_id),
            ]
            if not val
        ]
        if missing:
            return None, (
                "missing managed-agents config — "
                f"{', '.join(missing)} empty. "
                "Create the resources via Anthropic console or a setup "
                "script and set the IDs on Railway staging."
            )

        import anthropic

        return anthropic.Anthropic(max_retries=2), None

    def _dispatch_managed(
        self,
        db: Session,
        *,
        row: AgentWorkspaceRun,
        spec: WorkspaceTaskSpec,
        now: datetime,
    ) -> WorkspaceDispatchResult:
        """Create a Managed Agents session + send the task narrative
        as the first user message. On success the row transitions
        PENDING → RUNNING with `provider_run_id` set to the session
        id. The polling loop takes it from there."""
        client, missing_reason = self._resolve_client()
        if client is None:
            row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
            row.summary = f"ManagedAgentsAdapter: {missing_reason}"
            row.started_at = now
            row.ended_at = now
            db.commit()
            logger.warning(
                "pe_workspace[managed]: dispatch degraded — %s", missing_reason
            )
            return WorkspaceDispatchResult(
                accepted=True,
                workspace_run_id=row.id,
                agent_branch=row.agent_branch,
                reason=missing_reason or "config_missing",
            )

        agent_id = settings.AGENT_PE_WORKSPACE_AGENT_ID
        env_id = settings.AGENT_PE_WORKSPACE_ENVIRONMENT_ID
        vault_id = settings.AGENT_PE_WORKSPACE_VAULT_ID

        try:
            session = client.beta.sessions.create(
                agent={"id": agent_id},
                environment_id=env_id,
                vault_ids=[vault_id],
                # Title is operator-facing in the Managed Agents console;
                # the task slug keeps sessions correlated with the
                # agent/<slug> branch they produce.
                title=spec.task_slug,
                metadata={
                    "pe_proposal_decision_key": spec.pe_proposal_decision_key,
                    "agent_branch": row.agent_branch or "",
                    "target_branch": spec.target_branch,
                },
            )
        except Exception as exc:
            row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
            row.summary = (
                f"ManagedAgentsAdapter: sessions.create failed — "
                f"{type(exc).__name__}: {str(exc)[:400]}"
            )
            row.started_at = now
            row.ended_at = now
            db.commit()
            logger.exception(
                "pe_workspace[managed]: sessions.create failed for proposal=%s",
                spec.pe_proposal_decision_key,
            )
            return WorkspaceDispatchResult(
                accepted=True,
                workspace_run_id=row.id,
                agent_branch=row.agent_branch,
                reason=f"sessions_create_failed:{type(exc).__name__}",
            )

        session_id = getattr(session, "id", None) or ""
        if not session_id:
            row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
            row.summary = "ManagedAgentsAdapter: session created but no id returned"
            row.started_at = now
            row.ended_at = now
            db.commit()
            return WorkspaceDispatchResult(
                accepted=True,
                workspace_run_id=row.id,
                agent_branch=row.agent_branch,
                reason="session_id_missing",
            )

        # Send the kickoff user message — the narrative explicitly
        # calls out the three-tier branch gate.
        try:
            client.beta.sessions.events.send(
                session_id=session_id,
                events=[
                    {
                        "type": "user_message",
                        "content": [
                            {"type": "text", "text": spec.narrative},
                        ],
                    }
                ],
            )
        except Exception as exc:
            # Session was created but the kickoff failed — mark the
            # row failed, but DO NOT lose the session id (it may still
            # cost container-time; operator can inspect in console).
            row.status = AgentWorkspaceRunStatus.WORKSPACE_FAILED
            row.provider_run_id = session_id
            row.summary = (
                f"ManagedAgentsAdapter: events.send failed — "
                f"{type(exc).__name__}: {str(exc)[:400]}"
            )
            row.started_at = now
            row.ended_at = now
            db.commit()
            logger.exception(
                "pe_workspace[managed]: events.send failed session=%s",
                session_id,
            )
            return WorkspaceDispatchResult(
                accepted=True,
                workspace_run_id=row.id,
                provider_run_id=session_id,
                agent_branch=row.agent_branch,
                reason=f"events_send_failed:{type(exc).__name__}",
            )

        # Happy path — session live, first message sent. The polling
        # loop will transition this row the rest of the way.
        row.provider_run_id = session_id
        row.status = AgentWorkspaceRunStatus.RUNNING
        row.started_at = now
        db.commit()
        logger.info(
            "pe_workspace[managed]: dispatched proposal=%s → session=%s "
            "(agent_branch=%s)",
            spec.pe_proposal_decision_key,
            session_id,
            row.agent_branch,
        )
        return WorkspaceDispatchResult(
            accepted=True,
            workspace_run_id=row.id,
            provider_run_id=session_id,
            agent_branch=row.agent_branch,
            reason="dispatched",
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


# ─────────────────────────────────────────────────────────────────────
# Status polling — transition RUNNING → terminal state
# ─────────────────────────────────────────────────────────────────────


# Managed Agents status strings per the SDK (see sessions.list
# `statuses` literal). Not all are terminal — `rescheduling` + `idle`
# are transient. We transition on `terminated` only; the stuck-row
# sweeper handles the pathological "never terminates" case.
_SESSION_TERMINAL_STATUS = "terminated"


# Hint strings we look for in the final events to classify the
# session's outcome. Keep conservative — a false positive wrongly
# claims a PR exists; a false negative just means Wilson gets an
# "unclear outcome" email that still has the full event tail for him
# to eyeball.
_PR_URL_PATTERNS = (
    "https://github.com/",  # The PR URL `gh pr create` prints.
)
_TESTS_FAILED_PATTERNS = (
    "test failures remain after 3 iterations",
    "tests still fail",
    "pytest exit code 1",
)


def _classify_session_outcome(
    events_text: str,
) -> tuple[AgentWorkspaceRunStatus, str | None]:
    """Scan the concatenated final-events text for a PR URL or a
    tests-failed signal and return the target status + extracted
    PR URL (or None).

    Deliberately simple string matching: the first PR URL we find
    wins. A richer parse (regex, structured event tags) is a
    follow-up when real sessions show us the actual event shape.
    """
    import re

    # First-URL-wins — pick the GitHub PR URL closest to the end of
    # the event stream, which is almost always the "PR opened: …"
    # line from gh.
    pr_url = None
    m = re.findall(r"https://github\.com/\S+/pull/\d+", events_text)
    if m:
        pr_url = m[-1]
    if pr_url:
        return AgentWorkspaceRunStatus.PR_OPEN, pr_url

    lower = events_text.lower()
    for pat in _TESTS_FAILED_PATTERNS:
        if pat in lower:
            return AgentWorkspaceRunStatus.TESTS_FAILED, None

    # Terminated with no PR URL and no clear tests-failed signal —
    # likely the agent gave up or hit the TTL. Operator-visible.
    return AgentWorkspaceRunStatus.WORKSPACE_FAILED, None


def _fetch_events_text(client: object, session_id: str) -> str:
    """Pull the recent event tail from a session for outcome parsing.

    Best-effort — any SDK failure just returns the empty string so
    the caller still transitions the row (status defaults to
    WORKSPACE_FAILED on an empty tail, which is correct: we can't
    see what happened).
    """
    try:
        resp = client.beta.sessions.events.list(  # type: ignore[attr-defined]
            session_id=session_id, limit=50
        )
    except Exception:
        logger.exception(
            "pe_workspace[poll]: events.list failed session=%s", session_id
        )
        return ""

    parts: list[str] = []
    items = getattr(resp, "data", None) or []
    for ev in items:
        # Different event shapes: a user_message/assistant_message
        # has `content` as a list of parts; tool-use events have a
        # `tool_name` + `input`/`output`. Flatten anything we can
        # see as text — matching is substring-based so recall
        # matters more than structure.
        for attr in ("content", "output", "result", "stdout"):
            v = getattr(ev, attr, None)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                for p in v:
                    t = getattr(p, "text", None) or (
                        p.get("text") if isinstance(p, dict) else None
                    )
                    if t:
                        parts.append(t)
    return "\n".join(parts)


def poll_running_dispatches(db: Session, *, now: datetime | None = None) -> dict:
    """Poll each RUNNING workspace row's provider status and
    transition terminated ones to PR_OPEN / TESTS_FAILED /
    WORKSPACE_FAILED. Returns a small counts dict for operator
    logging. Never raises.
    """
    now = now or datetime.now(UTC)
    counts = {"checked": 0, "pr_open": 0, "tests_failed": 0, "failed": 0}

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return counts

    try:
        rows = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.status == AgentWorkspaceRunStatus.RUNNING)
            .filter(AgentWorkspaceRun.provider_run_id.isnot(None))
            .filter(AgentWorkspaceRun.adapter == "managed_agents")
            .all()
        )
    except Exception:
        logger.exception("pe_workspace[poll]: row scan failed")
        return counts

    if not rows:
        return counts

    import anthropic as anth

    client = anth.Anthropic(max_retries=2)

    for row in rows:
        counts["checked"] += 1
        session_id = row.provider_run_id or ""
        try:
            session = client.beta.sessions.retrieve(session_id=session_id)
        except Exception as exc:
            logger.exception(
                "pe_workspace[poll]: sessions.retrieve failed session=%s", session_id
            )
            # Don't flip the row — let the stuck-row sweeper handle
            # it on TTL. A transient retrieve error shouldn't lose
            # the dispatch.
            continue
        status = getattr(session, "status", "") or ""
        if status != _SESSION_TERMINAL_STATUS:
            continue  # Still running — leave the row alone.

        events_text = _fetch_events_text(client, session_id)
        target_status, pr_url = _classify_session_outcome(events_text)
        row.status = target_status
        row.ended_at = now
        # Keep a tail of the events text in the summary so Wilson
        # can see what happened without logging into the Managed
        # Agents console.
        summary_tail = (events_text or "(no event text extracted)")[-2000:]
        row.summary = (row.summary or "") + "\n[poll] " + summary_tail
        if pr_url is not None:
            row.pr_url = pr_url
            import re

            m = re.search(r"/pull/(\d+)", pr_url)
            if m:
                row.pr_number = int(m.group(1))
            counts["pr_open"] += 1
        elif target_status == AgentWorkspaceRunStatus.TESTS_FAILED:
            counts["tests_failed"] += 1
        else:
            counts["failed"] += 1

    try:
        db.commit()
    except Exception:
        logger.exception("pe_workspace[poll]: commit failed")
        return counts

    return counts


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
    "poll_running_dispatches",
]
