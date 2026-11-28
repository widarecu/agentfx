"""Unit tests for `agents.fleet.linear_pe_queue` (2026-11-01).

The helpers are thin wrappers around Linear GraphQL. We patch the
underlying `_gql` function so no HTTP is attempted, and assert:

- `list_ready_dispatch_candidates` builds the right filter payload
  (team key, Ready state id, agent-filed label, no needs-wilson,
  no assignee filter, AFX-142), honors the sort, and maps GraphQL nodes to the
  `ReadyIssue` dataclass.
- `transition_to_in_progress` claims through the shared
  `linear_delivery.start` and returns False when not Ready.
- `rollback_to_ready` only undoes the PE's own claim with no
  workspace started, and comments only when it did.
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
    rollback_to_ready,
    should_file_for_attention,
    transition_to_in_progress,
)
from agentfx.integrations import linear_agent_api, linear_delivery
from agentfx.integrations.claim_store import GitHubClaims, Taker

pytestmark = pytest.mark.unit


def _set_pe_creds(monkeypatch, client_id="pe-client-id", secret="pe-client-secret"):
    """The PE queue authenticates as the AgentFX Platform Engineer app."""
    monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_AGENT_CLIENT_ID", client_id)
    monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_AGENT_CLIENT_SECRET", secret)


@pytest.fixture(autouse=True)
def _fake_pe_token(monkeypatch):
    """No real token endpoint: the PE token getter returns a fixed token."""
    monkeypatch.setattr(
        linear_agent_api, "get_app_token", lambda **_kw: ("pe-token", "")
    )


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

    def test_missing_pe_credentials_returns_empty(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch, "", "")
        assert list_ready_dispatch_candidates(limit=5) == []

    def test_maps_nodes_to_ready_issues(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
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

    def test_accountable_assignee_does_not_hide_a_ready_ticket(self, monkeypatch):
        """AFX-142: Wilson stays assignee on Ready work; the shared claim,
        not an empty assignee, keeps a second taker off a ticket."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        node = {
            "id": "uuid-afx-9",
            "identifier": "AFX-9",
            "title": "assigned to Wilson",
            "description": "",
            "url": "u",
            "priority": 2,
            "createdAt": "2026-11-01T00:00:00Z",
            "assignee": {"name": "Wilson Reyes"},
            "labels": {"nodes": [{"id": "lbl-agent-filed", "name": "agent-filed"}]},
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value={"issues": {"nodes": [node]}},
        ) as gql:
            issues = list_ready_dispatch_candidates(limit=5)
        assert [i.identifier for i in issues] == ["AFX-9"]
        assert "assignee" not in gql.call_args.args[1]

    def test_sort_by_priority_then_created_at(self, monkeypatch):
        """Priority 1 (urgent) must win over priority 2 (high)
        regardless of createdAt. Within the same priority, older
        issue first."""
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
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
        _set_pe_creds(monkeypatch)
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
        _set_pe_creds(monkeypatch)
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
        _set_pe_creds(monkeypatch)
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
        _set_pe_creds(monkeypatch)
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


class _FakeDeliveryClient:
    """Records shared-client calls the PE makes (AFX-118)."""

    def __init__(self):
        self.comments: list[tuple[str, str]] = []

    def comment(self, issue_id, body):
        self.comments.append((issue_id, body))


def _result(ok: bool, reason: str = "") -> linear_delivery.TransitionResult:
    return linear_delivery.TransitionResult(
        ok=ok, changed=ok, identifier="AFX-700", reason=reason
    )


class TestTransitionToInProgress:
    """The PE claims through `linear_delivery.start` (AFX-118)."""

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)

    def test_success_uses_shared_start(self, monkeypatch):
        seen = {}

        def _start(client, ident, *, claims, taker):
            seen.update(ident=ident, claims=claims, taker=taker)
            return _result(True)

        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "gh-token")
        monkeypatch.setattr(linear_delivery, "start", _start)
        assert transition_to_in_progress("uuid-afx-700", link="branch x") is True
        assert seen["ident"] == "uuid-afx-700"
        assert seen["taker"] == Taker("AgentFX fleet PE", "branch x")
        assert isinstance(seen["claims"], GitHubClaims)

    def test_not_ready_returns_false(self, monkeypatch):
        """Already claimed by another scan or agent: no duplicate work."""
        monkeypatch.setattr(
            linear_delivery, "start", lambda c, i, **_: _result(False, "claimed")
        )
        assert transition_to_in_progress("uuid-x") is False

    def test_exception_returns_false(self, monkeypatch):
        def _boom(client, ident, **_):
            raise linear_delivery.LinearError("linear 500")

        monkeypatch.setattr(linear_delivery, "start", _boom)
        assert transition_to_in_progress("uuid-x") is False

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", False)
        assert transition_to_in_progress("uuid-x") is False


class TestClaimStoreIdentity:
    """AFX-180: the fleet claims as the agentfx-pe App, never falls back
    from a broken App to the personal token."""

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "")

    def _app(self, monkeypatch, state, minted=None):
        from agentfx.agents.fleet import github_app

        monkeypatch.setattr(github_app, "app_state", lambda: state)
        calls = []

        def _mint(*, owner, repo):
            calls.append((owner, repo))
            return minted

        monkeypatch.setattr(github_app, "mint_installation_token", _mint)
        return calls

    def test_app_on_uses_minted_token_with_empty_github_token(self, monkeypatch):
        from agentfx.agents.fleet import github_app

        calls = self._app(monkeypatch, "on", github_app.MintResult(token="app-tok"))
        seen = {}

        def _start(client, ident, *, claims, taker):
            seen["claims"] = claims
            return _result(True)

        monkeypatch.setattr(linear_delivery, "start", _start)
        assert transition_to_in_progress("AFX-9", link="x") is True
        assert calls == [("widarecu", "agentfx")]
        assert seen["claims"]._token == "app-tok"
        assert seen["claims"].repo == "widarecu/agentfx"

    def test_app_on_mint_fails_closed_without_fallback(self, monkeypatch, caplog):
        from agentfx.agents.fleet import github_app

        self._app(monkeypatch, "on", github_app.MintResult(reason="HTTP 401"))
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "personal-tok")
        started = []
        monkeypatch.setattr(
            linear_delivery, "start", lambda *a, **k: started.append(1) or _result(True)
        )
        out = linear_pe_queue.claim_for_dispatch("AFX-9")
        assert out.ok is False and out.refused is False
        assert "HTTP 401" in out.reason
        assert "personal-tok" not in out.reason
        assert "personal-tok" not in caplog.text
        assert "HTTP 401" in caplog.text
        # the store is built before start runs, so no tag was attempted
        assert started == []

    def test_app_partial_fails_closed(self, monkeypatch):
        self._app(monkeypatch, "partial")
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "personal-tok")
        assert transition_to_in_progress("AFX-9") is False

    def test_app_off_uses_github_token_unchanged(self, monkeypatch):
        calls = self._app(monkeypatch, "off")
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", " gh-token ")
        store = linear_pe_queue._claim_store()
        assert store._token == "gh-token"
        assert calls == []

    def test_refusal_is_reported_as_refused(self, monkeypatch):
        self._app(monkeypatch, "off")
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "t")
        monkeypatch.setattr(
            linear_delivery, "start", lambda *a, **k: _result(False, "claimed by X")
        )
        out = linear_pe_queue.claim_for_dispatch("AFX-9")
        assert (out.ok, out.refused, out.reason) == (False, True, "claimed by X")


class TestRollbackToReady:
    """Only the PE's own claim, only when no workspace started."""

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        self.client = _FakeDeliveryClient()
        monkeypatch.setattr(linear_pe_queue, "_delivery_client", lambda: self.client)

    def test_releases_own_claim_and_comments(self, monkeypatch):
        seen = {}

        def _release(client, ident, *, workspace_started):
            seen["workspace_started"] = workspace_started
            return _result(True)

        monkeypatch.setattr(linear_delivery, "release_own_claim", _release)
        assert rollback_to_ready("uuid-1", reason_summary="adapter_refused") is True
        assert seen["workspace_started"] is False
        assert len(self.client.comments) == 1
        assert "adapter_refused" in self.client.comments[0][1]

    def test_refused_release_leaves_issue_and_stays_quiet(self, monkeypatch):
        monkeypatch.setattr(
            linear_delivery,
            "release_own_claim",
            lambda c, i, *, workspace_started: _result(False, "another actor"),
        )
        assert rollback_to_ready("uuid-1", reason_summary="x") is False
        assert self.client.comments == []

    def test_exception_returns_false(self, monkeypatch):
        def _boom(c, i, *, workspace_started):
            raise linear_delivery.LinearError("down")

        monkeypatch.setattr(linear_delivery, "release_own_claim", _boom)
        assert rollback_to_ready("uuid-1", reason_summary="x") is False


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
        _set_pe_creds(monkeypatch)

    def _record(self, monkeypatch):
        calls: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            linear_pe_queue, "_build_client", lambda _t, **_kw: object()
        )
        monkeypatch.setattr(
            linear_pe_queue, "_resolve_issue_uuid", lambda _c, ident: f"uuid-{ident}"
        )
        monkeypatch.setattr(
            linear_pe_queue,
            "_issue_state",
            lambda _c, _i: {"name": "In Progress", "type": "started"},
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

    def test_first_failure_keeps_claim_and_flags(self, monkeypatch):
        """The session had started, so the claim is never rolled back."""
        calls = self._record(monkeypatch)
        ok = linear_pe_queue.handle_stuck_session_failure(
            "AFX-40", comment_body="reclaimed", escalate=False
        )
        assert ok is True
        kinds = [k for k, _ in calls]
        assert kinds == ["label", "comment"]
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

    @pytest.mark.parametrize(
        "state",
        [
            {"name": "In Review", "type": "started"},
            {"name": "Done", "type": "completed"},
            {"name": "Canceled", "type": "canceled"},
        ],
    )
    def test_delivered_ticket_is_left_alone(self, monkeypatch, state):
        """AFX-134: a reclaim after the PR opened must not flag the ticket."""
        calls = self._record(monkeypatch)
        monkeypatch.setattr(linear_pe_queue, "_issue_state", lambda _c, _i: state)
        ok = linear_pe_queue.handle_stuck_session_failure(
            "AFX-121", comment_body="reclaimed", escalate=False
        )
        assert ok is False and calls == []

    @pytest.mark.parametrize(
        "state", [{"name": "In Progress", "type": "started"}, None]
    )
    def test_in_progress_or_unknown_state_is_flagged(self, monkeypatch, state):
        calls = self._record(monkeypatch)
        monkeypatch.setattr(linear_pe_queue, "_issue_state", lambda _c, _i: state)
        linear_pe_queue.handle_stuck_session_failure(
            "AFX-40", comment_body="reclaimed", escalate=False
        )
        assert [k for k, _ in calls] == ["label", "comment"]

    def test_never_raises(self, monkeypatch):
        def _boom(_t, **_kw):
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


class TestFindMergedPr:
    """AFX-134/135: the dispatcher's merged-PR check."""

    def _get(self, monkeypatch, payloads, *, status=200):
        seen: list[str] = []

        class _Resp:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                if status >= 400:
                    raise linear_pe_queue.requests.HTTPError(str(status))

            def json(self):
                return self.body

        def _fake(url, **_kw):
            seen.append(url)
            return _Resp(payloads[url.rsplit("/", 1)[-1]])

        monkeypatch.setattr(linear_pe_queue.requests, "get", _fake)
        return seen

    @staticmethod
    def _pr(*, merged=True, base="feat/agent-fleet", title="", head="x"):
        return {
            "merged_at": "2026-11-23T00:00:00Z" if merged else None,
            "base": {"ref": base},
            "head": {"ref": head},
            "title": title,
        }

    _URL = "https://github.com/widarecu/agentfx/pull/{}"

    def test_returns_the_merged_pr_that_names_the_ticket(self, monkeypatch):
        seen = self._get(
            monkeypatch,
            {
                "1": self._pr(merged=False, title="AFX-121: open"),
                "2": self._pr(title="AFX-121: make deploy verify staging"),
            },
        )
        urls = [
            "https://linear.app/agentfx/issue/AFX-1",
            self._URL.format(1),
            self._URL.format(2),
        ]
        assert linear_pe_queue.find_merged_pr("AFX-121", urls) == urls[2]
        assert seen == [
            "https://api.github.com/repos/widarecu/agentfx/pulls/1",
            "https://api.github.com/repos/widarecu/agentfx/pulls/2",
        ]

    def test_branch_naming_the_ticket_counts(self, monkeypatch):
        self._get(monkeypatch, {"3": self._pr(head="devin/afx-122-sync-tickets")})
        assert linear_pe_queue.find_merged_pr("AFX-122", [self._URL.format(3)])

    def test_context_link_to_another_tickets_pr_does_not_count(self, monkeypatch):
        """AFX-129 had the merged #161 (AFX-121) attached as background."""
        self._get(
            monkeypatch,
            {"161": self._pr(title="AFX-121: verify", head="agent/AFX-121-verify")},
        )
        assert (
            linear_pe_queue.find_merged_pr("AFX-129", [self._URL.format(161)]) is None
        )
        assert linear_pe_queue.find_merged_pr("AFX-12", [self._URL.format(161)]) is None

    def test_other_repo_is_never_fetched(self, monkeypatch):
        seen = self._get(monkeypatch, {})
        url = "https://github.com/psf/requests/pull/9"
        assert linear_pe_queue.find_merged_pr("AFX-5", [url]) is None
        assert seen == []

    def test_non_delivery_base_does_not_count(self, monkeypatch):
        self._get(monkeypatch, {"7": self._pr(base="some-feature", title="AFX-5: x")})
        assert linear_pe_queue.find_merged_pr("AFX-5", [self._URL.format(7)]) is None

    def test_no_prs_return_none(self, monkeypatch):
        self._get(monkeypatch, {})
        assert linear_pe_queue.find_merged_pr("AFX-5", []) is None

    def test_api_failure_raises(self, monkeypatch):
        self._get(monkeypatch, {"4": {}}, status=502)
        with pytest.raises(linear_pe_queue.requests.HTTPError):
            linear_pe_queue.find_merged_pr("AFX-5", [self._URL.format(4)])

    def test_candidates_carry_linked_urls(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        node = {
            "id": "u",
            "identifier": "AFX-121",
            "title": "t",
            "description": "",
            "url": "https://linear.app/agentfx/issue/AFX-121",
            "priority": 0,
            "createdAt": "2026-11-22T00:00:00Z",
            "labels": {"nodes": [{"name": "agent-filed"}]},
            "attachments": {
                "nodes": [{"url": "https://github.com/widarecu/agentfx/pull/161"}]
            },
        }
        with patch(
            "agentfx.agents.fleet.linear_pe_queue._gql",
            return_value={"issues": {"nodes": [node]}},
        ):
            (issue,) = list_ready_dispatch_candidates(limit=5)
        assert issue.pr_urls == ["https://github.com/widarecu/agentfx/pull/161"]


# ─────────────────────────────────────────────────────────────────────
# Identity: the PE queue acts as the AgentFX Platform Engineer (AFX-174)
# ─────────────────────────────────────────────────────────────────────


class TestPeIdentity:
    def test_queue_client_carries_pe_bearer_token(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        client = linear_pe_queue._pe_client()
        assert client is not None
        assert client.headers["Authorization"] == "Bearer pe-token"

    def test_missing_pe_pair_returns_empty_and_logs_reason(self, monkeypatch, caplog):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch, "", "")
        # QA credentials present must not rescue the queue.
        monkeypatch.setattr(linear_pe_queue.settings, "LINEAR_QA_CLIENT_ID", "qa-id")
        monkeypatch.setattr(
            linear_pe_queue.settings, "LINEAR_QA_CLIENT_SECRET", "qa-secret"
        )
        caplog.set_level("INFO")
        with patch("agentfx.agents.fleet.linear_pe_queue._gql") as gql:
            assert list_ready_dispatch_candidates(limit=5) == []
        gql.assert_not_called()
        assert "LINEAR_AGENT_CLIENT_ID" in caplog.text

    def test_token_failure_returns_empty_without_raising(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        monkeypatch.setattr(
            linear_agent_api,
            "get_app_token",
            lambda **_kw: (None, "token_endpoint:HTTP 401"),
        )
        with patch("agentfx.agents.fleet.linear_pe_queue._gql") as gql:
            assert list_ready_dispatch_candidates(limit=5) == []
            assert file_for_attention("u", signatures=["a", "b"]) is False
        gql.assert_not_called()

    def test_queue_never_asks_for_the_qa_token(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)

        def _no_qa(**_kw):
            raise AssertionError("PE queue must not use the QA token")

        monkeypatch.setattr(linear_agent_api, "get_qa_token", _no_qa)
        assert linear_pe_queue._pe_client() is not None

    def test_refresh_callback_force_refreshes_pe_token(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            linear_agent_api,
            "get_app_token",
            lambda **kw: seen.append(kw) or ("pe-token-2", ""),
        )
        assert linear_pe_queue._refresh_pe_token() == "pe-token-2"
        assert seen == [{"force_refresh": True}]

    def test_delivery_client_takes_its_token_from_get_app_token(self, monkeypatch):
        seen = []

        def fake(**kw):
            seen.append(kw)
            return ("pe-token-2" if kw.get("force_refresh") else "pe-token", "")

        monkeypatch.setattr(linear_agent_api, "get_app_token", fake)
        client = linear_pe_queue._delivery_client()
        assert isinstance(client, linear_delivery.LinearClient)
        # It never holds the PE pair: it cannot mint a token of its own.
        assert client._client_id == "" and client._client_secret == ""
        assert client._authorization() == "Bearer pe-token"
        assert client._token_provider(True) == "pe-token-2"
        assert seen == [{"force_refresh": False}, {"force_refresh": True}]

    def test_claim_fails_closed_without_a_token_and_names_no_token(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "AGENT_LINEAR_ENABLED", True)
        _set_pe_creds(monkeypatch)
        monkeypatch.setattr(
            linear_agent_api,
            "get_app_token",
            lambda **_kw: (None, "token_endpoint:HTTP 401"),
        )
        monkeypatch.setattr(linear_pe_queue, "_claim_store", lambda: object())
        outcome = linear_pe_queue.claim_for_dispatch("AFX-1")
        assert outcome.ok is False and outcome.refused is False
        assert "token_endpoint:HTTP 401" in outcome.reason
        assert linear_pe_queue.rollback_to_ready("AFX-1", reason_summary="x") is False


class TestFleetGithubToken:
    """AFX-180: every GitHub call the fleet makes itself uses the PE App's
    token once the App is set up, so the personal token can be deleted."""

    def _app(self, monkeypatch, state, minted=None):
        from agentfx.agents.fleet import github_app

        monkeypatch.setattr(github_app, "app_state", lambda: state)
        monkeypatch.setattr(
            github_app, "mint_installation_token", lambda *, owner, repo: minted
        )

    def _capture_get(self, monkeypatch):
        seen: list[dict] = []

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "merged_at": "2026-11-23T00:00:00Z",
                    "base": {"ref": "main"},
                    "head": {"ref": "agent/AFX-9-x"},
                    "title": "AFX-9: x",
                }

        def _fake(url, **kw):
            seen.append(kw.get("headers") or {})
            return _Resp()

        monkeypatch.setattr(linear_pe_queue.requests, "get", _fake)
        return seen

    _URL = "https://github.com/widarecu/agentfx/pull/7"

    def test_merged_pr_check_uses_the_app_token(self, monkeypatch):
        from agentfx.agents.fleet import github_app

        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "personal-pat")
        self._app(
            monkeypatch, "on", github_app.MintResult(token="ghs_app", expires_at="")
        )
        seen = self._capture_get(monkeypatch)

        assert linear_pe_queue.find_merged_pr("AFX-9", [self._URL]) == self._URL
        assert seen[0]["Authorization"] == "Bearer ghs_app"

    def test_merged_pr_check_fails_closed_when_the_app_token_cannot_be_minted(
        self, monkeypatch
    ):
        from agentfx.agents.fleet import github_app

        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "personal-pat")
        self._app(monkeypatch, "on", github_app.MintResult(reason="HTTP 401"))
        seen = self._capture_get(monkeypatch)

        with pytest.raises(Exception, match="could not be minted"):
            linear_pe_queue.find_merged_pr("AFX-9", [self._URL])
        assert seen == []

    def test_without_the_app_the_personal_token_is_still_used(self, monkeypatch):
        monkeypatch.setattr(linear_pe_queue.settings, "GITHUB_TOKEN", "personal-pat")
        self._app(monkeypatch, "off")
        seen = self._capture_get(monkeypatch)

        linear_pe_queue.find_merged_pr("AFX-9", [self._URL])
        assert seen[0]["Authorization"] == "Bearer personal-pat"
