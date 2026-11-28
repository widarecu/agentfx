"""Delivery reconcile and drift digest (AFX-123).

`afx reconcile` runs daily (and on demand). It:

1. Replays the deploy sync for the current `deployed/staging` from its
   recorded range (`deploy_sync.recover`), repairing a missed sync. This is
   the only thing it changes, and only in `apply` mode.
2. Reads Linear and GitHub and lists drift, without moving any ticket:
   * In Progress for more than 3 days with no PR linked,
   * In Review with no open PR,
   * Done in the window without a deploy-sync (or legacy promote) marker,
   * Backlog/Ready moves in the window by anyone other than Wilson,
   * PRs merged into `main` in the window by anyone other than the fleet bot,
   * open PRs into `main` in conflict with it for over a day (AFX-162),
     dated from the oldest `pr-policy` conflict status on the PR head,
   * anything that could not be checked (history or PR state unavailable).
3. Publishes one digest: the GitHub Actions job summary. `digest.json`
   (uploaded as an artifact) carries the window and a fingerprint of the
   findings; when the next run's findings match, or there are none, the
   summary is one line instead of a repeated digest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agentfx.integrations import deploy_sync as ds
from agentfx.integrations import linear_delivery as ld

TEAM_KEY = "AFX"
DONE = "Done"
WATCHED_STATES = [ld.IN_PROGRESS, ld.IN_REVIEW]
HUMAN_OWNED_STATES = ("Backlog", ld.READY)
STALE_AFTER = timedelta(days=3)
CONFLICT_AFTER = timedelta(days=1)
# `scripts/pr_policy.py` puts this in the `pr-policy` status of a PR that
# conflicts with `main`; the digest dates the conflict from it.
PR_POLICY_CONTEXT = "pr-policy"
PR_CONFLICT_PHRASE = "has merge conflicts with main"
DEFAULT_WINDOW_DAYS = 7
DEFAULT_WILSON_USER_ID = "4c000ffe-cccc-4d3c-bd74-04a3a009032c"
FLEET_BOT_LOGIN = "agentfx-fleet-bot"
DEPLOY_MARKERS = (ds._DONE_PREFIX, "<!-- afx-70:promoted-to-main:")
MARKER = "<!-- afx-reconcile:window"
_PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")

STALE = "stale-in-progress"
REVIEW = "in-review-no-open-pr"
UNMARKED = "done-without-deploy"
TRANSITION = "status-set-by-other"
BY_HAND = "merged-by-hand"
CONFLICTED = "conflicted-pr"
UNCHECKED = "unchecked"

_TITLES = {
    STALE: "In Progress over 3 days, no PR",
    REVIEW: "In Review, no open PR",
    UNMARKED: "Done without a deploy marker",
    TRANSITION: "Backlog/Ready set by someone other than Wilson",
    BY_HAND: "PR merged into main by someone other than the fleet bot",
    CONFLICTED: "Open PR in conflict with main for over a day",
    UNCHECKED: "Could not be checked",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    ident: str
    detail: str
    key: str = ""  # stable identity for dedupe; detail may carry ages or errors

    @property
    def fingerprint_key(self) -> str:
        return f"{self.kind}|{self.ident}|{self.key or self.detail}"


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    sync_lines: list[str] = field(default_factory=list)
    errors: int = 0
    summary: str = ""
    state: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def linked_prs(issue: dict, repo: str) -> list[int]:
    numbers = []
    for node in (issue.get("attachments") or {}).get("nodes") or []:
        m = _PR_URL.search(node.get("url") or "")
        if m and m[1].lower() == repo.lower() and int(m[2]) not in numbers:
            numbers.append(int(m[2]))
    return numbers


def fingerprint(findings: list[Finding]) -> str:
    keys = sorted(f.fingerprint_key for f in findings)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def find_drift(
    gh,
    client: ld.LinearClient,
    *,
    repo: str,
    now: datetime,
    since: datetime,
    wilson_user_id: str,
) -> list[Finding]:
    """Read-only. Linear failures listing issues propagate; per-issue
    failures become `unchecked` findings so one bad issue never hides the
    rest of the digest."""
    issues = client.team_issues(
        TEAM_KEY, states=WATCHED_STATES, updated_since=_iso(since)
    )
    out: list[Finding] = []
    for issue in sorted(issues, key=lambda i: int(i["identifier"].split("-")[1])):
        ident = issue["identifier"]
        state = (issue.get("state") or {}).get("name") or ""
        prs = linked_prs(issue, repo)
        if state == ld.IN_PROGRESS:
            started = _parse(issue.get("startedAt"))
            if started and now - started > STALE_AFTER and not prs:
                days = (now - started).days
                out.append(
                    Finding(
                        STALE,
                        ident,
                        f"In Progress for {days} days",
                        key=issue.get("startedAt") or "",
                    )
                )
        elif state == ld.IN_REVIEW:
            out.extend(_review_drift(gh, ident, prs))
        elif state == DONE and (_parse(issue.get("completedAt")) or now) >= since:
            try:
                bodies = client.comment_bodies(issue["id"])
            except ld.LinearError as exc:
                out.append(
                    Finding(
                        UNCHECKED, ident, f"comments unavailable: {exc}", "comments"
                    )
                )
            else:
                if not any(m in b for b in bodies for m in DEPLOY_MARKERS):
                    out.append(Finding(UNMARKED, ident, "no staging deploy recorded"))
        if (_parse(issue.get("updatedAt")) or now) >= since:
            out.extend(_transition_drift(client, issue, since, wilson_user_id))
    out.extend(_by_hand_merges(gh, since))
    out.extend(_stale_conflicts(gh, now))
    return out


def _review_drift(gh, ident: str, prs: list[int]) -> list[Finding]:
    if not prs:
        return [Finding(REVIEW, ident, "no PR linked")]
    states: dict[int, str] = {}
    for number in prs:
        try:
            states[number] = gh.pull_state(number)
        except ds.GitHubError as exc:
            return [
                Finding(
                    UNCHECKED,
                    ident,
                    f"PR #{number} state unavailable: {exc}",
                    f"pr-state #{number}",
                )
            ]
    if "open" in states.values():
        return []
    listed = ", ".join(f"#{n} {s}" for n, s in states.items())
    return [Finding(REVIEW, ident, listed)]


def _transition_drift(
    client: ld.LinearClient, issue: dict, since: datetime, wilson_user_id: str
) -> list[Finding]:
    ident = issue["identifier"]
    try:
        moves = client.state_moves(issue["id"])
    except ld.LinearError as exc:
        return [Finding(UNCHECKED, ident, f"history unavailable: {exc}", "history")]
    out = []
    for move in moves:
        to = (move.get("toState") or {}).get("name")
        at = _parse(move.get("createdAt"))
        if to not in HUMAN_OWNED_STATES or at is None or at < since:
            continue
        actor = move.get("actor") or {}
        bot = move.get("botActor") or {}
        if actor.get("id") and actor["id"] == wilson_user_id:
            continue
        who = (
            actor.get("name")
            or bot.get("name")
            or bot.get("userDisplayName")
            or actor.get("email")
            or "unknown actor (integration)"
        )
        actor_id = actor.get("id") or bot.get("id") or "-"
        frm = (move.get("fromState") or {}).get("name") or "new"
        out.append(
            Finding(
                TRANSITION,
                ident,
                f"{frm} -> {to} by {who} at {_iso(at)}",
                key=f"{to}|{actor_id}|{move.get('createdAt')}",
            )
        )
    return out


def _is_fleet_bot(login: str | None) -> bool:
    return (login or "").lower().removesuffix("[bot]") == FLEET_BOT_LOGIN


def _by_hand_merges(gh, since: datetime) -> list[Finding]:
    try:
        pulls = gh.merged_pulls(_iso(since))
    except ds.GitHubError as exc:
        return [
            Finding(UNCHECKED, "merges", f"merged PRs unavailable: {exc}", "merges")
        ]
    out = []
    for pr in sorted(pulls, key=lambda p: p["number"]):
        if _is_fleet_bot(pr.get("merged_by")):
            continue
        n = pr["number"]
        out.append(
            Finding(
                BY_HAND,
                f"PR #{n}",
                f"{pr.get('title')} (author {pr.get('author') or 'unknown'}, "
                f"merged by {pr.get('merged_by') or 'unknown'})",
                key=f"#{n}|{pr.get('merged_at')}",
            )
        )
    return out


def conflict_since(statuses: list[dict]) -> datetime | None:
    """When `pr-policy` first reported the current conflict: the oldest
    of the unbroken run of conflict statuses at the top of `statuses`
    (newest first). None when the latest `pr-policy` status isn't one."""
    since = None
    for status in statuses:
        if status.get("context") != PR_POLICY_CONTEXT:
            continue
        if PR_CONFLICT_PHRASE not in (status.get("description") or ""):
            break
        since = _parse(status.get("created_at")) or since
    return since


def _stale_conflicts(gh, now: datetime) -> list[Finding]:
    try:
        pulls = gh.open_pulls()
    except ds.GitHubError as exc:
        return [
            Finding(UNCHECKED, "open PRs", f"open PRs unavailable: {exc}", "open-pulls")
        ]
    out = []
    for pr in sorted(pulls, key=lambda p: p["number"]):
        if pr.get("mergeable") is not False:
            continue  # clean, or GitHub hasn't decided: tomorrow's run looks again
        n = pr["number"]
        try:
            since = conflict_since(gh.commit_statuses(pr["head_sha"]))
        except ds.GitHubError as exc:
            out.append(
                Finding(
                    UNCHECKED,
                    f"PR #{n}",
                    f"statuses unavailable: {exc}",
                    f"status #{n}",
                )
            )
            continue
        if since is None:
            out.append(
                Finding(
                    UNCHECKED,
                    f"PR #{n}",
                    "conflicts with main, but pr-policy has not reported it yet",
                    f"conflict #{n}",
                )
            )
        elif now - since > CONFLICT_AFTER:
            out.append(
                Finding(
                    CONFLICTED,
                    f"PR #{n}",
                    f"{pr.get('title')} (author {pr.get('author') or 'unknown'}): "
                    f"in conflict since {_iso(since)}; its author merges main in "
                    "and fixes it",
                    key=f"#{n}|{_iso(since)}",
                )
            )
    return out


def render(
    findings: list[Finding],
    sync_lines: list[str],
    *,
    since: datetime,
    now: datetime,
    run_url: str,
    previous: dict | None,
) -> tuple[str, dict]:
    """(job summary markdown, durable state for the next run)."""
    fp = fingerprint(findings)
    state = {
        "window": {"since": _iso(since), "until": _iso(now)},
        "fingerprint": fp,
        "count": len(findings),
        "run_url": run_url,
    }
    marker = f"{MARKER} {_iso(since)}..{_iso(now)} fp={fp} -->"
    changes = [line for line in sync_lines if "->" in line or "::error" in line]
    if not findings and not changes:
        return f"{marker}\nNo delivery drift since {_iso(since)}.\n", state
    prev = previous or {}
    if prev.get("fingerprint") == fp and not changes:
        state["run_url"] = prev.get("run_url") or run_url
        return (
            f"{marker}\nSame {len(findings)} finding(s) as the last digest "
            f"({state['run_url']}); not repeated.\n",
            state,
        )
    lines = [
        marker,
        "## Delivery drift digest",
        "",
        f"Window: {_iso(since)} to {_iso(now)}. Run: {run_url or 'local'}",
        "Nothing here was moved; each item needs a person to decide.",
        "",
    ]
    if sync_lines:
        lines += ["### Deploy sync replay", "", *[f"- {s}" for s in sync_lines], ""]
    if findings:
        lines += [f"### Drift ({len(findings)})", "", "| Ticket | Finding | Detail |"]
        lines += ["|---|---|---|"]
        lines += [f"| {f.ident} | {_TITLES[f.kind]} | {f.detail} |" for f in findings]
    return "\n".join(lines) + "\n", state


def reconcile(
    gh,
    client: ld.LinearClient,
    *,
    repo: str,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    wilson_user_id: str = DEFAULT_WILSON_USER_ID,
    run_url: str = "",
    apply: bool,
    previous: dict | None = None,
) -> Result:
    result = Result()
    since = now - timedelta(days=window_days)
    report = ds.recover(gh, ld.DeployIssues(client), run_url=run_url, apply=apply)
    result.sync_lines = list(report.lines)
    result.errors += report.errors
    try:
        result.findings = find_drift(
            gh, client, repo=repo, now=now, since=since, wilson_user_id=wilson_user_id
        )
    except ld.LinearError as exc:
        result.errors += 1
        result.summary = f"::error ::Linear unavailable; no drift digest: {exc}\n"
        return result
    result.summary, result.state = render(
        result.findings,
        result.sync_lines,
        since=since,
        now=now,
        run_url=run_url,
        previous=previous,
    )
    return result
