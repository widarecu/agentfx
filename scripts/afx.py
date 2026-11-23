#!/usr/bin/env python3
"""Agent-facing Linear commands (AFX-118, AFX-122).

    python scripts/afx.py start AFX-123
    python scripts/afx.py block AFX-123 "Which pair should this use?"
    python scripts/afx.py deploy-sync record --sha <attempted>
    python scripts/afx.py deploy-sync run --sha <attempted> \
        --backend success --frontend skipped --verify success --run-url <url>
    python scripts/afx.py deploy-sync recover
    python scripts/afx.py reconcile --previous previous/digest.json \
        --state-out digest.json

`start` claims a Ready ticket (Ready -> In Progress) and refuses any
other state, so a duplicate start never launches duplicate work.
`block` moves an active ticket to Blocked, adds `needs-wilson` and
posts the question once. Reads LINEAR_API_KEY from the environment.

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
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
    block = sub.add_parser("block", help="Block an active ticket on Wilson")
    block.add_argument("issue", help="Ticket identifier, e.g. AFX-123")
    block.add_argument("question", help="The question Wilson needs to answer")
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
        "--wilson-email",
        default=os.environ.get("AFX_WILSON_LINEAR_EMAIL") or dr.DEFAULT_WILSON_EMAIL,
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
        wilson_email=args.wilson_email,
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
        if linear is None and os.environ.get("LINEAR_API_KEY", "").strip():
            linear = linear_delivery.DeployIssues(
                linear_delivery.LinearClient.from_env()
            )
        if apply and linear is None:
            print("::error ::LINEAR_API_KEY is not set; cannot apply ticket sync")
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


def main(
    argv: list[str] | None = None,
    *,
    client: linear_delivery.LinearClient | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "deploy-sync":
        return deploy_sync(args)
    if args.command == "reconcile":
        return reconcile(args, client=client)
    try:
        client = client or linear_delivery.LinearClient.from_env()
        if args.command == "start":
            result = linear_delivery.start(client, args.issue)
        else:
            result = linear_delivery.block(client, args.issue, args.question)
    except (linear_delivery.LinearError, ValueError) as exc:
        print(f"afx {args.command}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not result.ok:
        print(f"afx {args.command}: refused: {result.reason}", file=sys.stderr)
        return EXIT_REFUSED
    if result.changed:
        print(f"{result.identifier}: {result.from_state} -> {result.to_state}")
    else:
        print(f"{result.identifier}: already {result.to_state}; nothing to do")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
