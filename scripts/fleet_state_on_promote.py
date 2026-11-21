#!/usr/bin/env python3
"""Transition Linear tickets after a promote PR merge + deploy (AFX-70, AFX-105).

Run by `.github/workflows/agent-fleet-state-on-promote.yml`, which triggers on
the `Deploy` workflow's completion. The Deploy workflow runs three jobs (E2E
Tests, Deploy Backend → Railway, Deploy Frontend → Vercel), so a failed
workflow conclusion does not mean the deploy failed: on failure the script
reads the run's jobs and classifies the outcome (`classify_deploy_jobs`).

For every `AFX-<n>` tag in the subjects of the commits that just landed on
`main`, or in the titles and branch names of the PRs that merged them:

* Deploy succeeded → transition the ticket to `Done`, post a "promoted to main"
  comment with a staging link, and drop the `needs-wilson` label if an
  earlier failed deploy of this workflow added it.
* A deploy job (Railway / Vercel) failed, or the failing job can't be
  determined → transition the ticket to `Blocked`, add the `needs-wilson`
  label, post a warning comment.
* Only E2E failed (the code is live) → post an informational comment; the
  ticket keeps its state and moves to `Done` when a re-run goes green.
* Cancelled → skip.

Dedup is marker-based, same pattern as `fleet_promote_pr.py`. Linear failures
never fail the workflow — a missed comment is better than a blocked deploy.

Stdlib only + `gh` CLI (authenticated through `GH_TOKEN`):

    python3 scripts/fleet_state_on_promote.py --sha <merge-sha> --outcome success
    python3 scripts/fleet_state_on_promote.py --sha <merge-sha> --outcome failure \
        --deploy-run-id <deploy-run-id>
    python3 scripts/fleet_state_on_promote.py --sha <merge-sha> --outcome success --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

LINEAR_URL = "https://api.linear.app/graphql"
_AFX_RE = re.compile(r"\bAFX-(\d+)\b", re.IGNORECASE)

# Linear state IDs on team AFX. Captured 2026-11-14; verify via
# list_issue_statuses if any state ID changes.
STATE_IDS = {
    "Triage": "ade6e2ed-c2b1-4b49-8e05-c411b56dcd9e",
    "Backlog": "d8a54d8c-2505-4205-a667-565166fa67af",
    "Ready": "6e478120-c29c-4c74-948b-a57f3225793f",
    "In Progress": "7fcc84dc-1f85-44db-982a-03f6f89f3647",
    "In Review": "3def3864-8483-49c3-abae-30368b990a73",
    "In Staging": "5fc83a10-ff60-4190-b317-dcdf9510ce22",
    "Done": "0b6a08ed-2137-4f02-833a-6e9c52ba0fdb",
    "Canceled": "4bb78427-f22e-42a6-958a-022c0e2af6ba",
    "Duplicate": "cef17126-9cc5-4cf7-bc14-e418d76781e7",
    "Needs Input": "a6982810-86c4-4dc2-91a8-1a558944ce12",
    "Blocked": "f7439041-9e07-49da-9da6-5c0f1ff91523",
}

# States a ticket is allowed to be in to be moved by this workflow. Narrow
# on purpose:
#   * Only tickets that have been through the PR pipeline (In Review, In
#     Staging) or that were in-flight when the deploy ran (In Progress,
#     Needs Input, Blocked) should be affected by a deploy outcome.
#   * Triage / Backlog / Ready tickets referenced in a commit body as
#     context (e.g. "split out into AFX-94, AFX-95") must NOT be moved —
#     they weren't the subject of the merge. The `collect_afx_ids_from_commits`
#     subject-only scan is the other layer of defence.
#   * Done, Canceled, Duplicate are skipped so a re-run can't un-Done a
#     closed ticket.
TRANSITIONABLE = {
    "In Progress",
    "In Review",
    "In Staging",
    "Needs Input",
    "Blocked",
}

NEEDS_WILSON = "needs-wilson"
PASS_THROUGH_BRANCHES = {"main", "feat/agent-fleet"}
STAGING_URL = "https://agentfx-staging.up.railway.app"

SUCCESS_MARKER_FMT = "<!-- afx-70:promoted-to-main:{sha} -->"
FAILED_MARKER_PREFIX = "<!-- afx-70:deploy-failed:"
FAILED_MARKER_FMT = FAILED_MARKER_PREFIX + "{sha} -->"
E2E_FAILED_MARKER_FMT = "<!-- afx-105:e2e-failed:{sha} -->"
# Appended to the failed-deploy comment only when this workflow actually
# added `needs-wilson`; a later success removes the label only then.
NEEDS_WILSON_ADDED_MARKER = "<!-- afx-105:needs-wilson-added -->"

# Outcomes. `success` / `failure` / `cancelled` are workflow conclusions;
# `failure` is refined into one of the three below by `resolve_outcome`.
DEPLOY_FAILED = "deploy_failed"
E2E_FAILED = "e2e_failed"
UNKNOWN_FAILURE = "failure"

# Job names in `.github/workflows/deploy.yml`. Keep in sync.
DEPLOY_JOB_NAMES = ("Deploy Backend → Railway", "Deploy Frontend → Vercel")
_FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "cancelled"}


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested)
# ─────────────────────────────────────────────────────────────────────


def afx_ids(text: str) -> list[str]:
    """`AFX-<n>` tags in text, upper-cased, first-seen order."""
    seen: list[str] = []
    for m in _AFX_RE.finditer(text):
        ident = f"AFX-{m.group(1)}"
        if ident not in seen:
            seen.append(ident)
    return seen


def success_marker(sha: str) -> str:
    return SUCCESS_MARKER_FMT.format(sha=sha[:7])


def failed_marker(sha: str) -> str:
    return FAILED_MARKER_FMT.format(sha=sha[:7])


def e2e_failed_marker(sha: str) -> str:
    return E2E_FAILED_MARKER_FMT.format(sha=sha[:7])


def deploy_run_url(server_url: str, repo: str, run_id: str) -> str:
    """Link to the Deploy workflow run, or "" when no run id is known."""
    if not run_id:
        return ""
    return f"{server_url.rstrip('/')}/{repo}/actions/runs/{run_id}"


def classify_deploy_jobs(jobs: list[dict]) -> tuple[str, list[str]]:
    """Classify a Deploy run from its jobs. Returns (outcome, failed job names).

    * any deploy job failed                      → DEPLOY_FAILED
    * every deploy job succeeded, another failed → E2E_FAILED (code is live)
    * every job succeeded                        → "success"
    * anything else (deploy job skipped/missing,
      no job data)                               → UNKNOWN_FAILURE
    """
    by_name = {j.get("name"): j.get("conclusion") for j in jobs}
    failed = [j["name"] for j in jobs if j.get("conclusion") in _FAILED_CONCLUSIONS]
    failed_deploy = [n for n in failed if n in DEPLOY_JOB_NAMES]
    if failed_deploy:
        return DEPLOY_FAILED, failed_deploy
    deployed = all(by_name.get(n) == "success" for n in DEPLOY_JOB_NAMES)
    if deployed and failed:
        return E2E_FAILED, failed
    if deployed and not failed:
        return "success", []
    return UNKNOWN_FAILURE, failed


def resolve_outcome(outcome: str, repo: str, run_id: str) -> tuple[str, list[str]]:
    """Refine a `failure` workflow conclusion using the Deploy run's jobs.
    Any lookup problem falls back to UNKNOWN_FAILURE, which still blocks."""
    if outcome != UNKNOWN_FAILURE:
        return outcome, []
    if not run_id:
        return UNKNOWN_FAILURE, []
    try:
        jobs = fetch_deploy_jobs(repo, run_id)
    except Exception as exc:  # noqa: BLE001
        warn(f"Could not read jobs for Deploy run {run_id}: {exc}")
        return UNKNOWN_FAILURE, []
    return classify_deploy_jobs(jobs)


def success_comment(sha: str, *, now: datetime) -> str:
    return (
        f"Promoted to `main` and deployed to staging at "
        f"{now:%Y-%m-%dT%H:%M:%SZ} (commit `{sha[:7]}`).\n\n"
        f"Staging: {STAGING_URL}\n\n"
        f"{success_marker(sha)}\n"
        "_Posted by agent-fleet-state-on-promote.yml (AFX-70)._"
    )


def failed_comment(
    sha: str,
    *,
    run_url: str,
    outcome: str = DEPLOY_FAILED,
    failed_jobs: list[str] | None = None,
) -> str:
    if outcome == DEPLOY_FAILED:
        jobs = ", ".join(f"`{n}`" for n in failed_jobs or []) or "a deploy job"
        what = f"the deploy **failed** ({jobs})"
    else:
        what = (
            "the Deploy workflow **failed** and the automation could not "
            "tell which job failed, so the deploy may not be live"
        )
    return (
        f"Promoted to `main` but {what} at commit "
        f"`{sha[:7]}`. Ticket moved to `Blocked` pending investigation. "
        f"Do not re-attempt the promote until the deploy issue is resolved.\n\n"
        f"Deploy run: {run_url or '(not available)'}\n\n"
        f"{failed_marker(sha)}\n"
        "_Posted by agent-fleet-state-on-promote.yml (AFX-70)._"
    )


def e2e_failed_comment(
    sha: str, *, run_url: str, failed_jobs: list[str] | None = None
) -> str:
    jobs = ", ".join(f"`{n}`" for n in failed_jobs or []) or "`E2E Tests`"
    return (
        f"Promoted to `main` and deployed to staging (Railway and Vercel "
        f"succeeded) at commit `{sha[:7]}`, but {jobs} failed in the same "
        f"Deploy run. The change is live; the ticket was left in its current "
        f"state. Check whether the E2E failure is a real regression or a "
        f"flake (e.g. an apt mirror outage) and re-run the failed job: a "
        f"green re-run moves the ticket to `Done`.\n\n"
        f"Deploy run: {run_url or '(not available)'}\n\n"
        f"{e2e_failed_marker(sha)}\n"
        "_Posted by agent-fleet-state-on-promote.yml (AFX-105)._"
    )


def collect_afx_ids_from_commits(commits: list[dict]) -> list[str]:
    """Deduplicated AFX-IDs from every commit **subject** (first line) in
    the list. The body is deliberately ignored: agents reference other
    tickets in commit bodies as context ("split out into AFX-94, AFX-95"),
    and those references must NOT be treated as tickets this merge closes.
    """
    seen: list[str] = []
    for c in commits:
        message = ((c.get("commit") or {}).get("message")) or ""
        subject = message.split("\n", 1)[0]
        for ident in afx_ids(subject):
            if ident not in seen:
                seen.append(ident)
    return seen


def collect_afx_ids_from_prs(prs: list[dict]) -> list[str]:
    """Deduplicated AFX-IDs from the **titles and head branch names** of the
    merged PRs behind the commits. These are what Linear's GitHub
    integration links on, so they are how a ticket reached `In Staging`.
    A single-commit squash keeps the commit's own subject instead of the PR
    title, and follow-up PRs often name the ticket only in their branch
    (AFX-106: #137, #139, #141), so commit subjects alone miss them.
    PR bodies are ignored for the same reason as commit bodies. Promote
    PRs (head `feat/agent-fleet`) and back-merges (head `main`) are skipped:
    they carry other PRs' work, not their own.
    """
    seen: list[str] = []
    for pr in prs:
        if not pr.get("merged_at"):
            continue
        head = (pr.get("head") or {}).get("ref") or ""
        if head in PASS_THROUGH_BRANCHES:
            continue
        for ident in afx_ids(f"{pr.get('title') or ''} {head}"):
            if ident not in seen:
                seen.append(ident)
    return seen


def merge_afx_ids(*groups: list[str]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for ident in group:
            if ident not in seen:
                seen.append(ident)
    return seen


# ─────────────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────────────


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _gh_json(*args: str):
    return json.loads(_run("gh", *args).stdout or "null")


def warn(msg: str) -> None:
    print(f"::warning ::{msg}")


def fetch_deploy_jobs(repo: str, run_id: str) -> list[dict]:
    """Jobs of the Deploy run's latest attempt. Uses `ACTIONS_TOKEN` (the
    workflow's `GITHUB_TOKEN` with `actions: read`) when set, because the
    fleet-bot App token is only minted with contents/pull-requests."""
    env = dict(os.environ)
    if env.get("ACTIONS_TOKEN"):
        env["GH_TOKEN"] = env["ACTIONS_TOKEN"]
    proc = subprocess.run(
        (
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
            "--jq",
            ".jobs[] | {name, conclusion}",
        ),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def fetch_merge_commits(repo: str, sha: str) -> list[dict]:
    """Every commit in the merge range on main, oldest first.

    For a merge commit, this returns the merge commit itself plus every
    commit it brings in that wasn't already on main. In practice for a
    promote PR this is the single squash-merged commit (or the merge
    commit's immediate parents).
    """
    # The merge commit is the simplest source. For a squash merge from
    # feat/agent-fleet or an agent branch, the single commit on main
    # contains the AFX-ID in its subject. For a merge commit with
    # multiple parents, we also walk the parent that is NOT main.
    merge = _gh_json("api", f"repos/{repo}/commits/{sha}")
    commits = [merge]
    parents = merge.get("parents") or []
    if len(parents) >= 2:
        # Merge commit: the first parent is main's previous tip, the second
        # is the feature branch's tip. Walk everything on the feature side
        # that isn't already on main.
        base_sha = parents[0]["sha"]
        feature_sha = parents[1]["sha"]
        compare = _gh_json("api", f"repos/{repo}/compare/{base_sha}...{feature_sha}")
        for c in compare.get("commits") or []:
            commits.append(c)
    return commits


def fetch_merged_prs(repo: str, commits: list[dict]) -> list[dict]:
    """The PRs associated with each commit, deduplicated by number. A failed
    lookup warns and is skipped; commit subjects still apply."""
    prs: dict[int, dict] = {}
    for c in commits:
        sha = c.get("sha")
        if not sha:
            continue
        try:
            candidates = _gh_json("api", f"repos/{repo}/commits/{sha}/pulls") or []
        except (subprocess.CalledProcessError, ValueError) as exc:
            warn(f"PR lookup failed for {sha[:7]}: {exc}")
            continue
        for pr in candidates:
            prs.setdefault(pr["number"], pr)
    return list(prs.values())


# ─────────────────────────────────────────────────────────────────────
# Linear client
# ─────────────────────────────────────────────────────────────────────


class Linear:
    """Minimal Linear GraphQL client for state + label + comment ops.

    Modelled on `scripts/fleet_promote_pr.py`'s Linear class. Stdlib only.
    Every public method is best-effort: on API failure it warns and returns
    False / None rather than raising. The caller must never let a Linear
    failure break the workflow.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._issues: dict[str, dict | None] = {}
        self._label_ids: dict[str, str | None] = {}

    def _gql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL request. Linear returns HTTP 400 on schema
        violations + complexity-gate rejections with the real error in the
        response body — so parse the body on both success AND HTTPError
        and surface the GraphQL `errors` payload. Backend's
        `linear_client._gql` learned this the hard way (~15 min debugging
        2026-10-27); this mirrors that pattern.
        """
        req = urllib.request.Request(
            LINEAR_URL,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
        except urllib.error.HTTPError as http_err:
            # Linear's error body is JSON with a GraphQL `errors` list.
            # Surface it so the warning tells us the real cause instead of
            # just "HTTP Error 400: Bad Request".
            body = http_err.read() if http_err.fp else b""
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            errors = (
                payload.get("errors")
                or payload.get("error")
                or body.decode("utf-8", "replace")
            )
            raise RuntimeError(
                f"linear graphql {http_err.code}: {errors}"
            ) from http_err
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise RuntimeError(f"linear non-json response: {body!r}") from exc
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        return payload["data"]

    def issue(self, ident: str) -> dict | None:
        """Load the issue with its current state + labels. Cached."""
        if ident not in self._issues:
            try:
                self._issues[ident] = self._gql(
                    "query($id: String!) { issue(id: $id) { "
                    "id identifier state { id name } "
                    "labels { nodes { id name } } } }",
                    {"id": ident},
                )["issue"]
            except Exception as exc:  # noqa: BLE001
                warn(f"Linear lookup failed for {ident}: {exc}")
                self._issues[ident] = None
        return self._issues[ident]

    def comment_bodies(self, issue: dict) -> list[str]:
        """Every comment body on the issue, paginated. Cached on the issue."""
        if "comment_bodies" not in issue:
            bodies: list[str] = []
            cursor = None
            while True:
                page = self._gql(
                    "query($id: String!, $after: String) { issue(id: $id) { "
                    "comments(first: 100, after: $after) { nodes { body } "
                    "pageInfo { hasNextPage endCursor } } } }",
                    {"id": issue["id"], "after": cursor},
                )["issue"]["comments"]
                bodies += [n.get("body") or "" for n in page["nodes"]]
                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]
            issue["comment_bodies"] = bodies
        return issue["comment_bodies"]

    def transition(
        self,
        ident: str,
        target_state: str,
        *,
        allowed_from: set[str] | None = None,
    ) -> str | None:
        """Transition the issue to `target_state`. Returns the old state name
        on success, None on skip/failure. `allowed_from` restricts the move:
        if the current state isn't in it, the move is skipped (for honesty:
        a ticket already Done stays Done)."""
        issue = self.issue(ident)
        if not issue:
            return None
        current = (issue.get("state") or {}).get("name")
        if allowed_from is not None and current not in allowed_from:
            print(f"::notice ::{ident} in {current!r}; not moving to {target_state!r}")
            return None
        target_id = STATE_IDS.get(target_state)
        if not target_id:
            warn(f"Unknown target state {target_state!r} for {ident}")
            return None
        if current == target_state:
            return current  # idempotent no-op
        try:
            self._gql(
                "mutation($id: String!, $stateId: String!) { "
                "issueUpdate(id: $id, input: { stateId: $stateId }) "
                "{ success } }",
                {"id": issue["id"], "stateId": target_id},
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear state change failed for {ident}: {exc}")
            return None
        # Refresh our cached state so a later transition() call sees the new
        # value.
        issue["state"] = {"id": target_id, "name": target_state}
        print(f"Transitioned {ident}: {current} → {target_state}")
        return current

    def label_id(self, team_key: str, name: str) -> str | None:
        """Look up a label ID by team key + name. Cached."""
        cache_key = f"{team_key}::{name}"
        if cache_key not in self._label_ids:
            try:
                nodes = self._gql(
                    "query($teamKey: String!, $name: String!) { "
                    "issueLabels(filter: { team: { key: { eq: $teamKey } }, "
                    "name: { eq: $name } }) { nodes { id name } } }",
                    {"teamKey": team_key, "name": name},
                )["issueLabels"]["nodes"]
            except Exception as exc:  # noqa: BLE001
                warn(f"Linear label lookup failed for {name}: {exc}")
                self._label_ids[cache_key] = None
                return None
            self._label_ids[cache_key] = nodes[0]["id"] if nodes else None
        return self._label_ids[cache_key]

    def add_label(self, ident: str, team_key: str, name: str) -> bool:
        """Add a label to the issue by name. Idempotent via server-side."""
        issue = self.issue(ident)
        if not issue:
            return False
        existing = {n["name"] for n in (issue.get("labels") or {}).get("nodes", [])}
        if name in existing:
            return False
        label_id = self.label_id(team_key, name)
        if not label_id:
            warn(f"Label {name!r} not found for team {team_key!r}")
            return False
        try:
            self._gql(
                "mutation($id: String!, $labelId: String!) { "
                "issueAddLabel(id: $id, labelId: $labelId) { success } }",
                {"id": issue["id"], "labelId": label_id},
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear add label failed for {ident}: {exc}")
            return False
        # Update cached labels so a subsequent call sees it.
        issue["labels"]["nodes"].append({"id": label_id, "name": name})
        return True

    def remove_label(self, ident: str, name: str) -> bool:
        """Remove a label from the issue by name. No-op if absent."""
        issue = self.issue(ident)
        if not issue:
            return False
        nodes = (issue.get("labels") or {}).get("nodes", [])
        match = next((n for n in nodes if n["name"] == name), None)
        if not match:
            return False
        try:
            self._gql(
                "mutation($id: String!, $labelId: String!) { "
                "issueRemoveLabel(id: $id, labelId: $labelId) { success } }",
                {"id": issue["id"], "labelId": match["id"]},
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear remove label failed for {ident}: {exc}")
            return False
        nodes.remove(match)
        return True

    def current_state(self, ident: str) -> str | None:
        issue = self.issue(ident)
        return ((issue or {}).get("state") or {}).get("name")

    def has_comment_containing(self, ident: str, needle: str) -> bool:
        issue = self.issue(ident)
        if not issue:
            return False
        try:
            return any(needle in b for b in self.comment_bodies(issue))
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear comment lookup failed for {ident}: {exc}")
            return False

    def comment_once(self, ident: str, body: str, marker: str) -> bool:
        """Post the comment unless any existing comment already carries
        the marker. Returns True if a new comment was posted."""
        issue = self.issue(ident)
        if not issue:
            return False
        try:
            if any(marker in b for b in self.comment_bodies(issue)):
                return False
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear comment lookup failed for {ident}: {exc}")
            return False
        try:
            self._gql(
                "mutation($id: String!, $body: String!) { "
                "commentCreate(input: { issueId: $id, body: $body }) "
                "{ success } }",
                {"id": issue["id"], "body": body},
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Linear comment failed for {ident}: {exc}")
            return False
        issue["comment_bodies"].append(body)
        return True


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def dispatch(
    linear: Linear,
    idents: list[str],
    *,
    sha: str,
    outcome: str,
    run_url: str,
    team_key: str,
    now: datetime,
    failed_jobs: list[str] | None = None,
) -> None:
    """Apply the right state + comment to every AFX-ID."""
    if outcome == E2E_FAILED:
        marker = e2e_failed_marker(sha)
        body = e2e_failed_comment(sha, run_url=run_url, failed_jobs=failed_jobs)
        for ident in idents:
            current = linear.current_state(ident)
            if current not in TRANSITIONABLE:
                print(f"::notice ::{ident} in {current!r}; no E2E comment")
                continue
            if linear.comment_once(ident, body, marker):
                print(f"Commented on {ident}")
        return

    if outcome == "success":
        marker = success_marker(sha)
        body = success_comment(sha, now=now)
        target_state = "Done"
        add_needs_wilson = False
    else:
        marker = failed_marker(sha)
        body = failed_comment(
            sha, run_url=run_url, outcome=outcome, failed_jobs=failed_jobs
        )
        target_state = "Blocked"
        add_needs_wilson = True

    for ident in idents:
        old = linear.transition(ident, target_state, allowed_from=TRANSITIONABLE)
        if old is None:
            continue
        added = add_needs_wilson and linear.add_label(ident, team_key, NEEDS_WILSON)
        if not add_needs_wilson and linear.has_comment_containing(
            ident, NEEDS_WILSON_ADDED_MARKER
        ):
            # Only undo the label this workflow added; a needs-wilson set
            # for any other reason is a real blocker.
            if linear.remove_label(ident, NEEDS_WILSON):
                print(f"Removed {NEEDS_WILSON} from {ident}")
        comment = f"{body}\n{NEEDS_WILSON_ADDED_MARKER}" if added else body
        if linear.comment_once(ident, comment, marker):
            print(f"Commented on {ident}")
        elif added:
            # Failed comment for this SHA already posted (re-run): record
            # label ownership separately so a later success can clear it.
            linear.comment_once(
                ident,
                f"Added `{NEEDS_WILSON}` after the failed deploy at "
                f"`{sha[:7]}`.\n\n{NEEDS_WILSON_ADDED_MARKER}",
                NEEDS_WILSON_ADDED_MARKER,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", required=True, help="Merge commit SHA on main")
    parser.add_argument(
        "--outcome",
        required=True,
        choices=("success", "failure", "cancelled", DEPLOY_FAILED, E2E_FAILED),
        help=(
            "Deploy workflow conclusion; `failure` is refined from the run's "
            f"jobs. `{DEPLOY_FAILED}` / `{E2E_FAILED}` force a classification "
            "(manual dispatch)."
        ),
    )
    parser.add_argument(
        "--deploy-run-id",
        default="",
        help="ID of the Deploy workflow run (job lookup + comment link)",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="Override the Deploy run link (default: built from --deploy-run-id)",
    )
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "widarecu/agentfx")
    )
    parser.add_argument(
        "--team-key",
        default=os.environ.get("LINEAR_TEAM_KEY", "AFX"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)

    if args.outcome == "cancelled":
        print("Deploy was cancelled; not transitioning any tickets.")
        return 0

    run_url = args.run_url or deploy_run_url(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        args.repo,
        args.deploy_run_id,
    )

    commits = fetch_merge_commits(args.repo, args.sha)
    idents = merge_afx_ids(
        collect_afx_ids_from_commits(commits),
        collect_afx_ids_from_prs(fetch_merged_prs(args.repo, commits)),
    )
    if not idents:
        print(f"No AFX-IDs found in commits for {args.sha[:7]}; nothing to do.")
        return 0
    print(f"Found AFX-IDs: {', '.join(idents)}")

    if args.dry_run:
        print(f"Would mark these as {args.outcome!r} and transition.")
        return 0

    outcome, failed_jobs = resolve_outcome(args.outcome, args.repo, args.deploy_run_id)
    print(f"Deploy outcome: {outcome} (failed jobs: {failed_jobs or 'none'})")

    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        warn("LINEAR_API_KEY not set: cannot transition states.")
        return 0

    linear = Linear(api_key)
    dispatch(
        linear,
        idents,
        sha=args.sha,
        outcome=outcome,
        run_url=run_url,
        team_key=args.team_key,
        now=now,
        failed_jobs=failed_jobs,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
