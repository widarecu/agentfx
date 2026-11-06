"""Unit tests for the Linear Agent webhook receiver (AFX-66 Phase 1).

HTTP-level tests against a minimal FastAPI app that mounts only the
webhook router. The DB session is a MagicMock; IntegrityError
behaviour is simulated so the dedup branch is testable without a real
Postgres unique constraint.

These tests pin the Phase 1 contract: flag gating, signature
verification, replay window, idempotency, and payload parsing. Any
Phase 2 behaviour change that touches the handler must preserve these
assertions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentfx.api.core.config import settings
from agentfx.api.core.database import get_db
from agentfx.api.routers.integrations import linear as linear_router

pytestmark = pytest.mark.unit


_SECRET = "whsec_test_fake_1234567890"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    """Produce the hex HMAC-SHA256 header Linear would send."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _now_ms() -> int:
    """Linear webhookTimestamp field is ms since epoch."""
    return int(datetime.now(tz=UTC).timestamp() * 1000)


@pytest.fixture
def mock_db() -> MagicMock:
    """Fresh mock DB per test — avoids state leaking between cases."""
    return MagicMock(spec=Session)


@pytest.fixture
def client(mock_db: MagicMock, monkeypatch) -> TestClient:
    """TestClient with the router mounted + flag/secret pinned on."""
    # Pin settings per-test so one test's monkeypatch can't bleed into
    # another via the module-level `settings` singleton.
    monkeypatch.setattr(settings, "LINEAR_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "LINEAR_AGENT_WEBHOOK_SECRET", _SECRET)

    app = FastAPI()
    app.include_router(
        linear_router.router,
        prefix="/api/v1/integrations/linear",
    )
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def _post(
    client: TestClient,
    body: dict,
    *,
    signature: str | None = None,
    delivery: str | None = "dlv-01HPZ0ABC",
) -> object:
    """Helper: POST with correctly-serialised body + signed header."""
    raw = json.dumps(body).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if delivery is not None:
        headers["Linear-Delivery"] = delivery
    if signature is None:
        signature = _sign(raw)
    headers["Linear-Signature"] = signature
    return client.post(
        "/api/v1/integrations/linear/agent-webhook",
        content=raw,
        headers=headers,
    )


class TestFlagGating:
    def test_flag_off_acks_without_verification(self, monkeypatch, mock_db):
        """When LINEAR_AGENT_ENABLED is false, the endpoint ACKs 200
        without reading any header. Linear stops retrying; we don't
        process while the receiver is disabled."""
        monkeypatch.setattr(settings, "LINEAR_AGENT_ENABLED", False)
        app = FastAPI()
        app.include_router(
            linear_router.router,
            prefix="/api/v1/integrations/linear",
        )
        app.dependency_overrides[get_db] = lambda: mock_db
        test_client = TestClient(app)

        resp = test_client.post(
            "/api/v1/integrations/linear/agent-webhook",
            content=b"anything, even unsigned",
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "flag_off"}
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


class TestMisconfiguration:
    def test_flag_on_but_no_secret_returns_500(self, monkeypatch, mock_db):
        """Flag on but secret unset is a misconfiguration we must
        surface, not silently pass. A silent pass would mean every
        POST is trusted during the period between flag-on and
        secret-set, which is exactly when attack surface is highest."""
        monkeypatch.setattr(settings, "LINEAR_AGENT_ENABLED", True)
        monkeypatch.setattr(settings, "LINEAR_AGENT_WEBHOOK_SECRET", "")
        app = FastAPI()
        app.include_router(
            linear_router.router,
            prefix="/api/v1/integrations/linear",
        )
        app.dependency_overrides[get_db] = lambda: mock_db
        test_client = TestClient(app)

        resp = test_client.post(
            "/api/v1/integrations/linear/agent-webhook",
            content=b'{"anything": true}',
            headers={
                "Linear-Signature": "fakesig",
                "Linear-Delivery": "dlv-x",
            },
        )
        assert resp.status_code == 500


class TestSignature:
    def test_missing_signature_header_returns_401(self, client):
        resp = client.post(
            "/api/v1/integrations/linear/agent-webhook",
            content=b"{}",
            headers={"Linear-Delivery": "dlv-x"},
        )
        assert resp.status_code == 401

    def test_missing_delivery_header_returns_400(self, client):
        body = (
            b'{"action":"created","webhookTimestamp":' + str(_now_ms()).encode() + b"}"
        )
        resp = client.post(
            "/api/v1/integrations/linear/agent-webhook",
            content=body,
            headers={"Linear-Signature": _sign(body)},
        )
        assert resp.status_code == 400

    def test_wrong_signature_returns_401(self, client):
        body = {"action": "created", "webhookTimestamp": _now_ms()}
        # Sign with a different secret — simulates a spoofed webhook.
        wrong_sig = _sign(
            json.dumps(body).encode("utf-8"), secret="not-the-real-secret"
        )
        resp = _post(client, body, signature=wrong_sig)
        assert resp.status_code == 401

    def test_valid_signature_is_accepted(self, client, mock_db):
        body = {"action": "created", "webhookTimestamp": _now_ms()}
        resp = _post(client, body)
        assert resp.status_code == 200
        assert resp.json() == {"status": "logged"}
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestReplayGuard:
    def test_old_timestamp_rejected(self, client):
        """webhookTimestamp more than 60s in the past = rejected."""
        stale_ms = int((datetime.now(tz=UTC).timestamp() - 120) * 1000)
        body = {"action": "created", "webhookTimestamp": stale_ms}
        resp = _post(client, body)
        assert resp.status_code == 400

    def test_future_timestamp_rejected(self, client):
        """webhookTimestamp more than 60s in the future = rejected
        (clock-drift attack guard, matches Linear's docs)."""
        future_ms = int((datetime.now(tz=UTC).timestamp() + 120) * 1000)
        body = {"action": "created", "webhookTimestamp": future_ms}
        resp = _post(client, body)
        assert resp.status_code == 400

    def test_missing_timestamp_is_accepted_not_rejected(self, client):
        """Absent webhookTimestamp = soft-skip. Linear's docs mention
        the field but don't guarantee every event includes it; we
        prefer accepting valid traffic over dropping it on a
        parse-defensive check."""
        body = {"action": "created"}  # no webhookTimestamp
        resp = _post(client, body)
        assert resp.status_code == 200


class TestMalformedBody:
    def test_non_json_body_returns_400(self, client):
        raw = b"not json at all {{{"
        resp = client.post(
            "/api/v1/integrations/linear/agent-webhook",
            content=raw,
            headers={
                "Linear-Signature": _sign(raw),
                "Linear-Delivery": "dlv-badjson",
            },
        )
        assert resp.status_code == 400


class TestIdempotency:
    def test_duplicate_delivery_returns_duplicate_status(self, client, mock_db):
        """IntegrityError on insert (unique PK) → 200 with
        status=duplicate. The handler must not raise and must not
        reprocess."""
        mock_db.commit.side_effect = IntegrityError("dupe", {}, Exception())
        body = {"action": "created", "webhookTimestamp": _now_ms()}
        resp = _post(client, body, delivery="dlv-already-seen")
        assert resp.status_code == 200
        assert resp.json() == {"status": "duplicate"}
        mock_db.rollback.assert_called_once()


class TestSessionIdExtraction:
    """`_extract_session_id` probes a few payload shapes because
    Linear's docs don't name the exact webhook field. These tests pin
    the current probe order so a Phase 2 tightening doesn't silently
    change which shape wins."""

    def test_top_level_agent_session(self):
        payload = {"agentSession": {"id": "ags_01ABCDEF"}}
        assert linear_router._extract_session_id(payload) == "ags_01ABCDEF"

    def test_nested_data_agent_session(self):
        payload = {"data": {"agentSession": {"id": "ags_02XYZ"}}}
        assert linear_router._extract_session_id(payload) == "ags_02XYZ"

    def test_type_tagged_data_object(self):
        payload = {
            "action": "created",
            "data": {"type": "AgentSession", "id": "ags_03TAG"},
        }
        assert linear_router._extract_session_id(payload) == "ags_03TAG"

    def test_no_session_returns_none(self):
        payload = {"action": "created", "unrelated": "field"}
        assert linear_router._extract_session_id(payload) is None

    def test_malformed_session_field_returns_none(self):
        # Defensive: don't crash on an unexpected shape.
        payload = {"agentSession": "not-a-dict"}
        assert linear_router._extract_session_id(payload) is None


class TestPersistedRowShape:
    def test_persisted_row_carries_action_and_session_id(self, client, mock_db):
        """Row saved to the DB must carry `event_action` + `agent_session_id`
        so Phase 2 queries (`WHERE event_action='prompted'` etc.) work
        without re-parsing `raw_body`."""
        body = {
            "action": "prompted",
            "webhookTimestamp": _now_ms(),
            "agentSession": {"id": "ags_01PINME"},
            "agentActivity": {"body": "wilson's reply text"},
        }
        resp = _post(client, body, delivery="dlv-pin-shape")
        assert resp.status_code == 200
        mock_db.add.assert_called_once()
        row = mock_db.add.call_args.args[0]
        assert row.delivery_id == "dlv-pin-shape"
        assert row.event_action == "prompted"
        assert row.agent_session_id == "ags_01PINME"
        assert row.processing_status == "logged"
        assert row.raw_body == body


class TestPhase2DispatchWiring:
    """AFX-66 Phase 2: on action=created, the handler must schedule a
    background task that calls `dispatch_from_linear_webhook`. Phase 1
    was log-only; this test pins that the Phase 2 behaviour fires.

    We patch asyncio.create_task so no real task runs — this is a pin
    for the wiring, not the dispatch logic itself (that's tested in
    test_linear_webhook_dispatch.py)."""

    def test_action_created_schedules_background_dispatch(self, client, mock_db):
        body = {
            "action": "created",
            "webhookTimestamp": _now_ms(),
            "agentSession": {"id": "ags_01DISPATCH"},
            "promptContext": "<issue>dispatch me</issue>",
        }
        from unittest.mock import patch as _patch

        # Patch asyncio.create_task where the handler imports it from.
        import asyncio as _aio

        with _patch.object(_aio, "create_task", autospec=True) as mock_create_task:
            resp = _post(client, body, delivery="dlv-dispatch-wire")

        assert resp.status_code == 200
        assert resp.json() == {"status": "logged"}
        # Exactly one background task scheduled.
        assert mock_create_task.call_count == 1

    def test_action_prompted_schedules_background_dispatch(self, client):
        """`action=prompted` (Linear "Retry" button, user comment reply)
        also schedules the dispatcher. The dispatcher's idempotency
        check skips cleanly when a workspace run is already linked to
        the session; if the prior `created` never persisted a row (the
        2026-11-06 retry-after-crash case) it treats this as a first-
        time dispatch. True Phase 3 resume-on-active-session stays
        deferred but the wiring must fire so retries don't hang."""
        body = {
            "action": "prompted",
            "webhookTimestamp": _now_ms(),
            "agentSession": {"id": "ags_01REPLY"},
            "agentActivity": {"content": {"body": "follow-up"}},
        }
        from unittest.mock import patch as _patch

        import asyncio as _aio

        with _patch.object(_aio, "create_task", autospec=True) as mock_create_task:
            resp = _post(client, body, delivery="dlv-prompted")

        assert resp.status_code == 200
        assert mock_create_task.call_count == 1

    def test_duplicate_delivery_skips_background_dispatch(self, client, mock_db):
        """If the delivery was already seen (IntegrityError on insert),
        we return `duplicate` without scheduling a dispatch — otherwise
        Linear retries become duplicate dispatches."""
        mock_db.commit.side_effect = IntegrityError("dupe", {}, Exception())
        body = {
            "action": "created",
            "webhookTimestamp": _now_ms(),
            "agentSession": {"id": "ags_01DUPE"},
        }
        from unittest.mock import patch as _patch

        import asyncio as _aio

        with _patch.object(_aio, "create_task", autospec=True) as mock_create_task:
            resp = _post(client, body, delivery="dlv-dupe-retry")

        assert resp.status_code == 200
        assert resp.json() == {"status": "duplicate"}
        mock_create_task.assert_not_called()
