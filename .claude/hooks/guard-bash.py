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
LONG_LIVED_POSTGRES = {"agentfx-postgres", "agentfx-postgres-uat"}
AGENT_BRANCH = re.compile(r"agent/AFX-[0-9]+-[A-Za-z0-9][A-Za-z0-9._/-]*")
DESTRUCTIVE_SQL = re.compile(r"\b(?:DROP|TRUNCATE)\b", re.IGNORECASE)


def split_segments(command: str) -> list[list[str]]:
    """Split a shell command line into simple commands, each tokenised."""
    separators = ";&|\n()"
    lexer = shlex.shlex(command, posix=True, punctuation_chars=separators)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    segments, current = [], []
    for token in lexer:
        if token and all(c in separators for c in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
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
    if (cmd == "docker" or is_compose) and "exec" in args:
        exec_args = args[args.index("exec") + 1 :]
        targets = LONG_LIVED_POSTGRES | (
            {"postgres", "postgres-uat"} if is_compose else set()
        )
        for i, arg in enumerate(exec_args):
            if os.path.basename(arg) != "psql":
                continue
            if targets.intersection(exec_args[:i]) and any(
                DESTRUCTIVE_SQL.search(sql) for sql in exec_args[i + 1 :]
            ):
                return "Refusing DROP or TRUNCATE on the dev or uat database."
            break
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


def pr_create_refs(args: list[str]) -> tuple[list[str], list[str]]:
    refs: dict[str, list[str]] = {"base": [], "head": []}
    options_with_values = {
        "assignee",
        "base",
        "body",
        "body-file",
        "head",
        "label",
        "milestone",
        "project",
        "repo",
        "reviewer",
        "template",
        "title",
    }
    short_names = {
        "a": "assignee",
        "B": "base",
        "b": "body",
        "F": "body-file",
        "H": "head",
        "l": "label",
        "m": "milestone",
        "p": "project",
        "R": "repo",
        "r": "reviewer",
        "T": "template",
        "t": "title",
    }
    remaining = iter(args)
    for arg in remaining:
        if arg == "--":
            break
        if arg.startswith("--"):
            name, _, value = arg[2:].partition("=")
            if "=" not in arg and name in options_with_values:
                value = next(remaining, "")
        elif arg.startswith("-") and arg[1:2] in short_names:
            name = short_names[arg[1]]
            value = arg[2:].removeprefix("=") if len(arg) > 2 else next(remaining, "")
        else:
            continue
        if name in refs:
            refs[name].append(value)
    return refs["base"] or ["main"], refs["head"]


def skip_gh_repo_flag(args: list[str]) -> list[str]:
    if args[:1] in (["-R"], ["--repo"]):
        return args[2:]
    if args and args[0].startswith(("-R", "--repo=")):
        return args[1:]
    return args


def check_gh_pr(words: list[str]) -> str | None:
    if os.path.basename(words[0]) != "gh":
        return None
    args = skip_gh_repo_flag(words[1:])
    if args[:1] != ["pr"]:
        return None
    args = skip_gh_repo_flag(args[1:])
    if args[:1] == ["merge"]:
        return (
            "Do not merge PRs manually; pr-policy merges approved agent PRs into main."
        )
    if args[:1] == ["create"]:
        bases, heads = pr_create_refs(args[1:])
        if any(base in {"main", "refs/heads/main"} for base in bases) and not (
            len(heads) == 1 and AGENT_BRANCH.fullmatch(heads[0])
        ):
            return (
                "To open a PR into main, specify the ticket branch explicitly: "
                "`gh pr create --base main --head agent/AFX-<n>-<slug>`."
            )
    return None


def invokes(words: list[str], program: str) -> bool:
    cmd = os.path.basename(words[0])
    if cmd == "uv" and words[1:2] == ["run"]:
        return any(
            os.path.basename(word) == program
            or (
                os.path.basename(word).startswith("python")
                and words[i + 1 : i + 3] == ["-m", program]
            )
            for i, word in enumerate(words[2:], start=2)
        )
    return cmd == program or (
        cmd.startswith("python") and words[1:3] == ["-m", program]
    )


def check_test_runner(env: dict[str, str], words: list[str]) -> str | None:
    cmd = os.path.basename(words[0])
    is_pytest = invokes(words, "pytest")
    if is_pytest and (cmd == "uv" or env.get("ENVIRONMENT") != "test"):
        return (
            "Bare pytest loads the wrong config and can hit the wrong database. "
            "Use `make test-unit`, `make test-integration`, "
            "`make test-file FILE=...` or `make test-pattern PATTERN=...`."
        )
    if (cmd == "npx" and words[1:2] == ["vitest"]) or (
        cmd == "npm"
        and (
            words[1:2] in (["test"], ["t"], ["tst"])
            or words[1:3] in (["run", "test"], ["run-script", "test"])
        )
    ):
        return (
            "Running vitest directly skips the parity-fixture staleness check. "
            "Use `make test-unit`."
        )
    return None


def check_alembic(env: dict[str, str], words: list[str]) -> str | None:
    if (
        invokes(words, "alembic")
        and "downgrade" in words[1:]
        and env.get("ENVIRONMENT", os.environ.get("ENVIRONMENT", "")).lower() != "test"
    ):
        return "Alembic downgrade may only run with ENVIRONMENT=test."
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

    try:
        segments = split_segments(command)
    except ValueError:
        return 0
    for tokens in segments:
        env, words = split_env(tokens)
        if words and os.path.basename(words[0]) == "env":
            assignments, words = split_env(words[1:])
            env.update(assignments)
        if not words:
            continue
        reason = (
            check_e2e(env, words)
            or check_docker(words)
            or check_git_push(words)
            or check_gh_pr(words)
            or check_test_runner(env, words)
            or check_alembic(env, words)
        )
        if reason:
            print(f"Blocked: {reason}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
