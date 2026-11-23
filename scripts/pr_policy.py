#!/usr/bin/env python3
"""`pr-policy` check for PRs into `main` (AFX-120).

A PR passes when:
  1. its title and head branch name exactly one distinct AFX ticket,
  2. that Linear ticket is In Progress or In Review, and
  3. if the ticket carries the Money-path label, Wilson's latest review
     is an approval of the PR's current head commit.

Anything missing, ambiguous, unknown or unreachable fails closed, and so
does a head commit shared by more than one open PR (one commit status
can't hold two decisions). The result is written as the `pr-policy`
commit status on the PR head, so a re-run from a review event (which
runs in `main`'s context, not the PR's) updates the same check.

Auto-merge (AFX-130) is only turned on from a fresh pass once `CI
required` has passed on that exact head, pinned to it, so GitHub merges
within seconds of the check, not on a pass from before a Linear move or
a later push. Any other auto-merge request on the PR is turned off. Only trusted code from `main` runs this script;
PR titles and branch names are read through the API, never executed.

    python scripts/pr_policy.py --repo widarecu/agentfx --pr 123
    python scripts/pr_policy.py --repo widarecu/agentfx --head-sha <sha>

Exit codes: 0 pass (or nothing to check), 1 policy failed, 2 API error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentfx.integrations import linear_delivery as ld  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

CONTEXT = "pr-policy"
CI_CHECK = "CI required"
TARGET_BRANCH = "main"
ALLOWED_STATES = (ld.IN_PROGRESS, ld.IN_REVIEW)
MONEY_PATH_LABEL = "Money-path"
DEFAULT_APPROVER = "widarecu"
GITHUB_API = "https://api.github.com"
# Promote PRs from the fleet branch stay Wilson-approved merge commits
# until the cutover (AFX-125) retires them.
LEGACY_PROMOTE_HEADS = ("feat/agent-fleet",)
LEGACY_PROMOTE_PREFIX = "promote/"

_TICKET = re.compile(r"(?<![A-Za-z0-9])AFX-(\d+)(?![0-9])", re.IGNORECASE)
_REVIEW_VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")

Opener = Callable[..., Any]


class GitHubError(RuntimeError):
    pass


@dataclass
class Decision:
    ok: bool
    reason: str
    ticket: str | None = None
    money_path: bool = False


def ticket_ids(*texts: str | None) -> list[str]:
    """Distinct AFX IDs across the texts, normalised (afx-07 -> AFX-7)."""
    found = {f"AFX-{int(n)}" for text in texts for n in _TICKET.findall(text or "")}
    return sorted(found, key=lambda ident: int(ident.split("-")[1]))


def approved_by(reviews: Iterable[dict], approver: str, head_sha: str) -> bool:
    """True when `approver`'s latest verdict approves `head_sha` itself.

    Comment-only reviews don't change the verdict. A later change request
    or a dismissal replaces an earlier approval, and an approval of an
    older commit is stale once new commits are pushed."""
    latest: dict | None = None
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "").lower()
        if login == approver.lower() and review.get("state") in _REVIEW_VERDICTS:
            latest = review
    return bool(
        latest
        and latest.get("state") == "APPROVED"
        and latest.get("commit_id") == head_sha
    )


def evaluate(
    pr: dict, reviews: list[dict], linear: ld.LinearClient, approver: str
) -> Decision:
    """Policy decision for one PR. Linear API failures propagate."""
    ids = ticket_ids(pr.get("title"), (pr.get("head") or {}).get("ref"))
    if not ids:
        return Decision(False, "no AFX ticket ID in the PR title or branch")
    if len(ids) > 1:
        return Decision(False, f"more than one ticket ID: {', '.join(ids)}")
    ident = ids[0]
    try:
        issue = linear.issue(ident)
    except ld.NotFoundError:
        return Decision(False, f"{ident} not found in Linear", ident)
    if issue.state not in ALLOWED_STATES:
        return Decision(
            False,
            f"{ident} is {issue.state or 'in no state'}, "
            f"needs {' or '.join(ALLOWED_STATES)}",
            ident,
        )
    money = MONEY_PATH_LABEL in issue.labels
    if money and not approved_by(reviews, approver, pr["head"]["sha"]):
        return Decision(
            False,
            f"{ident} is Money-path: needs @{approver}'s approval of the latest commit",
            ident,
            money_path=True,
        )
    return Decision(True, f"{ident} is {issue.state}", ident, money_path=money)


@dataclass
class MergeAction:
    """What the workflow does with auto-merge after a check."""

    enable: bool = False
    disable: bool = False
    sha: str = ""


def merge_action(
    pr: dict, decision: Decision, repo: str, ci_passed: bool
) -> MergeAction:
    """Enable only on a fresh pass with CI already green, so the merge
    follows this check; turn off any auto-merge this check didn't grant."""
    if auto_merge_eligible(pr, decision, repo) and ci_passed:
        return MergeAction(enable=not pr.get("auto_merge"), sha=pr["head"]["sha"])
    return MergeAction(disable=bool(pr.get("auto_merge")))


def auto_merge_eligible(pr: dict, decision: Decision, repo: str) -> bool:
    """Normal auto-merge: passed, not a draft, and not from a fork."""
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    return decision.ok and not pr.get("draft") and head_repo == repo


class GitHub:
    """The few REST calls the check needs, standard library only."""

    def __init__(
        self,
        token: str,
        repo: str,
        *,
        url: str = GITHUB_API,
        timeout: float = 15.0,
        opener: Opener | None = None,
    ) -> None:
        if not token:
            raise GitHubError("GH_TOKEN is empty")
        self._token = token
        self._base = f"{url}/repos/{repo}"
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        req = urllib.request.Request(
            self._base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raise GitHubError(f"github http {err.code} on {method} {path}") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise GitHubError(f"github unreachable: {err}") from err
        try:
            return json.loads(raw) if raw else None
        except ValueError as err:
            raise GitHubError(f"github non-json response on {path}") from err

    def pull(self, number: int) -> dict:
        return self._call("GET", f"/pulls/{number}")

    def reviews(self, number: int) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            batch = self._call(
                "GET", f"/pulls/{number}/reviews?per_page=100&page={page}"
            )
            out.extend(batch or [])
            if not batch or len(batch) < 100:
                return out
            page += 1

    def pulls_for_sha(self, sha: str) -> list[int]:
        pulls = self._call("GET", f"/commits/{sha}/pulls") or []
        return [p["number"] for p in pulls if p.get("state") == "open"]

    def ci_passed(self, sha: str) -> bool:
        """The latest `CI required` run on `sha` concluded success."""
        body = self._call(
            "GET",
            f"/commits/{sha}/check-runs"
            f"?check_name={urllib.parse.quote(CI_CHECK)}&filter=latest",
        )
        runs = (body or {}).get("check_runs") or []
        return any(r.get("conclusion") == "success" for r in runs)

    def set_status(self, sha: str, state: str, description: str) -> None:
        self._call(
            "POST",
            f"/statuses/{sha}",
            {"state": state, "context": CONTEXT, "description": description[:140]},
        )


def check(
    number: int, github: GitHub, linear: ld.LinearClient, approver: str, repo: str
) -> tuple[int, MergeAction]:
    """Check one PR and record the status. Returns (exit code, merge action)."""
    pr = github.pull(number)
    if pr.get("state") != "open" or (pr.get("base") or {}).get("ref") != TARGET_BRANCH:
        print(f"#{number}: not an open PR into {TARGET_BRANCH}, nothing to check")
        return EXIT_OK, MergeAction()
    head_ref = pr["head"].get("ref") or ""
    if head_ref in LEGACY_PROMOTE_HEADS or head_ref.startswith(LEGACY_PROMOTE_PREFIX):
        print(f"#{number}: legacy promote PR from {head_ref}, not checked")
        return EXIT_OK, MergeAction()
    sha = pr["head"]["sha"]
    try:
        sharing = sorted(set(github.pulls_for_sha(sha)) - {number})
        if sharing:
            others = ", ".join(f"#{n}" for n in sharing)
            decision = Decision(False, f"head commit is also the head of {others}")
        else:
            decision = evaluate(pr, github.reviews(number), linear, approver)
        ci_passed = decision.ok and github.ci_passed(sha)
    except (ld.LinearError, GitHubError) as err:
        github.set_status(sha, "error", f"could not check, re-run: {err}")
        raise
    github.set_status(sha, "success" if decision.ok else "failure", decision.reason)
    print(f"#{number}: {'pass' if decision.ok else 'FAIL'}: {decision.reason}")
    action = merge_action(pr, decision, repo, ci_passed)
    return (EXIT_OK if decision.ok else EXIT_FAILED), action


def _write_outputs(path: str | None, **values: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def main(
    argv: list[str] | None = None,
    *,
    github: GitHub | None = None,
    linear: ld.LinearClient | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="pr-policy check (AFX-120)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--head-sha")
    parser.add_argument(
        "--approver",
        default=os.environ.get("PR_POLICY_APPROVER") or DEFAULT_APPROVER,
    )
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)

    try:
        if not args.repo:
            raise GitHubError("--repo or GITHUB_REPOSITORY is required")
        github = github or GitHub(os.environ.get("GH_TOKEN", "").strip(), args.repo)
        linear = linear or ld.LinearClient.from_env()
        numbers = [args.pr] if args.pr else github.pulls_for_sha(args.head_sha)
        code, enable, sha, disable = EXIT_OK, "", "", []
        for number in numbers:
            result, action = check(number, github, linear, args.approver, args.repo)
            code = max(code, result)
            if action.enable and not enable:
                enable, sha = str(number), action.sha
            if action.disable:
                disable.append(str(number))
    except (ld.LinearError, GitHubError) as err:
        print(f"pr-policy error: {err}", file=sys.stderr)
        _write_outputs(args.output, eligible="false", pr="", sha="", disable="")
        return EXIT_ERROR
    if not numbers:
        print(f"no open PR has head {args.head_sha}, nothing to check")
    _write_outputs(
        args.output,
        eligible="true" if enable else "false",
        pr=enable,
        sha=sha,
        disable=" ".join(disable),
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
