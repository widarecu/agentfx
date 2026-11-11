#!/usr/bin/env python3
"""Flag Settings-page PRs that ship without a Vitest change (AFX-55).

The bar (CLAUDE.md -> "UI testing bar: state-dependent portal UI"): any PR
that touches a Settings component must add or change a Vitest that pins the
pure selector driving its rendering, covering every branch a user can land
on. This check is the CI enforcement: if the diff touches a SETTINGS_SURFACE
file but no `portal/web/tests/unit/**/*.test.ts`, it reports the miss.

Advisory by default (GitHub `::warning::` annotation, exit 0). Pass
`--strict` to exit 1 instead; flip CI to strict once the pattern has held
for a month.

Stdlib only, so it runs without the project venv:

    python3 scripts/check_settings_vitest.py                    # vs origin/feat/agent-fleet
    python3 scripts/check_settings_vitest.py --range HEAD^1..HEAD --strict
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PAGE = "portal/web/src/pages/Settings.tsx"

# The Settings page plus every component / selector it renders through.
# tests/unit/scripts/test_check_settings_vitest.py fails if Settings.tsx
# imports a local component that is missing from this list.
SETTINGS_SURFACE = (
    SETTINGS_PAGE,
    "portal/web/src/pages/settingsView.ts",
    "portal/web/src/components/CredentialsForm.tsx",
    "portal/web/src/components/OandaAccountsTable.tsx",
)

VITEST_GLOB = "portal/web/tests/unit/*.test.ts"
VITEST_NESTED_GLOB = "portal/web/tests/unit/**/*.test.ts"

DEFAULT_RANGE = "origin/feat/agent-fleet...HEAD"


def is_vitest(path: str) -> bool:
    return fnmatch(path, VITEST_GLOB) or fnmatch(path, VITEST_NESTED_GLOB)


def find_problem(changed: list[str]) -> str | None:
    """A message when `changed` touches Settings UI with no Vitest change."""
    touched = sorted(p for p in set(changed) if p in SETTINGS_SURFACE)
    if not touched or any(is_vitest(p) for p in changed):
        return None
    return (
        "Settings UI changed with no new/changed Vitest "
        f"({', '.join(touched)}). Extract the branching into a pure "
        "selector and pin every rendered state in portal/web/tests/unit/ "
        "(CLAUDE.md -> UI testing bar, AFX-55)."
    )


def changed_files(diff_range: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", diff_range],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--range",
        default=DEFAULT_RANGE,
        help=f"git diff range to inspect (default: {DEFAULT_RANGE})",
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 instead of warning"
    )
    args = parser.parse_args(argv)

    problem = find_problem(changed_files(args.range))
    if problem is None:
        print("Settings UI testing bar OK.")
        return 0
    if args.strict:
        print(f"::error ::{problem}")
        return 1
    print(f"::warning ::{problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
