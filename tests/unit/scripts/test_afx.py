"""`scripts/afx.py` start/block CLI (AFX-118). No network: the client
is the real `LinearClient` wired to `linear_fake.FakeLinear`."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeLinear

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "afx.py"

_spec = importlib.util.spec_from_file_location("afx_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
afx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = afx
_spec.loader.exec_module(afx)


@pytest.fixture
def fake() -> FakeLinear:
    return FakeLinear()


def _run(fake: FakeLinear, *argv: str) -> int:
    return afx.main(list(argv), client=ld.LinearClient("k", opener=fake.opener))


def test_start_ready_exits_zero(fake, capsys):
    fake.add("AFX-1", "Ready")
    assert _run(fake, "start", "AFX-1") == afx.EXIT_OK
    assert fake.issues["AFX-1"].state == "In Progress"
    assert "Ready -> In Progress" in capsys.readouterr().out


def test_second_start_refused(fake, capsys):
    fake.add("AFX-1", "Ready")
    _run(fake, "start", "AFX-1")
    assert _run(fake, "start", "AFX-1") == afx.EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_start_wrong_state_refused(fake):
    fake.add("AFX-1", "Backlog")
    assert _run(fake, "start", "AFX-1") == afx.EXIT_REFUSED
    assert fake.mutations == []


def test_start_missing_issue_errors(fake, capsys):
    assert _run(fake, "start", "AFX-404") == afx.EXIT_ERROR
    assert "not found" in capsys.readouterr().err


def test_block_then_repeat(fake, capsys):
    fake.add("AFX-1", "In Progress")
    assert _run(fake, "block", "AFX-1", "Which pair?") == afx.EXIT_OK
    assert _run(fake, "block", "AFX-1", "Which pair?") == afx.EXIT_OK
    assert "nothing to do" in capsys.readouterr().out
    assert len(fake.issues["AFX-1"].comments) == 1


def test_block_ready_refused(fake):
    fake.add("AFX-1", "Ready")
    assert _run(fake, "block", "AFX-1", "q") == afx.EXIT_REFUSED
    assert fake.issues["AFX-1"].state == "Ready"


def test_block_empty_question_errors(fake):
    fake.add("AFX-1", "In Progress")
    assert _run(fake, "block", "AFX-1", "  ") == afx.EXIT_ERROR


def test_api_failure_errors(fake):
    fake.fail_with = urllib.error.URLError("down")
    assert _run(fake, "start", "AFX-1") == afx.EXIT_ERROR


def test_missing_key_errors(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert afx.main(["start", "AFX-1"]) == afx.EXIT_ERROR
    assert "LINEAR_API_KEY" in capsys.readouterr().err


def test_runs_as_standalone_script_without_backend_env():
    """The CLI must import with only the repo on disk: no PYTHONPATH,
    no ENVIRONMENT, no LINEAR_API_KEY (fails cleanly, exit 2)."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "start", "AFX-1"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert proc.returncode == afx.EXIT_ERROR, proc.stderr
    assert "LINEAR_API_KEY" in proc.stderr
