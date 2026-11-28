"""AFX-182: a hosted PE session's outcome is decided from its own events.

Event factories mirror the shapes in the installed SDK
(`anthropic/types/beta/sessions/`): `session.status_idle` with a
`stop_reason`, `session.error` with a typed `error` carrying a
`retry_status`, plus running / terminated status events and
`agent.message`. Events are built as plain objects and as dicts, since the
production image may run a different SDK version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import linear_pe_queue, pe_workspace
from agentfx.agents.fleet.pe_workspace import SessionOutcomeKind as K
from agentfx.api.core.database import Base
from agentfx.api.models import AgentWorkspaceRun, AgentWorkspaceRunStatus

pytestmark = pytest.mark.unit

PR = "https://github.com/widarecu/agentfx/pull/321"


# ── event factories (SDK shapes) ────────────────────────────────────


def idle(stop: str = "end_turn"):
    return SimpleNamespace(
        type="session.status_idle", stop_reason=SimpleNamespace(type=stop)
    )


def running():
    return SimpleNamespace(type="session.status_running")


def rescheduled():
    return SimpleNamespace(type="session.status_rescheduled")


def terminated():
    return SimpleNamespace(type="session.status_terminated")


def error(kind: str = "billing_error", retry: str = "terminal", message="boom"):
    return SimpleNamespace(
        type="session.error",
        error=SimpleNamespace(
            type=kind, message=message, retry_status=SimpleNamespace(type=retry)
        ),
    )


def said(text: str):
    return SimpleNamespace(
        type="agent.message", content=[SimpleNamespace(type="text", text=text)]
    )


# ── the pure function ───────────────────────────────────────────────


class TestDecideSessionOutcome:
    def test_idle_end_turn_without_pr_is_completed(self):
        out = pe_workspace.decide_session_outcome(
            [running(), said("Already fixed on main."), idle("end_turn")], "idle"
        )
        assert out.kind == K.COMPLETED_NO_PR
        assert out.final_message == "Already fixed on main."

    def test_pr_link_is_pr_opened_even_when_idle(self):
        out = pe_workspace.decide_session_outcome(
            [running(), said(f"Opened {PR}"), idle("end_turn")], "idle"
        )
        assert out.kind == K.PR_OPENED
        assert out.pr_url == PR

    @pytest.mark.parametrize("retry", ["terminal", "exhausted"])
    def test_billing_error_final_is_failed(self, retry):
        out = pe_workspace.decide_session_outcome(
            [running(), error("billing_error", retry, "spend limit"), idle("end_turn")],
            "idle",
        )
        assert out.kind == K.FAILED
        assert out.error_type == "billing_error"
        assert "spend limit" in out.reason

    def test_error_still_retrying_is_working(self):
        out = pe_workspace.decide_session_outcome(
            [running(), error("model_overloaded_error", "retrying")], "running"
        )
        assert out.kind == K.WORKING

    def test_retrying_error_with_idle_status_is_not_a_failure(self):
        out = pe_workspace.decide_session_outcome(
            [running(), error("model_overloaded_error", "retrying")], "idle"
        )
        assert out.kind == K.WORKING

    def test_requires_action_is_waiting(self):
        out = pe_workspace.decide_session_outcome(
            [running(), idle("requires_action")], "idle"
        )
        assert out.kind == K.WAITING

    def test_idle_retries_exhausted_is_failed(self):
        out = pe_workspace.decide_session_outcome(
            [running(), idle("retries_exhausted")], "idle"
        )
        assert out.kind == K.FAILED
        assert out.error_type == "retries_exhausted"

    def test_idle_then_running_again_is_working(self):
        out = pe_workspace.decide_session_outcome(
            [running(), idle("end_turn"), running()], "running"
        )
        assert out.kind == K.WORKING
        # Even if the live status read lags the event list.
        out = pe_workspace.decide_session_outcome(
            [running(), idle("end_turn"), running()], "idle"
        )
        assert out.kind == K.WORKING

    def test_error_then_running_again_clears_the_error(self):
        out = pe_workspace.decide_session_outcome(
            [error("billing_error", "exhausted"), idle("end_turn"), running()],
            "idle",
        )
        assert out.kind == K.WORKING

    def test_rescheduled_after_idle_is_working(self):
        out = pe_workspace.decide_session_outcome(
            [idle("end_turn"), rescheduled()], "idle"
        )
        assert out.kind == K.WORKING

    def test_live_running_status_wins_without_events(self):
        assert pe_workspace.decide_session_outcome([], "running").kind == K.WORKING
        assert pe_workspace.decide_session_outcome([], "rescheduling").kind == K.WORKING

    def test_no_events_idle_is_never_success(self):
        assert pe_workspace.decide_session_outcome([], "idle").kind == K.WORKING

    def test_terminated_without_error_defers_to_text_classification(self):
        out = pe_workspace.decide_session_outcome(
            [running(), terminated()], "terminated"
        )
        assert out.kind == K.TERMINATED
        assert (
            pe_workspace.decide_session_outcome([], "terminated").kind == K.TERMINATED
        )

    def test_terminated_with_error_is_failed(self):
        out = pe_workspace.decide_session_outcome(
            [running(), error("billing_error", "terminal"), terminated()], "terminated"
        )
        assert out.kind == K.FAILED

    def test_unknown_event_type_is_ignored(self):
        out = pe_workspace.decide_session_outcome(
            [running(), SimpleNamespace(type="session.something_new"), idle()], "idle"
        )
        assert out.kind == K.COMPLETED_NO_PR

    def test_unknown_error_type_final_is_failed_unknown(self):
        out = pe_workspace.decide_session_outcome(
            [error("brand_new_error", "exhausted", "odd")], "idle"
        )
        assert out.kind == K.FAILED
        assert out.error_type == "brand_new_error"

    def test_error_without_type_is_failed_unknown_error(self):
        ev = {"type": "session.error", "error": {"retry_status": {"type": "terminal"}}}
        out = pe_workspace.decide_session_outcome([ev], "idle")
        assert out.kind == K.FAILED
        assert out.error_type == "unknown_error"

    def test_unknown_retry_status_or_stop_reason_is_not_final(self):
        assert (
            pe_workspace.decide_session_outcome(
                [error("billing_error", "someday")], "idle"
            ).kind
            == K.WORKING
        )
        assert (
            pe_workspace.decide_session_outcome([idle("brand_new_stop")], "idle").kind
            == K.WORKING
        )

    def test_dict_shaped_events_are_read(self):
        events = [
            {"type": "session.status_running"},
            {
                "type": "agent.message",
                "content": [{"type": "text", "text": "Nothing to change."}],
            },
            {"type": "session.status_idle", "stop_reason": {"type": "end_turn"}},
        ]
        out = pe_workspace.decide_session_outcome(events, "idle")
        assert out.kind == K.COMPLETED_NO_PR
        assert out.final_message == "Nothing to change."

    def test_malformed_events_do_not_crash(self):
        out = pe_workspace.decide_session_outcome(
            [None, "x", 3, {"type": None}, SimpleNamespace()], "idle"
        )
        assert out.kind == K.WORKING


# ── the poll ────────────────────────────────────────────────────────


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess
    Base.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    pe_workspace.reset_devin_auth_state()
    monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "", raising=False)
    yield
    pe_workspace.reset_devin_auth_state()


def _seed(db, *, row_id="r1", started=None, adapter="managed_agents", **kw):
    started = started or datetime.now(UTC) - timedelta(minutes=7)
    row = AgentWorkspaceRun(
        id=row_id,
        pe_proposal_decision_key="AFX-111",
        linear_issue_identifier="AFX-111",
        adapter=adapter,
        status=AgentWorkspaceRunStatus.RUNNING,
        provider_run_id=f"sesn_{row_id}",
        started_at=started,
        created_at=started,
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def _client(monkeypatch, *, status, events):
    """A fake Anthropic client. `events` are given oldest first; the fake
    returns them newest first, as `order="desc"` does on the real API."""
    client = MagicMock()
    client.beta.sessions.retrieve.return_value = SimpleNamespace(
        status=status, usage=None
    )
    client.beta.sessions.events.list.return_value = SimpleNamespace(
        data=list(reversed(events))
    )
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: client, raising=True)
    return client


def _linear(monkeypatch):
    calls = []

    def fake(identifier, *, comment_body, escalate):
        calls.append({"id": identifier, "body": comment_body, "escalate": escalate})
        return True

    monkeypatch.setattr(linear_pe_queue, "handle_stuck_session_failure", fake)
    return calls


class TestPollClosesOnFinalOutcome:
    def test_end_turn_without_pr_closes_on_first_poll(self, db, monkeypatch):
        _seed(db)
        calls = _linear(monkeypatch)
        client = _client(
            monkeypatch,
            status="idle",
            events=[
                running(),
                said("Already fixed on main in #210. No code change needed."),
                idle("end_turn"),
            ],
        )
        counts = pe_workspace.poll_running_dispatches(db)

        assert counts["completed_no_pr"] == 1 and counts["failed"] == 0
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert row.ended_at is not None
        assert row.summary.startswith(pe_workspace.OUTCOME_COMPLETED_NO_PR)
        assert linear_pe_queue.stuck_session_signature(row.summary) == ""
        client.beta.sessions.archive.assert_called_once_with(session_id="sesn_r1")
        client.beta.sessions.events.send.assert_not_called()  # no interrupt
        assert len(calls) == 1
        body = calls[0]["body"]
        assert "Already fixed on main" in body
        assert "exceeded 4h" not in body and "reclaimed" not in body
        assert "needs-wilson" in body and "In Progress" in body
        assert calls[0]["escalate"] is False

    def test_long_final_message_is_trimmed_and_redacted(self, db, monkeypatch):
        _seed(db)
        calls = _linear(monkeypatch)
        secret = "ghp_" + "a" * 36
        _client(
            monkeypatch,
            status="idle",
            events=[said(f"{secret} " + "x" * 5000), idle()],
        )
        pe_workspace.poll_running_dispatches(db)
        body = calls[0]["body"]
        assert secret not in body and "[trimmed]" in body and len(body) < 2500
        assert secret not in db.query(AgentWorkspaceRun).one().summary

    @pytest.mark.parametrize("retry", ["terminal", "exhausted"])
    def test_billing_error_closes_failed_with_billing_wording(
        self, db, monkeypatch, retry
    ):
        _seed(db)
        calls = _linear(monkeypatch)
        client = _client(
            monkeypatch,
            status="idle",
            events=[running(), error("billing_error", retry, "out of credits"), idle()],
        )
        counts = pe_workspace.poll_running_dispatches(db)

        assert counts["failed"] == 1
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert row.summary.startswith(pe_workspace.OUTCOME_FAILED_BILLING)
        assert linear_pe_queue.stuck_session_signature(row.summary) == ""
        client.beta.sessions.archive.assert_called_once()
        body = calls[0]["body"]
        assert "out of credit or has hit its spend limit" in body
        assert "paused" in body and "exceeded 4h" not in body

    def test_other_error_fails_without_billing_pause(self, db, monkeypatch):
        _seed(db)
        calls = _linear(monkeypatch)
        _client(
            monkeypatch,
            status="idle",
            events=[error("mcp_connection_failed_error", "exhausted", "no route")],
        )
        pe_workspace.poll_running_dispatches(db)
        assert "mcp_connection_failed_error" in calls[0]["body"]
        assert "paused" not in calls[0]["body"]
        assert pe_workspace.billing_pause_until(db) is None

    @pytest.mark.parametrize(
        "events,status",
        [
            ([running(), error("model_overloaded_error", "retrying")], "running"),
            ([running(), error("model_overloaded_error", "retrying")], "idle"),
            ([running(), idle("requires_action")], "idle"),
            ([running(), idle("end_turn"), running()], "running"),
            ([running(), idle("end_turn"), running()], "idle"),
            ([running(), SimpleNamespace(type="session.future_event")], "idle"),
        ],
    )
    def test_run_stays_open(self, db, monkeypatch, events, status):
        _seed(db)
        calls = _linear(monkeypatch)
        client = _client(monkeypatch, status=status, events=events)
        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["failed"] == counts["completed_no_pr"] == counts["pr_open"] == 0
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.RUNNING
        assert row.ended_at is None and calls == []
        client.beta.sessions.archive.assert_not_called()

    def test_idle_session_that_opened_a_pr_is_pr_open(self, db, monkeypatch):
        _seed(db)
        calls = _linear(monkeypatch)
        client = _client(
            monkeypatch,
            status="idle",
            events=[running(), said(f"PR opened: {PR}"), idle("end_turn")],
        )
        counts = pe_workspace.poll_running_dispatches(db)
        assert counts["pr_open"] == 1
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.PR_OPEN
        assert row.pr_url == PR and row.pr_number == 321
        assert calls == []  # the PR is the report; no needs-wilson comment
        client.beta.sessions.archive.assert_called_once()

    def test_events_are_requested_newest_first(self, db, monkeypatch):
        _seed(db)
        _linear(monkeypatch)
        client = _client(monkeypatch, status="idle", events=[idle()])
        pe_workspace.poll_running_dispatches(db)
        kwargs = client.beta.sessions.events.list.call_args.kwargs
        assert kwargs["order"] == "desc"

    def test_events_list_failure_leaves_row_open(self, db, monkeypatch):
        _seed(db)
        client = _client(monkeypatch, status="idle", events=[])
        client.beta.sessions.events.list.side_effect = RuntimeError("blip")
        pe_workspace.poll_running_dispatches(db)
        assert db.query(AgentWorkspaceRun).one().status == (
            AgentWorkspaceRunStatus.RUNNING
        )

    def test_publish_failed_final_message_keeps_its_own_reclaim_path(
        self, db, monkeypatch
    ):
        _seed(db)
        calls = _linear(monkeypatch)
        _client(
            monkeypatch,
            status="idle",
            events=[said("diff...\nPUBLISH FAILED AFTER FALLBACKS"), idle()],
        )
        pe_workspace.poll_running_dispatches(db)
        row = db.query(AgentWorkspaceRun).one()
        assert "[publish-failed]" in row.summary
        assert "publish failed after fallbacks" in calls[0]["body"]

    def test_sweeper_still_reclaims_a_session_that_never_stops(self, db, monkeypatch):
        _seed(db, started=datetime.now(UTC) - timedelta(hours=5))
        calls = _linear(monkeypatch)
        _client(monkeypatch, status="running", events=[running()])
        pe_workspace.poll_running_dispatches(db)
        assert db.query(AgentWorkspaceRun).one().status == (
            AgentWorkspaceRunStatus.RUNNING
        )
        assert pe_workspace.sweep_stuck_runs(db) == 1
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "exceeded 4h TTL" in row.summary
        assert "exceeded 4h" in calls[0]["body"]


# ── the billing pause ───────────────────────────────────────────────


def _finished(db, *, row_id, ended, status, summary, adapter="managed_agents"):
    row = AgentWorkspaceRun(
        id=row_id,
        pe_proposal_decision_key=row_id,
        linear_issue_identifier="AFX-115",
        adapter=adapter,
        status=status,
        summary=summary,
        started_at=ended - timedelta(minutes=5),
        created_at=ended - timedelta(minutes=5),
        ended_at=ended,
    )
    db.add(row)
    db.commit()
    return row


BILLING = f"{pe_workspace.OUTCOME_FAILED_BILLING}: billing_error: out of credits"
NOON = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


class TestBillingPause:
    def test_billing_failure_refuses_scan_and_webhook_dispatch(self, db):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        later = NOON + timedelta(hours=3)
        for trigger in ("scan", "webhook"):
            ok, reason = pe_workspace.check_budget_before_dispatch(
                db, now=later, trigger=trigger
            )
            assert not ok
            assert reason.startswith("anthropic_billing_paused")
            assert "out of credit or has hit its spend limit" in reason

    def test_adapter_dispatch_is_refused_with_the_reason(self, db):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        result = pe_workspace.ManagedAgentsAdapter().dispatch(
            db,
            spec=pe_workspace.WorkspaceTaskSpec(
                task_slug="s",
                pe_proposal_decision_key="AFX-9",
                linear_issue_identifier="AFX-9",
                title="t",
                narrative="n",
                target_files=[],
                new_test_paths=[],
                estimated_profit_impact="",
                trigger="webhook",
            ),
            now=NOON + timedelta(hours=1),
        )
        assert not result.accepted
        assert result.reason.startswith("anthropic_billing_paused")
        assert db.query(AgentWorkspaceRun).count() == 1  # no new row

    def test_pause_expires_at_the_next_utc_midnight(self, db):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        before = datetime(2026, 11, 28, 23, 59, tzinfo=UTC)
        after = datetime(2026, 11, 29, 0, 1, tzinfo=UTC)
        assert pe_workspace.billing_pause_until(db, now=before) == datetime(
            2026, 11, 29, tzinfo=UTC
        )
        assert pe_workspace.billing_pause_until(db, now=after) is None
        ok, _ = pe_workspace.check_budget_before_dispatch(db, now=after)
        assert ok

    @pytest.mark.parametrize(
        "status,summary",
        [
            (AgentWorkspaceRunStatus.PR_OPEN, "[poll] PR opened"),
            (
                AgentWorkspaceRunStatus.WORKSPACE_FAILED,
                f"{pe_workspace.OUTCOME_COMPLETED_NO_PR}\nnothing to change",
            ),
        ],
    )
    def test_a_later_success_ends_the_pause(self, db, monkeypatch, status, summary):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        _finished(
            db,
            row_id="ok",
            ended=NOON + timedelta(hours=1),
            status=status,
            summary=summary,
        )
        monkeypatch.setattr(
            pe_workspace.settings, "AGENT_PE_WORKSPACE_MAX_PER_DAY", 10, raising=False
        )
        ok, _ = pe_workspace.check_budget_before_dispatch(
            db, now=NOON + timedelta(hours=2)
        )
        assert ok

    def test_other_failures_neither_start_nor_end_the_pause(self, db):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        _finished(
            db,
            row_id="f2",
            ended=NOON + timedelta(hours=1),
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=f"{pe_workspace.OUTCOME_FAILED} model_overloaded_error: x",
        )
        assert (
            pe_workspace.billing_pause_until(db, now=NOON + timedelta(hours=2))
            is not None
        )

    def test_no_billing_failure_means_no_pause(self, db):
        _finished(
            db,
            row_id="f1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary="ManagedAgentsAdapter: sessions.create failed",
        )
        assert pe_workspace.billing_pause_until(db, now=NOON) is None

    def test_pause_is_derived_from_rows_so_it_survives_a_restart(self, db):
        # Nothing is held in memory: a fresh process reading the same
        # rows reaches the same answer.
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        pe_workspace.reset_devin_auth_state()
        assert (
            pe_workspace.billing_pause_until(db, now=NOON + timedelta(hours=1))
            is not None
        )

    def test_devin_dispatch_is_not_held_by_an_anthropic_billing_pause(
        self, db, monkeypatch
    ):
        _finished(
            db,
            row_id="b1",
            ended=NOON,
            status=AgentWorkspaceRunStatus.WORKSPACE_FAILED,
            summary=BILLING,
        )
        seen = {}

        def spy(db_, *, now=None, trigger="scan", anthropic_billing_pause=True):
            seen["flag"] = anthropic_billing_pause
            return False, "stop-here"

        monkeypatch.setattr(pe_workspace, "check_budget_before_dispatch", spy)
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_API_KEY", "", raising=False)
        monkeypatch.setattr(pe_workspace.settings, "DEVIN_ORG_ID", "", raising=False)
        result = pe_workspace.DevinWorkspaceAdapter().dispatch(
            db,
            spec=pe_workspace.WorkspaceTaskSpec(
                task_slug="s",
                pe_proposal_decision_key="AFX-9",
                linear_issue_identifier="AFX-9",
                title="t",
                narrative="n",
                target_files=[],
                new_test_paths=[],
                estimated_profit_impact="",
            ),
            now=NOON,
        )
        assert result.reason == "stop-here" and seen["flag"] is False

    def test_a_run_closed_on_billing_starts_the_pause_end_to_end(self, db, monkeypatch):
        _seed(db)
        _linear(monkeypatch)
        _client(monkeypatch, status="idle", events=[error("billing_error"), idle()])
        pe_workspace.poll_running_dispatches(db)
        ok, reason = pe_workspace.check_budget_before_dispatch(db, trigger="webhook")
        assert not ok and reason.startswith("anthropic_billing_paused")


class TestRetryGateUnaffected:
    def test_completed_without_pr_summary_has_no_stuck_signature(self):
        s = f"{pe_workspace.OUTCOME_COMPLETED_NO_PR}\n[cost-cap] exceeded 5 tokens"
        assert linear_pe_queue.stuck_session_signature(s) == ""
