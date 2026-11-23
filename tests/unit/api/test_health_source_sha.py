"""Backend `/health` reports the source SHA the Deploy workflow verifies (AFX-121)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentfx.api import main

_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def no_sha(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "SOURCE_SHA", "")
    monkeypatch.setattr(main, "_SOURCE_SHA_FILE", tmp_path / "SOURCE_SHA")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    return tmp_path / "SOURCE_SHA"


def test_health_reports_the_source_sha_file(no_sha):
    no_sha.write_text(_SHA + "\n")
    response = TestClient(main.app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "source_sha": _SHA}


def test_health_reports_null_when_unknown(no_sha):
    assert TestClient(main.app).get("/health").json() == {
        "status": "healthy",
        "source_sha": None,
    }


def test_setting_overrides_the_file(no_sha, monkeypatch):
    no_sha.write_text("f" * 40)
    monkeypatch.setattr(main.settings, "SOURCE_SHA", _SHA.upper())
    assert main.deployed_source_sha() == _SHA


def test_railway_git_sha_is_the_fallback(no_sha, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", _SHA)
    assert main.deployed_source_sha() == _SHA


def test_malformed_values_are_ignored(no_sha, monkeypatch):
    no_sha.write_text("not-a-sha")
    monkeypatch.setattr(main.settings, "SOURCE_SHA", "abc")
    assert main.deployed_source_sha() is None
