"""Railway deploy job waits for Railway's verdict (AFX-112).

`railway up --detach` returns once the code is uploaded, so the Deploy job used
to go green for backend deploys whose build or health check failed later.
`scripts/railway_deploy.py` starts the deploy, then polls that deployment until
Railway reports a terminal status. These cases pin the workflow wiring, the
deployment-ID parsing and the status → exit-code mapping.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "railway_deploy.py"
_DEPLOY_WORKFLOW = _REPO / ".github" / "workflows" / "deploy.yml"

_spec = importlib.util.spec_from_file_location("railway_deploy", _SCRIPT)
assert _spec is not None and _spec.loader is not None
rd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)

_DEP_ID = "99288b05-ef29-4415-8487-92327920227b"
_LOGS_URL = (
    "https://railway.com/project/1e8f258e-10e8-46d2-83b6-625906f688ec/service/"
    f"c3e4ce31-ecc5-4ddc-8e53-9faf0cce5c1e?id={_DEP_ID}"
)


def _proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _listing(status: str, dep_id: str = _DEP_ID) -> subprocess.CompletedProcess:
    return _proc(
        json.dumps(
            [
                {"id": dep_id, "status": status, "createdAt": "2026-11-21T08:49:34Z"},
                {
                    "id": "older",
                    "status": "REMOVED",
                    "createdAt": "2026-11-20T00:00:00Z",
                },
            ]
        )
    )


class _FakeRailway:
    """Replays `railway up` once, then one `deployment list` result per poll."""

    def __init__(self, up, polls):
        self.up = up
        self.polls = list(polls)
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:2] == ["railway", "up"]:
            return self.up
        assert cmd[:3] == ["railway", "deployment", "list"], cmd
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _up_ok():
    return _proc(json.dumps({"deploymentId": _DEP_ID, "logsUrl": _LOGS_URL}) + "\n")


def _wait(fake, timeout=600, interval=10):
    clock = _Clock()
    logs: list[str] = []
    code = rd.wait_for_deployment(
        "agentfx",
        _DEP_ID,
        timeout=timeout,
        interval=interval,
        runner=fake,
        clock=clock,
        sleep=clock.sleep,
        log=logs.append,
    )
    return code, logs, clock


# ─────────────────────────────────────────────────────────────────────
# Workflow wiring
# ─────────────────────────────────────────────────────────────────────


def _deploy_backend_job():
    workflow = yaml.safe_load(_DEPLOY_WORKFLOW.read_text())
    return workflow["jobs"]["deploy-backend"]


def test_deploy_backend_job_does_not_finish_on_upload():
    """Reproduces AFX-112: a bare `railway up --detach` step ends the job on upload."""
    runs = [step.get("run", "") for step in _deploy_backend_job()["steps"]]
    assert not any("railway up" in run for run in runs), runs
    assert any("scripts/railway_deploy.py" in run for run in runs), runs


def test_railway_cli_is_pinned_for_the_json_contract():
    runs = [step.get("run", "") for step in _deploy_backend_job()["steps"]]
    install = next(run for run in runs if "@railway/cli" in run)
    assert "@railway/cli@" in install
    assert "@railway/cli@latest" not in install


def test_job_timeout_outlasts_the_script_wait():
    job = _deploy_backend_job()
    assert job["timeout-minutes"] * 60 > rd.DEFAULT_TIMEOUT_SECONDS


# ─────────────────────────────────────────────────────────────────────
# Starting the deploy
# ─────────────────────────────────────────────────────────────────────


def test_parse_deployment_id_from_json_line():
    out = "warning: something\n" + json.dumps(
        {"deploymentId": _DEP_ID, "logsUrl": _LOGS_URL}
    )
    assert rd.parse_deployment_id(out) == (_DEP_ID, _LOGS_URL)


def test_parse_deployment_id_falls_back_to_logs_url():
    out = f"Indexing...\nUploading...\n  Deployment: {_LOGS_URL}\n"
    assert rd.parse_deployment_id(out) == (_DEP_ID, None)


def test_parse_deployment_id_errors_without_an_id():
    try:
        rd.parse_deployment_id("Uploading...\n")
    except rd.DeployError as exc:
        assert "deployment ID" in str(exc)
    else:
        raise AssertionError("expected DeployError")


def test_start_deploy_uses_detach_json_and_service():
    fake = _FakeRailway(_up_ok(), [_listing("SUCCESS")])
    assert rd.start_deploy("agentfx", fake) == (_DEP_ID, _LOGS_URL)
    assert fake.calls[0] == [
        "railway",
        "up",
        "--detach",
        "--json",
        "--service",
        "agentfx",
    ]


def test_main_fails_when_upload_fails():
    fake = _FakeRailway(
        _proc(returncode=1, stderr="Invalid RAILWAY_TOKEN"), [_listing("SUCCESS")]
    )
    assert rd.main(["--service", "agentfx"], runner=fake) == 1
    assert len(fake.calls) == 1


# ─────────────────────────────────────────────────────────────────────
# Waiting for the verdict
# ─────────────────────────────────────────────────────────────────────


def test_success_only_after_railway_reports_success():
    fake = _FakeRailway(
        _up_ok(),
        [
            _listing("INITIALIZING"),
            _listing("BUILDING"),
            _listing("DEPLOYING"),
            _listing("SUCCESS"),
        ],
    )
    code, logs, _ = _wait(fake)
    assert code == 0
    assert len(fake.calls) == 4
    assert any("BUILDING" in line for line in logs)
    assert any("is live" in line for line in logs)


def test_failed_build_fails_the_job():
    fake = _FakeRailway(_up_ok(), [_listing("BUILDING"), _listing("FAILED")])
    code, logs, _ = _wait(fake)
    assert code == 1
    assert any(line.startswith("::error::") and "FAILED" in line for line in logs)


def test_failed_health_check_fails_the_job():
    # Railway marks a deployment FAILED when /health never passes.
    fake = _FakeRailway(_up_ok(), [_listing("DEPLOYING"), _listing("FAILED")])
    assert _wait(fake)[0] == 1


def test_crashed_and_removed_fail_the_job():
    for status in ("CRASHED", "REMOVED", "SKIPPED", "NEEDS_APPROVAL"):
        fake = _FakeRailway(_up_ok(), [_listing(status)])
        assert _wait(fake)[0] == 1, status


def test_waits_while_deployment_not_listed_yet():
    fake = _FakeRailway(
        _up_ok(), [_listing("SUCCESS", dep_id="older-one"), _listing("SUCCESS")]
    )
    assert _wait(fake)[0] == 0
    assert len(fake.calls) == 2


def test_times_out_when_never_terminal():
    fake = _FakeRailway(_up_ok(), [_listing("BUILDING")])
    code, logs, clock = _wait(fake, timeout=60, interval=10)
    assert code == 1
    assert clock.now == 60
    assert any("did not finish within 60s" in line for line in logs)


def test_transient_poll_errors_are_tolerated():
    fake = _FakeRailway(
        _up_ok(),
        [_proc(returncode=1, stderr="503"), _proc("not json"), _listing("SUCCESS")],
    )
    assert _wait(fake)[0] == 0


def test_persistent_poll_errors_fail_the_job():
    fake = _FakeRailway(_up_ok(), [_proc(returncode=1, stderr="503")])
    code, logs, _ = _wait(fake, timeout=10_000)
    assert code == 1
    assert len(fake.calls) == rd.MAX_CONSECUTIVE_POLL_ERRORS
    assert any("consecutive errors" in line for line in logs)


def test_main_end_to_end_success():
    fake = _FakeRailway(_up_ok(), [_listing("SUCCESS")])
    assert rd.main(["--service", "agentfx", "--interval", "0"], runner=fake) == 0
    assert fake.calls[1][:3] == ["railway", "deployment", "list"]
    assert "agentfx" in fake.calls[1]
