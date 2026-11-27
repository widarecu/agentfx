"""Unit tests for `integrations.linear_client` (AFX-11).

The GraphQL transport is mocked — these are tests for our dedupe /
cap / flag logic, not for Linear's API shape. We assert on the
calls that would have gone out, and on the FilingResult we hand
back to the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentfx.integrations import linear_client

pytestmark = pytest.mark.unit


def _spec(**overrides) -> linear_client.FindingSpec:
    defaults = dict(
        fingerprint="qa:test:abc",
        title="Fix duplicate order submits",
        body="## Spec\n\nSome details.",
        source_label="QA agent",
        type_label="Bug",
        area_label="Paper runner",
        risk_label="Normal",
    )
    defaults.update(overrides)
    return linear_client.FindingSpec(**defaults)


def _enable(monkeypatch):
    monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_ENABLED", True)
    monkeypatch.setattr(linear_client.settings, "LINEAR_QA_CLIENT_ID", "qa-id")
    monkeypatch.setattr(linear_client.settings, "LINEAR_QA_CLIENT_SECRET", "qa-secret")
    monkeypatch.setattr(
        linear_client.linear_agent_api, "get_qa_token", lambda **_kw: ("qa-token", "")
    )
    monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_TEAM_KEY", "AFX")
    monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", 5)


# ─────────────────────────────────────────────────────────────────────
# Flag + token short-circuits
# ─────────────────────────────────────────────────────────────────────


class TestShortCircuits:
    def test_flag_off_skips_without_any_graphql(self, monkeypatch):
        monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_ENABLED", False)
        with patch.object(linear_client, "_build_client") as build:
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:flag_off"
        build.assert_not_called()

    def test_no_qa_credentials_skips(self, monkeypatch):
        monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_client.settings, "LINEAR_QA_CLIENT_ID", "")
        monkeypatch.setattr(linear_client.settings, "LINEAR_QA_CLIENT_SECRET", "")
        with patch.object(linear_client, "_build_client") as build:
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:no_credentials"
        build.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Dedupe path
# ─────────────────────────────────────────────────────────────────────


class TestDedupe:
    def test_existing_open_issue_gets_a_comment(self, monkeypatch):
        _enable(monkeypatch)
        existing = {
            "id": "uuid-123",
            "identifier": "AFX-99",
            "url": "https://linear.app/agentfx/issue/AFX-99",
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=existing
            ),
            patch.object(linear_client, "_add_comment", return_value=True) as add,
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "commented"
        assert result.identifier == "AFX-99"
        create.assert_not_called()
        add.assert_called_once()

    def test_dedupe_comment_failure_returns_unfiled(self, monkeypatch):
        _enable(monkeypatch)
        existing = {"id": "u", "identifier": "AFX-5", "url": "u"}
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=existing
            ),
            patch.object(linear_client, "_add_comment", return_value=False),
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        # When the comment path fails, we don't fall through to create — that
        # would file a duplicate issue. Caller sees unfiled + an identifier
        # so it can route the finding back to email if needed.
        assert result.filed is False
        assert "dedupe_comment_failed" in result.action
        create.assert_not_called()

    def test_open_match_comments_with_only_the_new_evidence(self, monkeypatch):
        _enable(monkeypatch)
        existing = {"id": "u", "identifier": "AFX-5", "url": "u"}
        evidence = "Paper run: `run_new`\n\nTwo orders opened for a single signal."
        spec = _spec(evidence=evidence, body="## Proposed fix\n\nGuard order dispatch.")
        with (
            patch.object(linear_client, "_build_client"),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=existing
            ),
            patch.object(linear_client, "_add_comment", return_value=True) as add,
        ):
            result = linear_client.file_finding(spec)
        assert result.action == "commented"
        assert add.call_args.kwargs == {"issue_id": "u", "body": evidence}


# ─────────────────────────────────────────────────────────────────────
# Cap
# ─────────────────────────────────────────────────────────────────────


class TestCap:
    def test_cap_hit_skips_create(self, monkeypatch):
        _enable(monkeypatch)
        monkeypatch.setattr(
            linear_client.settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", 2
        )
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(linear_client, "_count_files_today", return_value=2),
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:cap_hit"
        create.assert_not_called()

    def test_cap_count_failure_defaults_to_zero_and_still_files(self, monkeypatch):
        """If we can't count today's files (transient network), don't
        block the new filing — the dedupe path still prevents true
        duplicates."""
        _enable(monkeypatch)
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_count_files_today",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(
                linear_client,
                "_create_triage_issue",
                return_value={
                    "id": "uuid-new",
                    "identifier": "AFX-100",
                    "url": "u",
                },
            ),
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "created"


# ─────────────────────────────────────────────────────────────────────
# Create path
# ─────────────────────────────────────────────────────────────────────


class TestCreate:
    def test_happy_path_returns_created(self, monkeypatch):
        _enable(monkeypatch)
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(linear_client, "_count_files_today", return_value=1),
            patch.object(
                linear_client,
                "_create_triage_issue",
                return_value={
                    "id": "uuid-new",
                    "identifier": "AFX-200",
                    "url": "https://linear.app/agentfx/issue/AFX-200",
                },
            ) as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "created"
        assert result.identifier == "AFX-200"
        create.assert_called_once()

    def test_create_failure_returns_unfiled(self, monkeypatch):
        _enable(monkeypatch)
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(linear_client, "_count_files_today", return_value=0),
            patch.object(linear_client, "_create_triage_issue", return_value=None),
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:create_failed"

    def test_create_exception_never_propagates(self, monkeypatch):
        _enable(monkeypatch)
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(linear_client, "_count_files_today", return_value=0),
            patch.object(
                linear_client,
                "_create_triage_issue",
                side_effect=RuntimeError("500"),
            ),
        ):
            result = linear_client.file_finding(_spec())
        # Never raises — fleet code depends on this contract.
        assert result.filed is False
        assert result.action == "skipped:create_failed"


# ─────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────


class TestRendering:
    def test_body_includes_fingerprint_marker(self):
        spec = _spec(fingerprint="qa:test:xyz", body="Some evidence.")
        rendered = linear_client._render_body_with_fingerprint(
            spec, now=datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
        )
        assert "**Fingerprint:**" in rendered
        assert "qa:test:xyz" in rendered
        assert "QA agent" in rendered
        assert "2026-10-26" in rendered

    def test_dedupe_comment_includes_evidence_and_fingerprint(self):
        spec = _spec(fingerprint="qa:test:xyz", body="Saw it again.")
        rendered = linear_client._render_dedupe_comment(
            spec, now=datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
        )
        assert "QA agent saw this again" in rendered
        assert "Saw it again." in rendered
        assert "qa:test:xyz" in rendered

    def test_reopen_comment_uses_new_evidence_and_retains_closure_context(self):
        evidence = "Paper run: `run_new`\n\nTwo orders opened for a single signal."
        spec = _spec(evidence=evidence, body="## Proposed fix\n\nGuard order dispatch.")
        rendered = linear_client._render_reopen_comment(
            spec,
            closed_state="Done",
            closed_at="2026-10-25T10:00:00Z",
            now=datetime(2026, 10, 26, 10, 0, tzinfo=UTC),
        )
        assert "This issue has come back" in rendered
        assert "previously closed as Done on 2026-10-25" in rendered
        assert evidence in rendered
        assert spec.body not in rendered

    def test_search_marker_is_substring_of_rendered_body(self):
        """AFX-76 pin: the string `_search_open_by_fingerprint` sends to
        Linear's `description: { contains }` filter MUST be a substring
        of what `_render_body_with_fingerprint` actually writes.

        Between 2026-10-27 and 2026-11-03 the two drifted (search string
        had no backticks, rendering wrapped the fingerprint in backticks),
        and the dedupe filter never matched a real duplicate. The QA
        agent re-filed the same findings on every scan (12 duplicate
        tickets across 3 real findings). This test closes that gap.
        """
        fingerprint = "qa:test:paper_runner"
        spec = _spec(fingerprint=fingerprint)
        body = linear_client._render_body_with_fingerprint(
            spec, now=datetime(2026, 11, 3, 11, 46, tzinfo=UTC)
        )
        # Mirror the exact construction `_search_open_by_fingerprint`
        # uses — if either side changes shape without the other, this
        # fails and the operator sees the drift before Linear does.
        search_marker = f"{linear_client._FINGERPRINT_MARKER} `{fingerprint}`"
        assert search_marker in body, (
            f"Search marker {search_marker!r} not found in rendered body — "
            "dedupe would fail against this issue in Linear."
        )

    def test_daily_cap_defaults_to_5_on_bad_settings(self, monkeypatch):
        monkeypatch.setattr(
            linear_client.settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", "oops"
        )
        assert linear_client._daily_cap_per_agent() == 5

    def test_daily_cap_zero_is_respected(self, monkeypatch):
        monkeypatch.setattr(
            linear_client.settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", 0
        )
        # 0 means "no filing today" — getattr-or default must not clobber.
        assert linear_client._daily_cap_per_agent() == 0


class TestGqlErrorSurfacing:
    """`_gql` must surface Linear's GraphQL error body on HTTP 400 —
    not just "Bad Request". Linear uses 400 for schema violations
    + complexity-gate rejections (seen 2026-10-27 as "Query too
    complex. Complexity: 15055")."""

    def _mk_client_with_response(self, status_code, json_body):
        """Build a mocked requests.Session returning the given status
        + body on `.post()`."""
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body
        import requests

        if status_code >= 400:

            def _raise():
                err = requests.exceptions.HTTPError(
                    f"{status_code} Client Error: Bad Request"
                )
                raise err

            resp.raise_for_status.side_effect = _raise
        session.post.return_value = resp
        return session

    def test_graphql_error_on_400_surfaces_user_message(self):
        """A 400 with a GraphQL error body should raise with the
        actual message visible (not just HTTP 400)."""
        client = self._mk_client_with_response(
            400,
            {
                "errors": [
                    {
                        "message": "Query too complex",
                        "extensions": {
                            "userPresentableMessage": "Complexity: 15055. Maximum: 10000"
                        },
                    }
                ]
            },
        )
        with pytest.raises(RuntimeError) as exc_info:
            linear_client._gql(client, "query X { a }", {})
        # The real error message must appear in the exception string
        # — otherwise Railway log review shows just "HTTPError 400"
        # and we lose 15 min debugging.
        assert "Query too complex" in str(exc_info.value)
        assert "status=400" in str(exc_info.value)

    def test_200_with_graphql_errors_still_raises(self):
        """GraphQL errors on a 200 (Linear's older behaviour) still raise."""
        client = self._mk_client_with_response(
            200, {"errors": [{"message": "field not found"}]}
        )
        with pytest.raises(RuntimeError) as exc_info:
            linear_client._gql(client, "query X { a }", {})
        assert "field not found" in str(exc_info.value)

    def test_200_with_data_returns_data(self):
        client = self._mk_client_with_response(200, {"data": {"team": {"id": "x"}}})
        result = linear_client._gql(client, "query X { a }", {})
        assert result == {"team": {"id": "x"}}

    def test_400_without_graphql_body_falls_through_to_http_error(self):
        """If the body isn't JSON or has no `errors`, `raise_for_status`
        still runs and raises an HTTPError — don't swallow silently."""
        import requests

        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 400
        resp.json.side_effect = ValueError("not json")
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "400 Bad Request"
        )
        session.post.return_value = resp
        with pytest.raises(requests.exceptions.HTTPError):
            linear_client._gql(session, "query X { a }", {})


# ─────────────────────────────────────────────────────────────────────
# Closed-issue reopening & Duplicate resolution (AFX-149)
# ─────────────────────────────────────────────────────────────────────


class TestReopenClosedIssues:
    """AFX-149: closed issues that recur are reopened to Triage instead
    of re-filed, and Duplicate tickets resolve to their canonical target."""

    def test_open_issue_returns_state_name(self, monkeypatch):
        _enable(monkeypatch)
        existing = {
            "id": "uuid-open",
            "identifier": "AFX-51",
            "url": "https://linear.app/agentfx/issue/AFX-51",
            "state": {"name": "In Progress", "type": "started"},
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=existing
            ),
            patch.object(
                linear_client, "_search_closed_by_fingerprint"
            ) as search_closed,
            patch.object(linear_client, "_add_comment", return_value=True),
            patch.object(linear_client, "_update_issue_state") as update_state,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "commented"
        assert result.identifier == "AFX-51"
        assert result.state_name == "In Progress"
        # Open match takes precedence — closed search and state updates never run.
        search_closed.assert_not_called()
        update_state.assert_not_called()

    @pytest.mark.parametrize(
        "state_name,state_type,closed_field",
        [
            ("Done", "completed", "completedAt"),
            ("Canceled", "canceled", "canceledAt"),
        ],
    )
    def test_closed_done_or_canceled_reopens_to_triage_with_comment(
        self, monkeypatch, state_name, state_type, closed_field
    ):
        _enable(monkeypatch)
        # Even when daily cap is 0, reopens must still proceed.
        monkeypatch.setattr(
            linear_client.settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", 0
        )
        closed_issue = {
            "id": "uuid-closed",
            "identifier": "AFX-88",
            "url": "https://linear.app/agentfx/issue/AFX-88",
            closed_field: "2026-11-20T14:30:00.000Z",
            "state": {"name": state_name, "type": state_type},
            "relations": {"nodes": []},
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_search_closed_by_fingerprint",
                return_value=[closed_issue],
            ),
            patch.object(
                linear_client, "_resolve_triage_state_id", return_value="state-triage"
            ),
            patch.object(
                linear_client, "_update_issue_state", return_value=True
            ) as update_state,
            patch.object(
                linear_client, "_add_comment", return_value=True
            ) as add_comment,
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(
                _spec(), now=datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
            )
        assert result.filed is True
        assert result.action == "reopened"
        assert result.identifier == "AFX-88"
        assert result.url == "https://linear.app/agentfx/issue/AFX-88"
        assert result.state_name == state_name
        update_state.assert_called_once()
        assert update_state.call_args.kwargs["issue_id"] == "uuid-closed"
        assert update_state.call_args.kwargs["state_id"] == "state-triage"
        add_comment.assert_called_once()
        comment_body = add_comment.call_args.kwargs["body"]
        assert "come back" in comment_body.lower()
        assert "2026-11-20" in comment_body
        assert "Some details." in comment_body
        create.assert_not_called()

    def test_prefers_done_over_canceled_and_most_recent_within_tier(self, monkeypatch):
        _enable(monkeypatch)
        canceled_newer = {
            "id": "uuid-can",
            "identifier": "AFX-75",
            "url": "https://linear.app/agentfx/issue/AFX-75",
            "canceledAt": "2026-11-24T00:00:00Z",
            "state": {"name": "Canceled", "type": "canceled"},
            "relations": {"nodes": []},
        }
        done_older = {
            "id": "uuid-done-1",
            "identifier": "AFX-58",
            "url": "https://linear.app/agentfx/issue/AFX-58",
            "completedAt": "2026-11-10T00:00:00Z",
            "state": {"name": "Done", "type": "completed"},
            "relations": {"nodes": []},
        }
        done_newer = {
            "id": "uuid-done-2",
            "identifier": "AFX-60",
            "url": "https://linear.app/agentfx/issue/AFX-60",
            "completedAt": "2026-11-22T00:00:00Z",
            "state": {"name": "Done", "type": "completed"},
            "relations": {"nodes": []},
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_search_closed_by_fingerprint",
                return_value=[canceled_newer, done_older, done_newer],
            ),
            patch.object(
                linear_client, "_resolve_triage_state_id", return_value="state-triage"
            ),
            patch.object(
                linear_client, "_update_issue_state", return_value=True
            ) as update_state,
            patch.object(linear_client, "_add_comment", return_value=True),
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "reopened"
        assert result.identifier == "AFX-60"
        assert update_state.call_args.kwargs["issue_id"] == "uuid-done-2"

    def test_duplicate_with_open_target_comments_on_target(self, monkeypatch):
        _enable(monkeypatch)
        dup_issue = {
            "id": "uuid-dup",
            "identifier": "AFX-90",
            "url": "https://linear.app/agentfx/issue/AFX-90",
            "canceledAt": "2026-11-21T00:00:00Z",
            "state": {"name": "Duplicate", "type": "duplicate"},
            "relations": {
                "nodes": [
                    {
                        "type": "duplicate",
                        "relatedIssue": {
                            "id": "uuid-canon",
                            "identifier": "AFX-49",
                            "url": "https://linear.app/agentfx/issue/AFX-49",
                            "state": {"name": "In Progress", "type": "started"},
                        },
                    }
                ]
            },
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_search_closed_by_fingerprint",
                return_value=[dup_issue],
            ),
            patch.object(
                linear_client, "_add_comment", return_value=True
            ) as add_comment,
            patch.object(linear_client, "_update_issue_state") as update_state,
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "commented"
        assert result.identifier == "AFX-49"
        assert result.state_name == "In Progress"
        add_comment.assert_called_once()
        assert add_comment.call_args.kwargs["issue_id"] == "uuid-canon"
        update_state.assert_not_called()
        create.assert_not_called()

    def test_duplicate_with_closed_target_reopens_target_not_duplicate(
        self, monkeypatch
    ):
        _enable(monkeypatch)
        dup_issue = {
            "id": "uuid-dup",
            "identifier": "AFX-91",
            "url": "https://linear.app/agentfx/issue/AFX-91",
            "canceledAt": "2026-11-21T00:00:00Z",
            "state": {"name": "Duplicate", "type": "duplicate"},
            "relations": {
                "nodes": [
                    {
                        "type": "duplicate",
                        "relatedIssue": {
                            "id": "uuid-canon",
                            "identifier": "AFX-50",
                            "url": "https://linear.app/agentfx/issue/AFX-50",
                            "completedAt": "2026-11-18T00:00:00Z",
                            "state": {"name": "Done", "type": "completed"},
                        },
                    }
                ]
            },
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_search_closed_by_fingerprint",
                return_value=[dup_issue],
            ),
            patch.object(
                linear_client, "_resolve_triage_state_id", return_value="state-triage"
            ),
            patch.object(
                linear_client, "_update_issue_state", return_value=True
            ) as update_state,
            patch.object(
                linear_client, "_add_comment", return_value=True
            ) as add_comment,
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert result.action == "reopened"
        assert result.identifier == "AFX-50"
        assert result.state_name == "Done"
        assert update_state.call_args.kwargs["issue_id"] == "uuid-canon"
        assert add_comment.call_args.kwargs["issue_id"] == "uuid-canon"
        create.assert_not_called()

    def test_reopen_failure_does_not_create_duplicate(self, monkeypatch):
        _enable(monkeypatch)
        closed_issue = {
            "id": "uuid-closed",
            "identifier": "AFX-88",
            "url": "https://linear.app/agentfx/issue/AFX-88",
            "completedAt": "2026-11-20T00:00:00Z",
            "state": {"name": "Done", "type": "completed"},
            "relations": {"nodes": []},
        }
        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client,
                "_search_closed_by_fingerprint",
                return_value=[closed_issue],
            ),
            patch.object(
                linear_client, "_resolve_triage_state_id", return_value="state-triage"
            ),
            patch.object(linear_client, "_update_issue_state", return_value=False),
            patch.object(linear_client, "_create_triage_issue") as create,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:reopen_failed"
        create.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Identity: file as the AgentFX QA app (AFX-174)
# ─────────────────────────────────────────────────────────────────────


def _resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.raise_for_status.return_value = None
    return r


class TestQaIdentity:
    @pytest.fixture(autouse=True)
    def _reset(self):
        linear_client.linear_agent_api._reset_token_cache_for_tests()
        yield
        linear_client.linear_agent_api._reset_token_cache_for_tests()

    def _flags(self, monkeypatch, *, qa=True, pe=True):
        s = linear_client.settings
        monkeypatch.setattr(s, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(s, "LINEAR_QA_CLIENT_ID", "qa-id" if qa else "")
        monkeypatch.setattr(s, "LINEAR_QA_CLIENT_SECRET", "qa-secret" if qa else "")
        monkeypatch.setattr(s, "LINEAR_AGENT_CLIENT_ID", "pe-id" if pe else "")
        monkeypatch.setattr(s, "LINEAR_AGENT_CLIENT_SECRET", "pe-secret" if pe else "")

    def test_filing_uses_qa_token_as_bearer(self, monkeypatch):
        self._flags(monkeypatch)
        token_calls = []

        def fake_token_post(url, data=None, **_kw):
            token_calls.append(data)
            return _resp(200, {"access_token": "qa-tok", "expires_in": 3600})

        sent = []

        def fake_session_post(self, url, **kw):
            sent.append(dict(self.headers))
            return _resp(200, {"data": {}})

        def fake_create(client, **_k):
            client.post("https://api.linear.app/graphql")
            return {"id": "i", "identifier": "AFX-1"}

        with (
            patch.object(
                linear_client.linear_agent_api.requests, "post", fake_token_post
            ),
            patch("requests.Session.post", fake_session_post),
            patch.object(
                linear_client, "_search_open_by_fingerprint", return_value=None
            ),
            patch.object(
                linear_client, "_search_closed_by_fingerprint", return_value=[]
            ),
            patch.object(linear_client, "_count_files_today", return_value=0),
            patch.object(linear_client, "_create_triage_issue", fake_create),
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is True
        assert [c["client_id"] for c in token_calls] == ["qa-id"]
        assert token_calls[0]["scope"] == "read,write"
        assert sent[0]["Authorization"] == "Bearer qa-tok"

    def test_missing_qa_pair_files_nothing_and_never_uses_pe(self, monkeypatch):
        self._flags(monkeypatch, qa=False, pe=True)
        with (
            patch.object(linear_client.linear_agent_api.requests, "post") as tok,
            patch("requests.Session.post") as gql,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:no_credentials"
        tok.assert_not_called()
        gql.assert_not_called()

    def test_token_endpoint_failure_files_nothing_and_leaks_nothing(
        self, monkeypatch, caplog
    ):
        self._flags(monkeypatch)
        caplog.set_level("DEBUG")
        with (
            patch.object(
                linear_client.linear_agent_api.requests,
                "post",
                return_value=_resp(500),
            ),
            patch("requests.Session.post") as gql,
        ):
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:token_error"
        gql.assert_not_called()
        blob = caplog.text + result.reason + result.action
        assert "qa-secret" not in blob

    def test_401_forces_one_refresh_and_one_retry(self, monkeypatch):
        self._flags(monkeypatch)
        minted = iter(["tok-1", "tok-2"])
        token_calls = []

        def fake_token_post(url, data=None, **_kw):
            token_calls.append(data["client_id"])
            return _resp(200, {"access_token": next(minted), "expires_in": 3600})

        auth_seen = []
        statuses = iter([401, 200])

        def fake_session_post(self, url, **kw):
            auth_seen.append(self.headers["Authorization"])
            return _resp(next(statuses), {"data": {"ok": 1}})

        with (
            patch.object(
                linear_client.linear_agent_api.requests, "post", fake_token_post
            ),
            patch("requests.Session.post", fake_session_post),
        ):
            token, _ = linear_client.linear_agent_api.get_qa_token()
            client = linear_client._build_client(
                token, refresh=linear_client._refresh_qa_token
            )
            data = linear_client._gql(client, "query {}", {})
        assert data == {"ok": 1}
        assert auth_seen == ["Bearer tok-1", "Bearer tok-2"]
        assert token_calls == ["qa-id", "qa-id"]
