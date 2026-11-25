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
from agentfx.integrations import claim_store as cs
from tests.unit.scripts.linear_fake import FakeClaims, FakeLinear

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


def _run(fake: FakeLinear, *argv: str, claims: FakeClaims | None = None) -> int:
    return afx.main(
        list(argv),
        client=ld.LinearClient("k", opener=fake.opener),
        claims=claims if claims is not None else FakeClaims(),
    )


def test_start_ready_exits_zero(fake, capsys):
    fake.add("AFX-1", "Ready")
    assert _run(fake, "start", "AFX-1") == afx.EXIT_OK
    assert fake.issues["AFX-1"].state == "In Progress"
    assert "Ready -> In Progress" in capsys.readouterr().out


def test_second_taker_refused(fake, capsys):
    fake.add("AFX-1", "Ready")
    claims = FakeClaims()
    _run(fake, "start", "AFX-1", "--taker", "Wilson", claims=claims)
    assert (
        _run(fake, "start", "AFX-1", "--taker", "Devin", claims=claims)
        == afx.EXIT_REFUSED
    )
    assert "claimed by Wilson" in capsys.readouterr().err


def test_start_comment_names_taker_and_link(fake):
    fake.add("AFX-1", "Ready")
    _run(fake, "start", "AFX-1", "--taker", "Devin", "--link", "https://s/1")
    [comment] = fake.issues["AFX-1"].comments
    assert "Claimed by Devin**: https://s/1" in comment


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


def test_missing_key_without_github_errors(monkeypatch, capsys):
    """Keyless needs GitHub credentials to send the request."""
    for var in ("LINEAR_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cs, "_gh_auth_token", lambda: "")
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

    def delete_tag_ref(self, name, sha):
        assert self.tags[name] == sha
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


def test_previous_sha_lets_dry_run_sync_after_the_tag_moved(monkeypatch, capsys):
    gh, linear = FakeGitHub({ds.STAGING_TAG: SHA}), FakeDeployLinear()
    assert _sync_cli(RUN_OK, gh, linear, monkeypatch) == 1
    capsys.readouterr()
    argv = [*RUN_OK, "--previous-sha", BASE]
    assert _sync_cli(argv, gh, linear, monkeypatch) == 0
    assert linear.writes == []
    assert "AFX-7: would move In Staging -> Done" in capsys.readouterr().out


def test_empty_previous_sha_is_ignored(monkeypatch):
    gh, linear = FakeGitHub({ds.STAGING_TAG: SHA}), FakeDeployLinear()
    assert _sync_cli([*RUN_OK, "--previous-sha", ""], gh, linear, monkeypatch) == 1


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


# ── deploy_staging.ticket_outcomes ───────────────────────────────────


def _ticket(outcome, backend, frontend, verify):
    return deploy_staging.ticket_outcomes(outcome, backend, frontend, verify)


def test_ticket_outcomes_success():
    got = _ticket("success", "deployed", "unchanged", "passed")
    assert got == {"backend": "success", "frontend": "skipped", "verify": "success"}
    assert ds.classify(got).kind == "success"


def test_ticket_outcomes_docs_only_is_success():
    got = _ticket("success", "unchanged", "unchanged", "passed")
    assert ds.classify(got).kind == "success"


def test_ticket_outcomes_failed_component_is_failure():
    got = _ticket("failure", "deployed", "failed", "not-run")
    assert got["frontend"] == "failure" and got["verify"] == ""
    assert ds.classify(got).kind == "failure"
    assert ds.classify(_ticket("failure", "deployed", "unchanged", "failed")).kind == (
        "failure"
    )


@pytest.mark.parametrize("outcome", ["stale", "", "unknown"])
def test_ticket_outcomes_not_final_changes_nothing(outcome):
    got = _ticket(outcome, "deployed", "deployed", "passed")
    assert ds.classify(got).kind == "indeterminate"


def test_ticket_outcomes_not_run_plan_changes_nothing():
    got = _ticket("failure", "not-run", "not-run", "not-run")
    assert ds.classify(got).kind == "indeterminate"


def test_ticket_outcomes_cli_writes_github_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    argv = ["ticket-outcomes", "--outcome", "success", "--backend", "deployed"]
    argv += ["--frontend", "unchanged", "--verify", "passed"]
    assert deploy_staging.main(argv) == 0
    assert set(out.read_text().split()) >= {
        "backend=success",
        "frontend=skipped",
        "verify=success",
    }


# ── workflow wiring ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def jobs():
    return yaml.safe_load(_WORKFLOW.read_text())["jobs"]


def test_sync_job_runs_after_every_deploy_outcome(jobs):
    sync = jobs["deploy-sync"]
    assert sync["if"] == "always()"
    assert {"plan", "summary", "deploy-sync-record"} <= set(sync["needs"])
    run = " ".join(s.get("run", "") for s in sync["steps"])
    assert "scripts/afx.py deploy-sync run" in run
    assert "--backend" in run and "--frontend" in run and "--verify" in run


def test_sync_jobs_are_inert_until_cutover(jobs):
    for name in ("deploy-sync-record", "deploy-sync"):
        for step in jobs[name]["steps"]:
            mode = (step.get("env") or {}).get("AFX_DEPLOY_SYNC_MODE")
            if "afx.py" in step.get("run", ""):
                assert mode == "${{ vars.AFX_DEPLOY_SYNC_MODE || 'dry-run' }}"


def test_sync_gets_the_replaced_tag_from_record(jobs):
    sync = jobs["deploy-sync"]
    assert "record" in sync["needs"]
    step = next(s for s in sync["steps"] if "deploy-sync run" in s.get("run", ""))
    assert '--previous-sha "$PREVIOUS_SHA"' in step["run"]
    assert step["env"]["PREVIOUS_SHA"] == "${{ needs.record.outputs.previous_sha }}"


def test_dry_run_sync_never_fails_the_deploy(jobs):
    """AFX-133: the legacy state workflow reads Deploy's conclusion as the
    deploy result, so a dry-run sync failure must not turn it red."""
    assert jobs["deploy-sync"]["continue-on-error"] == (
        "${{ (vars.AFX_DEPLOY_SYNC_MODE || 'dry-run') != 'apply' }}"
    )


def test_record_never_blocks_deploy(jobs):
    assert jobs["deploy-sync-record"]["continue-on-error"] is True
    for name in ("deploy-backend", "deploy-frontend", "verify"):
        assert "deploy-sync-record" not in (jobs[name].get("needs") or [])
    # The tag moves only after the range is recorded; record's own gate is
    # verify, so a failed range record cannot stop it.
    assert "deploy-sync-record" in jobs["record"]["needs"]
    assert jobs["record"]["if"].startswith("!cancelled()")


def test_workflow_expressions_are_not_interpolated_into_sync_cli(jobs):
    for name in ("deploy-sync-record", "deploy-sync"):
        for step in jobs[name]["steps"]:
            assert "${{" not in step.get("run", "")


def test_sync_job_can_read_pull_requests(jobs):
    assert jobs["deploy-sync"]["permissions"]["pull-requests"] == "read"
