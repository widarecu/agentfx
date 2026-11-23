#!/usr/bin/env python3
"""Deploy workflow helpers for paper staging.

`outcomes` turns the Deploy jobs' results (`${{ toJSON(needs) }}`) into the
explicit component outcomes `afx deploy-sync run` takes, written as
`backend=`, `frontend=`, `verify=` lines to `$GITHUB_OUTPUT` (or stdout):

* `backend`  <- job `deploy-backend`, `frontend` <- job `deploy-frontend`;
* `verify`   <- job `verify-staging` (AFX-121). Until that job exists the
  verify outcome is empty, which deploy-sync treats as "not final" and
  changes no ticket. A failed pre-deploy gate (e.g. `e2e`) is a verify
  failure, so a deploy that was skipped because a gate failed never counts
  as a success.
* `--docs-only true` reports every component as `skipped`.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

JOB_FOR = {
    "backend": "deploy-backend",
    "frontend": "deploy-frontend",
    "verify": "verify-staging",
}
SYNC_JOBS = {"deploy-sync-record"}


def outcomes(needs: dict, *, docs_only: bool = False) -> dict[str, str]:
    if docs_only:
        return dict.fromkeys(JOB_FOR, "skipped")
    result = {c: (needs.get(job) or {}).get("result", "") for c, job in JOB_FOR.items()}
    gates = set(needs) - set(JOB_FOR.values()) - SYNC_JOBS
    if any((needs[g] or {}).get("result") == "failure" for g in gates):
        result["verify"] = "failure"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    out = sub.add_parser("outcomes", help="Component outcomes from toJSON(needs)")
    out.add_argument("--needs-json", required=True)
    out.add_argument("--docs-only", choices=("true", "false"), default="false")
    args = parser.parse_args(argv)

    try:
        needs = json.loads(args.needs_json or "{}")
    except ValueError:
        print("::error ::--needs-json is not valid JSON", file=sys.stderr)
        return 2
    lines = [
        f"{k}={v}"
        for k, v in outcomes(needs, docs_only=args.docs_only == "true").items()
    ]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
