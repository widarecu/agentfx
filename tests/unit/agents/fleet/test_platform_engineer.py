"""Unit tests for the Linear-driven PE dispatch (2026-11-01 rewrite).

Covers `platform_engineer.scan_and_dispatch` with its external
dependencies monkey-patched: Linear queries/mutations stub out via
`agentfx.agents.fleet.linear_pe_queue`, and the Managed Agents
adapter is replaced with a recording stub.

What's being pinned:

- Happy path: a Ready Linear issue → transition In Progress →
  dispatch to the stub → AgentWorkspaceRun exists with the Linear
  identifier.
- Transition-fails: next scan still has the issue in Ready, no row.
- Adapter.dispatch returns rejected → Linear rolled back to Ready.
- Adapter.dispatch creates a WORKSPACE_FAILED row → Linear rolled
  back to Ready (so the next tick retries if the retry gate allows).
- Retry gate: 2 WORKSPACE_FAILED rows with the same signature on the
  SAME Linear identifier → `file_for_attention` fires, dispatch
  skipped.
- Retry gate: 2 WORKSPACE_FAILED rows with DIFFERENT signatures →
  dispatch proceeds (new error = new 2-attempt budget).
- Daily dispatch cap: hitting `_MAX_PE_DISPATCHES_PER_DAY` stops
  further dispatches that tick.
- Narrative rendering: Money-path risk + no-description + Feature
  type all surface in the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import platform_engineer
from agentfx.agents.fleet.linear_pe_queue import ReadyIssue
from agentfx.api.core.database import Base
from agentfx.api.models.agent_workspace_run import (
    AgentWorkspaceRun,
    AgentWorkspaceRunStatus,
)

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine):
    with Session(engine) as sess:
        yield sess


def _issue(
    *,
    identifier: str = "AFX-100",
    title: str = "Fix cost model",
    description: str = "The cost model mis-prices spread on EUR_USD M1.",
    labels: list[str] | None = None,
    priority: int = 3,
) -> ReadyIssue:
    return ReadyIssue(
        id=f"uuid-{identifier.lower()}",
        identifier=identifier,
        title=title,
        description=description,
        url=f"https://linear.app/agentfx/issue/{identifier}",
        priority=priority,
        labels=labels or ["agent-filed", "Bug", "QA agent"],
        created_at="2026-11-01T10:00:00Z",
    )


@dataclass
class _StubAdapterResult:
    """Captures what scan_and_dispatch passed to the adapter and
    lets each test control what the adapter returned (so we can
    pin the rollback/retry/success branches independently of real
    SDK or DB side-effects)."""

    accepted: bool = True
    row_status: AgentWorkspaceRunStatus = AgentWorkspaceRunStatus.RUNNING
    reason: str = "dispatched"
    summary: str | None = None


class _RecordingAdapter:
    """Minimal adapter stub that writes the AgentWorkspaceRun row
    with the status the test specified, so scan_and_dispatch's
    rollback-on-WORKSPACE_FAILED path can be exercised deterministically."""

    def __init__(self, result: _StubAdapterResult):
        self.result = result
        self.calls: list[Any] = []  # type: ignore[name-defined]

    def dispatch(self, db, *, spec, now):
        from agentfx.agents.fleet.pe_workspace import WorkspaceDispatchResult

        self.calls.append({"spec": spec, "now": now})
        # Create the row the real adapters create.
        row = AgentWorkspaceRun(
            id=f"row-{len(self.calls)}",
            pe_proposal_decision_key=spec.pe_proposal_decision_key,
            linear_issue_identifier=spec.linear_issue_identifier,
            target_branch=spec.target_branch,
            agent_branch=f"agent/{spec.task_slug}",
            adapter="recording",
            status=self.result.row_status,
            summary=self.result.summary,
            created_at=now,
        )
        db.add(row)
        db.commit()
        return WorkspaceDispatchResult(
            accepted=self.result.accepted,
            workspace_run_id=row.id,
            agent_branch=row.agent_branch,
            reason=self.result.reason,
        )


# A dict of what the Linear helpers recorded; the scan_and_dispatch
# flow calls into them and we assert what landed here.
@pytest.fixture
def linear_recorder(monkeypatch):
    recorded = {
        "transitions_to_in_progress": [],
        "rollbacks_to_ready": [],
        "attention_filings": [],
    }

    def _transition(issue_id):
        recorded["transitions_to_in_progress"].append(issue_id)
        return True

    def _rollback(issue_id, *, reason_summary):
        recorded["rollbacks_to_ready"].append(
            {"issue_id": issue_id, "reason": reason_summary}
        )
        return True

    def _attention(issue_id, *, signatures):
        recorded["attention_filings"].append(
            {"issue_id": issue_id, "signatures": list(signatures)}
        )
        return True

    monkeypatch.setattr(platform_engineer, "transition_to_in_progress", _transition)
    monkeypatch.setattr(platform_engineer, "rollback_to_ready", _rollback)
    monkeypatch.setattr(platform_engineer, "file_for_attention", _attention)
    return recorded


def _patch_list_candidates(monkeypatch, issues: list[ReadyIssue]) -> None:
    monkeypatch.setattr(
        platform_engineer,
        "list_ready_dispatch_candidates",
        lambda limit: issues[:limit],
    )


def _patch_adapter(monkeypatch, adapter: _RecordingAdapter) -> None:
    import agentfx.agents.fleet.pe_workspace as pw

    monkeypatch.setattr(pw, "resolve_adapter", lambda: adapter)
    monkeypatch.setattr(pw, "sweep_stuck_runs", lambda db, now=None: 0)


# Any = used in _RecordingAdapter annotation; declared here to keep
# the typing import out of the hot path.
from typing import Any  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_ready_issue_dispatches_and_persists_row(
        self, db, monkeypatch, linear_recorder
    ):
        issue = _issue(identifier="AFX-200")
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(
            _StubAdapterResult(row_status=AgentWorkspaceRunStatus.RUNNING)
        )
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 1
        assert linear_recorder["transitions_to_in_progress"] == ["uuid-afx-200"]
        assert linear_recorder["rollbacks_to_ready"] == []
        assert linear_recorder["attention_filings"] == []

        rows = db.query(AgentWorkspaceRun).all()
        assert len(rows) == 1
        assert rows[0].linear_issue_identifier == "AFX-200"
        assert rows[0].status == AgentWorkspaceRunStatus.RUNNING

    def test_scan_returns_zero_when_no_ready_issues(
        self, db, monkeypatch, linear_recorder
    ):
        _patch_list_candidates(monkeypatch, [])
        adapter = _RecordingAdapter(_StubAdapterResult())
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0
        assert adapter.calls == []


# ─────────────────────────────────────────────────────────────────────
# Failure branches
# ─────────────────────────────────────────────────────────────────────


class TestFailureBranches:
    def test_transition_fails_skips_issue_no_row(
        self, db, monkeypatch, linear_recorder
    ):
        """If Linear's state-transition mutation reports failure
        (409, network, race), skip the issue and leave no DB row —
        the next tick picks it up fresh."""
        issue = _issue(identifier="AFX-300")
        _patch_list_candidates(monkeypatch, [issue])
        monkeypatch.setattr(
            platform_engineer, "transition_to_in_progress", lambda _id: False
        )
        adapter = _RecordingAdapter(_StubAdapterResult())
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0
        assert adapter.calls == []
        assert db.query(AgentWorkspaceRun).count() == 0

    def test_adapter_refused_rolls_back_to_ready(
        self, db, monkeypatch, linear_recorder
    ):
        """When the adapter refuses (budget, config missing), the
        Linear issue must go back to Ready so the next tick retries
        once the config is fixed."""
        issue = _issue(identifier="AFX-301")
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(
            _StubAdapterResult(accepted=False, reason="config_missing")
        )
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 1
        assert len(linear_recorder["rollbacks_to_ready"]) == 1
        assert linear_recorder["rollbacks_to_ready"][0]["issue_id"] == "uuid-afx-301"
        assert "config_missing" in linear_recorder["rollbacks_to_ready"][0]["reason"]

    def test_workspace_failed_row_rolls_back_to_ready(
        self, db, monkeypatch, linear_recorder
    ):
        """Today's live failure mode: adapter returned accepted=True
        but the row ended up WORKSPACE_FAILED (sessions.create 400
        et al). Linear must still go back to Ready."""
        issue = _issue(identifier="AFX-302")
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(
            _StubAdapterResult(
                accepted=True,
                row_status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                reason="sessions_create_failed:BadRequestError",
                summary="BadRequestError: Error code: 400 — Field required",
            )
        )
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 1
        assert len(linear_recorder["rollbacks_to_ready"]) == 1
        rb = linear_recorder["rollbacks_to_ready"][0]
        assert rb["issue_id"] == "uuid-afx-302"
        assert "Field required" in rb["reason"]


# ─────────────────────────────────────────────────────────────────────
# Retry gate
# ─────────────────────────────────────────────────────────────────────


class TestRetryGate:
    def _seed_failed(
        self, db, *, identifier: str, summary: str, minutes_ago: int
    ) -> None:
        db.add(
            AgentWorkspaceRun(
                id=f"ws-{identifier}-{minutes_ago}",
                pe_proposal_decision_key=identifier,
                linear_issue_identifier=identifier,
                target_branch="feat/agent-fleet",
                adapter="managed_agents",
                status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                summary=summary,
                created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            )
        )
        db.commit()

    def test_two_same_signature_failures_file_for_attention(
        self, db, monkeypatch, linear_recorder
    ):
        """Wilson's rule: try twice, same error → file for attention."""
        issue = _issue(identifier="AFX-400")
        self._seed_failed(
            db,
            identifier="AFX-400",
            summary="ManagedAgentsAdapter: sessions.create failed — BadRequestError",
            minutes_ago=30,
        )
        self._seed_failed(
            db,
            identifier="AFX-400",
            summary="ManagedAgentsAdapter: sessions.create failed — BadRequestError",
            minutes_ago=15,
        )
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(_StubAdapterResult())
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0, "retry gate must stop the dispatch"
        assert adapter.calls == []
        assert linear_recorder["transitions_to_in_progress"] == []
        assert len(linear_recorder["attention_filings"]) == 1
        filing = linear_recorder["attention_filings"][0]
        assert filing["issue_id"] == "uuid-afx-400"
        assert len(filing["signatures"]) == 2

    def test_two_different_signature_failures_dispatch_proceeds(
        self, db, monkeypatch, linear_recorder
    ):
        """A NEW failure signature isn't the same error — it starts
        its own 2-attempt budget."""
        issue = _issue(identifier="AFX-401")
        self._seed_failed(
            db,
            identifier="AFX-401",
            summary="ManagedAgentsAdapter: sessions.create failed — BadRequestError",
            minutes_ago=30,
        )
        self._seed_failed(
            db,
            identifier="AFX-401",
            summary="ManagedAgentsAdapter: events.send failed — ConnectionError",
            minutes_ago=15,
        )
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(
            _StubAdapterResult(row_status=AgentWorkspaceRunStatus.RUNNING)
        )
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 1, (
            "a different-signature failure history must not block retry"
        )
        assert linear_recorder["attention_filings"] == []

    def test_single_prior_failure_still_dispatches(
        self, db, monkeypatch, linear_recorder
    ):
        """< 2 failures = always retry; this is just the first retry."""
        issue = _issue(identifier="AFX-402")
        self._seed_failed(
            db,
            identifier="AFX-402",
            summary="ManagedAgentsAdapter: sessions.create failed — BadRequestError",
            minutes_ago=15,
        )
        _patch_list_candidates(monkeypatch, [issue])
        adapter = _RecordingAdapter(
            _StubAdapterResult(row_status=AgentWorkspaceRunStatus.RUNNING)
        )
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 1


# ─────────────────────────────────────────────────────────────────────
# Daily cap
# ─────────────────────────────────────────────────────────────────────


class TestDailyDispatchCap:
    def test_cap_prevents_further_dispatches_this_tick(
        self, db, monkeypatch, linear_recorder
    ):
        """Fill the daily cap with pre-existing rows → scan sees 0 budget."""
        now = datetime.now(UTC)
        for i in range(platform_engineer._MAX_PE_DISPATCHES_PER_DAY):
            db.add(
                AgentWorkspaceRun(
                    id=f"cap-filler-{i}",
                    pe_proposal_decision_key=f"AFX-999{i}",
                    linear_issue_identifier=f"AFX-999{i}",
                    target_branch="feat/agent-fleet",
                    adapter="managed_agents",
                    status=AgentWorkspaceRunStatus.PR_OPEN,
                    created_at=now,
                )
            )
        db.commit()

        _patch_list_candidates(monkeypatch, [_issue()])
        adapter = _RecordingAdapter(_StubAdapterResult())
        _patch_adapter(monkeypatch, adapter)

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0
        assert adapter.calls == []


# ─────────────────────────────────────────────────────────────────────
# Prompt rendering
# ─────────────────────────────────────────────────────────────────────


class TestDispatchNarrative:
    def test_includes_identifier_title_and_url(self):
        iss = _issue(
            identifier="AFX-500",
            title="Fix cost model",
            description="The cost model is wrong.",
        )
        narrative = platform_engineer._render_dispatch_narrative(issue=iss)
        assert "AFX-500: Fix cost model" in narrative
        assert "The cost model is wrong." in narrative
        assert iss.url in narrative

    def test_calls_out_money_path_risk(self):
        iss = _issue(labels=["agent-filed", "Bug", "Money-path"])
        narrative = platform_engineer._render_dispatch_narrative(issue=iss)
        assert "Money-path" in narrative
        assert "needs-wilson" in narrative

    def test_notes_missing_description(self):
        iss = _issue(description="")
        narrative = platform_engineer._render_dispatch_narrative(issue=iss)
        assert "no description" in narrative.lower()
        assert "escape hatch" in narrative.lower()

    def test_always_mentions_needs_design_escape_hatch(self):
        iss = _issue(labels=["agent-filed", "Feature"])
        narrative = platform_engineer._render_dispatch_narrative(issue=iss)
        assert "[NEEDS DESIGN]" in narrative
        assert "ambiguous" in narrative.lower()

    def test_branch_gate_is_in_the_prompt(self):
        iss = _issue()
        narrative = platform_engineer._render_dispatch_narrative(issue=iss)
        assert "Never push to `main`" in narrative
        assert "feat/agent-fleet" in narrative
