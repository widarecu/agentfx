"""Unit tests for `agents.fleet.linear_webhook_dispatch` (AFX-66 Phase 2).

Covers the orchestrator that converts a Linear webhook payload into a
Managed Agents dispatch. Linear API calls (emit_thought, emit_error)
and the `WorkspaceAdapter.dispatch` call are mocked; the real DB
session is replaced with a MagicMock so each test is pure in-process.

Pins the sequence:
- parse payload → emit "picking up" → budget check → adapter.dispatch
  → link row back → emit "dispatched"

Failure branches pinned:
- payload missing required fields → no Linear call attempted
- duplicate (row already carries this linear_agent_session_id) →
  emit_error with duplicate template, no adapter call
- budget denied → emit_error with budget template, no adapter call
- adapter refused → emit_error with refusal template, row flipped
- adapter raised → emit_error unexpected, exception caught
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentfx.agents.fleet import linear_webhook_dispatch as lwd
from agentfx.agents.fleet.pe_workspace import (
    WorkspaceDispatchResult,
    WorkspaceTaskSpec,
)
from agentfx.api.models.agent_workspace_run import AgentWorkspaceRun

pytestmark = pytest.mark.unit


_SESSION_ID = "3ed78027-6d3c-4727-ba12-a47367e7563f"
_DELIVERY_ID = "92819383-f209-465f-85e0-d7baf87caddb"
_NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


def _real_payload() -> dict:
    """Shape confirmed against 2026-11-05 staging delivery for AFX-8.
    Truncated to the fields `_extract_payload_fields` actually reads."""
    return {
        "type": "AgentSessionEvent",
        "action": "created",
        "agentSession": {
            "id": _SESSION_ID,
            "issue": {
                "id": "aa95bec4-a213-458d-aa5e-9c41cdb8d782",
                "identifier": "AFX-8",
                "title": "QA tech-health reports OANDA pool health",
            },
        },
        "agentActivity": {
            "content": {
                "type": "prompt",
                "body": "Work on [AFX-8](https://linear.app/agentfx/issue/AFX-8/...)",
            },
        },
        "promptContext": (
            '<issue identifier="AFX-8">\n<title>QA tech-health ...</title>\n</issue>'
        ),
    }


def _patch_bg(
    monkeypatch,
    *,
    thought_result=(True, ""),
    error_result=(True, ""),
    sweep_return=0,
    budget_result=(True, ""),
    adapter_result=None,
    adapter_raises=None,
    existing_row=None,
    linked_row=None,
):
    """Wire up every external dependency as a MagicMock so the test
    only exercises the orchestrator's branching. Returns the mocks
    so individual tests can inspect call counts / args."""
    mock_thought = MagicMock(return_value=thought_result)
    mock_error = MagicMock(return_value=error_result)
    mock_sweep = MagicMock(return_value=sweep_return)
    mock_budget = MagicMock(return_value=budget_result)
    mock_build_slug = MagicMock(return_value="2026-11-06-afx-8-qa-tech-health")

    mock_adapter = MagicMock()
    if adapter_raises is not None:
        mock_adapter.dispatch.side_effect = adapter_raises
    else:
        mock_adapter.dispatch.return_value = adapter_result or (
            WorkspaceDispatchResult(
                accepted=True,
                workspace_run_id="run_wsp_abc123" + ("0" * 20),  # 36 chars
                provider_run_id="sesn_01HPZTEST",
                agent_branch="agent/2026-11-06-afx-8-qa-tech-health",
                reason="",
            )
        )
    mock_resolve = MagicMock(return_value=mock_adapter)

    # DB session: query().filter().first() returns existing_row for
    # the idempotency lookup; the second query (for the row-link step)
    # returns linked_row.
    mock_db = MagicMock()
    query_first_calls = [existing_row, linked_row]
    mock_filter = MagicMock()
    mock_filter.first.side_effect = query_first_calls
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_filter
    mock_db.query.return_value = mock_query

    monkeypatch.setattr(lwd, "emit_thought", mock_thought)
    monkeypatch.setattr(lwd, "emit_error", mock_error)
    monkeypatch.setattr(lwd, "SessionLocal", lambda: mock_db)

    # Patch the lazy imports in `dispatch_from_linear_webhook`.
    import agentfx.agents.fleet.pe_workspace as pe_ws

    monkeypatch.setattr(pe_ws, "sweep_stuck_runs", mock_sweep)
    monkeypatch.setattr(pe_ws, "check_budget_before_dispatch", mock_budget)
    monkeypatch.setattr(pe_ws, "resolve_adapter", mock_resolve)
    monkeypatch.setattr(pe_ws, "build_task_slug", mock_build_slug)

    return {
        "db": mock_db,
        "thought": mock_thought,
        "error": mock_error,
        "sweep": mock_sweep,
        "budget": mock_budget,
        "adapter": mock_adapter,
        "resolve": mock_resolve,
        "build_slug": mock_build_slug,
    }


# ─────────────────────────────────────────────────────────────────────
# Payload parsing
# ─────────────────────────────────────────────────────────────────────


class TestExtractPayloadFields:
    def test_real_payload_shape_extracts_cleanly(self):
        got = lwd._extract_payload_fields(_real_payload())
        assert got is not None
        assert got["agent_session_id"] == _SESSION_ID
        assert got["issue_identifier"] == "AFX-8"
        assert got["issue_title"] == "QA tech-health reports OANDA pool health"
        # Narrative should combine user prompt + promptContext
        assert "User request" in got["narrative"]
        assert "Work on [AFX-8]" in got["narrative"]
        assert '<issue identifier="AFX-8">' in got["narrative"]

    def test_missing_agent_session_returns_none(self):
        assert lwd._extract_payload_fields({"type": "AgentSessionEvent"}) is None

    def test_missing_session_id_returns_none(self):
        assert lwd._extract_payload_fields({"agentSession": {"issue": {}}}) is None

    def test_missing_issue_returns_none(self):
        assert (
            lwd._extract_payload_fields({"agentSession": {"id": _SESSION_ID}}) is None
        )

    def test_missing_identifier_returns_none(self):
        assert (
            lwd._extract_payload_fields(
                {
                    "agentSession": {
                        "id": _SESSION_ID,
                        "issue": {"title": "no identifier"},
                    }
                }
            )
            is None
        )

    def test_fallback_narrative_when_no_prompt_or_context(self):
        payload = {
            "agentSession": {
                "id": _SESSION_ID,
                "issue": {"identifier": "AFX-99", "title": "Bare minimum"},
            },
        }
        got = lwd._extract_payload_fields(payload)
        assert got["narrative"] == "# AFX-99 — Bare minimum"

    def test_user_prompt_only_when_no_context(self):
        payload = {
            "agentSession": {
                "id": _SESSION_ID,
                "issue": {"identifier": "AFX-99", "title": "T"},
            },
            "agentActivity": {"content": {"body": "just do the thing"}},
        }
        got = lwd._extract_payload_fields(payload)
        assert got["narrative"] == "just do the thing"


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_full_dispatch_flow(self, monkeypatch):
        """End-to-end: parse → thought → budget → adapter → link row
        → confirmation thought."""
        linked = MagicMock(spec=AgentWorkspaceRun)
        mocks = _patch_bg(monkeypatch, linked_row=linked)

        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )

        assert dispatched is True
        assert reason == "ok"

        # Thought emitted twice: picking up + dispatched
        assert mocks["thought"].call_count == 2
        first = mocks["thought"].call_args_list[0]
        assert first.args[0] == _SESSION_ID
        assert "Picking up AFX-8" in first.args[1]

        # Budget checked once with the webhook trigger (the whole point
        # of this call site: a human click shouldn't be gated by the
        # autonomous scan loop's per-day count cap).
        mocks["budget"].assert_called_once()
        assert mocks["budget"].call_args.kwargs.get("trigger") == "webhook"
        mocks["adapter"].dispatch.assert_called_once()

        # Spec passed to adapter carries the Linear-prefixed decision key
        # and trigger="webhook" so the adapter's own budget re-check also
        # skips the count cap.
        spec = mocks["adapter"].dispatch.call_args.kwargs["spec"]
        assert isinstance(spec, WorkspaceTaskSpec)
        assert spec.pe_proposal_decision_key == "linear:AFX-8"
        assert spec.title.startswith("AFX-8:")
        assert "User request" in spec.narrative
        assert getattr(spec, "trigger", "scan") == "webhook"

        # Row linked: linear_agent_session_id + linear_webhook_delivery_id set
        assert linked.linear_agent_session_id == _SESSION_ID
        assert linked.linear_webhook_delivery_id == _DELIVERY_ID
        mocks["db"].commit.assert_called()

        # No error emitted on happy path
        mocks["error"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Failure branches
# ─────────────────────────────────────────────────────────────────────


class TestFailureBranches:
    def test_payload_missing_fields_short_circuits(self, monkeypatch):
        """Can't dispatch, can't tell Linear (no session id) — just return."""
        mocks = _patch_bg(monkeypatch)
        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload={"type": "AgentSessionEvent"},
            now=_NOW,
        )
        assert dispatched is False
        assert reason == "payload_missing_required_fields"
        mocks["thought"].assert_not_called()
        mocks["error"].assert_not_called()
        mocks["adapter"].dispatch.assert_not_called()

    def test_duplicate_session_emits_error_and_skips_dispatch(self, monkeypatch):
        existing = MagicMock(spec=AgentWorkspaceRun)
        existing.id = "run_already_dispatched_012345678901234567"
        mocks = _patch_bg(monkeypatch, existing_row=existing)

        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )
        assert dispatched is False
        assert reason.startswith("already_dispatched:")
        mocks["adapter"].dispatch.assert_not_called()
        mocks["error"].assert_called_once()
        err_body = mocks["error"].call_args.args[1]
        assert "Already dispatched" in err_body

    def test_budget_denied_emits_error_and_skips_dispatch(self, monkeypatch):
        mocks = _patch_bg(
            monkeypatch, budget_result=(False, "daily_dispatch_cap_hit:2/2")
        )
        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )
        assert dispatched is False
        assert reason == "budget:daily_dispatch_cap_hit:2/2"
        mocks["adapter"].dispatch.assert_not_called()
        err_body = mocks["error"].call_args.args[1]
        assert "Daily dispatch cap reached" in err_body
        assert "daily_dispatch_cap_hit:2/2" in err_body

    def test_adapter_refused_emits_error_with_reason(self, monkeypatch):
        mocks = _patch_bg(
            monkeypatch,
            adapter_result=WorkspaceDispatchResult(
                accepted=False, reason="sandbox_not_configured"
            ),
        )
        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )
        assert dispatched is False
        assert reason == "adapter_refused:sandbox_not_configured"
        err_body = mocks["error"].call_args.args[1]
        assert "refused" in err_body.lower()
        assert "sandbox_not_configured" in err_body

    def test_adapter_raises_is_caught_and_errored(self, monkeypatch):
        mocks = _patch_bg(
            monkeypatch, adapter_raises=RuntimeError("provider went poof")
        )
        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )
        assert dispatched is False
        assert reason == "adapter_exception:RuntimeError"
        err_body = mocks["error"].call_args.args[1]
        assert "RuntimeError" in err_body


# ─────────────────────────────────────────────────────────────────────
# Resilience: Linear outage mid-flow doesn't break dispatch
# ─────────────────────────────────────────────────────────────────────


class TestLinearOutageTolerance:
    def test_picking_up_thought_failure_does_not_block_dispatch(self, monkeypatch):
        """If emit_thought fails (Linear outage, token trouble, etc.)
        the dispatch still proceeds. The agent session may look
        unresponsive in Linear's UI but the work still happens."""
        linked = MagicMock(spec=AgentWorkspaceRun)
        mocks = _patch_bg(
            monkeypatch,
            thought_result=(False, "token:no_credentials"),
            linked_row=linked,
        )
        dispatched, reason = lwd.dispatch_from_linear_webhook(
            delivery_id=_DELIVERY_ID,
            payload=_real_payload(),
            now=_NOW,
        )
        assert dispatched is True
        assert reason == "ok"
        mocks["adapter"].dispatch.assert_called_once()
