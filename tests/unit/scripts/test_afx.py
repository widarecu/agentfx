"""`scripts/afx.py` CLI: start/block (AFX-118) and deploy-sync (AFX-122).

No network: start/block use the real `LinearClient` wired to
`linear_fake.FakeLinear`; deploy-sync uses in-memory GitHub/Linear fakes."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

from agentfx.integrations import deploy_sync as ds
from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeLinear

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "afx.py"
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy.yml"

_spec = importlib.util.spec_from_file_location("afx_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
afx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = afx
_spec.loader.exec_module(afx)

_ds_spec = importlib.util.spec_from_file_location(
    "deploy_staging", _REPO / "scripts" / "deploy_staging.py"
)
assert _ds_spec is not None and _ds_spec.loader is not None
deploy_staging = importlib.util.module_from_spec(_ds_spec)
sys.modules[_ds_spec.name] = deploy_staging
_ds_spec.loader.exec_module(deploy_staging)


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


# ── deploy-sync ──────────────────────────────────────────────────────

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


class FakeDeployLinear:
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


def _sync_cli(argv, gh, linear, monkeypatch, mode=None):
    if mode is None:
        monkeypatch.delenv("AFX_DEPLOY_SYNC_MODE", raising=False)
    else:
        monkeypatch.setenv("AFX_DEPLOY_SYNC_MODE", mode)
    args = afx._parser().parse_args(argv)
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
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeDeployLinear()
    assert _sync_cli(RUN_OK, gh, linear, monkeypatch) == 0
    assert linear.writes == []
    out = capsys.readouterr().out
    assert "dry-run" in out and "AFX-7: would move In Staging -> Done" in out


def test_apply_mode_moves_ticket_to_done(monkeypatch):
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeDeployLinear()
    assert _sync_cli(RUN_OK, gh, linear, monkeypatch, mode="apply") == 0
    assert ("state", "Done") in linear.writes


def test_apply_failure_blocks_with_needs_wilson(monkeypatch):
    gh, linear = FakeGitHub({ds.STAGING_TAG: BASE}), FakeDeployLinear()
    argv = [*RUN_OK]
    argv[argv.index("--backend") + 1] = "failure"
    assert _sync_cli(argv, gh, linear, monkeypatch, mode="apply") == 0
    assert ("state", "Blocked") in linear.writes
    assert ("label+", "needs-wilson") in linear.writes


def test_apply_without_linear_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    gh = FakeGitHub({ds.STAGING_TAG: BASE})
    assert _sync_cli(RUN_OK, gh, None, monkeypatch, mode="apply") == 1
    assert "LINEAR_API_KEY" in capsys.readouterr().out


def test_record_dry_run_and_apply(monkeypatch):
    gh = FakeGitHub({ds.STAGING_TAG: BASE})
    assert (
        _sync_cli(["deploy-sync", "record", "--sha", SHA], gh, None, monkeypatch) == 0
    )
    assert gh.created == []
    assert (
        _sync_cli(
            ["deploy-sync", "record", "--sha", SHA], gh, None, monkeypatch, mode="apply"
        )
        == 0
    )
    assert gh.created == [(ds.record_tag(SHA), BASE)]


def test_recover_replays_recorded_range(monkeypatch):
    gh = FakeGitHub({ds.STAGING_TAG: SHA, ds.record_tag(SHA): BASE})
    linear = FakeDeployLinear()
    assert (
        _sync_cli(["deploy-sync", "recover"], gh, linear, monkeypatch, mode="apply")
        == 0
    )
    assert ("state", "Done") in linear.writes


def test_component_outcome_is_required():
    with pytest.raises(SystemExit):
        afx._parser().parse_args(
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


def test_workflow_expressions_are_not_interpolated_into_sync_cli(jobs):
    for name in ("deploy-sync-record", "deploy-sync"):
        for step in jobs[name]["steps"]:
            assert "${{" not in step.get("run", "")


def test_sync_job_can_read_pull_requests(jobs):
    assert jobs["deploy-sync"]["permissions"]["pull-requests"] == "read"
