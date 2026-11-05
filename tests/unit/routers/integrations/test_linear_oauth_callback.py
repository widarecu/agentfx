"""Unit tests for the Linear OAuth install callback (AFX-66).

HTTP-level tests against a minimal FastAPI app that mounts only the
Linear integration router. The outbound request to Linear's token
endpoint is mocked via `patch.object(linear_router.requests, "post")`,
so no network I/O occurs.

These tests pin the install-finalisation contract:

- happy path: valid code → POSTs correct body to Linear's token
  endpoint → returns 200 success page.
- user denied consent: `?error=access_denied` → 400 explain page.
- missing code (malformed redirect): 400.
- missing client credentials in settings: 500 (misconfiguration).
- Linear token endpoint returns 4xx/5xx: 502 with the Linear body
  surfaced in the page.
- Linear unreachable (RequestException): 502 with the exception
  class name surfaced in the page.

A regression pin asserts the `redirect_uri` sent to Linear matches
the URL the request landed on — a cosmetic mismatch is the #1
cause of `invalid_grant` on these flows.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentfx.api.core.config import settings
from agentfx.api.routers.integrations import linear as linear_router

pytestmark = pytest.mark.unit


_CLIENT_ID = "79063897d4b9daf1ed293422373a3c09"
_CLIENT_SECRET = "lin_client_secret_test_fake"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """TestClient with the router mounted + Linear OAuth creds pinned."""
    monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_SECRET", _CLIENT_SECRET)

    app = FastAPI()
    app.include_router(
        linear_router.router,
        prefix="/api/v1/integrations/linear",
    )
    return TestClient(app)


def _mock_token_response(
    status: int = 200,
    body: str = '{"access_token":"lin_tok_fake","token_type":"Bearer"}',
):
    """Minimal MagicMock standing in for a requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = body
    return resp


class TestHappyPath:
    def test_valid_code_exchanges_and_returns_success_page(self, client):
        """`?code=X` → POST to Linear token endpoint → 200 success HTML."""
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ) as mock_post:
            resp = client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "lin_code_abc123", "state": "install-test"},
            )

        assert resp.status_code == 200
        assert "Install complete" in resp.text
        assert "AgentFX Platform Engineer" in resp.text
        mock_post.assert_called_once()

        # Pin the exact body sent to Linear's token endpoint.
        call = mock_post.call_args
        assert call.args[0] == linear_router._LINEAR_TOKEN_URL
        sent = call.kwargs["data"]
        assert sent["grant_type"] == "authorization_code"
        assert sent["code"] == "lin_code_abc123"
        assert sent["client_id"] == _CLIENT_ID
        assert sent["client_secret"] == _CLIENT_SECRET
        # redirect_uri derived from the TestClient's base URL +
        # the callback path. Mismatch here == Linear rejects with
        # invalid_grant in production.
        assert sent["redirect_uri"].endswith(
            "/api/v1/integrations/linear/oauth/callback"
        )
        assert "?" not in sent["redirect_uri"], (
            "redirect_uri must not carry the query string back"
        )

    def test_success_response_content_type_is_html(self, client):
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ):
            resp = client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
            )
        assert resp.headers["content-type"].startswith("text/html")


class TestUserDeniedConsent:
    def test_access_denied_error_returns_400_with_explain_page(self, client):
        """User clicked Cancel on Linear's consent screen → ?error=..."""
        resp = client.get(
            "/api/v1/integrations/linear/oauth/callback",
            params={
                "error": "access_denied",
                "error_description": "user cancelled",
            },
        )
        assert resp.status_code == 400
        assert "access_denied" in resp.text
        assert "user cancelled" in resp.text
        assert "Install failed" in resp.text


class TestMalformedRedirect:
    def test_missing_code_without_error_returns_400(self, client):
        resp = client.get("/api/v1/integrations/linear/oauth/callback")
        assert resp.status_code == 400
        assert "No authorization code" in resp.text


class TestMisconfiguration:
    def test_missing_client_credentials_returns_500(self, monkeypatch, client):
        """If LINEAR_AGENT_CLIENT_ID or _SECRET is empty we can't
        exchange the code — fail loud with 500 rather than silently
        accept the install never completing."""
        monkeypatch.setattr(settings, "LINEAR_AGENT_CLIENT_ID", "")
        resp = client.get(
            "/api/v1/integrations/linear/oauth/callback",
            params={"code": "abc"},
        )
        assert resp.status_code == 500
        assert "LINEAR_AGENT_CLIENT_ID" in resp.text


class TestLinearError:
    def test_linear_returns_400_bubbles_up_as_502(self, client):
        """Linear returning invalid_grant / expired code / redirect
        mismatch must not look like OUR server erroring. 502 is the
        honest status for upstream failure."""
        body = '{"error":"invalid_grant","error_description":"code expired"}'
        with patch.object(
            linear_router.requests,
            "post",
            return_value=_mock_token_response(status=400, body=body),
        ):
            resp = client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "stale_code"},
            )
        assert resp.status_code == 502
        assert "HTTP 400" in resp.text
        assert "invalid_grant" in resp.text
        # redirect_uri hint surfaced in the page so the operator
        # doesn't have to guess what to check first.
        assert "redirect_uri" in resp.text

    def test_linear_unreachable_returns_502(self, client):
        """Network error reaching Linear = 502 with the exception
        class in the page."""
        with patch.object(
            linear_router.requests,
            "post",
            side_effect=requests.ConnectionError("dns fail"),
        ):
            resp = client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
            )
        assert resp.status_code == 502
        assert "ConnectionError" in resp.text


class TestHelpers:
    def test_render_install_page_escapes_structure(self):
        """Smoke test: the renderer builds valid HTML with the title
        and body embedded."""
        html = linear_router._render_install_page(
            title="Hello world", body="Install OK"
        )
        assert "<!doctype html>" in html
        assert "<title>Hello world</title>" in html
        assert "Install OK" in html


class TestRedirectUriProxyScheme:
    """Regression pin for the Railway TLS-termination case.

    Seen in production 2026-11-05: Railway's edge terminates TLS and
    forwards the request as http to FastAPI. Without honoring
    X-Forwarded-Proto, `_derive_redirect_uri` sent
    `http://agentfx-staging.up.railway.app/...` to Linear's token
    endpoint; Linear's App had `https://...` registered and rejected
    the exchange with `invalid_request: redirect_uri mismatch`.

    Any future refactor of `_derive_redirect_uri` that drops the
    X-Forwarded-Proto handling trips this test before the next deploy.
    """

    def test_x_forwarded_proto_https_overrides_inbound_scheme(self, client):
        """Mimic Railway: inbound http, X-Forwarded-Proto: https."""
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ) as mock_post:
            resp = client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
                headers={"X-Forwarded-Proto": "https"},
            )
        assert resp.status_code == 200
        sent_redirect = mock_post.call_args.kwargs["data"]["redirect_uri"]
        assert sent_redirect.startswith("https://"), (
            f"expected https:// scheme from X-Forwarded-Proto header, "
            f"got {sent_redirect!r}"
        )

    def test_x_forwarded_proto_chain_takes_leftmost(self, client):
        """Multiple proxies chained → first value is client-facing
        scheme per RFC 7239. Linear sees whatever the user's browser
        connected over, which is always the leftmost."""
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ) as mock_post:
            client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
                headers={"X-Forwarded-Proto": "https, http"},
            )
        sent_redirect = mock_post.call_args.kwargs["data"]["redirect_uri"]
        assert sent_redirect.startswith("https://")

    def test_no_forwarded_proto_keeps_inbound_scheme(self, client):
        """Local dev: no proxy header → use whatever FastAPI saw.
        TestClient uses http, so the redirect stays http."""
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ) as mock_post:
            client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
            )
        sent_redirect = mock_post.call_args.kwargs["data"]["redirect_uri"]
        assert sent_redirect.startswith("http://")

    def test_malformed_forwarded_proto_is_ignored(self, client):
        """Garbage value (e.g. `ws`) must NOT corrupt the scheme —
        fall back to the inbound scheme."""
        with patch.object(
            linear_router.requests, "post", return_value=_mock_token_response()
        ) as mock_post:
            client.get(
                "/api/v1/integrations/linear/oauth/callback",
                params={"code": "abc"},
                headers={"X-Forwarded-Proto": "ws"},
            )
        sent_redirect = mock_post.call_args.kwargs["data"]["redirect_uri"]
        assert sent_redirect.startswith("http://"), (
            "malformed X-Forwarded-Proto must not override the real scheme"
        )
