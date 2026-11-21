#!/usr/bin/env python3
"""One-shot: move tickets marked `Done` that are actually still on
`feat/agent-fleet` (not promoted to main yet) back to `In Staging` (AFX-70).

Before the state machine was wired, Linear's GitHub integration moved any
ticket to `Done` on feat/agent-fleet merge. That left plenty of tickets in
the wrong state: shipped to the integration branch but not to production.
This script finds them and corrects the state in one pass.

Logic:
  1. Read commits on `feat/agent-fleet` that are not on `main`
     (`git log --first-parent origin/main..origin/feat/agent-fleet`).
  2. Extract `AFX-<n>` tags from each commit message.
  3. For each AFX-N whose ticket is currently `Done`, transition to
     `In Staging`. Any other current state is left alone.

Run with --dry-run first to preview; run without to apply.

    python3 scripts/backfill_in_staging.py --dry-run
    python3 scripts/backfill_in_staging.py

Stdlib only. Requires `LINEAR_API_KEY` in env unless --dry-run.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SIBLING = _SCRIPT_DIR / "fleet_state_on_promote.py"

_spec = importlib.util.spec_from_file_location("fleet_state_on_promote", _SIBLING)
assert _spec is not None and _spec.loader is not None
fsop = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fsop
_spec.loader.exec_module(fsop)


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def unpromoted_commit_messages() -> list[str]:
    """First-parent commit messages on `feat/agent-fleet` but not on `main`."""
    out = _run(
        "git",
        "log",
        "--first-parent",
        "--format=%B%x00",  # NUL-separated so multi-line messages stay intact
        "origin/main..origin/feat/agent-fleet",
    ).stdout
    return [m.strip() for m in out.split("\x00") if m.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args(argv)

    messages = unpromoted_commit_messages()
    if not messages:
        print("No commits on feat/agent-fleet ahead of main.")
        return 0

    idents: list[str] = []
    for msg in messages:
        for ident in fsop.afx_ids(msg):
            if ident not in idents:
                idents.append(ident)
    if not idents:
        print("No AFX-IDs found in unpromoted commits.")
        return 0
    print(f"Candidates to backfill: {', '.join(idents)}")

    if args.dry_run:
        print("--dry-run: not calling Linear. Re-run without --dry-run to apply.")
        return 0

    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        print("::error ::LINEAR_API_KEY not set; cannot apply backfill.")
        return 1

    linear = fsop.Linear(api_key)
    moved = 0
    skipped = 0
    for ident in idents:
        # Only move tickets currently in Done. Anything else is already
        # in a sensible state (In Staging, In Progress, Ready, etc.).
        old = linear.transition(ident, "In Staging", allowed_from={"Done"})
        if old is None:
            skipped += 1
        else:
            moved += 1
    print(f"Backfilled: {moved} moved to In Staging, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
