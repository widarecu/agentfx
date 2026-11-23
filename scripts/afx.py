#!/usr/bin/env python3
"""`afx`: one delivery tool for agents and CI (AFX-117).

    python3 scripts/afx.py deploy-sync record --sha <attempted>
    python3 scripts/afx.py deploy-sync run --sha <attempted> \
        --backend success --frontend skipped --verify success --run-url <url>
    python3 scripts/afx.py deploy-sync recover

`--mode` (default `$AFX_DEPLOY_SYNC_MODE`, else `dry-run`) is `apply` to
write to GitHub/Linear. Dry-run only reads. GitHub auth comes from `GH_TOKEN`
(via `gh`), Linear from `LINEAR_API_KEY`. Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentfx.integrations import deploy_sync as ds  # noqa: E402
from agentfx.integrations.linear_delivery import (  # noqa: E402
    LinearDelivery,
    http_transport,
)

OUTCOMES = ("success", "failure", "skipped", "cancelled", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afx", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    deploy = commands.add_parser(
        "deploy-sync", help="Sync Linear tickets with a staging deploy"
    )
    actions = deploy.add_subparsers(dest="action", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "widarecu/agentfx")
    )
    common.add_argument("--team-key", default=os.environ.get("LINEAR_TEAM_KEY", "AFX"))
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
    return parser


def _linear(args) -> LinearDelivery | None:
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        return None
    return LinearDelivery(http_transport(key), team_key=args.team_key)


def deploy_sync(args, *, gh=None, linear=None) -> int:
    gh = gh or ds.GhCli(args.repo)
    apply = args.mode == "apply"
    if args.action == "record":
        report = ds.record(gh, args.sha, apply=apply)
    else:
        linear = linear or _linear(args)
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
            "dry-run: no GitHub or Linear writes (set AFX_DEPLOY_SYNC_MODE=apply to enable)"
        )
    for line in report.lines:
        print(line)
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "deploy-sync":
        return deploy_sync(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
