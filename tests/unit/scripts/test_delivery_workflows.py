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


class TestPrPolicyWorkflow:
    """`pr-policy.yml` + `pr-review-relay.yml` (AFX-120): trusted code only,
    review re-runs, fleet-bot auto-merge, no polling."""

    @pytest.fixture(scope="class")
    def policy(self) -> dict:
        return _load("pr-policy.yml")

    @pytest.fixture(scope="class")
    def relay(self) -> dict:
        return _load("pr-review-relay.yml")

    def test_runs_on_main_prs_and_review_relay(self, policy):
        on = _triggers(policy)
        assert on["pull_request_target"]["branches"] == ["main"]
        assert on["workflow_run"]["workflows"] == ["PR review relay"]
        assert "schedule" not in on and "pull_request" not in on

    def test_relay_reruns_on_every_review_change(self, relay):
        assert set(_triggers(relay)["pull_request_review"]["types"]) == {
            "submitted",
            "edited",
            "dismissed",
        }

    def test_relay_has_no_permissions_or_code(self, relay):
        assert relay["permissions"] == {}
        steps = relay["jobs"]["relay"]["steps"]
        assert not any("uses" in s for s in steps)
        assert "secrets." not in (_WORKFLOWS / "pr-review-relay.yml").read_text()

    def test_never_checks_out_pr_code(self, policy):
        text = (_WORKFLOWS / "pr-policy.yml").read_text()
        assert "pull_request.head" not in text
        assert "workflow_run.head_branch" not in text
        checkouts = [
            s
            for job in policy["jobs"].values()
            for s in job["steps"]
            if "actions/checkout" in s.get("uses", "")
        ]
        assert checkouts and all(
            s["with"]["ref"] == "${{ github.event.repository.default_branch }}"
            and s["with"]["persist-credentials"] is False
            for s in checkouts
        )

    def test_untrusted_values_reach_shell_only_via_env(self, policy):
        for job in policy["jobs"].values():
            assert "${{" not in _runs(job)

    def test_policy_job_has_least_privilege(self, policy):
        assert policy["permissions"] == {}
        assert policy["jobs"]["policy"]["permissions"] == {
            "contents": "read",
            "pull-requests": "read",
            "statuses": "write",
        }
        assert "environment" not in policy["jobs"]["policy"]

    def test_auto_merge_uses_fleet_bot_token(self, policy):
        job = policy["jobs"]["auto-merge"]
        assert job["needs"] == "policy"
        assert job["if"] == "needs.policy.outputs.eligible == 'true'"
        assert job["environment"] == "fleet-bot"
        assert job["permissions"] == {}
        assert any("create-github-app-token" in s.get("uses", "") for s in job["steps"])
        assert "gh pr merge" in _runs(job) and "--auto" in _runs(job)
        merge = job["steps"][-1]
        assert merge["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"

    def test_fleet_gate_untouched(self):
        fleet = _load("agent-fleet-ci.yml")
        assert _triggers(fleet)["pull_request"]["branches"] == ["feat/agent-fleet"]
        assert "auto-merge" in fleet["jobs"]
