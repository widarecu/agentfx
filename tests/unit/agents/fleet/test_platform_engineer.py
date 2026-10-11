"""Unit tests for `agents.fleet.platform_engineer`.

v0.8.1 Phase C.2 Phase 1 — PE as a diff-proposing analyst. Tests
cover:
- Work-order dedup via `decision_key` so the same QA report
  processed twice doesn't produce duplicate proposals.
- Daily rate limit (max 3 proposals/day) enforced via decision log.
- `_read_file_safely` path-escape guard (no `../../etc/passwd`).
- Never-raises contract — scan loop can't crash the orchestrator.
- Fallback body shape when LLM is unreachable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentfx.agents.fleet import platform_engineer as pe

pytestmark = pytest.mark.unit


class TestWorkOrderHash:
    """Hash must be stable across runs so dedup works."""

    def test_stable_for_same_title_and_target(self):
        wo = {"title": "Add idempotency", "target_file": "src/order.py"}
        assert pe._work_order_hash(wo) == pe._work_order_hash(wo)

    def test_differs_for_different_title(self):
        a = {"title": "Add idempotency", "target_file": "src/order.py"}
        b = {"title": "Different", "target_file": "src/order.py"}
        assert pe._work_order_hash(a) != pe._work_order_hash(b)

    def test_proposal_key_includes_qa_id(self):
        wo = {"title": "x", "target_file": "y"}
        k1 = pe._proposal_key_for("qa_abc", wo)
        k2 = pe._proposal_key_for("qa_xyz", wo)
        assert k1 != k2
        assert k1.startswith("pe_proposal_qa_abc_")


class TestReadFileSafely:
    """Read must bound I/O and refuse to escape the repo root."""

    def test_empty_path_returns_empty(self):
        assert pe._read_file_safely("") == ""

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTFX_REPO_ROOT", str(tmp_path))
        assert pe._read_file_safely("does/not/exist.py") == ""

    def test_reads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTFX_REPO_ROOT", str(tmp_path))
        f = tmp_path / "src" / "x.py"
        f.parent.mkdir(parents=True)
        f.write_text("def foo():\n    return 1\n")
        content = pe._read_file_safely("src/x.py")
        assert "def foo():" in content

    def test_truncates_long_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTFX_REPO_ROOT", str(tmp_path))
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line_{i}" for i in range(5000)))
        content = pe._read_file_safely("big.py")
        assert "truncated at 2000 lines" in content

    def test_refuses_path_escape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTFX_REPO_ROOT", str(tmp_path))
        # Attempt to escape the repo root via `..`.
        assert pe._read_file_safely("../../etc/passwd") == ""


class TestFallback:
    def test_fallback_marks_confidence_low(self):
        wo = {
            "title": "x",
            "target_file": "y",
            "expected_profit_impact": "savings",
        }
        p = pe._deterministic_fallback(wo)
        assert p.confidence == "low"
        assert p.proposed_changes == []
        assert "LLM analysis unavailable" in p.investigation_summary
        assert p.estimated_profit_impact == "savings"


class TestScanDedup:
    """Candidate scan must skip work orders that already have a
    PE proposal row — otherwise every daily scan re-proposes the
    same fixes."""

    def _db(self, qa_rows, pe_rows):
        """Build a stub DB. `qa_rows` is the QA decision list, `pe_rows`
        the existing PE proposals (keyed by `decision_key`)."""
        db = MagicMock()
        qa_q = MagicMock()
        qa_q.all.return_value = qa_rows
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value = qa_q

        pe_q = MagicMock()
        pe_q.all.return_value = pe_rows
        # Second db.query() call for PE rows — the test sets a
        # side_effect so distinct queries return the right thing.
        return db, qa_q, pe_q

    def test_skips_already_proposed_work_orders(self):
        qa_row = MagicMock(
            id="qa_123",
            outputs_json={
                "report": {
                    "work_orders": [
                        {"title": "Fix A", "target_file": "a.py"},
                        {"title": "Fix B", "target_file": "b.py"},
                    ]
                }
            },
        )
        # One of them has already been proposed; the other hasn't.
        already_key = pe._proposal_key_for(
            "qa_123", {"title": "Fix A", "target_file": "a.py"}
        )
        pe_row = MagicMock(decision_key=already_key)

        db = MagicMock()
        # Simulate two chained queries — QA rows then PE rows. Use
        # side_effect on query() so each distinct call gets its own
        # mock chain.
        qa_chain = MagicMock()
        qa_chain.all.return_value = [qa_row]
        pe_chain = MagicMock()
        pe_chain.all.return_value = [pe_row]

        # The order of query calls in `_collect_candidate_work_orders`:
        # 1. QA decisions (filtered on role + action + date)
        # 2. PE proposals (filtered on role + action + date)
        qa_mock = MagicMock()
        qa_mock.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value = qa_chain

        pe_mock = MagicMock()
        pe_mock.filter.return_value.filter.return_value.filter.return_value = pe_chain

        db.query.side_effect = [qa_mock, pe_mock]

        results = pe._collect_candidate_work_orders(db)
        titles = {wo.get("title") for _, wo in results}
        assert titles == {"Fix B"}


class TestScanNeverRaises:
    def test_candidate_scan_exception_returns_zero(self):
        db = MagicMock()
        with patch.object(
            pe, "_collect_candidate_work_orders", side_effect=RuntimeError("boom")
        ):
            assert pe.scan_and_propose(db=db) == 0

    def test_proposal_failure_doesnt_stop_subsequent(self):
        db = MagicMock()
        # Rate-limit count: 0.
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.count.return_value = 0
        with patch.object(
            pe,
            "_collect_candidate_work_orders",
            return_value=[
                ("qa1", {"title": "A"}),
                ("qa2", {"title": "B"}),
                ("qa3", {"title": "C"}),
            ],
        ):
            with patch.object(
                pe,
                "_generate_proposal",
                side_effect=[RuntimeError("x"), None, RuntimeError("z")],
            ):
                # Must not raise. One succeeded in the middle.
                produced = pe.scan_and_propose(db=db)
        assert produced == 1


class TestEmailProposal:
    """`_email_proposal` must never raise. Covers both the happy
    path (HTML render + send) and the failure path (import or send
    raises)."""

    def test_sends_tagged_email_with_html(self):
        wo = {"title": "Fix dup", "target_file": "src/x.py"}
        proposal = pe.PEProposal(
            investigation_summary="x",
            confidence="high",
        )
        with (
            patch(
                "agentfx.notifications.email.render_pe_proposal_html",
                return_value="<html>...</html>",
            ) as render,
            patch("agentfx.notifications.email.send_agent_message") as send,
        ):
            pe._email_proposal(work_order=wo, proposal=proposal, body="plain body")
        render.assert_called_once()
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["agent_role"] == "PE"
        assert "confidence=high" in kwargs["subject"]
        assert "Fix dup" in kwargs["subject"]

    def test_send_failure_does_not_raise(self):
        wo = {"title": "x", "target_file": "y"}
        proposal = pe.PEProposal(investigation_summary="x", confidence="medium")
        with patch(
            "agentfx.notifications.email.send_agent_message",
            side_effect=RuntimeError("smtp dead"),
        ):
            # Must not raise — proposal already in DB.
            pe._email_proposal(work_order=wo, proposal=proposal, body="x")


class TestPromptShape:
    """Prompt builder packs the work order + file excerpt into a
    JSON blob the LLM can parse. Covers the fallback path when
    the file wasn't readable."""

    def test_includes_work_order_and_file_content(self):
        wo = {"title": "Fix dup", "target_file": "src/x.py"}
        text = pe._render_user_prompt(
            work_order=wo, target_file_content="def foo(): pass"
        )
        assert "Fix dup" in text
        assert "src/x.py" in text
        assert "def foo(): pass" in text
        assert "PEProposal" in text

    def test_notes_missing_file_content(self):
        wo = {"title": "Fix", "target_file": "unknown.py"}
        text = pe._render_user_prompt(work_order=wo, target_file_content="")
        assert "not readable or path unresolved" in text


class TestMarkdownRendering:
    """`_render_markdown` is pure and shapes the inbox + scoreboard
    body. Covers both filled proposal and no-changes case."""

    def test_renders_full_proposal(self):
        wo = {"title": "Fix duplication", "target_file": "src/x.py"}
        proposal = pe.PEProposal(
            investigation_summary="Found a race condition.",
            proposed_changes=[
                pe.PEProposedChange(
                    file_path="src/x.py",
                    change_type="modify",
                    rationale="Add dedup key.",
                    diff="- old\n+ new",
                )
            ],
            new_tests=[
                pe.PENewTest(
                    file_path="tests/x.py",
                    name="test_dedup_blocks",
                    purpose="Covers the race.",
                )
            ],
            risk_assessment="Low — single file.",
            estimated_profit_impact="Saves $1.1k/week.",
            confidence="high",
        )
        md = pe._render_markdown(proposal, wo)
        assert "Fix duplication" in md
        assert "## Proposed changes" in md
        assert "test_dedup_blocks" in md
        assert "## Risk" in md
        assert "high" in md

    def test_renders_cleanly_when_no_changes_proposed(self):
        wo = {"title": "x", "target_file": "y"}
        proposal = pe.PEProposal(
            investigation_summary="Not actionable.",
            proposed_changes=[],
            new_tests=[],
            risk_assessment="n/a",
            estimated_profit_impact="n/a",
            confidence="low",
        )
        md = pe._render_markdown(proposal, wo)
        assert "## Proposed changes" not in md
        assert "## New tests" not in md
        assert "Not actionable" in md


class TestGenerateProposal:
    """`_generate_proposal` is the end-to-end orchestration for one
    work order — reads file, calls LLM, writes markdown + decision
    row + conditionally emails. Tests the LLM-success and LLM-skip
    paths without hitting Anthropic."""

    def test_llm_success_writes_decision_and_emails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        wo = {
            "title": "Fix dup",
            "target_file": "",  # unresolved — PE still runs
            "expected_profit_impact": "x",
        }
        db = MagicMock()
        fake = pe.PEProposal(
            investigation_summary="Found it.",
            confidence="high",
            risk_assessment="low",
            estimated_profit_impact="x",
        )
        llm_result = MagicMock(
            output=fake,
            model="claude-opus-5",
            reasoning="r",
            usd_cost=0.12,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            skipped_reason=None,
        )
        with (
            patch.object(pe, "_call_opus", return_value=llm_result),
            patch.object(pe, "record_decision") as rec,
            patch.object(pe, "_email_proposal") as email,
        ):
            pe._generate_proposal(db=db, qa_decision_id="qa_abc", work_order=wo)
        rec.assert_called_once()
        email.assert_called_once()
        db.commit.assert_called_once()

    def test_low_confidence_proposal_is_logged_but_not_emailed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        wo = {"title": "x", "target_file": "", "expected_profit_impact": ""}
        db = MagicMock()
        fake = pe.PEProposal(
            investigation_summary="not sure",
            confidence="low",
        )
        llm_result = MagicMock(
            output=fake,
            model="claude-opus-5",
            reasoning="",
            usd_cost=0.05,
            input_tokens=50,
            output_tokens=20,
            cache_read_tokens=0,
            skipped_reason=None,
        )
        with (
            patch.object(pe, "_call_opus", return_value=llm_result),
            patch.object(pe, "record_decision") as rec,
            patch.object(pe, "_email_proposal") as email,
        ):
            pe._generate_proposal(db=db, qa_decision_id="qa_abc", work_order=wo)
        rec.assert_called_once()
        email.assert_not_called()  # low-confidence stays silent

    def test_llm_skipped_uses_fallback_and_still_logs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        wo = {"title": "x", "target_file": "", "expected_profit_impact": ""}
        db = MagicMock()
        llm_result = MagicMock(
            output=None,
            skipped_reason="api_failure",
            model="claude-opus-5",
        )
        with (
            patch.object(pe, "_call_opus", return_value=llm_result),
            patch.object(pe, "record_decision") as rec,
            patch.object(pe, "_email_proposal") as email,
        ):
            pe._generate_proposal(db=db, qa_decision_id="qa_abc", work_order=wo)
        rec.assert_called_once()
        # Fallback is confidence='low' → no email.
        email.assert_not_called()


class TestRateLimit:
    def test_skips_when_daily_cap_reached(self):
        db = MagicMock()
        # Already-today count is at the cap.
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.count.return_value = pe._MAX_PE_PROPOSALS_PER_DAY
        with patch.object(
            pe,
            "_collect_candidate_work_orders",
            return_value=[("qa1", {"title": "A"})],
        ):
            with patch.object(pe, "_generate_proposal") as gen:
                produced = pe.scan_and_propose(db=db)
        assert produced == 0
        gen.assert_not_called()

    def test_processes_up_to_remaining_budget(self):
        db = MagicMock()
        # 1 already proposed today; cap is 3 → budget_left = 2.
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.count.return_value = 1
        with patch.object(
            pe,
            "_collect_candidate_work_orders",
            return_value=[
                ("q1", {"title": "A"}),
                ("q2", {"title": "B"}),
                ("q3", {"title": "C"}),
                ("q4", {"title": "D"}),
            ],
        ):
            with patch.object(pe, "_generate_proposal") as gen:
                produced = pe.scan_and_propose(db=db)
        assert produced == 2
        assert gen.call_count == 2
