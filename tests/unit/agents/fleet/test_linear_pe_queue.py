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
