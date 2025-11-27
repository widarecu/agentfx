import pytest
from fastapi.testclient import TestClient

from agentfx.portal_mock.app import app


@pytest.mark.integration
def test_auth_login_me_logout():
    client = TestClient(app)

    # unauthenticated me should 401
    r = client.get("/api/auth/me")
    assert r.status_code == 401

    # login
    r = client.post(
        "/api/auth/login",
        json={"email": "viewer@example.com", "password": "secret", "remember": True},
    )
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "viewer@example.com"

    # me should succeed with cookie persisted in client
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "viewer"

    # logout
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # me should 401 again
    r = client.get("/api/auth/me")
    assert r.status_code == 401
