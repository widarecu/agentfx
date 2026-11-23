"""Deploy workflow wiring: verified staging deploys (AFX-121).

Pins the job graph of `.github/workflows/deploy.yml`: every push to main runs
(docs-only included), retries accept a SHA, components deploy independently,
only a verified deploy moves `deployed/staging`, and an always-run summary
exposes backend / frontend / verify outcomes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_DEPLOY = _REPO / ".github" / "workflows" / "deploy.yml"


def _workflow() -> dict:
    return yaml.safe_load(_DEPLOY.read_text())


def _triggers() -> dict:
    wf = _workflow()
    return wf.get("on", wf.get(True))  # PyYAML reads `on:` as True


def _jobs() -> dict:
    return _workflow()["jobs"]


def _runs(job: str) -> str:
    return "\n".join(step.get("run", "") for step in _jobs()[job]["steps"])


def _needs(job: str) -> list[str]:
    needs = _jobs()[job].get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def test_every_push_to_main_runs_including_docs_only():
    push = _triggers()["push"]
    assert push["branches"] == ["main"]
    assert "paths-ignore" not in push and "paths" not in push


def test_manual_dispatch_accepts_a_sha():
    sha = _triggers()["workflow_dispatch"]["inputs"]["sha"]
    assert sha["required"] is False
    plan = _jobs()["plan"]["steps"][-1]
    assert plan["env"]["TARGET_SHA"] == "${{ inputs.sha || github.sha }}"
    assert '"$TARGET_SHA"' in plan["run"]  # never interpolated into the script


def test_deploys_are_serialized_without_cancelling_in_flight():
    concurrency = _workflow()["concurrency"]
    assert "${{" not in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_plan_compares_with_the_tag_using_full_history():
    plan = _jobs()["plan"]
    assert plan["steps"][0]["with"]["fetch-depth"] == 0
    assert "scripts/deploy_staging.py plan" in _runs("plan")
    assert {"sha", "backend", "frontend", "kind", "base_sha", "stale"} <= set(
        plan["outputs"]
    )


def test_components_deploy_only_when_needed_and_independently():
    for job, component in (
        ("deploy-backend", "backend"),
        ("deploy-frontend", "frontend"),
    ):
        assert _needs(job) == ["plan"]
        assert _jobs()[job]["if"] == f"needs.plan.outputs.{component} == 'true'"
        assert f'guard --sha "$TARGET_SHA" --component {component}' in _runs(job)
    assert "railway_deploy.py" in _runs("deploy-backend")
    assert "vercel deploy --prod" in _runs("deploy-frontend")


def test_guard_runs_before_the_deploy_and_backend_stamps_its_sha():
    for job, deploy_marker in (
        ("deploy-backend", 'railway_deploy.py" --service'),
        ("deploy-frontend", "vercel deploy --prod"),
    ):
        runs = [step.get("run", "") for step in _jobs()[job]["steps"]]
        guard = next(i for i, r in enumerate(runs) if "deploy_staging.py guard" in r)
        deploy = next(i for i, r in enumerate(runs) if deploy_marker in r)
        assert guard < deploy
    assert 'echo "$TARGET_SHA" > src/agentfx/SOURCE_SHA' in _runs("deploy-backend")


def test_verify_runs_after_successful_or_skipped_deploys_only():
    verify = _jobs()["verify"]
    assert set(_needs("verify")) == {"plan", "deploy-backend", "deploy-frontend"}
    cond = verify["if"]
    assert "!cancelled()" in cond
    assert "needs.plan.outputs.stale != 'true'" in cond
    for job in ("deploy-backend", "deploy-frontend"):
        assert (
            f"needs.{job}.result == 'success' || needs.{job}.result == 'skipped'"
            in cond
        )
    assert "scripts/deploy_staging.py verify" in _runs("verify")


def test_only_the_record_job_can_write_and_only_after_verify():
    assert _workflow()["permissions"] == {"contents": "read"}
    writers = [
        name
        for name, job in _jobs().items()
        if job.get("permissions", {}).get("contents") == "write"
    ]
    assert writers == ["record"]
    assert set(_needs("record")) == {"plan", "verify"}
    assert "needs.verify.result == 'success'" in _jobs()["record"]["if"]
    assert "scripts/deploy_staging.py record" in _runs("record")
    for name in _jobs():
        if name != "record":
            assert "deploy_staging.py record" not in _runs(name)


def test_e2e_cannot_block_the_tag():
    for job in ("verify", "record"):
        assert "e2e" not in _needs(job)


def test_summary_always_runs_and_exposes_outcomes():
    summary = _jobs()["summary"]
    assert summary["if"] == "always()"
    assert set(_needs("summary")) == {
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
