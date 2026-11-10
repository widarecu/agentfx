"""Unit tests for `agents.fleet.linear_pe_queue` (2026-11-01).

The helpers are thin wrappers around Linear GraphQL. We patch the
underlying `_gql` function so no HTTP is attempted, and assert:

- `list_ready_dispatch_candidates` builds the right filter payload
  (team key, Ready state id, agent-filed label, no needs-wilson,
  assignee null), honors the sort, and maps GraphQL nodes to the
  `ReadyIssue` dataclass.
- `transition_to_in_progress` calls `issueUpdate` with the right
  state id and returns True/False from the mutation's success flag.
- `rollback_to_ready` transitions back to Ready AND posts a comment.
- `file_for_attention` posts a formatted comment AND adds the
  `needs-wilson` label without stripping other labels.
- `failure_signature` is stable across hex id / timestamp noise.
- `should_file_for_attention` fires only when the last two
  signatures match and ≥ 2 failures exist.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentfx.agents.fleet import linear_pe_queue
from agentfx.agents.fleet.linear_pe_queue import (
    failure_signature,
    file_for_attention,
    list_ready_dispatch_candidates,
    should_file_for_attention,
    transition_to_in_progress,
)

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Signature + retry gate
# ─────────────────────────────────────────────────────────────────────


class TestFailureSignature:
    def test_identical_summaries_match(self):
        a = failure_signature("BadRequestError: Field required")
        b = failure_signature("BadRequestError: Field required")
        assert a == b
        assert a  # not empty

    def test_hex_ids_dont_break_match(self):
        """Session id / run id tokens shouldn't make equivalent
        failures look distinct."""
        a = failure_signature(
            "sessions.create failed — session=c0cc471c386a4126b360e01c14da7cd9"
        )
        b = failure_signature(
            "sessions.create failed — session=aabbccddeeff11223344556677889900"
        )
        assert a == b

    def test_long_numbers_dont_break_match(self):
        a = failure_signature("timeout after 12345 ms")
        b = failure_signature("timeout after 67890 ms")
        assert a == b

    def test_different_errors_differ(self):
        a = failure_signature("sessions.create failed — BadRequestError")
        b = failure_signature("events.send failed — ConnectionError")
        assert a != b

    def test_empty_input(self):
        assert failure_signature(None) == ""
        assert failure_signature("") == ""


class TestShouldFileForAttention:
    def test_zero_prior_failures(self):
        assert should_file_for_attention([]) is False

    def test_one_prior_failure(self):
        assert should_file_for_attention(["BadRequest"]) is False

    def test_two_matching_signatures(self):
        """Realistic shape: Anthropic errors carry a request_id that
        changes every attempt but the surrounding failure message is
        identical. Signature extraction must drop the id."""
        assert (
            should_file_for_attention(
                [
                    "sessions.create failed — BadRequestError req_011Cgc8ybHsDyHZAJxLQpqqa",
                    "sessions.create failed — BadRequestError req_013ZbdTqB4ec9rh12ja4xw2P",
                ]
            )
            is True
        )

    def test_two_different_signatures(self):
        assert (
            should_file_for_attention(
                [
                    "sessions.create failed — BadRequest",
                    "events.send failed — Timeout",
                ]
            )
            is False
        )


# ─────────────────────────────────────────────────────────────────────
# list_ready_dispatch_candidates
# ─────────────────────────────────────────────────────────────────────


class TestListReadyDispatchCandidates:
    def test_disabled_feature_returns_empty(self, monkeypatch):
        """Without AGENT_LINEAR_ENABLED, PE never hits Linear."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", False)
        assert list_ready_dispatch_candidates(limit=5) == []

    def test_empty_api_key_returns_empty(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "")
        assert list_ready_dispatch_candidates(limit=5) == []

    def test_maps_nodes_to_ready_issues(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        fake_response = {
            "issues": {
                "nodes": [
                    {
                        "id": "uuid-afx-56",
                        "identifier": "AFX-56",
                        "title": "Fix PnL ledger",
                        "description": "balance/pnl disconnect",
                        "url": "https://linear.app/agentfx/issue/AFX-56",
                        "priority": 2,
                        "createdAt": "2026-10-31T11:44:00Z",
                        "labels": {
                            "nodes": [
                                {"name": "agent-filed"},
                                {"name": "QA agent"},
                                {"name": "Paper runner"},
                            ]
                        },
                    },
                ]
            }
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value=fake_response,
        ):
            issues = list_ready_dispatch_candidates(limit=5)
        assert len(issues) == 1
        iss = issues[0]
        assert iss.identifier == "AFX-56"
        assert iss.priority == 2
        assert set(iss.labels) == {"agent-filed", "QA agent", "Paper runner"}

    def test_sort_by_priority_then_created_at(self, monkeypatch):
        """Priority 1 (urgent) must win over priority 2 (high)
        regardless of createdAt. Within the same priority, older
        issue first."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        fake_response = {
            "issues": {
                "nodes": [
                    {
                        "id": "uuid-c",
                        "identifier": "AFX-C",
                        "title": "high priority",
                        "description": "",
                        "url": "u",
                        "priority": 2,
                        "createdAt": "2026-10-01T00:00:00Z",
                        "labels": {"nodes": []},
                    },
                    {
                        "id": "uuid-a",
                        "identifier": "AFX-A",
                        "title": "urgent",
                        "description": "",
                        "url": "u",
                        "priority": 1,
                        "createdAt": "2026-11-01T00:00:00Z",
                        "labels": {"nodes": []},
                    },
                    {
                        "id": "uuid-b",
                        "identifier": "AFX-B",
                        "title": "another urgent",
                        "description": "",
                        "url": "u",
                        "priority": 1,
                        "createdAt": "2026-10-15T00:00:00Z",
                        "labels": {"nodes": []},
                    },
                ]
            }
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value=fake_response,
        ):
            issues = list_ready_dispatch_candidates(limit=5)
        assert [i.identifier for i in issues] == ["AFX-B", "AFX-A", "AFX-C"]

    def test_no_priority_issues_sort_last(self, monkeypatch):
        """priority=0 means 'no priority' in Linear — those should
        come after any issue with an actual priority set."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        fake_response = {
            "issues": {
                "nodes": [
                    {
                        "id": "uuid-nop",
                        "identifier": "AFX-NOP",
                        "title": "no priority",
                        "description": "",
                        "url": "u",
                        "priority": 0,
                        "createdAt": "2026-10-01T00:00:00Z",
                        "labels": {"nodes": []},
                    },
                    {
                        "id": "uuid-low",
                        "identifier": "AFX-LOW",
                        "title": "low priority",
                        "description": "",
                        "url": "u",
                        "priority": 4,
                        "createdAt": "2026-11-01T00:00:00Z",
                        "labels": {"nodes": []},
                    },
                ]
            }
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value=fake_response,
        ):
            issues = list_ready_dispatch_candidates(limit=5)
        assert [i.identifier for i in issues] == ["AFX-LOW", "AFX-NOP"]

    def test_needs_wilson_label_excludes_candidate(self, monkeypatch):
        """Issues carrying the needs-wilson label MUST NOT be returned —
        the GraphQL filter proved unreliable (2026-11-06–08 comment
        storm on AFX-50/52/73 traced to Linear's nested `nin` matching
        the opposite of what we wanted). Fix is a Python post-filter
        that compares label ids. This test pins that behaviour."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        monkeypatch.setattr(
            linear_pe_queue.settings,
            "PE_LINEAR_NEEDS_WILSON_LABEL_ID",
            "lbl-needs-wilson",
        )
        fake_response = {
            "issues": {
                "nodes": [
                    {
                        "id": "uuid-ok",
                        "identifier": "AFX-OK",
                        "title": "proceed",
                        "description": "",
                        "url": "u",
                        "priority": 2,
                        "createdAt": "2026-11-01T00:00:00Z",
                        "labels": {
                            "nodes": [
                                {"id": "lbl-agent-filed", "name": "agent-filed"},
                            ]
                        },
                    },
                    {
                        "id": "uuid-blocked",
                        "identifier": "AFX-BLOCKED",
                        "title": "flagged, must not dispatch",
                        "description": "",
                        "url": "u",
                        "priority": 1,
                        "createdAt": "2026-11-01T00:00:00Z",
                        "labels": {
                            "nodes": [
                                {"id": "lbl-agent-filed", "name": "agent-filed"},
                                {
                                    "id": "lbl-needs-wilson",
                                    "name": "needs-wilson",
                                },
                            ]
                        },
                    },
                ]
            }
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value=fake_response,
        ):
            issues = list_ready_dispatch_candidates(limit=5)
        assert [i.identifier for i in issues] == ["AFX-OK"]

    def test_gql_failure_returns_empty(self, monkeypatch):
        """Linear outage must never raise into the orchestrator loop."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            side_effect=RuntimeError("linear 500"),
        ):
            assert list_ready_dispatch_candidates(limit=5) == []


# ─────────────────────────────────────────────────────────────────────
# transition_to_in_progress
# ─────────────────────────────────────────────────────────────────────


class TestFileForAttentionIdempotency:
    """Fingerprint-dedup for the file_for_attention comment (2026-11-08).
    The label-add is already idempotent via _add_label's label-read
    check; the comment wasn't, which caused the AFX-50/52/73 comment
    storm when the Ready-list exclusion filter failed for 48 hours.
    """

    def _enable(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        monkeypatch.setattr(
            linear_pe_queue.settings,
            "PE_LINEAR_NEEDS_WILSON_LABEL_ID",
            "lbl-needs-wilson",
        )

    @staticmethod
    def _tag(query: str) -> str:
        """Classify an outgoing GraphQL payload by the operation it
        performs. Works on both queries and mutations; matches on the
        operation name so the first line of a `\"\"\"` triple-quoted
        string (the leading blank) doesn't obscure the signal."""
        if "comments(first: 50)" in query:
            return "read_comments"
        if "commentCreate" in query:
            return "mutate_commentCreate"
        if "labels(first: 50)" in query:
            return "read_labels"
        if "issueUpdate" in query:
            return "mutate_issueUpdate"
        return "unknown"

    def test_first_call_posts_comment_and_adds_label(self, monkeypatch):
        self._enable(monkeypatch)
        tags: list[str] = []
        comment_bodies: list[str] = []

        def _mock_gql(client, query, variables):
            tag = self._tag(query)
            tags.append(tag)
            if tag == "read_comments":
                return {"issue": {"id": variables["id"], "comments": {"nodes": []}}}
            if tag == "mutate_commentCreate":
                comment_bodies.append(variables["body"])
                return {"commentCreate": {"success": True}}
            if tag == "read_labels":
                return {
                    "issue": {
                        "id": variables["id"],
                        "labels": {"nodes": [{"id": "lbl-agent-filed"}]},
                    }
                }
            if tag == "mutate_issueUpdate":
                return {"issueUpdate": {"success": True}}
            return {}

        with patch("agentfx.agents.fleet.linear_pe_queue._gql", side_effect=_mock_gql):
            ok = file_for_attention(
                "uuid-afx-500", signatures=["BadRequest", "BadRequest"]
            )
        assert ok is True
        assert tags.count("mutate_commentCreate") == 1, (
            "first call must post exactly one comment"
        )
        assert linear_pe_queue._FILE_FOR_ATTENTION_MARKER in comment_bodies[0], (
            "marker must be embedded so subsequent calls can detect it"
        )

    def test_second_call_skips_comment_but_still_ensures_label(self, monkeypatch):
        """If a prior file_for_attention comment is already present
        on the issue, a repeat call MUST NOT post another comment.
        It must still call the label-add path (idempotent itself)
        so the Ready-exclusion contract holds even if someone peeled
        the label by hand."""
        self._enable(monkeypatch)
        tags: list[str] = []

        existing_comment = {
            "id": "cmt-existing",
            "body": (
                linear_pe_queue._FILE_FOR_ATTENTION_MARKER
                + "\n**PE dispatch keeps failing...** posted an hour ago"
            ),
        }

        def _mock_gql(client, query, variables):
            tag = self._tag(query)
            tags.append(tag)
            if tag == "read_comments":
                return {
                    "issue": {
                        "id": variables["id"],
                        "comments": {"nodes": [existing_comment]},
                    }
                }
            if tag == "mutate_commentCreate":
                raise AssertionError("duplicate comment MUST NOT be posted")
            if tag == "read_labels":
                # Label not present (simulating a Wilson-initiated peel).
                return {
                    "issue": {
                        "id": variables["id"],
                        "labels": {"nodes": [{"id": "lbl-agent-filed"}]},
                    }
                }
            if tag == "mutate_issueUpdate":
                return {"issueUpdate": {"success": True}}
            return {}

        with patch("agentfx.agents.fleet.linear_pe_queue._gql", side_effect=_mock_gql):
            ok = file_for_attention(
                "uuid-afx-501", signatures=["BadRequest", "BadRequest"]
            )
        assert ok is True
        assert "mutate_commentCreate" not in tags
        assert "mutate_issueUpdate" in tags, (
            "label-add path must still fire so the Ready filter stays correct"
        )

    def test_marker_read_failure_falls_back_to_posting(self, monkeypatch):
        """If Linear 500s on the marker read, we don't know whether a
        prior comment exists — degrade to posting (one extra comment
        on a transient outage is fine; silencing a real file-for-
        attention on a true first failure is not). Must not raise."""
        self._enable(monkeypatch)
        tags: list[str] = []

        def _mock_gql(client, query, variables):
            tag = self._tag(query)
            tags.append(tag)
            if tag == "read_comments":
                raise RuntimeError("linear 500")
            if tag == "mutate_commentCreate":
                return {"commentCreate": {"success": True}}
            if tag == "read_labels":
                return {
                    "issue": {
                        "id": variables["id"],
                        "labels": {"nodes": []},
                    }
                }
            if tag == "mutate_issueUpdate":
                return {"issueUpdate": {"success": True}}
            return {}

        with patch("agentfx.agents.fleet.linear_pe_queue._gql", side_effect=_mock_gql):
            ok = file_for_attention(
                "uuid-afx-502", signatures=["BadRequest", "BadRequest"]
            )
        assert ok is True
        assert "mutate_commentCreate" in tags, (
            "unknown prior-comment state must degrade to posting, not silence"
        )


class TestTransitionToInProgress:
    def test_success_calls_issueUpdate_with_state_id(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        captured = {}

        def _mock_gql(client, query, variables):
            captured["query"] = query
            captured["variables"] = variables
            return {
                "issueUpdate": {
                    "success": True,
                    "issue": {
                        "id": variables["id"],
                        "identifier": "AFX-700",
                        "state": {"name": "In Progress"},
                    },
                }
            }

        with patch("agentfx.agents.fleet.linear_pe_queue._gql", side_effect=_mock_gql):
            ok = transition_to_in_progress("uuid-afx-700")

        assert ok is True
        assert captured["variables"]["id"] == "uuid-afx-700"
        assert (
            captured["variables"]["stateId"]
            == linear_pe_queue.settings.PE_LINEAR_IN_PROGRESS_STATE_ID
        )

    def test_graphql_reports_no_success_returns_false(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value={"issueUpdate": {"success": False}},
        ):
            assert transition_to_in_progress("uuid-x") is False

    def test_exception_returns_false(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "test-key")
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            side_effect=RuntimeError("linear 500"),
        ):
            assert transition_to_in_progress("uuid-x") is False


# ─────────────────────────────────────────────────────────────────────
# AFX-72 — stuck-session signatures + Linear recovery
# ─────────────────────────────────────────────────────────────────────


class TestStuckSessionSignatures:
    def test_ttl_signature_ignores_prior_summary(self):
        a = failure_signature("[sweeper] exceeded 4h TTL, marked WORKSPACE_FAILED")
        b = failure_signature(
            "dispatched session=sesn_abc\n[sweeper] exceeded 4h TTL, marked "
            "WORKSPACE_FAILED"
        )
        assert a == b == "stuck-session: exceeded 4h ttl"

    def test_cost_cap_signature_ignores_token_counts(self):
        a = failure_signature(
            "[cost-cap] exceeded 100000 tokens (input=150000, output=9)"
        )
        b = failure_signature(
            "[cost-cap] exceeded 200000 tokens (input=10, output=250000)"
        )
        assert a == b == "stuck-session: cost-cap exceeded n tokens"

    def test_publish_failed_signature_ignores_event_tail(self):
        a = failure_signature(
            "[poll] blah 1\n[publish-failed] publish failed after fallbacks"
        )
        b = failure_signature(
            "[poll] other\n[publish-failed] publish failed after fallbacks"
        )
        assert a == b == "stuck-session: publish failed after fallbacks"

    def test_two_ttl_failures_file_for_attention(self):
        long_prefix = "[poll] " + "x " * 200
        assert should_file_for_attention(
            [
                long_prefix + "\n[sweeper] exceeded 4h TTL, marked WORKSPACE_FAILED",
                "[sweeper] exceeded 4h TTL, marked WORKSPACE_FAILED",
            ]
        )

    def test_non_stuck_summary_unchanged(self):
        assert linear_pe_queue.stuck_session_signature("BadRequestError") == ""


class TestHandleStuckSessionFailure:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_API_KEY", "lin_test")

    def _record(self, monkeypatch):
        calls: list[tuple[str, dict]] = []
        monkeypatch.setattr(linear_pe_queue, "_build_client", lambda _k: object())
        monkeypatch.setattr(
            linear_pe_queue, "_resolve_issue_uuid", lambda _c, ident: f"uuid-{ident}"
        )
        monkeypatch.setattr(
            linear_pe_queue,
            "_issue_update",
            lambda _c, **kw: calls.append(("state", kw)) or True,
        )
        monkeypatch.setattr(
            linear_pe_queue,
            "_add_label",
            lambda _c, **kw: calls.append(("label", kw)) or True,
        )
        monkeypatch.setattr(
            linear_pe_queue,
            "_comment",
            lambda _c, **kw: calls.append(("comment", kw)) or True,
        )
        return calls

    def test_first_failure_rolls_back_to_ready_and_comments(self, monkeypatch):
        calls = self._record(monkeypatch)
        ok = linear_pe_queue.handle_stuck_session_failure(
            "AFX-40", comment_body="reclaimed", escalate=False
        )
        assert ok is True
        kinds = [k for k, _ in calls]
        assert kinds == ["state", "comment"]
        assert (
            calls[0][1]["state_id"] == linear_pe_queue.settings.PE_LINEAR_READY_STATE_ID
        )
        assert calls[1][1] == {"issue_id": "uuid-AFX-40", "body": "reclaimed"}

    def test_escalation_adds_needs_wilson_and_keeps_state(self, monkeypatch):
        calls = self._record(monkeypatch)
        linear_pe_queue.handle_stuck_session_failure(
            "AFX-40", comment_body="second", escalate=True
        )
        kinds = [k for k, _ in calls]
        assert "state" not in kinds
        assert kinds == ["label", "comment"]
        assert calls[0][1]["label_id"] == (
            linear_pe_queue.settings.PE_LINEAR_NEEDS_WILSON_LABEL_ID
        )

    def test_never_raises(self, monkeypatch):
        def _boom(_k):
            raise RuntimeError("down")

        monkeypatch.setattr(linear_pe_queue, "_build_client", _boom)
        assert (
            linear_pe_queue.handle_stuck_session_failure(
                "AFX-40", comment_body="x", escalate=False
            )
            is False
        )

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", False)
        assert (
            linear_pe_queue.handle_stuck_session_failure(
                "AFX-40", comment_body="x", escalate=False
            )
            is False
        )
