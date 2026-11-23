#!/usr/bin/env python3
"""Agent-facing Linear commands (AFX-118, AFX-122).

    python scripts/afx.py start AFX-123
    python scripts/afx.py block AFX-123 "Which pair should this use?"
    python scripts/afx.py deploy-sync record --sha <attempted>
    python scripts/afx.py deploy-sync run --sha <attempted> \
        --backend success --frontend skipped --verify success --run-url <url>
    python scripts/afx.py deploy-sync recover

`start` claims a Ready ticket (Ready -> In Progress) and refuses any
other state, so a duplicate start never launches duplicate work.
`block` moves an active ticket to Blocked, adds `needs-wilson` and
posts the question once. Reads LINEAR_API_KEY from the environment.

`deploy-sync` moves the tickets of the PRs merged since the last
successful `deployed/staging` SHA (see `agentfx.integrations.deploy_sync`).
`--mode` (default `$AFX_DEPLOY_SYNC_MODE`, else `dry-run`) must be `apply`
to write to GitHub or Linear. GitHub auth comes from `GH_TOKEN` via `gh`.

Exit codes: 0 done, 1 refused (wrong state) or deploy-sync error, 2 error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
    return parser


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
