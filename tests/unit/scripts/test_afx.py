"""`afx deploy-sync` CLI and Deploy workflow wiring (AFX-122).

GitHub and Linear are fakes; nothing here talks to a real API.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from agentfx.integrations import deploy_sync as ds

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy.yml"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, _REPO / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


afx = _load("afx")
deploy_staging = _load("deploy_staging")

BASE = "b" * 40
SHA = "a" * 40


class FakeGitHub:
    def __init__(self, tags=None):
        self.tags = dict(tags or {})
        self.created: list[tuple[str, str]] = []

    def get_tag_sha(self, name):
        return self.tags.get(name)

    def create_tag_ref(self, name, sha):
        self.created.append((name, sha))
        self.tags[name] = sha

    def delete_tag_ref(self, name):
        self.tags.pop(name, None)

    def list_tag_refs(self, prefix):
        return {k: v for k, v in self.tags.items() if k.startswith(prefix)}

    def compare(self, base, head):
        return "ahead", [SHA]

    def commit_pulls(self, sha):
        return [
            {
                "number": 7,
                "title": "AFX-7: thing",
                "head": {"ref": "agent/afx-7"},
                "merged_at": "2026-11-23T00:00:00Z",
                "merge_commit_sha": SHA,
            }
        ]


class FakeLinear:
    def __init__(self):
        self.issue_obj = {
            "id": "i7",
            "identifier": "AFX-7",
            "state": {"name": "In Staging", "type": "started"},
            "labels": [],
        }
        self.writes: list[tuple] = []

    def issue(self, ident):
        return self.issue_obj

    def comment_bodies(self, issue):
        return [b for (kind, b) in self.writes if kind == "comment"]

    def set_state(self, issue, name):
        if issue["state"]["name"] == name:
            return False
        issue["state"] = {
            "name": name,
            "type": "completed" if name == "Done" else "started",
        }
        self.writes.append(("state", name))
        return True

    def has_label(self, issue, name):
        return name in issue["labels"]

    def add_label(self, issue, name):
        issue["labels"].append(name)
        self.writes.append(("label+", name))
        return True

    def remove_label(self, issue, name):
        issue["labels"].remove(name)
        self.writes.append(("label-", name))
        return True

    def comment_once(self, issue, body, marker):
        if any(marker in b for b in self.comment_bodies(issue)):
            return False
        self.writes.append(("comment", body))
        return True


def _run(argv, gh, linear, monkeypatch, mode=None):
    if mode is None:
        monkeypatch.delenv("AFX_DEPLOY_SYNC_MODE", raising=False)
    else:
        monkeypatch.setenv("AFX_DEPLOY_SYNC_MODE", mode)
    args = afx.build_parser().parse_args(argv)
    return afx.deploy_sync(args, gh=gh, linear=linear)


RUN_OK = [
    "deploy-sync",
    "run",
    "--sha",
    SHA,
    "--backend",
    "success",
    "--frontend",
    "skipped",
    "--verify",
    "success",
    "--run-url",
    "https://run/1",
]


def test_default_mode_is_dry_run_and_writes_nothing(monkeypatch, capsys):
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeLinear()
    assert _run(RUN_OK, gh, linear, monkeypatch) == 0
    assert linear.writes == []
    out = capsys.readouterr().out
    assert "dry-run" in out and "AFX-7: would move In Staging -> Done" in out


def test_apply_mode_moves_ticket_to_done(monkeypatch):
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeLinear()
    assert _run(RUN_OK, gh, linear, monkeypatch, mode="apply") == 0
    assert ("state", "Done") in linear.writes


def test_apply_failure_blocks_with_needs_wilson(monkeypatch):
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeLinear()
    argv = [*RUN_OK]
    argv[argv.index("--backend") + 1] = "failure"
    assert _run(argv, gh, linear, monkeypatch, mode="apply") == 0
    assert ("state", "Blocked") in linear.writes
    assert ("label+", "needs-wilson") in linear.writes


def test_apply_without_linear_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    gh = FakeGitHub({ds.STAGING_TAG: BASE})
    assert _run(RUN_OK, gh, None, monkeypatch, mode="apply") == 1
    assert "LINEAR_API_KEY" in capsys.readouterr().out


def test_record_dry_run_and_apply(monkeypatch):
    gh = FakeGitHub({ds.STAGING_TAG: BASE})
    assert _run(["deploy-sync", "record", "--sha", SHA], gh, None, monkeypatch) == 0
    assert gh.created == []
    assert (
        _run(
            ["deploy-sync", "record", "--sha", SHA], gh, None, monkeypatch, mode="apply"
        )
        == 0
    )
    assert gh.created == [(ds.record_tag(SHA), BASE)]


def test_recover_replays_recorded_range(monkeypatch):
    gh = FakeGitHub({ds.STAGING_TAG: SHA, ds.record_tag(SHA): BASE})
    linear = FakeLinear()
    assert _run(["deploy-sync", "recover"], gh, linear, monkeypatch, mode="apply") == 0
    assert ("state", "Done") in linear.writes


def test_component_outcome_is_required():
    with pytest.raises(SystemExit):
        afx.build_parser().parse_args(
            ["deploy-sync", "run", "--sha", SHA, "--backend", "success"]
        )


# ── deploy_staging.outcomes ──────────────────────────────────────────


def _needs(**results):
    return {
        job.replace("_", "-"): {"result": r, "outputs": {}}
        for job, r in results.items()
    }


def test_outcomes_map_jobs_to_components():
    got = deploy_staging.outcomes(
        _needs(
            deploy_backend="success",
            deploy_frontend="failure",
            verify_staging="success",
            e2e="success",
            deploy_sync_record="failure",
        )
    )
    assert got == {"backend": "success", "frontend": "failure", "verify": "success"}


def test_outcomes_missing_verify_job_is_not_final():
    got = deploy_staging.outcomes(
        _needs(deploy_backend="success", deploy_frontend="success", e2e="success")
    )
    assert got["verify"] == ""
    assert ds.classify(got).kind == "indeterminate"


def test_outcomes_failed_gate_is_a_verify_failure():
    got = deploy_staging.outcomes(
        _needs(deploy_backend="skipped", deploy_frontend="skipped", e2e="failure")
    )
    assert ds.classify(got).kind == "failure"
    assert got["verify"] == "failure"


def test_outcomes_docs_only_is_success():
    assert ds.classify(deploy_staging.outcomes({}, docs_only=True)).kind == "success"


def test_outcomes_cli_writes_github_output(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    needs = json.dumps(_needs(deploy_backend="success", deploy_frontend="skipped"))
    assert deploy_staging.main(["outcomes", "--needs-json", needs]) == 0
    assert out.read_text().splitlines() == [
        "backend=success",
        "frontend=skipped",
        "verify=",
    ]


# ── workflow wiring ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def jobs():
    return yaml.safe_load(_WORKFLOW.read_text())["jobs"]


def test_sync_job_runs_after_every_deploy_outcome(jobs):
    sync = jobs["deploy-sync"]
    assert sync["if"] == "always()"
    assert {"deploy-backend", "deploy-frontend", "e2e", "deploy-sync-record"} <= set(
        sync["needs"]
    )
    run = " ".join(s.get("run", "") for s in sync["steps"])
    assert "scripts/afx.py deploy-sync run" in run
    assert "--backend" in run and "--frontend" in run and "--verify" in run


def test_sync_jobs_are_inert_until_cutover(jobs):
    for name in ("deploy-sync-record", "deploy-sync"):
        for step in jobs[name]["steps"]:
            mode = (step.get("env") or {}).get("AFX_DEPLOY_SYNC_MODE")
            if "afx.py" in step.get("run", ""):
                assert mode == "${{ vars.AFX_DEPLOY_SYNC_MODE || 'dry-run' }}"


def test_record_never_blocks_deploy(jobs):
    assert jobs["deploy-sync-record"]["continue-on-error"] is True
    for name in ("deploy-backend", "deploy-frontend"):
        assert "deploy-sync-record" not in (jobs[name].get("needs") or [])


def test_workflow_expressions_are_not_interpolated_into_run(jobs):
    for name in ("deploy-sync-record", "deploy-sync"):
        for step in jobs[name]["steps"]:
            assert "${{" not in step.get("run", "")
