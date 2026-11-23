"""Workflow contracts: the `main` PR gate (AFX-119) and Deploy (AFX-121).

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
        assert on["workflow_run"]["workflows"] == ["PR review relay", "CI"]
        assert "auto_merge_enabled" in on["pull_request_target"]["types"]
        assert "review_request_removed" in on["pull_request_target"]["types"]
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
            "checks": "read",
        }
        assert "environment" not in policy["jobs"]["policy"]

    def test_auto_merge_uses_fleet_bot_token(self, policy):
        job = policy["jobs"]["auto-merge"]
        assert job["needs"] == "policy"
        assert "needs.policy.outputs.eligible == 'true'" in job["if"]
        assert "needs.policy.outputs.disable != ''" in job["if"]
        assert job["environment"] == "fleet-bot"
        assert job["permissions"] == {}
        assert any("create-github-app-token" in s.get("uses", "") for s in job["steps"])
        assert "gh pr merge" in _runs(job) and "--auto" in _runs(job)
        merge = job["steps"][-1]
        assert merge["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
        assert '--match-head-commit "$HEAD_SHA"' in merge["run"]
        assert "--disable-auto" in merge["run"]
        assert merge["env"]["HEAD_SHA"] == "${{ needs.policy.outputs.sha }}"

    def test_ci_workflow_name_matches_trigger(self):
        assert _load("ci.yml")["name"] == "CI"
        assert "ci-required" in _load("ci.yml")["jobs"]

    def test_codeowners_cover_the_merge_gate(self):
        owners = (_WORKFLOWS.parent / "CODEOWNERS").read_text()
        for path in (
            "/scripts/pr_policy.py",
            "/scripts/afx.py",
            "/src/agentfx/integrations/linear_delivery.py",
            "/src/agentfx/integrations/deploy_sync.py",
            "/src/agentfx/integrations/delivery_reconcile.py",
        ):
            assert any(
                line.split()[:2] == [path, "@widarecu"] for line in owners.splitlines()
            ), path

    def test_fleet_gate_untouched(self):
        fleet = _load("agent-fleet-ci.yml")
        assert _triggers(fleet)["pull_request"]["branches"] == ["feat/agent-fleet"]
        assert "auto-merge" in fleet["jobs"]


# ─────────────────────────────────────────────────────────────────────
# Deploy: verified staging deploys (AFX-121)
#
# Every push to main runs (docs-only included), retries accept a SHA,
# components deploy independently, only a verified deploy moves
# `deployed/staging`, and an always-run summary exposes the outcomes.
# ─────────────────────────────────────────────────────────────────────


def _deploy() -> dict:
    return _load("deploy.yml")


def _deploy_triggers() -> dict:
    return _triggers(_deploy())


def _deploy_jobs() -> dict:
    return _deploy()["jobs"]


def _deploy_runs(job: str) -> str:
    return "\n".join(step.get("run", "") for step in _deploy_jobs()[job]["steps"])


def _deploy_needs(job: str) -> list[str]:
    needs = _deploy_jobs()[job].get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def test_every_push_to_main_runs_including_docs_only():
    push = _deploy_triggers()["push"]
    assert push["branches"] == ["main"]
    assert "paths-ignore" not in push and "paths" not in push


def test_manual_dispatch_accepts_a_sha():
    sha = _deploy_triggers()["workflow_dispatch"]["inputs"]["sha"]
    assert sha["required"] is False
    plan = _deploy_jobs()["plan"]["steps"][-1]
    assert plan["env"]["TARGET_SHA"] == "${{ inputs.sha || github.sha }}"
    assert '"$TARGET_SHA"' in plan["run"]  # never interpolated into the script


def test_deploys_are_serialized_without_cancelling_in_flight():
    concurrency = _deploy()["concurrency"]
    assert "${{" not in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_plan_compares_with_the_tag_using_full_history():
    plan = _deploy_jobs()["plan"]
    assert plan["steps"][0]["with"]["fetch-depth"] == 0
    assert "scripts/deploy_staging.py plan" in _deploy_runs("plan")
    assert {"sha", "backend", "frontend", "kind", "base_sha", "stale"} <= set(
        plan["outputs"]
    )


def test_components_deploy_only_when_needed_and_independently():
    for job, component in (
        ("deploy-backend", "backend"),
        ("deploy-frontend", "frontend"),
    ):
        assert _deploy_needs(job) == ["plan"]
        assert _deploy_jobs()[job]["if"] == f"needs.plan.outputs.{component} == 'true'"
        assert f'guard --sha "$TARGET_SHA" --component {component}' in _deploy_runs(job)
    assert "railway_deploy.py" in _deploy_runs("deploy-backend")
    assert "vercel deploy --prod" in _deploy_runs("deploy-frontend")


def test_guard_runs_before_the_deploy_and_backend_stamps_its_sha():
    for job, deploy_marker in (
        ("deploy-backend", 'railway_deploy.py" --service'),
        ("deploy-frontend", "vercel deploy --prod"),
    ):
        runs = [step.get("run", "") for step in _deploy_jobs()[job]["steps"]]
        guard = next(i for i, r in enumerate(runs) if "deploy_staging.py guard" in r)
        deploy = next(i for i, r in enumerate(runs) if deploy_marker in r)
        assert guard < deploy
    assert 'echo "$TARGET_SHA" > src/agentfx/SOURCE_SHA' in _deploy_runs(
        "deploy-backend"
    )


def test_verify_runs_after_successful_or_skipped_deploys_only():
    verify = _deploy_jobs()["verify"]
    assert set(_deploy_needs("verify")) == {"plan", "deploy-backend", "deploy-frontend"}
    cond = verify["if"]
    assert "!cancelled()" in cond
    assert "needs.plan.outputs.stale != 'true'" in cond
    for job in ("deploy-backend", "deploy-frontend"):
        assert (
            f"needs.{job}.result == 'success' || needs.{job}.result == 'skipped'"
            in cond
        )
    assert "scripts/deploy_staging.py verify" in _deploy_runs("verify")


def test_only_the_record_job_can_write_and_only_after_verify():
    assert _deploy()["permissions"] == {"contents": "read"}
    writers = [
        name
        for name, job in _deploy_jobs().items()
        if job.get("permissions", {}).get("contents") == "write"
    ]
    # The AFX-122 ticket-sync jobs write only deploy-sync/baseline/* tags.
    assert sorted(writers) == ["deploy-sync", "deploy-sync-record", "record"]
    assert set(_deploy_needs("record")) == {"plan", "verify", "deploy-sync-record"}
    assert "needs.verify.result == 'success'" in _deploy_jobs()["record"]["if"]
    assert "scripts/deploy_staging.py record" in _deploy_runs("record")
    for name in _deploy_jobs():
        if name != "record":
            assert "deploy_staging.py record" not in _deploy_runs(name)


def test_deploy_has_no_e2e_job():
    """E2E runs at PR time (ci.yml, AFX-119); it never gates a deploy."""
    assert "e2e" not in _deploy_jobs()
    assert "e2e" in _load("ci.yml")["jobs"]


def test_summary_always_runs_and_exposes_outcomes():
    summary = _deploy_jobs()["summary"]
    assert summary["if"] == "always()"
    assert set(_deploy_needs("summary")) == {
        "plan",
        "deploy-backend",
        "deploy-frontend",
        "verify",
        "record",
    }
    assert {
        "outcome",
        "backend",
        "frontend",
        "verify",
        "tag",
        "previous_sha",
        "deployed_sha",
    } <= set(summary["outputs"])
    upload = next(s for s in summary["steps"] if "upload-artifact" in s.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "deploy-result"
    assert upload["with"]["overwrite"] is True  # re-runs replace the result


class TestDeliveryReconcileWorkflow:
    """`delivery-reconcile.yml` (AFX-123): daily + manual, one digest."""

    @pytest.fixture(scope="class")
    def wf(self) -> dict:
        return _load("delivery-reconcile.yml")

    def test_daily_and_manual_only(self, wf):
        on = _triggers(wf)
        assert set(on) == {"schedule", "workflow_dispatch"}
        assert len(on["schedule"]) == 1

    def test_least_privilege(self, wf):
        assert wf["permissions"] == {}
        assert wf["jobs"]["reconcile"]["permissions"] == {
            "contents": "write",
            "pull-requests": "read",
            "actions": "read",
        }

    def test_runs_reconcile_inert_until_cutover(self, wf):
        job = wf["jobs"]["reconcile"]
        assert "scripts/afx.py reconcile" in _runs(job)
        step = next(s for s in job["steps"] if "afx.py reconcile" in s.get("run", ""))
        assert "|| 'dry-run'" in step["env"]["AFX_DEPLOY_SYNC_MODE"]

    def test_expressions_reach_shell_only_via_env(self, wf):
        assert "${{" not in _runs(wf["jobs"]["reconcile"])

    def test_digest_marker_is_kept(self, wf):
        upload = next(
            s
            for s in wf["jobs"]["reconcile"]["steps"]
            if "upload-artifact" in s.get("uses", "")
        )
        assert upload["with"]["name"] == "reconcile-digest"
        assert upload["with"]["overwrite"] is True
        assert upload["if"] == "always()"
