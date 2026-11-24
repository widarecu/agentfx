#!/usr/bin/env python3
"""PreToolUse guard for Bash commands in Claude Code sessions.

Refuses the handful of commands that have caused (or would cause) real
damage on this project. Exit code 2 blocks the command and shows the
message on stderr to Claude; exit 0 lets it through.

Rules live here rather than in prose because prose can be skipped.
Background: planning/runbooks/environments.md and
planning/runbooks/delivery-pipeline.md.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED_PUSH = {"main"}
PROTECTED_FORCE_PUSH = {"main", "feat/agent-fleet"}
# Dev volume is `postgres_data`, UAT is `postgres_uat_data`; the test
# volume `postgres_test_data` deliberately does not match.
LONG_LIVED_VOLUME = re.compile(r"postgres_(uat_)?data\b")


def split_segments(command: str) -> list[list[str]]:
    """Split a shell command line into simple commands, each tokenised."""
    segments = []
    for raw in re.split(r"&&|\|\||[;|\n]", command):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            continue
        if tokens:
            segments.append(tokens)
    return segments


def split_env(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Separate leading VAR=value assignments from the command words."""
    env: dict[str, str] = {}
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        name, _, value = tokens[i].partition("=")
        env[name] = value
        i += 1
    return env, tokens[i:]


def current_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.environ.get("CLAUDE_PROJECT_DIR") or None,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_e2e(env: dict[str, str], words: list[str]) -> str | None:
    cmd = os.path.basename(words[0])
    is_e2e = (cmd == "make" and "e2e" in words[1:]) or cmd == "run-e2e-tests.sh"
    if not is_e2e:
        return None
    profiles = [env.get("E2E_PROFILE"), env.get("PROFILE")]
    profiles += [
        w.partition("=")[2] for w in words[1:] if re.match(r"^(E2E_)?PROFILE=", w)
    ]
    bad = [p for p in profiles if p and p != "test"]
    if bad:
        return (
            f"E2E wipes its target database volume and may only run against "
            f"the `test` profile (got `{bad[0]}`). Run `make e2e` with no "
            f"profile override."
        )
    return None


def check_docker(words: list[str]) -> str | None:
    cmd = os.path.basename(words[0])
    args = words[1:]
    is_compose = cmd == "docker-compose" or (cmd == "docker" and "compose" in args)
    if is_compose and "down" in args and any(a in ("-v", "--volumes") for a in args):
        profiles = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--profile"]
        profiles += [a.partition("=")[2] for a in args if a.startswith("--profile=")]
        if profiles != ["test"]:
            return (
                "`docker compose down -v` deletes database volumes. It is only "
                "allowed as `docker compose --profile test down -v`. The dev "
                "and uat volumes hold long-lived data."
            )
    if cmd == "docker" and args[:1] == ["volume"]:
        if "prune" in args:
            return (
                "`docker volume prune` would delete the dev and uat database volumes."
            )
        if "rm" in args and any(LONG_LIVED_VOLUME.search(a) for a in args):
            return "Refusing to remove the dev or uat database volume."
    return None


def check_git_push(words: list[str]) -> str | None:
    if os.path.basename(words[0]) != "git" or "push" not in words[1:]:
        return None
    args = words[words.index("push") + 1 :]
    force = any(
        a in ("-f", "--force") or a.startswith("--force-with-lease") for a in args
    )
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # positional[0] is the remote
    if refspecs:
        targets = {r.rpartition(":")[2].removeprefix("refs/heads/") for r in refspecs}
    else:
        targets = {current_branch()}
    if targets & PROTECTED_PUSH:
        return (
            "Never push to `main`. Push your `agent/AFX-<n>-<slug>` branch "
            "and open a PR into `main`."
        )
    if force and targets & PROTECTED_FORCE_PUSH:
        return (
            "Never force-push `feat/agent-fleet`; it is the shared integration branch."
        )
    return None


def check_test_runner(
    env: dict[str, str], words: list[str], command: str
) -> str | None:
    cmd = os.path.basename(words[0])
    is_pytest = cmd == "pytest" or (
        cmd.startswith("python") and words[1:3] == ["-m", "pytest"]
    )
    if is_pytest and env.get("ENVIRONMENT") != "test":
        # Inside the test container the environment is already set.
        if "ENVIRONMENT=test" in command or "backend-test" in command:
            return None
        return (
            "Bare pytest loads the wrong config and can hit the wrong database. "
            "Use `make test-unit`, `make test-integration`, "
            "`make test-file FILE=...` or `make test-pattern PATTERN=...`."
        )
    if cmd == "npx" and words[1:2] == ["vitest"]:
        return (
            "Running vitest directly skips the parity-fixture staleness check. "
            "Use `make test-unit`."
        )
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0

    # Catches `export E2E_PROFILE=uat` and similar forms the per-command
    # checks below would not see.
    exported = re.search(r"\bE2E_PROFILE=[\"']?(?!test\b)\w+", command)
    if exported:
        print("Blocked: E2E may only run against the `test` profile.", file=sys.stderr)
        return 2

    for tokens in split_segments(command):
        env, words = split_env(tokens)
        if not words:
            continue
        reason = (
            check_e2e(env, words)
            or check_docker(words)
            or check_git_push(words)
            or check_test_runner(env, words, command)
        )
        if reason:
            print(f"Blocked: {reason}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
