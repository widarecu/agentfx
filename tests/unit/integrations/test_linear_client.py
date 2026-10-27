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
    monkeypatch.setattr(linear_client.settings, "LINEAR_API_KEY", "lk_test_token")
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

    def test_no_api_key_skips(self, monkeypatch):
        monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_client.settings, "LINEAR_API_KEY", "")
        with patch.object(linear_client, "_build_client") as build:
            result = linear_client.file_finding(_spec())
        assert result.filed is False
        assert result.action == "skipped:no_key"
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
