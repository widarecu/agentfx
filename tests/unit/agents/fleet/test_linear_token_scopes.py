"""One scope set per Linear application (AFX-184).

Linear cancels an application's earlier client-credentials tokens when a
token is requested with a different scope set. This drives the fleet's
real Linear paths with the token endpoint faked at the HTTP level and
fails if any client ID is ever asked for two scope sets, or if the token
written to the PE's vault was cancelled before the session started."""

from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentfx.agents.fleet import linear_pe_queue, pe_workspace
from agentfx.api.core.database import Base
from agentfx.integrations import linear_agent_api
from tests.unit.agents.fleet.test_pe_workspace import _spec
from tests.unit.scripts.linear_fake import FakeClaims, FakeLinear

pytestmark = pytest.mark.unit

_PE_ID, _PE_SECRET = "pe-client-id", "pe-client-secret"
_QA_ID, _QA_SECRET = "qa-client-id", "qa-client-secret"


class _Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body

    def read(self) -> bytes:
        return self.text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLinearAuth:
    """Token endpoint plus an auth check that models Linear's rule: a
    token dies when its application later requests a different scope set."""

    def __init__(self, linear: FakeLinear) -> None:
        self.linear = linear
        self.n = 0
        self.token_requests: list[tuple[str, str]] = []  # (client_id, scope)
        self.events: list[str] = []
        self.issued: dict[str, tuple[str, str]] = {}  # token -> (client, scope)
        self.latest_scope: dict[str, str] = {}
        self.delivery_tokens: list[str] = []
        self.rejected = 0

    def _token(self, form: dict[str, str]) -> dict:
        cid, scope = form["client_id"], form["scope"]
        self.n += 1
        token = f"tok-{self.n}"
        self.token_requests.append((cid, scope))
        self.events.append(f"mint:{cid}")
        for old, (c, s) in self.issued.items():
            if c == cid and s != scope:
                self.issued[old] = (c, "CANCELLED")
        self.issued[token] = (cid, scope)
        self.latest_scope[cid] = scope
        return {"access_token": token, "expires_in": 2591999}

    def _valid(self, token: str) -> bool:
        entry = self.issued.get(token)
        return entry is not None and entry[1] != "CANCELLED"

    def post(self, url, *, data=None, json=None, headers=None, timeout=None, **_):
        """Replacement for `requests.post`."""
        if url == linear_agent_api._LINEAR_TOKEN_URL:
            return _Resp(200, self._token(data))
        token = (headers or {})["Authorization"].removeprefix("Bearer ")
        if not self._valid(token):
            self.rejected += 1
            return _Resp(401, {})
        self.events.append("activity")
        return _Resp(200, {"data": {"agentActivityCreate": {"success": True}}})

    def urlopen(self, req, timeout=None):
        """Replacement for `urllib.request.urlopen` (the delivery client)."""
        if req.full_url.endswith("/oauth/token"):
            form = {
                k: v[0] for k, v in urllib.parse.parse_qs(req.data.decode()).items()
            }
            return _Resp(200, self._token(form))
        token = req.get_header("Authorization").removeprefix("Bearer ")
        if not self._valid(token):
            self.rejected += 1
            raise urllib.error.HTTPError(
                req.full_url, 401, "no", {}, io.BytesIO(b'{"errors":[]}')
            )
        self.delivery_tokens.append(token)
        self.events.append("delivery")
        return self.linear.opener(req)


@pytest.fixture
def world(monkeypatch):
    s = linear_agent_api.settings
    for name, value in {
        "LINEAR_AGENT_CLIENT_ID": _PE_ID,
        "LINEAR_AGENT_CLIENT_SECRET": _PE_SECRET,
        "LINEAR_QA_CLIENT_ID": _QA_ID,
        "LINEAR_QA_CLIENT_SECRET": _QA_SECRET,
        "AGENT_LINEAR_ENABLED": True,
        "AGENT_PE_GITHUB_VAULT_CREDENTIAL_ID": "",
        "AGENT_PE_LINEAR_VAULT_CREDENTIAL_ID": "vcrd_lin",
        "AGENT_PE_WORKSPACE_AGENT_ID": "agent_abc",
        "AGENT_PE_WORKSPACE_ENVIRONMENT_ID": "env_xyz",
        "AGENT_PE_WORKSPACE_VAULT_ID": "vault_123",
        "GITHUB_TOKEN": "ghp_personal",
        "AGENT_PE_GITHUB_APP_CLIENT_ID": "",
        "AGENT_PE_GITHUB_APP_PRIVATE_KEY": "",
    }.items():
        monkeypatch.setattr(s, name, value)
    linear_agent_api._reset_token_cache_for_tests()

    linear = FakeLinear()
    linear.add("AFX-1", "Ready")
    linear.add("AFX-2", "Ready")
    auth = FakeLinearAuth(linear)
    monkeypatch.setattr(linear_agent_api.requests, "post", auth.post)
    monkeypatch.setattr(urllib.request, "urlopen", auth.urlopen)
    monkeypatch.setattr(linear_pe_queue, "_claim_store", lambda: FakeClaims())

    session = MagicMock()
    session.id = "sess_1"
    client = MagicMock()
    client.beta.sessions.create.return_value = session
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: client)
    yield auth, client
    linear_agent_api._reset_token_cache_for_tests()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _vault_token(client) -> str:
    update = client.beta.vaults.credentials.update
    auth = update.call_args.kwargs["auth"]
    assert auth["type"] == "static_bearer"
    return auth["token"]


def test_one_scope_set_per_client_id_and_vault_token_is_live(world, db):
    auth, client = world

    # Webhook acknowledgement: a thought in the Linear agent session.
    assert linear_agent_api.emit_thought("sess-a", "picking up") == (True, "")
    # Claim, then dispatch with the Linear vault credential set.
    assert linear_pe_queue.claim_for_dispatch("AFX-1").ok
    claim_end = len(auth.events)
    pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=_spec())
    vault_token = _vault_token(client)
    # Post-dispatch activity.
    assert linear_agent_api.emit_response("sess-a", "dispatched") == (True, "")
    # Scheduled scan: another claim, another dispatch.
    assert linear_pe_queue.claim_for_dispatch("AFX-2").ok
    pe_workspace.ManagedAgentsAdapter().dispatch(db, spec=_spec())
    # A rollback builds a client too.
    linear_pe_queue.rollback_to_ready("AFX-2", reason_summary="x")

    scopes: dict[str, set[str]] = {}
    for cid, scope in auth.token_requests:
        scopes.setdefault(cid, set()).add(scope)
    assert scopes == {_PE_ID: {linear_agent_api._PE_SCOPE}}
    assert all(len(v) == 1 for v in scopes.values())

    # The claim used the one token, and nothing was minted between it and
    # the vault write, so the vault holds the token the claim used.
    assert "mint:" + _PE_ID not in auth.events[claim_end:]
    assert set(auth.delivery_tokens) == {vault_token}
    assert auth.issued[vault_token][1] != "CANCELLED"
    assert auth.rejected == 0


def test_qa_and_pe_applications_each_keep_their_own_single_scope(world):
    auth, _ = world
    assert linear_agent_api.get_app_token()[0]
    assert linear_agent_api.get_qa_token()[0]
    assert linear_agent_api.get_app_token(force_refresh=True)[0]
    assert linear_agent_api.get_qa_token(force_refresh=True)[0]
    assert set(auth.token_requests) == {
        (_PE_ID, linear_agent_api._PE_SCOPE),
        (_QA_ID, linear_agent_api._QA_SCOPE),
    }
    assert linear_agent_api._PE_SCOPE != linear_agent_api._QA_SCOPE
