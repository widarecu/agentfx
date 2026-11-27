"""GitHub App token minting and its use in PE dispatch (AFX-172). No network."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import github_app, pe_workspace
from agentfx.api.core.database import Base
from agentfx.api.models import AgentWorkspaceRun, AgentWorkspaceRunStatus

_PERSONAL = "ghp_PERSONALTOKENabcdefghijklmnopqrstuvwxyz"
_MINTED = "ghs_MINTEDTOKENabcdefghijklmnopqrstuvwxyz0123"
_CLIENT_ID = "Iv23liTESTCLIENT"


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


@pytest.fixture(autouse=True)
def _clean_minted():
    github_app._minted.clear()
    yield
    github_app._minted.clear()


def _resp(status: int, body: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


def _set_app(monkeypatch, private: str, client_id: str = _CLIENT_ID):
    monkeypatch.setattr(github_app.settings, "AGENT_PE_GITHUB_APP_CLIENT_ID", client_id)
    monkeypatch.setattr(github_app.settings, "AGENT_PE_GITHUB_APP_PRIVATE_KEY", private)


class TestMint:
    def test_happy_path(self, monkeypatch, keys):
        private, public = keys
        _set_app(monkeypatch, private)
        get = MagicMock(return_value=_resp(200, {"id": 4242}))
        post = MagicMock(
            return_value=_resp(
                201, {"token": _MINTED, "expires_at": "2026-11-28T10:00:00Z"}
            )
        )
        monkeypatch.setattr(github_app.requests, "get", get)
        monkeypatch.setattr(github_app.requests, "post", post)

        out = github_app.mint_installation_token(owner="widarecu", repo="agentfx")

        assert out.ok and out.token == _MINTED
        assert out.expires_at == "2026-11-28T10:00:00Z"
        assert get.call_args.args[0] == (
            "https://api.github.com/repos/widarecu/agentfx/installation"
        )
        assert post.call_args.args[0] == (
            "https://api.github.com/app/installations/4242/access_tokens"
        )
        assert post.call_args.kwargs["json"] == {
            "repositories": ["agentfx"],
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            },
        }
        bearer = get.call_args.kwargs["headers"]["Authorization"].removeprefix(
            "Bearer "
        )
        claims = jwt.decode(bearer, public, algorithms=["RS256"])
        assert claims["iss"] == _CLIENT_ID
        assert claims["exp"] - claims["iat"] <= 10 * 60 + 60
        assert claims["exp"] - claims["iat"] > 60
        assert github_app.minted_tokens() == [_MINTED]

    def test_pem_with_literal_backslash_n(self, monkeypatch, keys):
        private, public = keys
        _set_app(monkeypatch, private.strip().replace("\n", "\\n"))
        monkeypatch.setattr(
            github_app.requests, "get", MagicMock(return_value=_resp(200, {"id": 1}))
        )
        monkeypatch.setattr(
            github_app.requests,
            "post",
            MagicMock(return_value=_resp(201, {"token": _MINTED})),
        )
        assert github_app.mint_installation_token(owner="o", repo="r").ok

    def test_bad_key(self, monkeypatch):
        _set_app(
            monkeypatch,
            "-----BEGIN RSA PRIVATE KEY-----\nnot a key\n-----END RSA PRIVATE KEY-----",
        )
        get = MagicMock()
        monkeypatch.setattr(github_app.requests, "get", get)
        out = github_app.mint_installation_token(owner="o", repo="r")
        assert not out.ok
        assert "valid RSA PEM" in out.reason
        assert "not a key" not in out.reason
        get.assert_not_called()

    def test_installation_404(self, monkeypatch, keys):
        _set_app(monkeypatch, keys[0])
        monkeypatch.setattr(
            github_app.requests,
            "get",
            MagicMock(return_value=_resp(404, {"message": "Not Found"})),
        )
        out = github_app.mint_installation_token(owner="o", repo="r")
        assert not out.ok
        assert "HTTP 404" in out.reason and "installed" in out.reason

    def test_token_response_not_201(self, monkeypatch, keys):
        _set_app(monkeypatch, keys[0])
        monkeypatch.setattr(
            github_app.requests, "get", MagicMock(return_value=_resp(200, {"id": 1}))
        )
        monkeypatch.setattr(
            github_app.requests,
            "post",
            MagicMock(return_value=_resp(422, {"message": "Validation Failed"})),
        )
        out = github_app.mint_installation_token(owner="o", repo="r")
        assert not out.ok and "HTTP 422" in out.reason

    def test_network_error(self, monkeypatch, keys):
        _set_app(monkeypatch, keys[0])
        monkeypatch.setattr(
            github_app.requests,
            "get",
            MagicMock(side_effect=requests.ConnectionError("boom")),
        )
        out = github_app.mint_installation_token(owner="o", repo="r")
        assert not out.ok and "network error" in out.reason

    def test_secrets_never_logged_or_in_reason(self, monkeypatch, keys, caplog):
        private, _ = keys
        _set_app(monkeypatch, private)
        get = MagicMock(return_value=_resp(200, {"id": 1}))
        post = MagicMock(return_value=_resp(201, {"token": _MINTED}))
        monkeypatch.setattr(github_app.requests, "get", get)
        monkeypatch.setattr(github_app.requests, "post", post)
        with caplog.at_level(logging.DEBUG):
            github_app.mint_installation_token(owner="o", repo="r")
        bearer = get.call_args.kwargs["headers"]["Authorization"].removeprefix(
            "Bearer "
        )
        for record in caplog.records:
            text = record.getMessage()
            assert _MINTED not in text
            assert bearer not in text
            assert "PRIVATE KEY" not in text


class TestAppState:
    @pytest.mark.parametrize(
        "cid,key,expected",
        [("", "", "off"), ("a", "", "partial"), ("", "k", "partial"), ("a", "k", "on")],
    )
    def test_state(self, monkeypatch, cid, key, expected):
        monkeypatch.setattr(github_app.settings, "AGENT_PE_GITHUB_APP_CLIENT_ID", cid)
        monkeypatch.setattr(github_app.settings, "AGENT_PE_GITHUB_APP_PRIVATE_KEY", key)
        assert github_app.app_state() == expected


class TestDispatchWithApp:
    @pytest.fixture
    def db(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            yield s

    @pytest.fixture
    def fake_client(self, monkeypatch):
        session = MagicMock()
        session.id = "sess_1"
        client = MagicMock()
        client.beta.sessions.create.return_value = session
        import anthropic

        monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: client, raising=True)
        return client

    def _configure(self, monkeypatch, *, pat=_PERSONAL, cid="", key=""):
        s = pe_workspace.settings
        monkeypatch.setattr(s, "AGENT_PE_WORKSPACE_AGENT_ID", "agent_abc")
        monkeypatch.setattr(s, "AGENT_PE_WORKSPACE_ENVIRONMENT_ID", "env_xyz")
        monkeypatch.setattr(s, "AGENT_PE_WORKSPACE_VAULT_ID", "vault_123")
        monkeypatch.setattr(s, "GITHUB_TOKEN", pat)
        monkeypatch.setattr(s, "AGENT_PE_GITHUB_APP_CLIENT_ID", cid)
        monkeypatch.setattr(s, "AGENT_PE_GITHUB_APP_PRIVATE_KEY", key)

    def _spec(self):
        from tests.unit.agents.fleet.test_pe_workspace import _spec

        return _spec()

    def test_app_token_is_the_resource_token(self, db, monkeypatch, fake_client):
        self._configure(monkeypatch, cid="cid", key="pem")
        mint = MagicMock(return_value=github_app.MintResult(token=_MINTED))
        monkeypatch.setattr(github_app, "mint_installation_token", mint)
        result = pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=self._spec())
        assert result.provider_run_id == "sess_1"
        mint.assert_called_once_with(owner="widarecu", repo="agentfx")
        kwargs = fake_client.beta.sessions.create.call_args.kwargs
        assert kwargs["resources"][0]["authorization_token"] == _MINTED
        # Route B (the vault's `gh` credential) cannot be changed from the
        # SDK, so the backend must not touch vault credentials.
        fake_client.beta.vaults.credentials.update.assert_not_called()
        fake_client.beta.vaults.credentials.create.assert_not_called()
        kickoff = fake_client.beta.sessions.events.send.call_args.kwargs["events"][0][
            "content"
        ][0]["text"]
        assert "one hour" in kickoff

    def test_mint_failure_refuses_without_personal_fallback(
        self, db, monkeypatch, fake_client
    ):
        self._configure(monkeypatch, cid="cid", key="pem")
        monkeypatch.setattr(
            github_app,
            "mint_installation_token",
            MagicMock(return_value=github_app.MintResult(reason="HTTP 404 nope")),
        )
        pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=self._spec())
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "GitHub App token could not be minted" in row.summary
        assert "HTTP 404" in row.summary
        assert row.provider_run_id is None
        fake_client.beta.sessions.create.assert_not_called()

    @pytest.mark.parametrize("cid,key", [("cid", ""), ("", "pem")])
    def test_partial_config_refused(self, db, monkeypatch, fake_client, cid, key):
        self._configure(monkeypatch, cid=cid, key=key)
        pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=self._spec())
        row = db.query(AgentWorkspaceRun).one()
        assert row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
        assert "misconfigured" in row.summary
        fake_client.beta.sessions.create.assert_not_called()

    def test_no_app_settings_uses_personal_token(self, db, monkeypatch, fake_client):
        self._configure(monkeypatch)
        mint = MagicMock()
        monkeypatch.setattr(github_app, "mint_installation_token", mint)
        pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=self._spec())
        mint.assert_not_called()
        kwargs = fake_client.beta.sessions.create.call_args.kwargs
        assert kwargs["resources"][0]["authorization_token"] == _PERSONAL

    def test_app_without_personal_token_still_dispatches(
        self, db, monkeypatch, fake_client
    ):
        self._configure(monkeypatch, pat="", cid="cid", key="pem")
        monkeypatch.setattr(
            github_app,
            "mint_installation_token",
            MagicMock(return_value=github_app.MintResult(token=_MINTED)),
        )
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_WORKSPACE_ENABLED", True)
        monkeypatch.setattr(pe_workspace.settings, "AGENT_PE_BACKEND", "managed_agents")
        assert isinstance(
            pe_workspace.resolve_adapter(None), pe_workspace.ManagedAgentsAdapter
        )

    def test_minted_token_is_scrubbed(self, monkeypatch):
        monkeypatch.setattr(pe_workspace.settings, "GITHUB_TOKEN", "")
        github_app._remember("opaque-minted-value")
        out = pe_workspace._redact_secrets("saw opaque-minted-value and " + _MINTED)
        assert "opaque-minted-value" not in out and _MINTED not in out
