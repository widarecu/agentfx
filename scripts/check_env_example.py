#!/usr/bin/env python3
"""Fail when `.env.example` is missing a setting declared in config.py (AFX-80).

`.env.example` is the tracked template of record for env vars: the real
`.env.*` files are gitignored, so it is the only place a new operator (or a
future PR author) sees the whole config contract, and `make dotenv` copies it
verbatim into `.env.development`. When a PR adds a setting to
`src/agentfx/api/core/config.py` without mirroring it here, nothing fails
until someone deploys without a setting they never knew existed. This check
makes that drift fail in the PR that introduces it.

Contract, for every `Field(..., env="NAME")` in config.py:

* `.env.example` has an entry for NAME: an active `NAME=...` line, or a
  commented `# NAME=...` line for a setting that is documented but
  deliberately left unset (deprecated, not honoured, ...).
* Exception: SURFACE_GOVERNED settings (the ingestion knobs) have one source
  of truth per execution surface (CLAUDE.md -> "Ingestion toggle:
  three-surface model"). `.env.example` must mention them, to say where they
  live, but must NOT assign them: `make dotenv` would copy the assignment
  into `.env.development` and re-create the shadowing that model prevents.

Entries for variables that are not config.py fields (ANTHROPIC_API_KEY, the
nightly-cron vars, ...) are fine; they are read elsewhere.

Stdlib only, so it runs without the project venv:

    python3 scripts/check_env_example.py      # or: make check-env-example

Exits 0 when in sync, 1 on drift (one line per problem). The unit suite runs
the same check (tests/unit/scripts/test_check_env_example.py), which is what
gates CI.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO_ROOT / "src" / "agentfx" / "api" / "core" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Owned by pytest / docker-compose / Railway, one source per surface. Keep in
# sync with CLAUDE.md and the "Candle Ingestion Worker" block of .env.example.
SURFACE_GOVERNED = frozenset(
    {
        "INGESTION_ENABLED",
        "BACKFILL_DAYS",
        "CANDLE_INSTRUMENTS",
        "CANDLE_GRANULARITIES",
    }
)

_ACTIVE = re.compile(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=")
_COMMENTED = re.compile(r"^#\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=")


def declared_env_names(config_source: str) -> set[str]:
    """Every string passed as an `env=` keyword (i.e. `Field(env=...)`)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(config_source)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "env"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    names.add(kw.value.value)
    return names


def documented_env_names(env_example: str) -> tuple[set[str], set[str]]:
    """Return `(active, commented)`: names assigned in the template."""
    active: set[str] = set()
    commented: set[str] = set()
    for line in env_example.splitlines():
        line = line.strip()
        if match := _ACTIVE.match(line):
            active.add(match.group(1))
        elif match := _COMMENTED.match(line):
            commented.add(match.group(1))
    return active, commented


def find_drift(
    config_source: str,
    env_example: str,
    surface_governed: frozenset[str] = SURFACE_GOVERNED,
) -> list[str]:
    """Every way `env_example` breaks the contract; empty means in sync."""
    declared = declared_env_names(config_source)
    active, commented = documented_env_names(env_example)
    problems = [
        f"{name}: declared in config.py but has no `{name}=` entry"
        for name in sorted(declared - surface_governed - active - commented)
    ]
    for name in sorted(declared & surface_governed):
        if name in active:
            problems.append(
                f"{name}: surface-governed (three-surface model), so it must "
                f"not be assigned; remove the active `{name}=` line"
            )
        elif not re.search(rf"\b{name}\b", env_example):
            problems.append(f"{name}: surface-governed but never mentioned")
    for name in sorted(surface_governed - declared):
        problems.append(
            f"{name}: in SURFACE_GOVERNED but no longer declared in config.py; "
            "drop it from scripts/check_env_example.py"
        )
    return problems


def main() -> int:
    config_source = CONFIG_PY.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    problems = find_drift(config_source, env_example, SURFACE_GOVERNED)
    if problems:
        print(
            ".env.example is out of sync with src/agentfx/api/core/config.py:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Mirror new settings into .env.example in the same PR (AFX-80).",
            file=sys.stderr,
        )
        return 1
    count = len(declared_env_names(config_source))
    print(f".env.example OK: all {count} config.py env settings are documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
