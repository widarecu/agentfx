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

    def test_webhook_trigger_skips_count_cap(self, db, monkeypatch):
        """User-initiated (Linear delegation) dispatches ignore the
        daily count cap. The cap bounds autonomous scan-loop churn;
        a human click doesn't belong under that budget."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 1)
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
        # scan trigger (default) should still be blocked.
        ok_scan, _ = pe_workspace.check_budget_before_dispatch(db, now=now)
        assert ok_scan is False
        # webhook trigger bypasses the count cap.
        ok_webhook, reason = pe_workspace.check_budget_before_dispatch(
            db, now=now, trigger="webhook"
        )
        assert ok_webhook is True
        assert reason == ""

    def test_webhook_trigger_still_honors_usd_cap(self, db, monkeypatch):
        """The USD cap is the real $ guardrail — webhook dispatches
        must NOT bypass it, or a runaway click flood could drain
        budget silently."""
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
        ok, reason = pe_workspace.check_budget_before_dispatch(
            db, now=now, trigger="webhook"
        )
        assert ok is False
        assert "daily_usd_cap_hit" in reason


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
        # Without `AGENT_PE_WORKSPACE_*` IDs set, the adapter gracefully
        # degrades to WORKSPACE_FAILED with an operator-readable summary
        # instead of calling the SDK.
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "missing managed-agents config" in (row.summary or "")

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

    def test_flag_on_backend_devin_returns_devin(self, monkeypatch):
        """AFX-79: `AGENT_PE_BACKEND=devin` routes to DevinWorkspaceAdapter."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "devin")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.DevinWorkspaceAdapter
        )

    def test_flag_on_backend_managed_agents_still_default(self, monkeypatch):
        """Default AGENT_PE_BACKEND=managed_agents preserves pre-AFX-79
        behaviour. No surprise switches."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "managed_agents")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.ManagedAgentsAdapter
        )

    def test_unknown_backend_falls_back_to_managed_agents(self, monkeypatch):
        """Unknown backend value logs a warning and falls back, never
        silently switches to stub (that would hide a config typo)."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "cursor")
        assert isinstance(
            pe_workspace.resolve_adapter(), pe_workspace.ManagedAgentsAdapter
        )

    def test_route_label_overrides_default_backend(self, monkeypatch):
        """Per-label routing beats the global default. Spec carrying a
        routed label picks the mapped backend."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "managed_agents")
        monkeypatch.setattr(
            pe_workspace.settings,
            "AGENT_PE_BACKEND_ROUTE",
            "UX:devin, Portal:devin",
        )
        spec = _spec()
        spec.labels = ["UX", "Validator"]  # noqa: attribute-defined-outside-init
        assert isinstance(
            pe_workspace.resolve_adapter(spec=spec),
            pe_workspace.DevinWorkspaceAdapter,
        )

    def test_route_falls_through_on_no_label_match(self, monkeypatch):
        """Spec whose labels don't match any route entry uses the
        global default backend."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "managed_agents")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND_ROUTE", "UX:devin")
        spec = _spec()
        spec.labels = ["Validator", "Money-path"]  # noqa
        assert isinstance(
            pe_workspace.resolve_adapter(spec=spec),
            pe_workspace.ManagedAgentsAdapter,
        )

    def test_malformed_route_entries_are_tolerated(self, monkeypatch):
        """Routing config tolerates whitespace, trailing commas, and
        no-colon entries (logged + skipped) without raising."""
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "managed_agents")
        monkeypatch.setattr(
            pe_workspace.settings,
            "AGENT_PE_BACKEND_ROUTE",
            "  UX:devin ,bad_entry_no_colon, ,:missing_label,  ",
        )
        spec = _spec()
        spec.labels = ["UX"]  # noqa
        # UX:devin still works; other malformed entries skipped.
        assert isinstance(
            pe_workspace.resolve_adapter(spec=spec),
            pe_workspace.DevinWorkspaceAdapter,
        )


# ─────────────────────────────────────────────────────────────────────
# DevinWorkspaceAdapter — AFX-79
# ─────────────────────────────────────────────────────────────────────


class TestDevinAdapterDispatch:
    """Covers the HTTP contract against devin.ai/v3 — all requests
    patched, no network. Pins:
    - missing credentials → WORKSPACE_FAILED row + accepted=True
      (scan-loop don't-retry semantic)
    - happy path → RUNNING row + provider_run_id populated
    - HTTP error → WORKSPACE_FAILED row carrying body snippet
    - request exception → WORKSPACE_FAILED row
    - missing session id in payload → WORKSPACE_FAILED row
    - API key never appears in persisted summaries or log messages
    """

    def _enable(self, monkeypatch, *, api_key="cog_test", org_id="org_123"):
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "ghp_x")
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "devin")
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", api_key)
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_ORG_ID", org_id)
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_MAX_ACU_PER_SESSION", 5)
        # Raise dispatch caps so the budget check doesn't short-circuit.
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 50)

    def test_missing_api_key_fails_row_accepted_true(self, db, monkeypatch):
        self._enable(monkeypatch, api_key="")
        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True, "scan-loop semantic preserved"
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "DEVIN_API_KEY" in (row.summary or "")

    def test_missing_org_id_fails_row_accepted_true(self, db, monkeypatch):
        self._enable(monkeypatch, org_id="")
        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "DEVIN_ORG_ID" in (row.summary or "")

    def test_happy_path_records_session_id_and_flips_running(self, db, monkeypatch):
        self._enable(monkeypatch)

        class _Resp:
            status_code = 201
            text = ""

            def json(self_inner):
                return {"session_id": "sesn_dev_test_01", "status_enum": "working"}

        captured: dict = {}

        def _mock_post(url, *, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp()

        monkeypatch.setattr(pe_workspace.requests, "post", _mock_post)

        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())

        assert result.accepted is True
        assert result.provider_run_id == "sesn_dev_test_01"
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.RUNNING
        assert row.provider_run_id == "sesn_dev_test_01"
        assert row.adapter == "devin"

        # URL pins org id + v3 endpoint shape.
        assert captured["url"] == (
            "https://api.devin.ai/v3/organizations/org_123/sessions"
        )
        # Auth header carries the key (verify, then make sure it
        # doesn't leak into persisted fields below).
        assert captured["headers"]["Authorization"] == "Bearer cog_test"
        # max_acu honored.
        assert captured["json"]["limits"]["max_acu_limit"] == 5
        # Prompt carries target branch + repo reference + no secrets.
        prompt = captured["json"]["prompt"]
        assert "feat/agent-fleet" in prompt
        assert "@widarecu/agentfx" in prompt
        assert "cog_test" not in prompt  # never bake the secret

        # Credential MUST NOT appear in the DB row's summary.
        assert "cog_test" not in (row.summary or "")

    def test_http_error_body_persisted_without_secret_leak(self, db, monkeypatch):
        self._enable(monkeypatch)

        class _Resp:
            status_code = 403
            text = '{"error": "forbidden: scope insufficient"}'

            def json(self_inner):
                raise AssertionError("don't parse non-2xx body as JSON")

        monkeypatch.setattr(pe_workspace.requests, "post", lambda *a, **k: _Resp())

        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "HTTP 403" in (row.summary or "")
        assert "scope insufficient" in (row.summary or "")
        assert "cog_test" not in (row.summary or "")

    def test_request_exception_handled(self, db, monkeypatch):
        self._enable(monkeypatch)

        def _boom(*a, **k):
            raise pe_workspace.requests.ConnectionError("devin down")

        monkeypatch.setattr(pe_workspace.requests, "post", _boom)

        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "ConnectionError" in (row.summary or "")

    def test_response_missing_session_id_fails(self, db, monkeypatch):
        self._enable(monkeypatch)

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"status_enum": "working"}  # no session_id

        monkeypatch.setattr(pe_workspace.requests, "post", lambda *a, **k: _Resp())

        adapter = pe_workspace.DevinWorkspaceAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        row = (
            db.query(AgentWorkspaceRun)
            .filter(AgentWorkspaceRun.id == result.workspace_run_id)
            .one()
        )
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "no session id" in (row.summary or "").lower()


class TestDevinPollRow:
    """Covers `_poll_devin_row` state transitions. HTTP patched."""

    def _row(self, *, session_id="sesn_poll_01"):
        return AgentWorkspaceRun(
            id=f"wsrun-{session_id}",
            pe_proposal_decision_key="k",
            adapter="devin",
            status=AgentWorkspaceRunStatus.RUNNING,
            provider_run_id=session_id,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

    def test_still_running_leaves_row_alone(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "cog_x")

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"status_enum": "working"}

        monkeypatch.setattr(pe_workspace.requests, "get", lambda *a, **k: _Resp())
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "still_running"
        assert row.status == AgentWorkspaceRunStatus.RUNNING

    def test_finished_with_pr_url_flips_pr_open(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "cog_x")
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_ACU_USD_RATE", 2.5)

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {
                    "status_enum": "finished",
                    "pull_request": {
                        "url": "https://github.com/widarecu/agentfx/pull/999"
                    },
                    "acu_consumed": 2,
                }

        monkeypatch.setattr(pe_workspace.requests, "get", lambda *a, **k: _Resp())
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "pr_open"
        assert row.status == AgentWorkspaceRunStatus.PR_OPEN
        assert row.pr_url == "https://github.com/widarecu/agentfx/pull/999"
        assert row.pr_number == 999
        assert row.llm_usd == 5.0  # 2 ACU × $2.50

    def test_finished_without_pr_flips_failed(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "cog_x")

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"status_enum": "finished", "pull_request": None}

        monkeypatch.setattr(pe_workspace.requests, "get", lambda *a, **k: _Resp())
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "failed"
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED

    def test_404_marks_row_failed(self, monkeypatch):
        """Devin dropped the session (TTL / wrong org id) — don't poll
        this row forever, mark it failed so the sweeper + budget can
        reconcile."""
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "cog_x")

        class _Resp:
            status_code = 404
            text = ""

            def json(self_inner):
                return {}

        monkeypatch.setattr(pe_workspace.requests, "get", lambda *a, **k: _Resp())
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "failed"
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "404" in (row.summary or "")

    def test_transient_http_error_leaves_row_alone(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "cog_x")

        class _Resp:
            status_code = 500
            text = "oops"

            def json(self_inner):
                raise ValueError()

        monkeypatch.setattr(pe_workspace.requests, "get", lambda *a, **k: _Resp())
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "poll_error"
        assert row.status == AgentWorkspaceRunStatus.RUNNING

    def test_missing_api_key_returns_poll_error(self, monkeypatch):
        """No credential → can't poll. Row untouched; stuck-sweeper
        will reclaim on TTL if credentials don't come back."""
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "")
        row = self._row()
        outcome = pe_workspace._poll_devin_row(row, now=datetime.now(UTC))
        assert outcome == "poll_error"
        assert row.status == AgentWorkspaceRunStatus.RUNNING


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


# ─────────────────────────────────────────────────────────────────────
# ManagedAgentsAdapter — real SDK path (v0.8.1 C.2 Phase 2b)
# ─────────────────────────────────────────────────────────────────────


class TestManagedAgentsAdapterSDK:
    """Covers the real `sessions.create` + `events.send` path. The
    SDK itself is patched — these are unit tests for the dispatch
    glue, not integration tests against Anthropic."""

    def _configure_settings(self, monkeypatch):
        monkeypatch.setattr(
            pe_workspace.settings, "AGENT_PE_WORKSPACE_AGENT_ID", "agent_abc"
        )
        monkeypatch.setattr(
            pe_workspace.settings,
            "AGENT_PE_WORKSPACE_ENVIRONMENT_ID",
            "env_xyz",
        )
        monkeypatch.setattr(
            pe_workspace.settings, "AGENT_PE_WORKSPACE_VAULT_ID", "vault_123"
        )

    def test_missing_agent_id_degrades_without_sdk_call(self, db, monkeypatch):
        """Any missing ID → row persisted as WORKSPACE_FAILED with an
        operator-readable summary. No SDK call is made."""
        self._configure_settings(monkeypatch)
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_AGENT_ID", "")
        adapter = pe_workspace.ManagedAgentsAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True  # Row persisted
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "AGENT_PE_WORKSPACE_AGENT_ID" in (row.summary or "")
        assert row.provider_run_id is None  # No session created

    def test_happy_path_creates_session_and_sends_message(self, db, monkeypatch):
        from unittest.mock import MagicMock

        self._configure_settings(monkeypatch)

        session = MagicMock()
        session.id = "sess_opus5_xyz"
        fake_client = MagicMock()
        fake_client.beta.sessions.create.return_value = session

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        adapter = pe_workspace.ManagedAgentsAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        assert result.provider_run_id == "sess_opus5_xyz"

        # SDK was called with the expected args
        fake_client.beta.sessions.create.assert_called_once()
        kwargs = fake_client.beta.sessions.create.call_args.kwargs
        assert kwargs["agent"] == {"id": "agent_abc"}
        assert kwargs["environment_id"] == "env_xyz"
        assert kwargs["vault_ids"] == ["vault_123"]
        assert kwargs["title"] == "2026-10-15-fix-order-dedup"

        fake_client.beta.sessions.events.send.assert_called_once()
        events_kwargs = fake_client.beta.sessions.events.send.call_args.kwargs
        assert events_kwargs["session_id"] == "sess_opus5_xyz"
        assert events_kwargs["events"][0]["type"] == "user_message"

        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.RUNNING
        assert row.provider_run_id == "sess_opus5_xyz"
        assert row.started_at is not None

    def test_sessions_create_exception_marks_failed(self, db, monkeypatch):
        from unittest.mock import MagicMock

        self._configure_settings(monkeypatch)
        fake_client = MagicMock()
        fake_client.beta.sessions.create.side_effect = RuntimeError("503")

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        adapter = pe_workspace.ManagedAgentsAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        assert "sessions_create_failed" in result.reason

        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert row.provider_run_id is None
        assert "sessions.create failed" in (row.summary or "")

    def test_events_send_exception_preserves_session_id(self, db, monkeypatch):
        """If the kickoff message fails, the row keeps the session id
        so the operator can inspect the orphan in the console."""
        from unittest.mock import MagicMock

        self._configure_settings(monkeypatch)
        session = MagicMock()
        session.id = "sess_orphan"
        fake_client = MagicMock()
        fake_client.beta.sessions.create.return_value = session
        fake_client.beta.sessions.events.send.side_effect = RuntimeError("network blip")

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        adapter = pe_workspace.ManagedAgentsAdapter()
        result = adapter.dispatch(db, spec=_spec())
        assert result.accepted is True
        assert result.provider_run_id == "sess_orphan"

        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert row.provider_run_id == "sess_orphan"
        assert "events.send failed" in (row.summary or "")


# ─────────────────────────────────────────────────────────────────────
# poll_running_dispatches
# ─────────────────────────────────────────────────────────────────────


class TestPollRunningDispatches:
    """Status polling — the loop that transitions RUNNING to a
    terminal state once the Managed Agents session wraps up."""

    def _seed_running(
        self, db, *, session_id="sess_abc", status_at=None
    ) -> AgentWorkspaceRun:
        from datetime import datetime as _dt

        row = AgentWorkspaceRun(
            id="ws_running",
            pe_proposal_decision_key="pe_proposal_xyz",
            adapter="managed_agents",
            status=AgentWorkspaceRunStatus.RUNNING,
            provider_run_id=session_id,
            started_at=status_at or _dt.now(UTC),
            created_at=status_at or _dt.now(UTC),
        )
        db.add(row)
        db.commit()
        return row

    def test_terminated_with_pr_url_transitions_to_pr_open(self, db, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        self._seed_running(db)

        session_resp = SimpleNamespace(status="terminated")
        events_resp = SimpleNamespace(
            data=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            text="PR opened: https://github.com/widarecu/agentfx/pull/42"
                        )
                    ]
                )
            ]
        )
        fake_client = MagicMock()
        fake_client.beta.sessions.retrieve.return_value = session_resp
        fake_client.beta.sessions.events.list.return_value = events_resp

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["checked"] == 1
        assert counts["pr_open"] == 1

        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.PR_OPEN
        assert row.pr_url == "https://github.com/widarecu/agentfx/pull/42"
        assert row.pr_number == 42

    def test_still_running_leaves_row_untouched(self, db, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        self._seed_running(db)

        fake_client = MagicMock()
        fake_client.beta.sessions.retrieve.return_value = SimpleNamespace(
            status="running"
        )

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["checked"] == 1
        assert counts["pr_open"] == 0

        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.RUNNING

    def test_terminated_without_pr_or_tests_marks_failed(self, db, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        self._seed_running(db)

        fake_client = MagicMock()
        fake_client.beta.sessions.retrieve.return_value = SimpleNamespace(
            status="terminated"
        )
        fake_client.beta.sessions.events.list.return_value = SimpleNamespace(data=[])

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["failed"] == 1
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED

    def test_retrieve_exception_leaves_row_untouched(self, db, monkeypatch):
        from unittest.mock import MagicMock

        self._seed_running(db)

        fake_client = MagicMock()
        fake_client.beta.sessions.retrieve.side_effect = RuntimeError("blip")

        import anthropic as _anth

        monkeypatch.setattr(_anth, "Anthropic", lambda **_kw: fake_client, raising=True)

        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["checked"] == 1
        row = db.query(AgentWorkspaceRun).one()
        # Transient retrieve failure → leave as RUNNING for next tick.
        assert row.status == AgentWorkspaceRunStatus.RUNNING


class TestClassifySessionOutcome:
    def test_detects_github_pr_url(self):
        s, url = pe_workspace._classify_session_outcome(
            "created branch\nPR: https://github.com/widarecu/agentfx/pull/99 ready"
        )
        assert s == AgentWorkspaceRunStatus.PR_OPEN
        assert url == "https://github.com/widarecu/agentfx/pull/99"

    def test_last_pr_url_wins(self):
        s, url = pe_workspace._classify_session_outcome(
            "https://github.com/a/b/pull/1\nredirect\nhttps://github.com/a/b/pull/2"
        )
        assert s == AgentWorkspaceRunStatus.PR_OPEN
        assert url.endswith("/pull/2")

    def test_tests_failed_signal(self):
        s, url = pe_workspace._classify_session_outcome(
            "ran make test-unit — pytest exit code 1\nstopping"
        )
        assert s == AgentWorkspaceRunStatus.TESTS_FAILED
        assert url is None

    def test_unknown_falls_through_to_failed(self):
        s, url = pe_workspace._classify_session_outcome("some random tail text")
        assert s == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert url is None
