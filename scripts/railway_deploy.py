#!/usr/bin/env python3
"""Deploy the backend to Railway and wait for Railway's verdict (AFX-112).

`railway up --detach` returns as soon as the code is uploaded, before Railway
has built the image or passed the service's `/health` check (`.railway/railway.ts`). Used on
its own it turns the Deploy workflow green for deploys that later fail.

This script starts the deploy with `railway up --detach --json`, takes the
deployment ID it prints, then polls `railway deployment list --json` until that
deployment reaches a terminal status:

* `SUCCESS` (built and healthy) → exit 0.
* `FAILED`, `CRASHED`, `REMOVED`, `SKIPPED`, ... → exit 1.
* No terminal status within `--timeout` seconds → exit 1.

The job conclusion then is the real deploy outcome, which is what
`afx deploy-sync` reads to move tickets to Done or Blocked.

Stdlib only + the Railway CLI (authenticated through `RAILWAY_TOKEN`):

    python3 scripts/railway_deploy.py --service agentfx
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

# Railway `DeploymentStatus` values. SLEEPING only follows a healthy deploy.
SUCCESS_STATUSES = frozenset({"SUCCESS", "SLEEPING"})
FAILURE_STATUSES = frozenset(
    {"FAILED", "CRASHED", "REMOVED", "REMOVING", "SKIPPED", "NEEDS_APPROVAL"}
)

DEFAULT_TIMEOUT_SECONDS = 25 * 60
DEFAULT_INTERVAL_SECONDS = 10
MAX_CONSECUTIVE_POLL_ERRORS = 6

_LOGS_URL_ID_RE = re.compile(r"[?&]id=([0-9a-fA-F-]{36})")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class DeployError(Exception):
    """The deploy could not be started or its status could not be read."""


def run_cli(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def parse_deployment_id(stdout: str) -> tuple[str, str | None]:
    """Return (deployment_id, logs_url) from `railway up --detach --json` output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("deploymentId"):
            return str(payload["deploymentId"]), payload.get("logsUrl")
    match = _LOGS_URL_ID_RE.search(stdout)
    if match:
        return match.group(1), None
    raise DeployError(
        "could not find a deployment ID in `railway up` output:\n" + stdout.strip()
    )


def start_deploy(service: str, runner: Runner = run_cli) -> tuple[str, str | None]:
    result = runner(["railway", "up", "--detach", "--json", "--service", service])
    if result.returncode != 0:
        raise DeployError(
            f"`railway up` exited {result.returncode}:\n"
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    return parse_deployment_id(result.stdout)


def fetch_status(
    service: str, deployment_id: str, runner: Runner = run_cli
) -> str | None:
    """Return the deployment's status, or None if Railway does not list it yet."""
    result = runner(["railway", "deployment", "list", "--service", service, "--json"])
    if result.returncode != 0:
        raise DeployError(
            f"`railway deployment list` exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    try:
        deployments = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeployError(f"`railway deployment list` returned non-JSON: {exc}")
    if not isinstance(deployments, list):
        raise DeployError("`railway deployment list` did not return a list")
    for deployment in deployments:
        if isinstance(deployment, dict) and deployment.get("id") == deployment_id:
            return str(deployment.get("status", "")).upper() or None
    return None


def wait_for_deployment(
    service: str,
    deployment_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    runner: Runner = run_cli,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    """Poll until the deployment is terminal. Return the process exit code."""
    deadline = clock() + timeout
    last_status: str | None = None
    errors = 0
    while True:
        try:
            status = fetch_status(service, deployment_id, runner)
            errors = 0
        except DeployError as exc:
            errors += 1
            log(f"Could not read deployment status ({errors}): {exc}")
            if errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                log(
                    f"::error::Gave up reading Railway deployment {deployment_id} "
                    f"after {errors} consecutive errors."
                )
                return 1
            status = last_status

        if status != last_status:
            log(f"Railway deployment {deployment_id}: {status or 'not listed yet'}")
            last_status = status

        if status in SUCCESS_STATUSES:
            log(f"Railway deployment {deployment_id} is live ({status}).")
            return 0
        if status in FAILURE_STATUSES:
            log(
                f"::error::Railway deployment {deployment_id} ended {status}; "
                "staging is not running this commit. Check the build and "
                "deploy logs in Railway."
            )
            return 1
        if clock() >= deadline:
            log(
                f"::error::Railway deployment {deployment_id} did not finish within "
                f"{int(timeout)}s (last status: {last_status or 'unknown'})."
            )
            return 1
        sleep(interval)


def main(argv: list[str] | None = None, runner: Runner = run_cli) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--service", default="agentfx")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds to wait for a terminal status",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between status polls",
    )
    args = parser.parse_args(argv)

    try:
        deployment_id, logs_url = start_deploy(args.service, runner)
    except DeployError as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Started Railway deployment {deployment_id}")
    if logs_url:
        print(f"Logs: {logs_url}")
    return wait_for_deployment(
        args.service,
        deployment_id,
        timeout=args.timeout,
        interval=args.interval,
        runner=runner,
    )


if __name__ == "__main__":
    sys.exit(main())
