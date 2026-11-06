"""Unit tests for `integrations.linear_agent_api` (AFX-66 Phase 2).

HTTP calls are mocked via `patch.object(linear_agent_api.requests, ...)`.
No network I/O occurs. Token cache is reset between tests to prevent
state bleed through the module-level singleton.

Three pin areas:

1. Token acquisition: happy path, caching, forced refresh on 401,
   missing credentials, HTTP error, malformed response.
2. Activity emission: happy path, 401-retry-with-refresh, GraphQL
   errors, HTTP error, missing session id / body.
3. Convenience wrappers set the right content_type.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from agentfx.api.core.config import settings
from agentfx.integrations import linear_agent_api

pytestmark = pytest.mark.unit


_CLIENT_ID = "79063897d4b9daf1ed293422373a3c09"
_CLIENT_SECRET = "lin_client_secret_fake"
_SESSION_ID = "3ed78027-6d3c-4727-ba12-a47367e7563f"


@pytest.fixture(autouse=True)
def _reset_cache_and_pin_creds(monkeypatch):
    """Every test starts with an empty token cache + pinned creds."""
    monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_SECRET", _CLIENT_SECRET)
    linear_agent_api._reset_token_cache_for_tests()
    yield
    linear_agent_api._reset_token_cache_for_tests()


def _mock_response(status: int, json_body=None, text_body: str = ""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("not json")
    resp.text = text_body
    return resp


def _token_ok_body():
    return {
        "access_token": "lin_app_token_fake_abc123",
        "token_type": "Bearer",
        "expires_in": 2_592_000,  # 30 days in seconds
        "scope": "read,app:assignable,app:mentionable,comments:create,issues:create",
    }


def _activity_ok_body():
    return {
        "data": {
            "agentActivityCreate": {
                "success": True,
                "agentActivity": {"id": "act_fake_1"},
            }
        }
    }


# ─────────────────────────────────────────────────────────────────────
# get_app_token
# ─────────────────────────────────────────────────────────────────────


class TestGetAppToken:
    def test_missing_credentials_returns_no_credentials(self, monkeypatch):
        monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_ID", "")
        token, err = linear_agent_api.get_app_token()
        assert token is None
        assert err == "no_credentials"

    def test_happy_path_returns_token_and_caches(self):
        """First call fetches; second call reuses cache without a
        second HTTP request."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, _token_ok_body()),
        ) as mock_post:
            t1, e1 = linear_agent_api.get_app_token()
            t2, e2 = linear_agent_api.get_app_token()

        assert t1 == "lin_app_token_fake_abc123"
        assert e1 == ""
        assert t2 == t1
        assert e2 == ""
        assert mock_post.call_count == 1  # cached

    def test_token_body_shape_sent_to_linear(self):
        """Pin the POST body — client_credentials grant, both
        client_id and client_secret, scope included."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, _token_ok_body()),
        ) as mock_post:
            linear_agent_api.get_app_token()

        call = mock_post.call_args
        assert call.args[0] == linear_agent_api._LINEAR_TOKEN_URL
        sent = call.kwargs["data"]
        assert sent["grant_type"] == "client_credentials"
        assert sent["client_id"] == _CLIENT_ID
        assert sent["client_secret"] == _CLIENT_SECRET
        # AFX-66 2026-11-06: `write` is required by
        # agentActivityCreate. Without it Linear returns
        # `Invalid scope: write required` on every mutation.
        assert "write" in sent["scope"]
        assert "app:assignable" in sent["scope"]
        assert "app:mentionable" in sent["scope"]

    def test_force_refresh_bypasses_cache(self):
        """When a caller passes force_refresh=True, we always hit
        the network — used after a 401 to get a fresh token."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, _token_ok_body()),
        ) as mock_post:
            linear_agent_api.get_app_token()
            linear_agent_api.get_app_token(force_refresh=True)

        assert mock_post.call_count == 2

    def test_http_error_returns_reason(self):
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(401, text_body="unauthorized"),
        ):
            token, err = linear_agent_api.get_app_token()
        assert token is None
        assert err == "token_endpoint:HTTP 401"

    def test_request_exception_returns_reason(self):
        with patch.object(
            linear_agent_api.requests,
            "post",
            side_effect=requests.ConnectionError("dns fail"),
        ):
            token, err = linear_agent_api.get_app_token()
        assert token is None
        assert err == "token_endpoint:ConnectionError"

    def test_malformed_json_returns_malformed_response(self):
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, json_body=None),
        ):
            token, err = linear_agent_api.get_app_token()
        assert token is None
        assert err == "malformed_response"

    def test_missing_access_token_in_response_is_malformed(self):
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, {"token_type": "Bearer"}),
        ):
            token, err = linear_agent_api.get_app_token()
        assert token is None
        assert err == "malformed_response"

    def test_near_expiry_cached_token_triggers_refetch(self):
        """A cached token that expires within the refresh buffer
        window (60s) is treated as stale — re-fetched on next call.
        This prevents sending a token that expires mid-request."""
        # Seed the cache with a token that expires in 10 seconds.
        linear_agent_api._token_cache = linear_agent_api._CachedToken(
            token="stale_token",
            expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(200, _token_ok_body()),
        ) as mock_post:
            token, _ = linear_agent_api.get_app_token()
        assert token == "lin_app_token_fake_abc123"
        assert mock_post.called


# ─────────────────────────────────────────────────────────────────────
# emit_agent_activity
# ─────────────────────────────────────────────────────────────────────


class TestEmitAgentActivity:
    def test_missing_session_id_returns_reason(self):
        ok, err = linear_agent_api.emit_agent_activity(
            agent_session_id="", content_type="thought", body="x"
        )
        assert ok is False
        assert err == "no_session_id"

    def test_empty_body_returns_reason(self):
        ok, err = linear_agent_api.emit_agent_activity(
            agent_session_id=_SESSION_ID, content_type="thought", body=""
        )
        assert ok is False
        assert err == "empty_body"

    def test_happy_path_posts_mutation_and_returns_ok(self):
        """Pin the full outbound shape: token acquired, Bearer header
        set, mutation + variables sent, response parsed."""
        with (
            patch.object(
                linear_agent_api.requests,
                "post",
                side_effect=[
                    _mock_response(200, _token_ok_body()),
                    _mock_response(200, _activity_ok_body()),
                ],
            ) as mock_post,
        ):
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID,
                content_type="thought",
                body="Picking up AFX-8, scoping...",
            )
        assert ok is True
        assert err == ""
        # Second call was the mutation — pin its shape.
        gql_call = mock_post.call_args_list[1]
        assert gql_call.args[0] == linear_agent_api._LINEAR_GRAPHQL_URL
        assert (
            gql_call.kwargs["headers"]["Authorization"]
            == "Bearer lin_app_token_fake_abc123"
        )
        body = gql_call.kwargs["json"]
        assert "agentActivityCreate" in body["query"]
        assert body["variables"]["input"]["agentSessionId"] == _SESSION_ID
        assert body["variables"]["input"]["content"] == {
            "type": "thought",
            "body": "Picking up AFX-8, scoping...",
        }

    def test_401_triggers_token_refresh_and_retry(self):
        """A 401 on the mutation refreshes the token and retries
        once — covers the revoked / rotated secret mid-session case."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            side_effect=[
                # First attempt: token fetch (cold cache)
                _mock_response(200, _token_ok_body()),
                # Mutation returns 401 (stale token)
                _mock_response(401, text_body="unauthorized"),
                # Second attempt: forced refresh
                _mock_response(200, _token_ok_body()),
                # Mutation now succeeds
                _mock_response(200, _activity_ok_body()),
            ],
        ) as mock_post:
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID,
                content_type="thought",
                body="retry test",
            )
        assert ok is True
        assert err == ""
        assert mock_post.call_count == 4

    def test_graphql_error_surfaces_message(self):
        with patch.object(
            linear_agent_api.requests,
            "post",
            side_effect=[
                _mock_response(200, _token_ok_body()),
                _mock_response(
                    200,
                    {"errors": [{"message": "agentSession not found"}]},
                ),
            ],
        ):
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID, content_type="thought", body="x"
            )
        assert ok is False
        assert err == "graphql:agentSession not found"

    def test_non_200_returns_http_reason_with_body_snippet(self):
        """Non-2xx responses from Linear's GraphQL endpoint usually
        carry the exact cause in the body (missing scope, bad input,
        etc.). The reason string now embeds a body snippet so the
        operator can diagnose from the dispatcher's log line without
        having to tail Linear or curl the API by hand.

        Staging 2026-11-06: emit_thought on real delegation was
        returning bare `HTTP 400` with no diagnostic — this test
        pins the richer format."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            side_effect=[
                _mock_response(200, _token_ok_body()),
                _mock_response(
                    500,
                    text_body='{"errors":["Internal server error"]}',
                ),
            ],
        ):
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID, content_type="thought", body="x"
            )
        assert ok is False
        assert err.startswith("HTTP 500:")
        assert "Internal server error" in err

    def test_success_false_in_response_is_an_error(self):
        """Linear's mutation returning success:false is not ok."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            side_effect=[
                _mock_response(200, _token_ok_body()),
                _mock_response(
                    200,
                    {"data": {"agentActivityCreate": {"success": False}}},
                ),
            ],
        ):
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID, content_type="thought", body="x"
            )
        assert ok is False
        assert err == "mutation_returned_success_false"

    def test_token_fetch_failure_short_circuits(self):
        """If we can't get a token, don't attempt the mutation at all."""
        with patch.object(
            linear_agent_api.requests,
            "post",
            return_value=_mock_response(500, text_body="x"),
        ) as mock_post:
            ok, err = linear_agent_api.emit_agent_activity(
                agent_session_id=_SESSION_ID, content_type="thought", body="x"
            )
        assert ok is False
        assert err == "token:token_endpoint:HTTP 500"
        # Only one POST (the failed token fetch) — no mutation attempt.
        assert mock_post.call_count == 1


# ─────────────────────────────────────────────────────────────────────
# Convenience wrappers
# ─────────────────────────────────────────────────────────────────────


class TestWrappers:
    def test_emit_thought_sets_content_type_thought(self):
        with patch.object(
            linear_agent_api, "emit_agent_activity", return_value=(True, "")
        ) as mock_emit:
            linear_agent_api.emit_thought(_SESSION_ID, "picked up")
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["content_type"] == "thought"
        assert kwargs["agent_session_id"] == _SESSION_ID
        assert kwargs["body"] == "picked up"

    def test_emit_error_sets_content_type_error(self):
        with patch.object(
            linear_agent_api, "emit_agent_activity", return_value=(True, "")
        ) as mock_emit:
            linear_agent_api.emit_error(_SESSION_ID, "boom")
        assert mock_emit.call_args.kwargs["content_type"] == "error"

    def test_emit_response_sets_content_type_response(self):
        with patch.object(
            linear_agent_api, "emit_agent_activity", return_value=(True, "")
        ) as mock_emit:
            linear_agent_api.emit_response(_SESSION_ID, "shipped PR #99")
        assert mock_emit.call_args.kwargs["content_type"] == "response"
