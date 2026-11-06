"""Webhook-triggered dispatch orchestrator (AFX-66 Phase 2).

When Linear POSTs an `AgentSessionEvent` with `action=created` to our
webhook endpoint, we need to:

1. Emit a `thought` activity back to Linear within 10s so the session
   doesn't mark as unresponsive.
2. Run the budget pre-flight — if over the daily cap, emit an `error`
   activity and stop. Otherwise the session hangs with no feedback.
3. Resolve the worker adapter, build a `WorkspaceTaskSpec` from the
   webhook payload (Linear's `promptContext` is already a well-formed
   prompt — we don't need to rebuild it), and call `adapter.dispatch`.
4. Link the created `agent_workspace_runs` row back to the Linear
   session via `linear_agent_session_id` + `linear_webhook_delivery_id`
   so the resume path (Phase 3) can find it.
5. Emit a confirmation `thought` ("Session sesn_... started") or an
   `error` activity carrying the failure reason.

This module is the webhook-driven peer of
`platform_engineer.scan_and_dispatch`. It reuses the same
`WorkspaceAdapter`, the same budget helpers, and the same row
schema — only the trigger shape differs.

Design rules:

- Never raise. The webhook handler schedules this as a background
  task; an uncaught exception would be invisible to the operator
  (no 5xx, no retry).
- Opens its own DB Session (`SessionLocal`) — the FastAPI
  request-scoped session has already closed by the time this runs.
- Idempotency key: `linear_agent_session_id`. If a row already
  carries this session id, we don't dispatch again.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentfx.api.core.database import SessionLocal
from agentfx.api.models.agent_workspace_run import AgentWorkspaceRun
from agentfx.integrations.linear_agent_api import emit_error, emit_thought

logger = logging.getLogger(__name__)


_THOUGHT_PICKED_UP_TEMPLATE = "Picking up {identifier}, scoping the work..."
_THOUGHT_DISPATCHED_TEMPLATE = (
    "Dispatched to Managed Agents workspace. Session: `{provider_id}`. "
    "Branch: `{branch}`. Watching for PR."
)
_ERROR_BUDGET_TEMPLATE = (
    "**Daily dispatch cap reached** — can't start this session today "
    "({reason}). I'll pick it up when the cap resets at 00:00 UTC. "
    "If this is urgent, Wilson can raise `AGENT_PE_WORKSPACE_MAX_PER_DAY` "
    "or `AGENT_PE_WORKSPACE_DAILY_USD_CAP` on Railway."
)
_ERROR_ADAPTER_REFUSED_TEMPLATE = (
    "Workspace adapter refused the dispatch: {reason}. No PR will "
    "open. Check the Railway logs for `pe_agent:` entries around "
    "this timestamp for the full trace."
)
_ERROR_UNEXPECTED_TEMPLATE = (
    "Unexpected error while dispatching: `{exc_type}`. No PR will "
    "open. Check Railway logs."
)
_ERROR_DUPLICATE_TEMPLATE = (
    "Already dispatched for this agent session (workspace run "
    "`{run_id}`). If that run is stuck, cancel it in the Managed "
    "Agents console and reopen the ticket."
)


def dispatch_from_linear_webhook(
    *,
    delivery_id: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Dispatch a Managed Agents session from a Linear agent-session
    `action=created` event.

    Opens its own DB session so it can safely run as a background
    task after the webhook request has returned. Never raises.

    Returns `(dispatched, reason)` for logging. `dispatched=True`
    means we accepted the task and a `agent_workspace_runs` row
    exists; the actual PR open happens asynchronously via the poll
    loop as usual.
    """
    now = now or datetime.now(UTC)

    extracted = _extract_payload_fields(payload)
    if extracted is None:
        return False, "payload_missing_required_fields"

    session_id = extracted["agent_session_id"]
    identifier = extracted["issue_identifier"]
    title = extracted["issue_title"]
    narrative = extracted["narrative"]

    # Step 1 — Emit the "picking up" thought FIRST. Linear's 10s
    # responsiveness timer starts at webhook-send, and by the time
    # the background task runs we've already spent ~1s acknowledging.
    # Fire-and-forget the result: a transient Linear outage shouldn't
    # block the dispatch itself.
    ok, err = emit_thought(
        session_id,
        _THOUGHT_PICKED_UP_TEMPLATE.format(identifier=identifier),
    )
    if not ok:
        logger.warning(
            "linear_webhook_dispatch: emit_thought (picking up) failed "
            "session=%s reason=%s — continuing with dispatch",
            session_id,
            err,
        )

    # Everything from here on needs a DB session. Open a fresh one —
    # the FastAPI request-scoped Depends(get_db) session closed when
    # the webhook handler returned.
    db: Session = SessionLocal()
    try:
        # Step 2 — Idempotency. If a workspace run already carries
        # this linear_agent_session_id, we've already dispatched —
        # don't double-dispatch on a Linear retry or a bug upstream.
        existing = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.linear_agent_session_id == session_id)
            .first()
        )
        if existing is not None:
            logger.info(
                "linear_webhook_dispatch: session=%s already dispatched as "
                "workspace_run=%s — skipping",
                session_id,
                existing.id,
            )
            emit_error(
                session_id,
                _ERROR_DUPLICATE_TEMPLATE.format(run_id=existing.id[:8]),
            )
            return False, f"already_dispatched:{existing.id}"

        # Lazy imports keep Phase 1-only installs (webhook receiver
        # without dispatch) from pulling the Managed Agents stack on
        # import.
        from agentfx.agents.fleet.pe_workspace import (
            build_task_slug,
            check_budget_before_dispatch,
            resolve_adapter,
            sweep_stuck_runs,
        )

        sweep_stuck_runs(db, now=now)

        # Step 3 — Budget pre-flight. Over cap → emit error activity
        # so Wilson sees the reason in Linear, not just Railway logs.
        budget_ok, budget_reason = check_budget_before_dispatch(db, now=now)
        if not budget_ok:
            logger.info(
                "linear_webhook_dispatch: budget denied session=%s reason=%s",
                session_id,
                budget_reason,
            )
            emit_error(
                session_id,
                _ERROR_BUDGET_TEMPLATE.format(reason=budget_reason),
            )
            return False, f"budget:{budget_reason}"

        # Step 4 — Build the task spec. Linear's `promptContext` is
        # the ready-made prompt (issue + labels + project + blocked-by).
        # We prepend the user's own trigger message so the agent knows
        # what the human asked, then the context.
        spec = _build_task_spec(
            identifier=identifier,
            title=title,
            narrative=narrative,
            now=now,
            slug_fn=build_task_slug,
        )

        # Step 5 — Dispatch via the resolved adapter.
        adapter = resolve_adapter()
        try:
            result = adapter.dispatch(db, spec=spec, now=now)
        except Exception as exc:  # noqa: BLE001 — never raise
            logger.exception(
                "linear_webhook_dispatch: adapter raised — session=%s",
                session_id,
            )
            emit_error(
                session_id,
                _ERROR_UNEXPECTED_TEMPLATE.format(exc_type=type(exc).__name__),
            )
            return False, f"adapter_exception:{type(exc).__name__}"

        if not result.accepted:
            logger.info(
                "linear_webhook_dispatch: adapter refused session=%s reason=%s",
                session_id,
                result.reason,
            )
            emit_error(
                session_id,
                _ERROR_ADAPTER_REFUSED_TEMPLATE.format(reason=result.reason),
            )
            return False, f"adapter_refused:{result.reason}"

        # Step 6 — Link the row back to the Linear session so the
        # resume path (Phase 3, action=prompted) can find it.
        run_id = result.workspace_run_id
        if run_id:
            row = (
                db.query(AgentWorkspaceRun)
                .filter(AgentWorkspaceRun.id == run_id)
                .first()
            )
            if row is not None:
                row.linear_agent_session_id = session_id
                row.linear_webhook_delivery_id = delivery_id
                db.commit()
            else:
                logger.warning(
                    "linear_webhook_dispatch: adapter returned run_id=%s but "
                    "no such row found — link skipped",
                    run_id,
                )

        # Step 7 — Emit a confirmation thought so the Linear UI
        # shows the session as active (session-id + branch for the
        # operator to trace).
        emit_thought(
            session_id,
            _THOUGHT_DISPATCHED_TEMPLATE.format(
                provider_id=result.provider_run_id or "<unknown>",
                branch=result.agent_branch or "<unknown>",
            ),
        )

        logger.info(
            "linear_webhook_dispatch: dispatched session=%s workspace_run=%s "
            "provider=%s branch=%s",
            session_id,
            run_id,
            result.provider_run_id,
            result.agent_branch,
        )
        return True, "ok"

    except Exception as exc:  # noqa: BLE001 — final catch-all
        logger.exception(
            "linear_webhook_dispatch: unexpected failure session=%s",
            session_id,
        )
        emit_error(
            session_id,
            _ERROR_UNEXPECTED_TEMPLATE.format(exc_type=type(exc).__name__),
        )
        return False, f"exception:{type(exc).__name__}"
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Payload parsing + task-spec construction
# ─────────────────────────────────────────────────────────────────────


def _extract_payload_fields(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields Phase 2 needs from the Linear webhook body.

    Returns None if any required field is missing — callers treat
    that as "can't dispatch, nothing to log back to Linear because
    we don't even know which session."

    Shape pinned against a real 2026-11-05 delivery stored in
    `linear_webhook_deliveries` (see AFX-66 Phase 1 saved payload).
    """
    agent_session = payload.get("agentSession")
    if not isinstance(agent_session, dict):
        return None

    session_id = agent_session.get("id")
    if not isinstance(session_id, str) or not session_id:
        return None

    issue = agent_session.get("issue")
    if not isinstance(issue, dict):
        return None

    identifier = issue.get("identifier")
    if not isinstance(identifier, str) or not identifier:
        return None

    title = issue.get("title") if isinstance(issue.get("title"), str) else identifier

    # Narrative preference order:
    # 1. Linear's pre-formatted promptContext (XML-ish with issue +
    #    labels + project + blocked-by relations).
    # 2. agentActivity.content.body — the user's trigger message.
    # 3. Fallback: just the identifier + title.
    narrative_parts: list[str] = []

    activity = payload.get("agentActivity")
    user_prompt = ""
    if isinstance(activity, dict):
        content = activity.get("content")
        if isinstance(content, dict):
            body = content.get("body")
            if isinstance(body, str) and body.strip():
                user_prompt = body.strip()

    prompt_context = payload.get("promptContext")
    if isinstance(prompt_context, str) and prompt_context.strip():
        if user_prompt:
            narrative_parts.append(f"## User request\n\n{user_prompt}\n")
        narrative_parts.append(prompt_context.strip())
    elif user_prompt:
        narrative_parts.append(user_prompt)
    else:
        narrative_parts.append(f"# {identifier} — {title}")

    narrative = "\n\n".join(narrative_parts)

    return {
        "agent_session_id": session_id,
        "issue_identifier": identifier,
        "issue_title": title,
        "narrative": narrative,
    }


def _build_task_spec(
    *,
    identifier: str,
    title: str,
    narrative: str,
    now: datetime,
    slug_fn,
) -> Any:
    """Construct the `WorkspaceTaskSpec` for the resolved adapter.

    Lazy-imports WorkspaceTaskSpec so this module doesn't drag the
    full pe_workspace stack when it's only needed for parsing tests.

    **Portability note (AFX-66):** `WorkspaceTaskSpec` on `main`
    carries a `linear_issue_identifier` field that `feat/agent-fleet`
    doesn't yet have (the 2026-11-01 Linear-driven dispatch rewrite
    was promoted to main but never back-merged into feat/agent-fleet).
    We inspect the dataclass at runtime and only pass the field when
    it's actually defined. Keeps this module working on both branch
    shapes until feat/agent-fleet re-syncs.
    """
    import dataclasses

    from agentfx.agents.fleet.pe_workspace import WorkspaceTaskSpec

    task_slug = slug_fn(f"{identifier}-{title}", now=now)
    kwargs: dict[str, Any] = {
        "task_slug": task_slug,
        # Use the Linear identifier as the proposal key. That keeps
        # the audit trail obvious (the "proposal" here is Wilson's
        # delegation, not a PE-generated one).
        "pe_proposal_decision_key": f"linear:{identifier}",
        "title": f"{identifier}: {title}",
        "narrative": narrative,
        "target_files": [],  # Agent discovers these inside the sandbox.
        "new_test_paths": [],
        "estimated_profit_impact": "see Linear issue",
    }

    spec_field_names = {f.name for f in dataclasses.fields(WorkspaceTaskSpec)}
    if "linear_issue_identifier" in spec_field_names:
        kwargs["linear_issue_identifier"] = identifier

    return WorkspaceTaskSpec(**kwargs)


__all__ = [
    "dispatch_from_linear_webhook",
]
