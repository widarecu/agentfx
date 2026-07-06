"""Integration tests for /users/me/credentials/* (US-019).

Exercises the CRUD surface + verify flow against the real FastAPI
TestClient + Postgres. OANDA network calls are patched with a
`unittest.mock` so the tests don't depend on a live broker; the
underlying `OandaClient.list_accounts` shape is asserted by adapter
unit tests.

Locks:
- 404 before first PUT
- PUT persists, encrypts, and clears verified state
- DELETE is idempotent
- verify success writes accounts + verified_at
- verify failure sets verify_failure_reason and returns 400
- one user cannot see another user's credentials or accounts
"""

from __future__ import annotations

import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _auth_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/login", data={"username": email, "password": password})
    if resp.status_code != 200:  # pragma: no cover — env issue
        pytest.skip(f"Auth failed for {email}: {resp.json()}")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def credential_users(db_session: Session):
    """Fresh emails to avoid credential-row bleed between tests."""
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    unique = str(uuid4())[:8]
    creds = {
        "alice": (f"creds_alice_{unique}@example.com", "alicepass"),
        "bob": (f"creds_bob_{unique}@example.com", "bobpass"),
    }
    users = {}
    for key, (email, password) in creds.items():
        user = User(
            email=email,
            hashed_password=get_password_hash(password),
            role="trader",
            is_active=True,
        )
        db_session.add(user)
        users[key] = {"email": email, "password": password, "user": user}
    db_session.commit()
    for v in users.values():
        db_session.refresh(v["user"])
    return users


class TestCredentialsCrud:
    def test_get_before_set_returns_404(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        assert resp.status_code == 404

    def test_put_persists_encrypted(
        self, test_client_with_db, credential_users, db_session
    ):
        from src.agentfx.api.models.user_credentials import UserOandaCredentials

        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={
                "oanda_api_key": "test-api-token-12345678",
                "environment": "practice",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is False
        assert body["environment"] == "practice"
        # Response must NEVER echo the plaintext key.
        assert "test-api-token-12345678" not in resp.text

        # Persisted ciphertext is not the plaintext bytes.
        row = (
            db_session.query(UserOandaCredentials)
            .filter_by(user_id=credential_users["alice"]["user"].id)
            .one()
        )
        assert b"test-api-token-12345678" not in row.oanda_api_key_encrypted

    def test_get_after_put_returns_public_view(
        self, test_client_with_db, credential_users
    ):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "some-token-abcdefghij"},
        )
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["environment"] == "practice"
        assert body["verified"] is False
        assert body["key_version"] == 1
        # Plaintext must not leak on GET either.
        assert "some-token-abcdefghij" not in resp.text

    def test_delete_removes_row(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "token-to-be-removed"},
        )
        resp = test_client_with_db.delete(
            "/api/v1/users/me/credentials", headers=headers
        )
        assert resp.status_code == 204
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        assert resp.status_code == 404

    def test_delete_when_absent_is_idempotent(
        self, test_client_with_db, credential_users
    ):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.delete(
            "/api/v1/users/me/credentials", headers=headers
        )
        assert resp.status_code == 204

    def test_put_below_min_length_rejected(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "short"},
        )
        assert resp.status_code == 422

    def test_live_environment_rejected(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={
                "oanda_api_key": "some-valid-length-token-abc",
                "environment": "live",
            },
        )
        # Literal["practice"] on the schema → 422 at parse time.
        assert resp.status_code == 422


class TestVerifyFlow:
    def test_verify_before_put_returns_404(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        resp = test_client_with_db.post(
            "/api/v1/users/me/credentials/verify", headers=headers
        )
        assert resp.status_code == 404

    def test_verify_success_writes_accounts(
        self, test_client_with_db, credential_users
    ):
        fake_accounts = [
            {
                "account_id": "101-004-12345678-001",
                "alias": "Primary",
                "currency": "USD",
                "balance": 10000.0,
                "margin_available": 9824.10,
            },
            {
                "account_id": "101-004-12345678-002",
                "alias": "Aggressive",
                "currency": "USD",
                "balance": 5120.40,
                "margin_available": 4891.30,
            },
        ]
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "valid-looking-token-abc"},
        )
        # Patch the OANDA network call — no real broker touched.
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            return_value=fake_accounts,
        ):
            resp = test_client_with_db.post(
                "/api/v1/users/me/credentials/verify", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is True
        assert body["verify_failure_reason"] is None
        assert len(body["accounts"]) == 2
        ids = {a["oanda_account_id"] for a in body["accounts"]}
        assert ids == {"101-004-12345678-001", "101-004-12345678-002"}

        # Follow-up GET reflects the verified state.
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        assert resp.json()["verified"] is True

    def test_verify_failure_records_reason(self, test_client_with_db, credential_users):
        from agentfx.adapters.oanda import OandaError

        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "wrong-looking-token-abc"},
        )
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            side_effect=OandaError("401 unauthorized"),
        ):
            resp = test_client_with_db.post(
                "/api/v1/users/me/credentials/verify", headers=headers
            )
        assert resp.status_code == 400
        assert "401" in resp.json()["detail"]

        # State persisted on the row.
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        body = resp.json()
        assert body["verified"] is False
        assert body["verify_failure_reason"] is not None
        assert "401" in body["verify_failure_reason"]

    def test_verify_prunes_stale_accounts(self, test_client_with_db, credential_users):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "valid-looking-token-abc"},
        )
        first = [
            {
                "account_id": "acct-a",
                "alias": "A",
                "currency": "USD",
                "balance": 1.0,
                "margin_available": 1.0,
            },
            {
                "account_id": "acct-b",
                "alias": "B",
                "currency": "USD",
                "balance": 1.0,
                "margin_available": 1.0,
            },
        ]
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            return_value=first,
        ):
            test_client_with_db.post(
                "/api/v1/users/me/credentials/verify", headers=headers
            )

        # Simulate OANDA now reporting only acct-a — acct-b should be
        # pruned from the cache table.
        second = [
            {
                "account_id": "acct-a",
                "alias": "A-renamed",
                "currency": "USD",
                "balance": 2.0,
                "margin_available": 2.0,
            },
        ]
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            return_value=second,
        ):
            resp = test_client_with_db.post(
                "/api/v1/users/me/credentials/verify", headers=headers
            )
        assert resp.status_code == 200
        ids = {a["oanda_account_id"] for a in resp.json()["accounts"]}
        assert ids == {"acct-a"}
        # Alias update propagates.
        assert resp.json()["accounts"][0]["alias"] == "A-renamed"

    def test_put_after_verify_clears_verified(
        self, test_client_with_db, credential_users
    ):
        headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "valid-looking-token-abc"},
        )
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            return_value=[
                {
                    "account_id": "acct-1",
                    "alias": None,
                    "currency": "USD",
                    "balance": 100.0,
                    "margin_available": 100.0,
                }
            ],
        ):
            test_client_with_db.post(
                "/api/v1/users/me/credentials/verify", headers=headers
            )

        # Editing the key MUST clear verified + prune accounts.
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=headers,
            json={"oanda_api_key": "rotated-token-abc-def"},
        )
        resp = test_client_with_db.get("/api/v1/users/me/credentials", headers=headers)
        assert resp.json()["verified"] is False

        accts = test_client_with_db.get(
            "/api/v1/users/me/credentials/accounts", headers=headers
        )
        assert accts.status_code == 200
        assert accts.json()["items"] == []


class TestPerUserIsolation:
    """Alice must never see Bob's credentials or accounts, and vice
    versa. Backend enforces caller-scope on every endpoint."""

    def test_alice_credentials_invisible_to_bob(
        self, test_client_with_db, credential_users
    ):
        alice_headers = _auth_headers(
            test_client_with_db,
            credential_users["alice"]["email"],
            credential_users["alice"]["password"],
        )
        test_client_with_db.put(
            "/api/v1/users/me/credentials",
            headers=alice_headers,
            json={"oanda_api_key": "alice-token-alicealice"},
        )
        with patch(
            "agentfx.adapters.oanda.OandaClient.list_accounts",
            return_value=[
                {
                    "account_id": "alice-only-account",
                    "alias": None,
                    "currency": "USD",
                    "balance": 1.0,
                    "margin_available": 1.0,
                }
            ],
        ):
            test_client_with_db.post(
                "/api/v1/users/me/credentials/verify",
                headers=alice_headers,
            )

        # Bob authenticates and hits the same endpoints — sees NOTHING.
        bob_headers = _auth_headers(
            test_client_with_db,
            credential_users["bob"]["email"],
            credential_users["bob"]["password"],
        )
        assert (
            test_client_with_db.get(
                "/api/v1/users/me/credentials", headers=bob_headers
            ).status_code
            == 404
        )
        accts = test_client_with_db.get(
            "/api/v1/users/me/credentials/accounts", headers=bob_headers
        ).json()
        assert accts["items"] == []
        # Also — Bob's own accounts endpoint must NOT leak Alice's.
        assert not any(
            a["oanda_account_id"] == "alice-only-account" for a in accts["items"]
        )
