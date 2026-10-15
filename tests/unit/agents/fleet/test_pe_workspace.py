"""Unit tests for `agents.fleet.pe_workspace`.

v0.8.1 C.2 Phase 2 — covers:
- Daily dispatch cap enforcement (count-based).
- Daily USD cap enforcement.
- StubWorkspaceAdapter records intent, marks WORKSPACE_FAILED,
  returns accepted=True so Phase 2 orchestration is exercised.
- ManagedAgentsAdapter without SDK wire-up falls through to stub
  behaviour (no provider call made).
- resolve_adapter respects flag + GITHUB_TOKEN.
- sweep_stuck_runs reclaims old RUNNING rows.
- Task slug is deterministic + branch-safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import pe_workspace
from agentfx.api.core.database import Base
from agentfx.api.models import AgentWorkspaceRun, AgentWorkspaceRunStatus

pytestmark = pytest.mark.unit


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


def _spec(**overrides) -> pe_workspace.WorkspaceTaskSpec:
    defaults = dict(
        task_slug="2026-10-15-fix-order-dedup",
        pe_proposal_decision_key="pe_proposal_qa123_abcdef",
        title="Order dedup idempotency guard",
        narrative="# Task: ...",
        target_files=["src/execution/order_manager.py"],
        new_test_paths=["tests/unit/execution/test_order_manager.py"],
        estimated_profit_impact="Avoid $1,087/wk duplicate-order loss",
    )
    defaults.update(overrides)
    return pe_workspace.WorkspaceTaskSpec(**defaults)


# ─────────────────────────────────────────────────────────────────────
# Budget / cap
# ─────────────────────────────────────────────────────────────────────


class TestBudget:
    def test_zero_rows_passes_check(self, db):
        ok, reason = pe_workspace.check_budget_before_dispatch(db)
        assert ok is True
        assert reason == ""

    def test_dispatch_count_cap(self, db, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 1)
        # Seed one dispatch today.
        now = datetime.now(UTC)
        db.add(
            AgentWorkspaceRun(
                id="r1",
                pe_proposal_decision_key="k1",
                adapter="stub",
                status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                created_at=now,
            )
        )
        db.commit()
        ok, reason = pe_workspace.check_budget_before_dispatch(db, now=now)
        assert ok is False
        assert "daily_dispatch_cap_hit" in reason

    def test_dispatch_usd_cap(self, db, monkeypatch):
        monkeypatch.setattr(
            pe_workspace.settings, "AGENT_PE_WORKSPACE_DAILY_USD_CAP", 1.00
        )
        now = datetime.now(UTC)
        db.add(
            AgentWorkspaceRun(
                id="r1",
                pe_proposal_decision_key="k1",
                adapter="managed_agents",
                status=AgentWorkspaceRunStatus.PR_OPEN,
                container_usd=0.75,
                llm_usd=0.50,  # 1.25 total → over 1.00 cap
                created_at=now,
            )
        )
        db.commit()
        ok, reason = pe_workspace.check_budget_before_dispatch(db, now=now)
        assert ok is False
        assert "daily_usd_cap_hit" in reason

    def test_yesterday_counts_dont_block_today(self, db, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 1)
        yesterday = datetime.now(UTC) - timedelta(days=2)
        db.add(
            AgentWorkspaceRun(
                id="r_old",
                pe_proposal_decision_key="k_old",
                adapter="stub",
                status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                created_at=yesterday,
            )
        )
        db.commit()
        ok, _ = pe_workspace.check_budget_before_dispatch(db)
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# Task slug
# ─────────────────────────────────────────────────────────────────────


class TestTaskSlug:
    def test_slug_is_deterministic(self):
        now = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
        a = pe_workspace.build_task_slug("Fix Cost Model EUR_USD M1", now=now)
        b = pe_workspace.build_task_slug("Fix Cost Model EUR_USD M1", now=now)
        assert a == b
        assert a.startswith("2026-10-15-")

    def test_slug_safe_for_git_branch(self):
        slug = pe_workspace.build_task_slug(
            "Fix cost-model: EUR/USD @ M1 (0 TP exits!)",
            now=datetime(2026, 10, 15, tzinfo=UTC),
        )
        # Must be lowercase + only [a-z0-9-]
        assert slug.islower()
        import re

        assert re.fullmatch(r"[a-z0-9-]+", slug), slug

    def test_slug_fallback_on_empty_title(self):
        slug = pe_workspace.build_task_slug("", now=datetime(2026, 10, 15, tzinfo=UTC))
        assert slug.endswith("-pe-task")


# ─────────────────────────────────────────────────────────────────────
# StubWorkspaceAdapter
# ─────────────────────────────────────────────────────────────────────


class TestStubAdapter:
    def test_stub_records_intent_and_marks_failed(self, db):
        adapter = pe_workspace.StubWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        assert result.workspace_run_id is not None
        assert result.agent_branch == "agent/2026-10-15-fix-order-dedup"

        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.adapter == "stub"
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "stub adapter" in (row.summary or "")
        assert row.pr_url is None

    def test_stub_rejects_when_cap_hit(self, db, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 0)
        adapter = pe_workspace.StubWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is False
        assert "daily_dispatch_cap_hit" in result.reason
        # No row should have been persisted.
        assert db.query(AgentWorkspaceRun).count() == 0


# ─────────────────────────────────────────────────────────────────────
# ManagedAgentsAdapter (pre-SDK scaffold)
# ─────────────────────────────────────────────────────────────────────


class TestManagedAgentsAdapterScaffold:
    def test_dispatch_persists_row_and_marks_failed(self, db):
        adapter = pe_workspace.ManagedAgentsAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.adapter == "managed_agents"
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "SDK wire-up pending" in (row.summary or "")

    def test_dispatch_respects_budget(self, db, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 0)
        result = pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=_spec())
        assert result.accepted is False


# ─────────────────────────────────────────────────────────────────────
# resolve_adapter
# ─────────────────────────────────────────────────────────────────────


class TestResolveAdapter:
    def test_flag_off_returns_stub(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", False)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_123")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.StubWorkspaceAdapter
        )

    def test_flag_on_without_pat_returns_stub(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.StubWorkspaceAdapter
        )

    def test_flag_on_with_pat_returns_managed(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.ManagedAgentsAdapter
        )


# ─────────────────────────────────────────────────────────────────────
# sweep_stuck_runs
# ─────────────────────────────────────────────────────────────────────


class TestSweepStuckRuns:
    def test_old_running_rows_are_reclaimed(self, db):
        old = datetime.now(UTC) - timedelta(hours=5)
        db.add(
            AgentWorkspaceRun(
                id="stuck",
                pe_proposal_decision_key="k_stuck",
                adapter="managed_agents",
                status=AgentWorkspaceRunStatus.RUNNING,
                started_at=old,
                created_at=old,
            )
        )
        db.commit()
        reclaimed = pe_workspace.sweep_stuck_runs(db)
        assert reclaimed == 1
        row = db.query(AgentWorkspaceRun).filter(AgentWorkspaceRun.id == "stuck").one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "exceeded 4h TTL" in (row.summary or "")

    def test_fresh_running_rows_are_left_alone(self, db):
        fresh = datetime.now(UTC) - timedelta(minutes=10)
        db.add(
            AgentWorkspaceRun(
                id="active",
                pe_proposal_decision_key="k_active",
                adapter="managed_agents",
                status=AgentWorkspaceRunStatus.RUNNING,
                started_at=fresh,
                created_at=fresh,
            )
        )
        db.commit()
        reclaimed = pe_workspace.sweep_stuck_runs(db)
        assert reclaimed == 0
        row = db.query(AgentWorkspaceRun).filter(AgentWorkspaceRun.id == "active").one()
        assert row.status == AgentWorkspaceRunStatus.RUNNING


# ─────────────────────────────────────────────────────────────────────
# platform_engineer.scan_and_dispatch — orchestration integration
# ─────────────────────────────────────────────────────────────────────


class TestScanAndDispatch:
    """Exercises the Phase-2 orchestration against a real in-memory DB.

    The actual Opus call inside `scan_and_propose` is patched to a
    no-op so we isolate the dispatch logic from the LLM path (Phase 1
    tests already cover `scan_and_propose`)."""

    def _seed_pe_proposal(
        self,
        db,
        *,
        decision_id: str,
        decision_key: str,
        confidence: str,
        target_file: str = "src/execution/order_manager.py",
    ):
        from agentfx.api.models.agent_decision import AgentDecision

        now = datetime.now(UTC)
        db.add(
            AgentDecision(
                id=decision_id,
                agent_role="PE",
                action="proposed_fix",
                decision_key=decision_key,
                inputs_json={
                    "qa_decision_id": "qa_abc",
                    "work_order_title": "Order dedup idempotency",
                    "target_file": target_file,
                    "file_was_readable": True,
                },
                outputs_json={
                    "proposal": {
                        "investigation_summary": "Found race in order submit.",
                        "proposed_changes": [
                            {
                                "file_path": target_file,
                                "change_type": "modify",
                                "rationale": "Add idempotency key.",
                                "diff": "+ idempotency_key = ...",
                            }
                        ],
                        "new_tests": [
                            {
                                "file_path": "tests/unit/execution/test_dedup.py",
                                "name": "test_dedup_blocks_duplicate_signals",
                                "purpose": "Catch the race.",
                            }
                        ],
                        "risk_assessment": "Low.",
                        "estimated_profit_impact": "$1,087/wk saved.",
                        "confidence": confidence,
                    }
                },
                created_at=now,
            )
        )
        db.commit()

    def test_high_confidence_dispatches_through_stub(self, db, monkeypatch):
        from agentfx.agents.fleet import platform_engineer

        # Short-circuit scan_and_propose — we only care about dispatch.
        monkeypatch.setattr(platform_engineer, "scan_and_propose", lambda **_: 0)
        self._seed_pe_proposal(
            db,
            decision_id="dec_high",
            decision_key="pe_proposal_qa_abc_111111",
            confidence="high",
        )

        proposals, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert proposals == 0  # scan patched to no-op
        assert attempted == 1

        rows = db.query(AgentWorkspaceRun).all()
        assert len(rows) == 1
        assert rows[0].pe_proposal_decision_key == "pe_proposal_qa_abc_111111"
        assert rows[0].agent_branch.startswith("agent/")

    def test_medium_confidence_is_skipped(self, db, monkeypatch):
        from agentfx.agents.fleet import platform_engineer

        monkeypatch.setattr(platform_engineer, "scan_and_propose", lambda **_: 0)
        self._seed_pe_proposal(
            db,
            decision_id="dec_med",
            decision_key="pe_proposal_qa_abc_222222",
            confidence="medium",
        )

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0
        assert db.query(AgentWorkspaceRun).count() == 0

    def test_already_dispatched_proposal_is_skipped(self, db, monkeypatch):
        from agentfx.agents.fleet import platform_engineer

        monkeypatch.setattr(platform_engineer, "scan_and_propose", lambda **_: 0)
        key = "pe_proposal_qa_abc_333333"
        self._seed_pe_proposal(
            db, decision_id="dec_dup", decision_key=key, confidence="high"
        )
        # Pre-existing workspace row for this proposal.
        db.add(
            AgentWorkspaceRun(
                id="ws_existing",
                pe_proposal_decision_key=key,
                adapter="stub",
                status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()

        _, attempted = platform_engineer.scan_and_dispatch(db=db)
        assert attempted == 0
        # Still only the one pre-existing row.
        assert db.query(AgentWorkspaceRun).count() == 1


class TestRenderDispatchNarrative:
    """The workspace prompt — must call out the branch gate explicitly
    + include the test the workspace needs to add."""

    def test_narrative_includes_branch_gate(self):
        from agentfx.agents.fleet.platform_engineer import (
            _render_dispatch_narrative,
        )

        md = _render_dispatch_narrative(
            proposal_dict={
                "investigation_summary": "Problem.",
                "proposed_changes": [],
                "new_tests": [],
                "risk_assessment": "",
                "estimated_profit_impact": "",
            },
            work_order={"title": "Fix thing", "target_file": "x.py"},
        )
        assert "feat/agent-fleet" in md
        assert "NEVER push to `main`" in md
        assert "# Task: Fix thing" in md

    def test_narrative_includes_proposed_changes_and_tests(self):
        from agentfx.agents.fleet.platform_engineer import (
            _render_dispatch_narrative,
        )

        md = _render_dispatch_narrative(
            proposal_dict={
                "investigation_summary": "",
                "proposed_changes": [
                    {
                        "file_path": "src/foo.py",
                        "change_type": "modify",
                        "rationale": "Add guard",
                        "diff": "- old\n+ new",
                    }
                ],
                "new_tests": [
                    {
                        "file_path": "tests/unit/test_foo.py",
                        "name": "test_guard_blocks_bad_input",
                        "purpose": "Covers the regression.",
                    }
                ],
                "risk_assessment": "",
                "estimated_profit_impact": "",
            },
            work_order={"title": "T", "target_file": ""},
        )
        assert "src/foo.py" in md
        assert "- old\n+ new" in md
        assert "test_guard_blocks_bad_input" in md
