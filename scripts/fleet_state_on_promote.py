#!/usr/bin/env python3
"""Transition Linear tickets after a promote PR merge + Railway deploy (AFX-70).

Run by `.github/workflows/agent-fleet-state-on-promote.yml`, which triggers on
the `Deploy` workflow's completion. The Deploy workflow IS the Railway deploy,
so `workflow_run.conclusion` is the Railway deploy outcome.

For every `AFX-<n>` tag in the commits that just landed on `main`:

* Deploy succeeded → transition the ticket to `Done`, post a "promoted to main"
  comment with a staging link.
* Deploy failed / cancelled → transition the ticket to `Blocked`, add the
  `needs-wilson` label, post a warning comment.

Dedup is marker-based, same pattern as `fleet_promote_pr.py`. Linear failures
never fail the workflow — a missed comment is better than a blocked deploy.

Stdlib only + `gh` CLI (authenticated through `GH_TOKEN`):

    python3 scripts/fleet_state_on_promote.py --sha <merge-sha> --outcome success
    python3 scripts/fleet_state_on_promote.py --sha <merge-sha> --outcome failure
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
STAGING_URL = "https://agentfx-staging.up.railway.app"

SUCCESS_MARKER_FMT = "<!-- afx-70:promoted-to-main:{sha} -->"
FAILED_MARKER_FMT = "<!-- afx-70:deploy-failed:{sha} -->"


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


def success_comment(sha: str, *, now: datetime) -> str:
    return (
        f"Promoted to `main` and deployed to staging at "
        f"{now:%Y-%m-%dT%H:%M:%SZ} (commit `{sha[:7]}`).\n\n"
        f"Staging: {STAGING_URL}\n\n"
        f"{success_marker(sha)}\n"
        "_Posted by agent-fleet-state-on-promote.yml (AFX-70)._"
    )


def failed_comment(sha: str, *, run_url: str) -> str:
    return (
        f"Promoted to `main` but the Railway deploy **failed** at commit "
        f"`{sha[:7]}`. Ticket moved to `Blocked` pending investigation. "
        f"Do not re-attempt the promote until the deploy issue is resolved.\n\n"
        f"Deploy run: {run_url}\n\n"
        f"{failed_marker(sha)}\n"
        "_Posted by agent-fleet-state-on-promote.yml (AFX-70)._"
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


# ─────────────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────────────


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _gh_json(*args: str):
    return json.loads(_run("gh", *args).stdout or "null")


def warn(msg: str) -> None:
    print(f"::warning ::{msg}")


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
) -> None:
    """Apply the right state + comment to every AFX-ID."""
    if outcome == "success":
        marker = success_marker(sha)
        body = success_comment(sha, now=now)
        target_state = "Done"
        add_needs_wilson = False
    else:
        marker = failed_marker(sha)
        body = failed_comment(sha, run_url=run_url)
        target_state = "Blocked"
        add_needs_wilson = True

    for ident in idents:
        old = linear.transition(ident, target_state, allowed_from=TRANSITIONABLE)
        if old is None:
            continue
        if add_needs_wilson:
            linear.add_label(ident, team_key, NEEDS_WILSON)
        if linear.comment_once(ident, body, marker):
            print(f"Commented on {ident}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", required=True, help="Merge commit SHA on main")
    parser.add_argument(
        "--outcome",
        required=True,
        choices=("success", "failure", "cancelled"),
        help="Deploy workflow conclusion",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="GitHub Actions run URL of the failed deploy (for the failure comment)",
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

    commits = fetch_merge_commits(args.repo, args.sha)
    idents = collect_afx_ids_from_commits(commits)
    if not idents:
        print(f"No AFX-IDs found in commits for {args.sha[:7]}; nothing to do.")
        return 0
    print(f"Found AFX-IDs: {', '.join(idents)}")

    if args.dry_run:
        print(f"Would mark these as {args.outcome!r} and transition.")
        return 0

    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        warn("LINEAR_API_KEY not set: cannot transition states.")
        return 0

    linear = Linear(api_key)
    dispatch(
        linear,
        idents,
        sha=args.sha,
        outcome=args.outcome,
        run_url=args.run_url,
        team_key=args.team_key,
        now=now,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
