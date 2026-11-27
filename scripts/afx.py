#!/usr/bin/env python3
"""Agent-facing Linear commands (AFX-118, AFX-122).

    python scripts/afx.py start AFX-123 --taker "Devin" --link <session-url>
    python scripts/afx.py block AFX-123 "Which pair should this use?"
    python scripts/afx.py request run      # Actions only: afx-request.yml
    python scripts/afx.py deploy-sync record --sha <attempted>
    python scripts/afx.py deploy-sync run --sha <attempted> \
        --backend success --frontend skipped --verify success --run-url <url>
    python scripts/afx.py deploy-sync recover
    python scripts/afx.py reconcile --previous previous/digest.json \
        --state-out digest.json

`start` is the one shared claim (AFX-142): of all takers racing for a
Ready ticket exactly one wins (Ready -> In Progress, one comment naming
it); everyone else is refused. Assignee and delegate are never touched.
`block` moves an active ticket to Blocked, adds `needs-wilson` and
posts the question once, however often it is retried.

With Linear credentials set (LINEAR_API_KEY, or the pipeline app's
LINEAR_PIPELINE_CLIENT_ID and _SECRET), both run here. Without it (Devin and other
keyless sessions) they push an `afx-request/*` branch; the
`afx-request.yml` workflow runs the same `start`/`block` with the repo's
key and reports back as the `afx/request` commit status, which this
command waits for. Claims are serialized by `claim_store` (GitHub tags),
so GitHub credentials are needed either way.

`deploy-sync` moves the tickets of the PRs merged since the last
successful `deployed/staging` SHA (see `agentfx.integrations.deploy_sync`).
`--mode` (default `$AFX_DEPLOY_SYNC_MODE`, else `dry-run`) must be `apply`
to write to GitHub or Linear. GitHub auth comes from `GH_TOKEN` via `gh`.

`reconcile` (AFX-123) replays the deploy sync for the current tag (same
`--mode`), then writes one drift digest to `$GITHUB_STEP_SUMMARY`
without moving any ticket (see `agentfx.integrations.delivery_reconcile`).

Exit codes: 0 done, 1 refused (wrong state) or deploy-sync error, 2 error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentfx.integrations import claim_store  # noqa: E402
from agentfx.integrations import delivery_reconcile as dr  # noqa: E402
from agentfx.integrations import deploy_sync as ds  # noqa: E402
from agentfx.integrations import linear_delivery  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

OUTCOMES = ("success", "failure", "skipped", "cancelled", "")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afx", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Claim a Ready ticket (Ready -> In Progress)")
    start.add_argument("issue", help="Ticket identifier, e.g. AFX-123")
    start.add_argument(
        "--taker",
        default=os.environ.get("AFX_TAKER") or _git_user() or "unknown taker",
        help="Who takes the ticket, as the claim comment names it "
        "(default $AFX_TAKER, else git user.name)",
    )
    start.add_argument(
        "--link",
        default=os.environ.get("AFX_TAKER_LINK", ""),
        help="The session or branch doing the work (default $AFX_TAKER_LINK)",
    )
    block = sub.add_parser("block", help="Block an active ticket on Wilson")
    block.add_argument("issue", help="Ticket identifier, e.g. AFX-123")
    block.add_argument("question", help="The question Wilson needs to answer")
    for p in (start, block):
        p.add_argument(
            "--wait",
            type=int,
            default=600,
            help="Keyless only: seconds to wait for the request's verdict",
        )
    request = sub.add_parser(
        "request", help="Run a keyless start/block request (afx-request.yml only)"
    )
    request.add_argument("action", choices=("run",))
    _add_deploy_sync(sub)
    _add_reconcile(sub)
    return parser


def _add_reconcile(sub) -> None:
    rec = sub.add_parser(
        "reconcile", help="Replay a missed deploy sync and publish the drift digest"
    )
    rec.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "widarecu/agentfx")
    )
    rec.add_argument(
        "--mode",
        choices=("apply", "dry-run"),
        default=os.environ.get("AFX_DEPLOY_SYNC_MODE") or "dry-run",
    )
    rec.add_argument("--run-url", default="")
    rec.add_argument("--window-days", type=int, default=dr.DEFAULT_WINDOW_DAYS)
    rec.add_argument(
        "--wilson-user-id",
        default=os.environ.get("AFX_WILSON_LINEAR_USER_ID")
        or dr.DEFAULT_WILSON_USER_ID,
    )
    rec.add_argument("--previous", help="digest.json from the last run, if any")
    rec.add_argument("--state-out", help="Where to write this run's digest.json")
    rec.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))


def _read_previous(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def reconcile(args, *, gh=None, client=None, now: datetime | None = None) -> int:
    try:
        client = client or linear_delivery.LinearClient.from_env()
    except linear_delivery.LinearError as exc:
        print(f"afx reconcile: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    result = dr.reconcile(
        gh or ds.GhCli(args.repo),
        client,
        repo=args.repo,
        now=now or datetime.now(UTC),
        window_days=args.window_days,
        wilson_user_id=args.wilson_user_id,
        run_url=args.run_url,
        apply=args.mode == "apply",
        previous=_read_previous(args.previous),
    )
    if args.mode != "apply":
        print("dry-run: deploy sync replay makes no GitHub or Linear writes")
    for line in result.sync_lines:
        print(line)
    print(result.summary, end="")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(result.summary)
    if args.state_out and result.state:
        with open(args.state_out, "w", encoding="utf-8") as fh:
            json.dump(result.state, fh, indent=2)
    return result.exit_code


def _add_deploy_sync(sub) -> None:
    deploy = sub.add_parser(
        "deploy-sync", help="Sync Linear tickets with a staging deploy"
    )
    actions = deploy.add_subparsers(dest="action", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "widarecu/agentfx")
    )
    common.add_argument(
        "--mode",
        choices=("apply", "dry-run"),
        default=os.environ.get("AFX_DEPLOY_SYNC_MODE") or "dry-run",
    )
    common.add_argument("--staging-url", default=ds.STAGING_URL)
    common.add_argument("--run-url", default="")

    rec = actions.add_parser(
        "record", parents=[common], help="Persist the baseline before the tag moves"
    )
    rec.add_argument("--sha", required=True, help="Attempted SHA")

    run = actions.add_parser(
        "run", parents=[common], help="Move tickets for this deploy's outcome"
    )
    run.add_argument("--sha", required=True, help="Attempted SHA")
    run.add_argument(
        "--baseline", default=None, help="Override the previous successful SHA"
    )
    run.add_argument(
        "--previous-sha",
        default=None,
        help="SHA this run's deployed/staging move replaced; used only when "
        "the tag is already at --sha and no baseline record exists",
    )
    for component in ds.COMPONENTS:
        run.add_argument(f"--{component}", choices=OUTCOMES, required=True)

    actions.add_parser(
        "recover",
        parents=[common],
        help="Replay the sync for the current deployed/staging",
    )


def deploy_sync(args, *, gh=None, linear=None) -> int:
    gh = gh or ds.GhCli(args.repo)
    apply = args.mode == "apply"
    if args.action == "record":
        report = ds.record(gh, args.sha, apply=apply)
    else:
        if linear is None and linear_delivery.has_credentials():
            linear = linear_delivery.DeployIssues(
                linear_delivery.LinearClient.from_env()
            )
        if apply and linear is None:
            print(
                "::error ::no Linear credentials (LINEAR_API_KEY or the "
                "LINEAR_PIPELINE_CLIENT_ID/SECRET pair); cannot apply ticket sync"
            )
            return 1
        if args.action == "run":
            verdict = ds.classify({c: getattr(args, c) for c in ds.COMPONENTS})
            report = ds.sync(
                gh,
                linear,
                attempted=args.sha,
                verdict=verdict,
                run_url=args.run_url,
                staging_url=args.staging_url,
                apply=apply,
                baseline=args.baseline,
                previous=args.previous_sha or None,
            )
        else:
            report = ds.recover(
                gh,
                linear,
                run_url=args.run_url,
                staging_url=args.staging_url,
                apply=apply,
            )
    if not apply:
        print(
            "dry-run: no GitHub or Linear writes "
            "(set AFX_DEPLOY_SYNC_MODE=apply to enable)"
        )
    for line in report.lines:
        print(line)
    return report.exit_code


def _git_user() -> str:
    try:
        out = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _transition(args, client, claims) -> linear_delivery.TransitionResult:
    if args.command == "start":
        return linear_delivery.start(
            client,
            args.issue,
            claims=claims,
            taker=claim_store.Taker(args.taker.strip(), args.link.strip()),
        )
    return linear_delivery.block(client, args.issue, args.question)


def _report(command: str, result: linear_delivery.TransitionResult) -> int:
    if not result.ok:
        print(f"afx {command}: refused: {result.reason}", file=sys.stderr)
        return EXIT_REFUSED
    if result.changed:
        print(f"{result.identifier}: {result.from_state} -> {result.to_state}")
    else:
        print(f"{result.identifier}: already {result.to_state}; nothing to do")
    if result.reason:
        print(f"afx {command}: warning: {result.reason}", file=sys.stderr)
    return EXIT_OK


# Keyless requests: the request is a commit on `afx-request/<uuid>` whose
# tree is main's (so CI, Vercel and the workflow itself run main's code)
# and whose message carries the arguments. Issue numbers, not AFX-n IDs,
# so Linear's GitHub integration never links the request to the ticket.

REQUEST_BRANCH_PREFIX = "afx-request/"
REQUEST_SUBJECT = "afx request"


def request_message(args) -> str:
    number = args.issue.upper().removeprefix("AFX-")
    if not number.isdigit():
        raise ValueError(f"not an AFX ticket: {args.issue}")
    payload = {"command": args.command, "issue": number}
    if args.command == "start":
        payload.update(taker=args.taker.strip(), link=args.link.strip())
    else:
        payload["question"] = args.question.strip()
    return f"{REQUEST_SUBJECT}: {args.command} {number}\n\n{json.dumps(payload)}"


def parse_request(message: str) -> argparse.Namespace:
    body = next(
        (line for line in reversed(message.splitlines()) if line.startswith("{")), ""
    )
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError("request message carries no JSON payload") from exc
    command, number = data.get("command"), str(data.get("issue") or "")
    if command not in ("start", "block") or not number.isdigit():
        raise ValueError(f"bad request: {data}")
    args = argparse.Namespace(command=command, issue=f"AFX-{number}")
    if command == "start":
        args.taker = str(data.get("taker") or "").strip() or "unknown taker"
        args.link = str(data.get("link") or "")
    else:
        args.question = str(data.get("question") or "")
    return args


def _git(*argv: str) -> str:
    return subprocess.run(
        ["git", *argv], check=True, capture_output=True, text=True
    ).stdout.strip()


def push_request(message: str) -> str:
    """Push the request commit; returns its SHA."""
    _git("fetch", "--quiet", "origin", "main")
    base = _git("rev-parse", "origin/main")
    sha = _git("commit-tree", f"{base}^{{tree}}", "-p", base, "-m", message)
    branch = f"{REQUEST_BRANCH_PREFIX}{uuid.uuid4().hex[:12]}"
    _git("push", "--quiet", "origin", f"{sha}:refs/heads/{branch}")
    return sha


def keyless(
    args,
    *,
    push=push_request,
    github: claim_store.GitHubClaims | None = None,
    sleep=time.sleep,
    poll_seconds: float = 10.0,
) -> int:
    try:
        message = request_message(args)
        github = github or claim_store.GitHubClaims.from_env()
        sha = push(message)
    except (
        ValueError,
        claim_store.ClaimStoreError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            f"afx {args.command}: error: no LINEAR_API_KEY, and the keyless "
            f"request could not be sent: {exc}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    print(f"no LINEAR_API_KEY: sent request {sha[:12]} to afx-request.yml")
    waited = 0.0
    while True:
        try:
            status = github.request_status(sha)
        except claim_store.ClaimStoreError as exc:
            status = None
            print(f"afx {args.command}: status read failed: {exc}", file=sys.stderr)
        if status and status.get("state") != "pending":
            break
        if waited >= args.wait:
            print(
                f"afx {args.command}: error: no verdict after {args.wait}s; "
                f"check the ticket in Linear before retrying (request {sha})",
                file=sys.stderr,
            )
            return EXIT_ERROR
        sleep(poll_seconds)
        waited += poll_seconds
    state, text = status.get("state"), status.get("description") or ""
    if state == "success":
        print(text)
        return EXIT_OK
    if state == "failure":
        print(f"afx {args.command}: refused: {text}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"afx {args.command}: error: {text} {status.get('target_url') or ''}")
    return EXIT_ERROR


def run_request(
    *,
    client: linear_delivery.LinearClient | None = None,
    github: claim_store.GitHubClaims | None = None,
    env: dict | None = None,
) -> int:
    """`afx-request.yml`: run one keyless request and report it as the
    `afx/request` status on the request commit, then drop its branch."""
    env = os.environ if env is None else env
    sha, ref = env.get("REQUEST_SHA", ""), env.get("REQUEST_REF", "")
    run_url = env.get("RUN_URL", "")
    github = github or claim_store.GitHubClaims.from_env()
    try:
        args = parse_request(env.get("REQUEST_MESSAGE", ""))
        client = client or linear_delivery.LinearClient.from_env()
        result = _transition(args, client, github)
    except (
        linear_delivery.LinearError,
        claim_store.ClaimStoreError,
        ValueError,
    ) as exc:
        github.set_status(sha, "error", str(exc), run_url)
        print(f"afx request: error: {exc}", file=sys.stderr)
        code = EXIT_ERROR
    else:
        if result.ok:
            verdict = (
                f"{result.identifier}: {result.from_state} -> {result.to_state}"
                if result.changed
                else f"{result.identifier}: already {result.to_state}; nothing to do"
            )
        else:
            verdict = result.reason
        github.set_status(sha, "success" if result.ok else "failure", verdict, run_url)
        print(verdict)
        code = EXIT_OK
    if ref.startswith(f"refs/heads/{REQUEST_BRANCH_PREFIX}"):
        github.delete_branch(ref.removeprefix("refs/heads/"))
    return code


def main(
    argv: list[str] | None = None,
    *,
    client: linear_delivery.LinearClient | None = None,
    claims: claim_store.ClaimStore | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "deploy-sync":
        return deploy_sync(args)
    if args.command == "reconcile":
        return reconcile(args, client=client)
    if args.command == "request":
        return run_request(client=client)
    if client is None and not linear_delivery.has_credentials():
        return keyless(args)
    try:
        client = client or linear_delivery.LinearClient.from_env()
        if args.command == "start" and claims is None:
            claims = claim_store.GitHubClaims.from_env()
        result = _transition(args, client, claims)
    except (
        linear_delivery.LinearError,
        claim_store.ClaimStoreError,
        ValueError,
    ) as exc:
        print(f"afx {args.command}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return _report(args.command, result)


if __name__ == "__main__":
    sys.exit(main())
