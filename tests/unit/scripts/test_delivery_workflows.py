"""Workflow contract for the `main` PR gate (AFX-119).

Pins what `.github/workflows/ci.yml` must keep doing: run the whole
required suite on every PR into `main` (docs-only included), fail the
single required check when any job fails, and run E2E only against the
disposable Docker `test` profile. Also pins that the legacy fleet fast
gate stays until the cutover (AFX-125).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text())


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare `on:` key as boolean True.
    return workflow.get("on") or workflow[True]


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


@pytest.fixture(scope="module")
def ci() -> dict:
    return _load("ci.yml")


class TestTriggers:
    def test_runs_on_prs_into_main(self, ci):
        assert "main" in _triggers(ci)["pull_request"]["branches"]

    def test_docs_only_prs_still_report_checks(self, ci):
        pr = _triggers(ci)["pull_request"]
        assert "paths-ignore" not in pr and "paths" not in pr

    def test_no_job_is_skipped_on_pull_requests(self, ci):
        for name in ("backend", "frontend", "e2e"):
            assert "if" not in ci["jobs"][name], name


class TestRequiredSuite:
    @pytest.mark.parametrize(
        ("job", "command"),
        [
            ("backend", "ruff check src/ tests/"),
            ("backend", "ruff format --check src/ tests/"),
            ("backend", "pytest tests/unit/"),
            ("backend", "pytest tests/integration/"),
            ("frontend", "npm test"),
            ("frontend", "npx tsc --noEmit"),
            ("frontend", "npm run build"),
            ("e2e", "make e2e"),
        ],
    )
    def test_check_is_present(self, ci, job, command):
        assert command in _runs(ci["jobs"][job])

    def test_no_failure_is_tolerated(self, ci):
        for name, job in ci["jobs"].items():
            if name == "settings-ui-test-bar":
                continue
            assert not job.get("continue-on-error"), name
            for step in job.get("steps", []):
                assert not step.get("continue-on-error"), (name, step.get("name"))
                assert "|| true" not in step.get("run", ""), (name, step.get("name"))

    def test_required_check_needs_every_job(self, ci):
        gate = ci["jobs"]["ci-required"]
        assert gate["name"] == "CI required"
        assert set(gate["needs"]) == {"backend", "frontend", "e2e"}
        assert gate["if"] == "always()"
        script = _runs(gate)
        assert '"success"' in script and "exit 1" in script


class TestE2ESafety:
    def test_uses_test_profile_only(self, ci):
        job = ci["jobs"]["e2e"]
        script = _runs(job)
        assert "make e2e" in script
        env = {}
        for step in job["steps"]:
            env.update(step.get("env") or {})
        assert env.get("E2E_PROFILE") == "test"

    def test_never_touches_dev_or_uat(self, ci):
        text = (_WORKFLOWS / "ci.yml").read_text()
        for forbidden in (
            "PROFILE=dev",
            "PROFILE=uat",
            "--profile dev",
            "--profile uat",
        ):
            assert forbidden not in text
        assert "down -v" not in text

    def test_runner_script_refuses_other_profiles(self):
        script = (_WORKFLOWS.parents[1] / "scripts" / "run-e2e-tests.sh").read_text()
        assert 'TARGET_PROFILE="${E2E_PROFILE:-test}"' in script
        assert 'if [ "$TARGET_PROFILE" != "test" ]' in script


class TestLegacyFleetGate:
    def test_fast_gate_still_guards_fleet_prs(self):
        fleet = _load("agent-fleet-ci.yml")
        assert "feat/agent-fleet" in _triggers(fleet)["pull_request"]["branches"]
        assert "fast-gate" in fleet["jobs"]
