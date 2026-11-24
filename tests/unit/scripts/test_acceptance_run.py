"""Acceptance criterion 1: the nightly evolution trigger must be scheduled by
something that actually runs it, not by a config block Railway ignores."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "acceptance_run.py"
_spec = importlib.util.spec_from_file_location("acceptance_run", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ar = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ar
_spec.loader.exec_module(ar)

_SCHEDULED = """\
on:
  schedule:
    - cron: "0 2 * * *"
jobs:
  nightly:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/trigger_nightly_evolution.py
"""


def _workflow(root: Path, name: str, body: str) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / name).write_text(body)


def test_scheduled_workflow_running_the_trigger_passes(tmp_path):
    _workflow(tmp_path, "nightly.yml", _SCHEDULED)
    declared, note = ar.nightly_cron_declared(tmp_path)
    assert declared
    assert "nightly.yml" in note


def test_no_workflows_fails(tmp_path):
    declared, note = ar.nightly_cron_declared(tmp_path)
    assert not declared
    assert "trigger_nightly_evolution.py" in note


def test_unscheduled_workflow_running_the_trigger_fails(tmp_path):
    _workflow(
        tmp_path,
        "manual.yml",
        _SCHEDULED.replace(
            '  schedule:\n    - cron: "0 2 * * *"', "  workflow_dispatch:"
        ),
    )
    assert not ar.nightly_cron_declared(tmp_path)[0]


def test_scheduled_workflow_not_running_the_trigger_fails(tmp_path):
    _workflow(
        tmp_path,
        "reconcile.yml",
        _SCHEDULED.replace("trigger_nightly_evolution.py", "afx.py reconcile"),
    )
    assert not ar.nightly_cron_declared(tmp_path)[0]


def test_criterion_1_uses_the_workflow_check(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "REPO_ROOT", tmp_path)
    _workflow(tmp_path, "nightly.yml", _SCHEDULED)
    label, passed, _ = ar.evaluate_criteria({}, [], [], None)[0]
    assert label.startswith("1.")
    assert passed
