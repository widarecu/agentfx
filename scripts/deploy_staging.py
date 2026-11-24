#!/usr/bin/env python3
"""Plan, guard, verify and record staging deploys (AFX-121).

A green upload is not proof that staging runs a commit. The Deploy workflow
uses this script so that only a verified deploy moves the `deployed/staging`
tag, the record of the last commit fully running on staging:

* `plan`    — compare the target SHA with `deployed/staging` and decide which
  components (backend on Railway, frontend on Vercel) need deploying.
  Docs-only (or tests-only) changes need nothing and are an explicit success.
* `guard`   — refuse to deploy a commit older than what staging already runs,
  so a stale "Re-run failed jobs" or manual retry cannot regress staging.
* `verify`  — backend `/health` must report the target SHA when the backend
  was deployed, or a SHA with the same backend source when it was not; the
  frontend URL must serve.
* `record`  — after a verified deploy, keep the previous SHA in
  `deployed/staging-previous`, then fast-forward `deployed/staging`.
* `summary` — always runs: one backend / frontend / verify / tag outcome,
  as job outputs, `deploy-result.json` and the run summary page.
* `ticket-outcomes` — map the summary outcomes to the component outcomes
  `afx deploy-sync run` takes (AFX-122).

Stdlib only, plus `git` and `gh` (`gh api`, authenticated by `GH_TOKEN`):

    python3 scripts/deploy_staging.py plan --sha <sha>
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

TAG = "deployed/staging"
TAG_REF = f"refs/tags/{TAG}"
PREVIOUS_TAG = "deployed/staging-previous"
PREVIOUS_TAG_REF = f"refs/tags/{PREVIOUS_TAG}"

DEFAULT_BACKEND_URL = "https://agentfx-staging.up.railway.app"
DEFAULT_FRONTEND_URL = "https://agentfx-liart.vercel.app"
DEFAULT_REPO = "widarecu/agentfx"

BACKEND = "backend"
FRONTEND = "frontend"
COMPONENTS = (BACKEND, FRONTEND)

# Paths that never change what runs on staging. Checked first, so a README
# inside src/ or portal/web/ is still docs.
DOCS_PATTERNS = (
    "planning/*",
    "docs/*",
    "*.md",
    "LICENSE",
    ".gitignore",
    "portal/web/wireframes/*",
)
# The deploy machinery itself: a change here redeploys both components so the
# new pipeline is proven end to end.
SHARED_PATHS = (
    ".github/workflows/deploy.yml",
    "scripts/deploy_staging.py",
    "scripts/railway_deploy.py",
)
# Everything the Railway image is built from (Dockerfile COPY lines and the
# nightly trigger script) and the service settings in .railway/.
BACKEND_PATHS = (
    "Dockerfile",
    ".dockerignore",
    ".railway/",
    "requirements.txt",
    "requirements-api.txt",
    "alembic.ini",
    "src/",
    "migrations/",
    "config/",
    "scripts/seed_instruments.py",
    "scripts/trigger_nightly_evolution.py",
)
FRONTEND_PATHS = ("portal/web/",)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]
Fetch = Callable[[str, float], tuple[int, str]]


class DeployError(Exception):
    """A step could not run: bad input, git failure or GitHub API failure."""


def run_cli(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def http_get(url: str, timeout: float) -> tuple[int, str]:
    """Return (status, body). Network errors are status 0."""
    request = urllib.request.Request(url, headers={"User-Agent": "agentfx-deploy"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


# ─────────────────────────────────────────────────────────────────────
# Change classification
# ─────────────────────────────────────────────────────────────────────


def _matches(path: str, entries: Sequence[str]) -> bool:
    return any(
        path.startswith(entry) if entry.endswith("/") else path == entry
        for entry in entries
    )


def is_docs(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in DOCS_PATTERNS
    )


def components_for(path: str) -> set[str]:
    """The components whose deployed artefact a change to `path` affects."""
    if is_docs(path):
        return set()
    if _matches(path, SHARED_PATHS):
        return {BACKEND, FRONTEND}
    if _matches(path, BACKEND_PATHS):
        return {BACKEND}
    if _matches(path, FRONTEND_PATHS):
        return {FRONTEND}
    return set()


def components_changed(paths: Sequence[str]) -> set[str]:
    changed: set[str] = set()
    for path in paths:
        changed |= components_for(path)
    return changed


def component_paths(component: str) -> tuple[str, ...]:
    own = BACKEND_PATHS if component == BACKEND else FRONTEND_PATHS
    return tuple(p.rstrip("/") for p in own + SHARED_PATHS)


# ─────────────────────────────────────────────────────────────────────
# Git
# ─────────────────────────────────────────────────────────────────────


class Git:
    def __init__(self, cwd: str | None = None, runner: Runner = run_cli) -> None:
        self.cwd = cwd
        self.runner = runner

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        prefix = ["git", "-C", self.cwd] if self.cwd else ["git"]
        return self.runner([*prefix, *args])

    def _out(self, *args: str) -> str:
        result = self._run(*args)
        if result.returncode != 0:
            raise DeployError(
                f"`git {' '.join(args)}` exited {result.returncode}: "
                f"{(result.stderr or result.stdout or '').strip()}"
            )
        return result.stdout

    def resolve(self, ref: str) -> str | None:
        result = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        sha = (result.stdout or "").strip()
        return sha if result.returncode == 0 and _SHA_RE.match(sha) else None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True when `ancestor` is reachable from `descendant` (or equal)."""
        return (
            self._run("merge-base", "--is-ancestor", ancestor, descendant).returncode
            == 0
        )

    def shares_history(self, a: str, b: str) -> bool:
        return self._run("merge-base", a, b).returncode == 0

    def changed_files(self, base: str, head: str) -> list[str]:
        # --no-renames: a move lists both paths, so moving a file out of a
        # deployed directory still counts as a change to that component.
        out = self._out("diff", "--no-renames", "--name-only", base, head)
        return [line for line in out.splitlines() if line.strip()]

    def touched_files(self, base: str, head: str) -> list[str]:
        """Files any commit in `base..head` changed, even if later reverted."""
        out = self._out(
            "log", "--no-renames", "--name-only", "--format=", f"{base}..{head}"
        )
        return [line for line in out.splitlines() if line.strip()]

    def last_commit(self, ref: str, paths: Sequence[str]) -> str | None:
        out = self._out("log", "-1", "--format=%H", ref, "--", *paths).strip()
        return out or None

    def remote_ref(self, ref: str, remote: str = "origin") -> str | None:
        out = self._out("ls-remote", remote, ref)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
        return None


def relation(git: Git, target: str, tag_sha: str | None) -> str:
    """How `target` relates to the last verified deploy.

    `first` (no tag), `same`, `newer` (tag is an ancestor), `stale` (target is
    an ancestor of the tag: deploying it would regress staging), `diverged`
    (neither contains the other, so deploying would drop commits staging
    runs) or `unrelated` (no shared history: main was rewritten).
    """
    if not tag_sha:
        return "first"
    if tag_sha == target:
        return "same"
    if git.is_ancestor(tag_sha, target):
        return "newer"
    if git.is_ancestor(target, tag_sha):
        return "stale"
    if git.shares_history(tag_sha, target):
        return "diverged"
    return "unrelated"


def diverged_problem(target: str, tag_sha: str | None) -> str:
    return (
        f"{target} does not contain {TAG} ({tag_sha}); deploying it would drop "
        "commits staging already runs. Deploy a commit on main that contains both"
    )


# ─────────────────────────────────────────────────────────────────────
# plan
# ─────────────────────────────────────────────────────────────────────


@dataclass
class Plan:
    sha: str
    base_sha: str
    relation: str
    kind: str
    backend: bool
    frontend: bool
    files: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.relation == "stale"

    def outputs(self) -> dict[str, str]:
        return {
            "sha": self.sha,
            "base_sha": self.base_sha,
            "relation": self.relation,
            "kind": self.kind,
            "backend": _bool(self.backend),
            "frontend": _bool(self.frontend),
            "stale": _bool(self.stale),
        }


def resolve_target(git: Git, ref: str, main_ref: str) -> str:
    sha = git.resolve(ref) if ref else None
    if not sha:
        raise DeployError(f"`{ref}` is not a commit in this repository")
    if not git.is_ancestor(sha, main_ref):
        raise DeployError(
            f"{sha} is not on {main_ref}; only commits on main deploy to staging"
        )
    return sha


def make_plan(git: Git, target: str, tag_sha: str | None) -> Plan:
    rel = relation(git, target, tag_sha)
    base = tag_sha or ""
    if rel == "diverged":
        raise DeployError(diverged_problem(target, tag_sha))
    if rel in ("first", "unrelated"):
        return Plan(target, base, rel, rel, True, True)
    if rel in ("same", "stale"):
        kind = "already-deployed" if rel == "same" else "stale"
        return Plan(target, base, rel, kind, False, False)

    # Union with every commit's files: after a partial deploy, a later revert
    # leaves no net diff but the live component may still run the reverted code.
    files = sorted(
        set(git.changed_files(tag_sha, target))  # type: ignore[arg-type]
        | set(git.touched_files(tag_sha, target))  # type: ignore[arg-type]
    )
    changed = components_changed(files)
    if changed == {BACKEND, FRONTEND}:
        kind = "both"
    elif changed:
        kind = next(iter(changed))
    elif files and all(is_docs(f) for f in files):
        kind = "docs-only"
    else:
        kind = "no-runtime-change"
    return Plan(target, base, rel, kind, BACKEND in changed, FRONTEND in changed, files)


# ─────────────────────────────────────────────────────────────────────
# Backend source SHA
# ─────────────────────────────────────────────────────────────────────


def read_backend_sha(
    fetch: Fetch, backend_url: str, timeout: float = 15
) -> tuple[str | None, str]:
    """Return (source_sha, problem) from the backend `/health` endpoint."""
    status, body = fetch(f"{backend_url.rstrip('/')}/health", timeout)
    if status != 200:
        return None, f"/health returned HTTP {status or 'error'} {body[:200]}".strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, "/health did not return JSON"
    sha = str((payload or {}).get("source_sha") or "").strip().lower()
    if not _SHA_RE.match(sha):
        return None, "/health does not report a source_sha"
    return sha, ""


def same_component_source(git: Git, component: str, live: str, target: str) -> bool:
    """True when `live` runs the same component source as `target`."""
    if live == target:
        return True
    if not git.resolve(live) or not git.is_ancestor(live, target):
        return False
    return component not in components_changed(git.changed_files(live, target))


# ─────────────────────────────────────────────────────────────────────
# guard
# ─────────────────────────────────────────────────────────────────────


def guard(
    git: Git,
    target: str,
    component: str,
    tag_sha: str | None,
    *,
    fetch: Fetch = http_get,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> str:
    """Return "" when deploying `target` is safe, else why it is not."""
    rel = relation(git, target, tag_sha)
    if rel == "diverged":
        return diverged_problem(target, tag_sha)
    if rel == "stale":
        return (
            f"{TAG} is already at {tag_sha}, which contains {target}; "
            "deploying the older commit would regress staging"
        )
    if component == BACKEND:
        live, _ = read_backend_sha(fetch, backend_url)
        if (
            live
            and live != target
            and git.resolve(live)
            and git.is_ancestor(target, live)
        ):
            return (
                f"the staging backend already runs {live}, which contains {target}; "
                "deploying the older commit would regress it"
            )
    return ""


# ─────────────────────────────────────────────────────────────────────
# verify
# ─────────────────────────────────────────────────────────────────────


@dataclass
class Check:
    component: str
    ok: bool
    expected: str
    detail: str
    live_sha: str = ""


def verify_backend(
    git: Git,
    target: str,
    deployed: bool,
    *,
    fetch: Fetch,
    backend_url: str,
    timeout: float,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Check:
    if deployed:
        expected = target
    else:
        expected = git.last_commit(target, component_paths(BACKEND)) or target
    deadline = clock() + timeout
    while True:
        live, problem = read_backend_sha(fetch, backend_url)
        if live and (
            live == target
            if deployed
            else same_component_source(git, BACKEND, live, target)
        ):
            how = "deployed" if deployed else f"unchanged since {expected[:12]}"
            return Check(
                BACKEND, True, expected, f"/health reports {live[:12]} ({how})", live
            )
        if live:
            problem = (
                f"/health reports {live[:12]}, expected {target[:12]}"
                if deployed
                else f"/health reports {live[:12]}, whose backend source differs from {target[:12]}"
            )
        if clock() >= deadline:
            return Check(BACKEND, False, expected, problem, live or "")
        sleep(interval)


def verify_frontend(
    git: Git,
    target: str,
    deployed: bool,
    *,
    fetch: Fetch,
    frontend_url: str,
    timeout: float,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Check:
    if deployed:
        expected = target
    else:
        expected = git.last_commit(target, component_paths(FRONTEND)) or target
    how = "deployed" if deployed else f"unchanged since {expected[:12]}"
    deadline = clock() + timeout
    while True:
        status, _ = fetch(frontend_url, 15)
        if status == 200:
            return Check(FRONTEND, True, expected, f"{frontend_url} serves ({how})")
        if clock() >= deadline:
            return Check(
                FRONTEND,
                False,
                expected,
                f"{frontend_url} returned HTTP {status or 'error'}",
            )
        sleep(interval)


# ─────────────────────────────────────────────────────────────────────
# record
# ─────────────────────────────────────────────────────────────────────


@dataclass
class Record:
    recorded: bool
    previous_sha: str
    detail: str


def gh_api(
    runner: Runner, method: str, path: str, fields: dict[str, str]
) -> subprocess.CompletedProcess:
    cmd = ["gh", "api", "-X", method, path]
    for key, value in fields.items():
        flag = "-F" if value in ("true", "false") else "-f"
        cmd += [flag, f"{key}={value}"]
    return runner(cmd)


def _write_ref(
    runner: Runner, repo: str, ref: str, sha: str, *, exists: bool, force: bool
) -> None:
    if exists:
        result = gh_api(
            runner,
            "PATCH",
            f"repos/{repo}/git/{ref}",
            {"sha": sha, "force": _bool(force)},
        )
    else:
        result = gh_api(
            runner, "POST", f"repos/{repo}/git/refs", {"ref": ref, "sha": sha}
        )
    if result.returncode != 0:
        raise DeployError(
            f"could not point {ref} at {sha}: {(result.stderr or result.stdout).strip()}"
        )


def record(
    git: Git,
    target: str,
    tag_sha: str | None,
    previous_tag_sha: str | None,
    *,
    repo: str,
    runner: Runner = run_cli,
) -> Record:
    """Move `deployed/staging` to `target`, never backwards."""
    rel = relation(git, target, tag_sha)
    if rel == "same":
        return Record(False, previous_tag_sha or "", f"{TAG} is already at {target}")
    if rel == "stale":
        return Record(
            False,
            tag_sha or "",
            f"{TAG} is at {tag_sha}, which already contains {target}; left unchanged",
        )
    if rel == "diverged":
        raise DeployError(diverged_problem(target, tag_sha))
    if tag_sha:
        # Kept first so ticket sync can still find the start of the range.
        _write_ref(
            runner,
            repo,
            PREVIOUS_TAG_REF,
            tag_sha,
            exists=bool(previous_tag_sha),
            force=True,
        )
    # force=false: GitHub rejects anything but a fast-forward, so a racing
    # older run cannot move the tag backwards even past the checks above.
    _write_ref(
        runner, repo, TAG_REF, target, exists=bool(tag_sha), force=rel == "unrelated"
    )
    return Record(
        True, tag_sha or "", f"{TAG} moved {(tag_sha or 'none')[:12]} -> {target[:12]}"
    )


# ─────────────────────────────────────────────────────────────────────
# summary
# ─────────────────────────────────────────────────────────────────────


def component_outcome(planned: bool, result: str) -> str:
    """Map a deploy job result to the component outcome."""
    if not planned:
        return "unchanged"
    return {
        "success": "deployed",
        "failure": "failed",
        "cancelled": "cancelled",
    }.get(result, "not-run")


def summarize(
    *,
    sha: str,
    plan_result: str,
    kind: str,
    backend_planned: bool,
    frontend_planned: bool,
    backend_result: str,
    frontend_result: str,
    verify_result: str,
    record_result: str,
    recorded: bool,
    base_sha: str,
    previous_sha: str,
    stale_now: bool,
    backend_detail: str = "",
    frontend_detail: str = "",
    run_url: str = "",
) -> dict:
    planned = plan_result == "success"
    backend = (
        component_outcome(backend_planned, backend_result) if planned else "not-run"
    )
    frontend = (
        component_outcome(frontend_planned, frontend_result) if planned else "not-run"
    )
    verify = {"success": "passed", "failure": "failed", "cancelled": "cancelled"}.get(
        verify_result, "not-run"
    )
    tag_moved = record_result == "success" and recorded
    if stale_now or kind == "stale":
        outcome = "stale"
    elif planned and verify == "passed" and record_result == "success":
        outcome = "success"
    else:
        outcome = "failure"
    return {
        "sha": sha,
        "kind": kind or "unknown",
        "outcome": outcome,
        "backend": backend,
        "frontend": frontend,
        "verify": verify,
        "backend_detail": backend_detail,
        "frontend_detail": frontend_detail,
        "tag": "moved" if tag_moved else "unchanged",
        # The last verified SHA before this run: the start of the ticket range.
        "previous_sha": previous_sha if tag_moved else base_sha,
        "deployed_sha": sha if outcome == "success" else base_sha,
        "run_url": run_url,
    }


_TICKET_OUTCOME = {
    "deployed": "success",
    "unchanged": "skipped",
    "passed": "success",
    "failed": "failure",
    "cancelled": "cancelled",
}


def ticket_outcomes(
    outcome: str, backend: str, frontend: str, verify: str
) -> dict[str, str]:
    """Summary outcomes as `afx deploy-sync run` outcomes. Anything not final
    (not-run, unknown, a stale retry) is empty, so no ticket changes."""
    if outcome not in ("success", "failure"):
        return dict.fromkeys((BACKEND, FRONTEND, "verify"), "")
    parts = {BACKEND: backend, FRONTEND: frontend, "verify": verify}
    return {name: _TICKET_OUTCOME.get(value, "") for name, value in parts.items()}


def summary_markdown(result: dict) -> str:
    sha = result["sha"][:12] or "unknown"
    lines = [
        f"## Staging deploy `{sha}`: {result['outcome']}",
        "",
        "| Part | Outcome | Detail |",
        "| --- | --- | --- |",
        f"| Backend (Railway) | {result['backend']} | {result['backend_detail']} |",
        f"| Frontend (Vercel) | {result['frontend']} | {result['frontend_detail']} |",
        f"| Verify | {result['verify']} | |",
        f"| `{TAG}` | {result['tag']} | now `{(result['deployed_sha'] or 'none')[:12]}` |",
        "",
        f"Change kind: `{result['kind']}`.",
    ]
    if result["outcome"] == "failure":
        lines += [
            "",
            f"Staging is not verified at `{sha}`; `{TAG}` was not moved. To recover, "
            "use **Re-run failed jobs** on this run, or **Run workflow** on Deploy "
            f"with `sha={result['sha']}`.",
        ]
    elif result["outcome"] == "stale":
        lines += [
            "",
            f"`{TAG}` already contains this commit; nothing was deployed and the tag "
            "was not moved.",
        ]
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def write_outputs(values: dict[str, str], path: str | None = None) -> None:
    path = path or os.environ.get("GITHUB_OUTPUT")
    lines = [
        f"{key}={str(value).replace(chr(10), ' ')}" for key, value in values.items()
    ]
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner = run_cli,
    fetch: Fetch = http_get,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--sha", required=True)
    p_plan.add_argument("--main-ref", default="origin/main")

    p_guard = sub.add_parser("guard")
    p_guard.add_argument("--sha", required=True)
    p_guard.add_argument("--component", choices=COMPONENTS, required=True)
    p_guard.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--sha", required=True)
    p_verify.add_argument("--backend-deployed", default="false")
    p_verify.add_argument("--frontend-deployed", default="false")
    p_verify.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_verify.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    p_verify.add_argument("--timeout", type=float, default=300)
    p_verify.add_argument("--interval", type=float, default=10)

    p_record = sub.add_parser("record")
    p_record.add_argument("--sha", required=True)
    p_record.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    )

    p_sum = sub.add_parser("summary")
    p_sum.add_argument("--sha", default="")
    p_sum.add_argument("--plan-result", default="")
    p_sum.add_argument("--kind", default="")
    p_sum.add_argument("--base-sha", default="")
    p_sum.add_argument("--backend-planned", default="false")
    p_sum.add_argument("--frontend-planned", default="false")
    p_sum.add_argument("--backend-result", default="")
    p_sum.add_argument("--frontend-result", default="")
    p_sum.add_argument("--verify-result", default="")
    p_sum.add_argument("--backend-detail", default="")
    p_sum.add_argument("--frontend-detail", default="")
    p_sum.add_argument("--record-result", default="")
    p_sum.add_argument("--recorded", default="false")
    p_sum.add_argument("--previous-sha", default="")
    p_sum.add_argument("--run-url", default="")
    p_sum.add_argument("--json-out", default="deploy-result.json")

    p_tick = sub.add_parser("ticket-outcomes")
    for name in ("outcome", BACKEND, FRONTEND, "verify"):
        p_tick.add_argument(f"--{name}", default="")

    args = parser.parse_args(argv)
    if args.command == "ticket-outcomes":
        result = ticket_outcomes(args.outcome, args.backend, args.frontend, args.verify)
        write_outputs(result)
        return 0
    git = Git(args.repo_dir, runner)

    try:
        if args.command == "plan":
            target = resolve_target(git, args.sha.strip(), args.main_ref)
            plan = make_plan(git, target, git.remote_ref(TAG_REF))
            print(
                f"Changed files since {plan.base_sha[:12] or 'no deploy'}: {len(plan.files)}"
            )
            for path in plan.files:
                print(f"  {path}")
            write_outputs(plan.outputs())
            return 0

        if args.command == "guard":
            problem = guard(
                git,
                args.sha,
                args.component,
                git.remote_ref(TAG_REF),
                fetch=fetch,
                backend_url=args.backend_url,
            )
            if problem:
                print(f"::error::Not deploying the {args.component}: {problem}.")
                return 1
            print(f"OK to deploy the {args.component} at {args.sha}.")
            return 0

        if args.command == "verify":
            common = {
                "timeout": args.timeout,
                "interval": args.interval,
                "clock": clock,
                "sleep": sleep,
            }
            backend = verify_backend(
                git,
                args.sha,
                _truthy(args.backend_deployed),
                fetch=fetch,
                backend_url=args.backend_url,
                **common,
            )
            frontend = verify_frontend(
                git,
                args.sha,
                _truthy(args.frontend_deployed),
                fetch=fetch,
                frontend_url=args.frontend_url,
                **common,
            )
            for check in (backend, frontend):
                prefix = "" if check.ok else "::error::"
                print(f"{prefix}{check.component}: {check.detail}")
            write_outputs(
                {
                    "backend_check": "passed" if backend.ok else "failed",
                    "backend_detail": backend.detail,
                    "backend_live_sha": backend.live_sha,
                    "backend_expected_sha": backend.expected,
                    "frontend_check": "passed" if frontend.ok else "failed",
                    "frontend_detail": frontend.detail,
                    "frontend_expected_sha": frontend.expected,
                }
            )
            return 0 if backend.ok and frontend.ok else 1

        if args.command == "record":
            result = record(
                git,
                args.sha,
                git.remote_ref(TAG_REF),
                git.remote_ref(PREVIOUS_TAG_REF),
                repo=args.repo,
                runner=runner,
            )
            print(result.detail)
            write_outputs(
                {
                    "recorded": _bool(result.recorded),
                    "previous_sha": result.previous_sha,
                }
            )
            return 0

        # summary
        tag_sha = git.remote_ref(TAG_REF) if args.sha else None
        stale_now = bool(args.sha) and relation(git, args.sha, tag_sha) == "stale"
        result = summarize(
            sha=args.sha,
            plan_result=args.plan_result,
            kind=args.kind,
            backend_planned=_truthy(args.backend_planned),
            frontend_planned=_truthy(args.frontend_planned),
            backend_result=args.backend_result,
            frontend_result=args.frontend_result,
            verify_result=args.verify_result,
            record_result=args.record_result,
            recorded=_truthy(args.recorded),
            base_sha=args.base_sha,
            previous_sha=args.previous_sha,
            stale_now=stale_now,
            backend_detail=args.backend_detail,
            frontend_detail=args.frontend_detail,
            run_url=args.run_url,
        )
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        markdown = summary_markdown(result)
        _append(os.environ.get("GITHUB_STEP_SUMMARY"), markdown)
        print(markdown)
        write_outputs({k: v for k, v in result.items() if isinstance(v, str)})
        if result["outcome"] == "failure":
            print(
                f"::error::Staging deploy of {args.sha or 'unknown SHA'} is not verified "
                f"(backend {result['backend']}, frontend {result['frontend']}, "
                f"verify {result['verify']}); {TAG} unchanged."
            )
            return 1
        return 0
    except DeployError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
