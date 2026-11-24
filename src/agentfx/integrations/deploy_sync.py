"""Deploy ticket sync: Linear status from the successful deployment range (AFX-122).

After every Deploy run (success, failure or skip), the tickets of the PRs
merged between the last successful `deployed/staging` SHA (the baseline) and
the attempted SHA are moved:

* every component succeeded or was skipped (docs-only) -> Done + staging link;
* any backend / frontend / verify failure -> Blocked + `needs-wilson` + one
  comment naming the failed part, the run URL and "Re-run failed jobs";
* cancelled or unknown outcome -> no change.

A failed deploy leaves the tag where it was, so the next good deploy (or the
re-run) covers the same tickets again and moves them to Done.

`record()` runs before Deploy moves the tag. It persists the baseline as tag
`deploy-sync/baseline/<attempted>`, so a sync that was missed after the tag
moved can still find its range (`recover()`). With no `deployed/staging` tag
(first deploy) only the attempted commit's own PR is synced; no historical
range is guessed.

One ticket per PR, from the PR title and head branch only (never the body).
A PR naming two different tickets is skipped (fail closed).

Stdlib only; GitHub through the `gh` CLI, Linear through `linear_delivery`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from agentfx.integrations.linear_delivery import LinearError

STAGING_TAG = "deployed/staging"
RECORD_PREFIX = "deploy-sync/baseline/"
SYNCED_PREFIX = "deploy-sync/synced/"
STAGING_URL = "https://agentfx-staging.up.railway.app"
NEEDS_WILSON = "needs-wilson"

COMPONENTS = ("backend", "frontend", "verify")
COMPONENT_NAMES = {
    "backend": "backend (Railway)",
    "frontend": "frontend (Vercel)",
    "verify": "staging verification",
}

LABEL_ADDED_MARKER = f"<!-- afx-deploy-sync:label-added {NEEDS_WILSON} -->"
LABEL_CLEARED_MARKER = f"<!-- afx-deploy-sync:label-cleared {NEEDS_WILSON} -->"
_FOOTER = "_Posted by `afx deploy-sync` (AFX-122)._"
_AFX_RE = re.compile(r"\bAFX-(\d+)\b", re.IGNORECASE)
_PAGE = 100


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BaselineUnknownError(RuntimeError):
    """The tag already points at the attempted SHA and no record exists."""


class GitHub(Protocol):
    def get_tag_sha(self, name: str) -> str | None: ...
    def create_tag_ref(self, name: str, sha: str) -> None: ...
    def delete_tag_ref(self, name: str) -> None: ...
    def list_tag_refs(self, prefix: str) -> dict[str, str]: ...
    def compare(self, base: str, head: str) -> tuple[str, list[str]]: ...
    def commit_pulls(self, sha: str) -> list[dict]: ...


def record_tag(attempted: str) -> str:
    return f"{RECORD_PREFIX}{attempted}"


def synced_tag(deployed: str) -> str:
    return f"{SYNCED_PREFIX}{deployed}"


_DONE_PREFIX = "<!-- afx-deploy-sync:done "


def done_marker(sha: str) -> str:
    return f"{_DONE_PREFIX}{sha} -->"


def blocked_marker(sha: str) -> str:
    return f"<!-- afx-deploy-sync:blocked {sha} -->"


# ── verdict ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    kind: str  # "success" | "failure" | "indeterminate"
    failed: tuple[str, ...] = ()
    detail: str = ""


def classify(outcomes: dict[str, str]) -> Verdict:
    """Explicit per-component outcomes -> verdict. `skipped` counts as success
    (docs-only or unchanged component); anything but success/skipped/failure
    (cancelled, empty) is indeterminate and changes no ticket."""
    values = {c: (outcomes.get(c) or "").strip().lower() for c in COMPONENTS}
    failed = tuple(c for c in COMPONENTS if values[c] == "failure")
    if failed:
        return Verdict("failure", failed)
    unclear = [
        f"{c}={values[c] or 'missing'}"
        for c in COMPONENTS
        if values[c] not in ("success", "skipped")
    ]
    if unclear:
        return Verdict("indeterminate", detail=", ".join(unclear))
    return Verdict("success")


# ── PR -> ticket ─────────────────────────────────────────────────────


def _afx_ids(text: str) -> list[str]:
    seen: list[str] = []
    for m in _AFX_RE.finditer(text or ""):
        ident = f"AFX-{int(m.group(1))}"
        if ident not in seen:
            seen.append(ident)
    return seen


def ticket_for_pr(pr: dict) -> tuple[str | None, str]:
    """The one ticket named by the PR title and head branch, else (None, why)."""
    ids = _afx_ids(pr.get("title") or "")
    for ident in _afx_ids((pr.get("head") or {}).get("ref") or ""):
        if ident not in ids:
            ids.append(ident)
    if len(ids) == 1:
        return ids[0], ""
    if not ids:
        return None, "no AFX id in title or branch"
    return None, f"ambiguous: names {', '.join(ids)}"


def collect_tickets(
    gh: GitHub, commits: list[str]
) -> tuple[dict[str, list[int]], list[str]]:
    """Tickets of the PRs whose merge commit is one of `commits`."""
    in_range = set(commits)
    tickets: dict[str, list[int]] = {}
    skipped: list[str] = []
    seen: set[int] = set()
    for sha in commits:
        for pr in gh.commit_pulls(sha):
            number = pr["number"]
            if number in seen or not pr.get("merged_at"):
                continue
            if pr.get("merge_commit_sha") not in in_range:
                continue
            seen.add(number)
            ticket, reason = ticket_for_pr(pr)
            if ticket is None:
                skipped.append(f"PR #{number} skipped: {reason}")
                continue
            tickets.setdefault(ticket, []).append(number)
    return tickets, skipped


# ── range ────────────────────────────────────────────────────────────


def resolve_baseline(
    gh: GitHub,
    attempted: str,
    explicit: str | None = None,
    *,
    extend: bool = False,
    previous: str | None = None,
) -> tuple[str | None, str, list[str], list[str]]:
    """(baseline, how it was found, records this range covers, extra commits).

    A deploy whose success sync completed gets a `synced_tag`. With `extend`
    (success only) a baseline that still has a record but no synced tag is an
    earlier deploy whose sync was missed: the range is extended back through
    it. A synced baseline stops the extension, so its tickets (possibly
    reopened since) are left alone. A failure never extends, so shipped work
    is never Blocked.
    A self-pointing record is a first deploy: its own commit is the extra.
    `previous` is the SHA this run's tag move replaced: the baseline when the
    tag already points at `attempted` and no record exists (dry-run never
    writes one, and a failed record is tolerated)."""
    if explicit:
        return explicit, "explicit --baseline", [], []
    recorded = gh.get_tag_sha(record_tag(attempted))
    if recorded == attempted:
        return None, "first deploy (recorded)", [], []
    if recorded:
        base, source = recorded, f"recorded {record_tag(attempted)}"
    else:
        tag = gh.get_tag_sha(STAGING_TAG)
        if tag is None:
            return None, f"first deploy: no {STAGING_TAG} tag", [], []
        if tag == attempted and previous and previous != attempted:
            base, source = previous, f"previous {STAGING_TAG} from this run's tag move"
        elif tag == attempted:
            raise BaselineUnknownError(
                f"{STAGING_TAG} already points at {attempted[:7]} and no "
                f"{record_tag(attempted)} record exists, so the previous successful "
                "SHA is unknown. No ticket was changed. Re-run with --baseline <sha>."
            )
        else:
            base, source = tag, f"{STAGING_TAG} tag"
    covered: list[str] = []
    extra: list[str] = []
    while extend and base not in covered:
        if gh.get_tag_sha(synced_tag(base)):
            break
        earlier = gh.get_tag_sha(record_tag(base))
        if not earlier:
            break
        covered.append(base)
        source += f", extended over {base[:7]}"
        if earlier == base:
            extra.append(base)
            break
        base = earlier
    return base, source, covered, extra


def range_commits(gh: GitHub, baseline: str | None, attempted: str) -> list[str]:
    if baseline is None:
        return [attempted]
    status, commits = gh.compare(baseline, attempted)
    if status in ("identical", "behind"):
        return []
    if status != "ahead":
        raise GitHubError(
            f"{attempted[:7]} is {status} relative to baseline {baseline[:7]}; "
            "refusing to guess the deploy range"
        )
    return commits


# ── report ───────────────────────────────────────────────────────────


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    errors: int = 0

    def info(self, line: str) -> None:
        self.lines.append(line)

    def warn(self, line: str) -> None:
        self.lines.append(f"::warning ::{line}")

    def error(self, line: str) -> None:
        self.errors += 1
        self.lines.append(f"::error ::{line}")

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0


# ── comments ─────────────────────────────────────────────────────────


def _prs(numbers: list[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers)


def success_comment(
    sha: str, prs: list[int], *, staging_url: str, run_url: str, cleared: bool
) -> str:
    lines = [
        f"Deployed to paper staging: commit `{sha[:7]}` (PR {_prs(prs)}) is live.",
        "",
        f"Staging: {staging_url}",
        f"Deploy run: {run_url}",
        "",
        done_marker(sha),
    ]
    if cleared:
        lines.append(LABEL_CLEARED_MARKER)
    return "\n".join([*lines, _FOOTER])


def failure_comment(
    sha: str, prs: list[int], verdict: Verdict, *, run_url: str, claim: bool
) -> str:
    parts = ", ".join(COMPONENT_NAMES[c] for c in verdict.failed)
    lines = [
        f"Deploy of commit `{sha[:7]}` (PR {_prs(prs)}) to paper staging "
        f"**failed** in: {parts}. Moved to Blocked.",
        "",
        f"Fix: open the deploy run and press **Re-run failed jobs**: {run_url}",
        "If it fails the same way, the change itself is at fault: remove "
        f"`{NEEDS_WILSON}` and move this ticket to Ready so an agent can open a "
        "fix PR naming it.",
        "When a re-run or a later deploy succeeds, this ticket moves to Done "
        "automatically. No hand edits needed.",
        "",
        blocked_marker(sha),
    ]
    if claim:
        lines.append(LABEL_ADDED_MARKER)
    return "\n".join([*lines, _FOOTER])


def _owns_label(bodies: list[str]) -> bool:
    added = sum(b.count(LABEL_ADDED_MARKER) for b in bodies)
    cleared = sum(b.count(LABEL_CLEARED_MARKER) for b in bodies)
    return added > cleared


# ── per-ticket transitions ───────────────────────────────────────────


def _apply_success(
    linear, issue, ident, prs, *, sha, staging_url, run_url, apply, report
) -> None:
    state = issue["state"]
    if state.get("type") == "canceled":
        report.info(f"{ident}: {state['name']} (canceled); left alone")
        return
    if state.get("type") not in ("started", "completed"):
        report.info(f"{ident}: {state['name']} is not in delivery; left alone")
        return
    bodies = linear.comment_bodies(issue)
    owned = _owns_label(bodies)
    if (
        state.get("type") == "completed"
        and not owned
        and any(_DONE_PREFIX in b for b in bodies)
    ):
        report.info(f"{ident}: already reported Done")
        return
    if state.get("type") == "started" and any(done_marker(sha) in b for b in bodies):
        # Reported Done for this deploy, then reopened by a person: a
        # re-run or reconcile replay of the same deploy must not re-close it.
        report.info(f"{ident}: reopened after this deploy's sync; left alone")
        return
    if not apply:
        report.info(
            f"{ident}: would move {state['name']} -> Done"
            + (f", clear {NEEDS_WILSON}" if owned else "")
        )
        return
    if state.get("type") == "started" and linear.set_state(issue, "Done"):
        report.info(f"{ident}: {state['name']} -> Done")
    if owned and linear.remove_label(issue, NEEDS_WILSON):
        report.info(f"{ident}: cleared {NEEDS_WILSON} (added by deploy sync)")
    body = success_comment(
        sha, prs, staging_url=staging_url, run_url=run_url, cleared=owned
    )
    if linear.comment_once(issue, body, done_marker(sha)):
        report.info(f"{ident}: commented")


def _apply_failure(
    linear, issue, ident, prs, *, sha, verdict, run_url, apply, report
) -> None:
    state = issue["state"]
    if state.get("type") != "started":
        report.info(f"{ident}: {state['name']}; not moved to Blocked")
        return
    marker = blocked_marker(sha)
    existing = [b for b in linear.comment_bodies(issue) if marker in b]
    claim = (
        LABEL_ADDED_MARKER in existing[0]
        if existing
        else not linear.has_label(issue, NEEDS_WILSON)
    )
    if not apply:
        report.info(
            f"{ident}: would move {state['name']} -> Blocked"
            + (f", add {NEEDS_WILSON}" if claim else "")
        )
        return
    if linear.set_state(issue, "Blocked"):
        report.info(f"{ident}: {state['name']} -> Blocked")
    body = failure_comment(sha, prs, verdict, run_url=run_url, claim=claim)
    if linear.comment_once(issue, body, marker):
        report.info(f"{ident}: commented")
    if claim and linear.add_label(issue, NEEDS_WILSON):
        report.info(f"{ident}: added {NEEDS_WILSON}")


def _ticket_key(ident: str) -> int:
    return int(ident.split("-")[1])


# ── entry points ─────────────────────────────────────────────────────


def record(gh: GitHub, attempted: str, *, apply: bool) -> Report:
    """Persist the baseline for `attempted` before Deploy can move the tag."""
    report = Report()
    try:
        existing = gh.get_tag_sha(record_tag(attempted))
        if existing:
            report.info(f"baseline already recorded: {existing[:7]}..{attempted[:7]}")
            return report
        tag = gh.get_tag_sha(STAGING_TAG)
        if tag is None and not apply:
            report.info(f"dry-run: would record first deploy {record_tag(attempted)}")
        elif tag is None:
            # Points at itself: "no earlier deploy", so a tag created before
            # sync still resolves to just this commit's PR.
            gh.create_tag_ref(record_tag(attempted), attempted)
            report.info(f"first deploy: recorded {record_tag(attempted)}")
        elif tag == attempted:
            report.warn(
                f"{STAGING_TAG} already at {attempted[:7]}; not recording an empty range"
            )
        elif not apply:
            report.info(f"dry-run: would record {record_tag(attempted)} -> {tag[:7]}")
        else:
            gh.create_tag_ref(record_tag(attempted), tag)
            report.info(f"recorded {record_tag(attempted)} -> {tag[:7]}")
    except GitHubError as exc:
        report.error(f"GitHub API failed while recording the baseline: {exc}")
    return report


def sync(
    gh: GitHub,
    linear,
    *,
    attempted: str,
    verdict: Verdict,
    run_url: str,
    staging_url: str = STAGING_URL,
    apply: bool,
    baseline: str | None = None,
    previous: str | None = None,
) -> Report:
    """Move the range's tickets for this deploy verdict. Safe to re-run."""
    report = Report()
    if verdict.kind == "indeterminate":
        report.info(f"deploy outcome not final ({verdict.detail}); no ticket changed")
        return report
    try:
        base, source, covered, extra = resolve_baseline(
            gh,
            attempted,
            baseline,
            extend=verdict.kind == "success",
            previous=previous,
        )
        commits = extra + range_commits(gh, base, attempted)
        tickets, skipped = collect_tickets(gh, commits) if commits else ({}, [])
    except BaselineUnknownError as exc:
        report.error(str(exc))
        return report
    except GitHubError as exc:
        report.error(f"GitHub API failed; no ticket changed: {exc}")
        return report

    span = f"{base[:7]}..{attempted[:7]}" if base else attempted[:7]
    report.info(
        f"deploy {verdict.kind} for {span} ({source}); {len(commits)} commit(s)"
    )
    for line in skipped:
        report.warn(line)
    if not tickets:
        report.info("no merged PRs with a ticket in range; nothing to do")
    for ident in sorted(tickets, key=_ticket_key):
        prs = tickets[ident]
        if linear is None:
            report.info(
                f"{ident} (PR {_prs(prs)}): would sync as {verdict.kind}; no Linear key"
            )
            continue
        try:
            issue = linear.issue(ident)
            if issue is None:
                report.warn(f"{ident} (PR {_prs(prs)}): not found in Linear; skipped")
                continue
            if verdict.kind == "success":
                _apply_success(
                    linear,
                    issue,
                    ident,
                    prs,
                    sha=attempted,
                    staging_url=staging_url,
                    run_url=run_url,
                    apply=apply,
                    report=report,
                )
            else:
                _apply_failure(
                    linear,
                    issue,
                    ident,
                    prs,
                    sha=attempted,
                    verdict=verdict,
                    run_url=run_url,
                    apply=apply,
                    report=report,
                )
        except LinearError as exc:
            report.error(f"{ident}: Linear API failed: {exc}")

    if apply and verdict.kind == "success" and not report.errors:
        _mark_synced(gh, attempted, report)
        done = {*commits, *covered, *([base] if base else [])}
        _prune_records(gh, attempted, done, report)
    return report


def _mark_synced(gh: GitHub, attempted: str, report: Report) -> None:
    try:
        if not gh.get_tag_sha(synced_tag(attempted)):
            gh.create_tag_ref(synced_tag(attempted), attempted)
            report.info(f"marked {synced_tag(attempted)}")
    except GitHubError as exc:
        report.warn(f"could not mark {attempted[:7]} synced: {exc}")


def _prune_records(
    gh: GitHub, attempted: str, deployed: set[str], report: Report
) -> None:
    """Drop records and synced tags of deploys this success covered (failed
    attempts in the range, earlier deploys it extended over, the baseline);
    keep the current ones so a duplicate run or reconcile can replay it."""
    try:
        for prefix in (RECORD_PREFIX, SYNCED_PREFIX):
            for name in gh.list_tag_refs(prefix):
                sha = name[len(prefix) :]
                if sha != attempted and sha in deployed:
                    gh.delete_tag_ref(name)
                    report.info(f"pruned {name}")
    except GitHubError as exc:
        report.warn(f"could not prune old baseline records: {exc}")


def recover(
    gh: GitHub, linear, *, run_url: str, staging_url: str = STAGING_URL, apply: bool
) -> Report:
    """Replay the success sync for the current tag from its recorded baseline."""
    try:
        tag = gh.get_tag_sha(STAGING_TAG)
        recorded = gh.get_tag_sha(record_tag(tag)) if tag else None
    except GitHubError as exc:
        report = Report()
        report.error(f"GitHub API failed: {exc}")
        return report
    if not tag or not recorded:
        report = Report()
        report.info(f"no recorded range for {STAGING_TAG}; nothing to recover")
        return report
    return sync(
        gh,
        linear,
        attempted=tag,
        verdict=Verdict("success"),
        run_url=run_url,
        staging_url=staging_url,
        apply=apply,
    )


# ── gh CLI client ────────────────────────────────────────────────────


def _gh_runner(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        status = (
            404 if "HTTP 404" in proc.stderr or "Not Found" in proc.stderr else None
        )
        raise GitHubError(proc.stderr.strip() or f"gh exited {proc.returncode}", status)
    return proc.stdout


class GhCli:
    """GitHub REST through `gh api` (auth from `GH_TOKEN`)."""

    def __init__(
        self, repo: str, *, runner: Callable[[list[str]], str] = _gh_runner
    ) -> None:
        self.repo = repo
        self._run = runner

    def _get(self, path: str):
        try:
            out = self._run(["api", f"repos/{self.repo}/{path}"])
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        try:
            return json.loads(out)
        except ValueError as exc:
            raise GitHubError(f"non-json from gh api {path}") from exc

    def get_tag_sha(self, name: str) -> str | None:
        ref = self._get(f"git/ref/tags/{name}")
        if not ref:
            return None
        obj = ref["object"]
        if obj.get("type") == "tag":
            tag = self._get(f"git/tags/{obj['sha']}")
            if not tag:
                raise GitHubError(f"annotated tag {name} vanished")
            obj = tag["object"]
        return obj["sha"]

    def create_tag_ref(self, name: str, sha: str) -> None:
        self._run(
            [
                "api",
                "-X",
                "POST",
                "-f",
                f"ref=refs/tags/{name}",
                "-f",
                f"sha={sha}",
                f"repos/{self.repo}/git/refs",
            ]
        )

    def delete_tag_ref(self, name: str) -> None:
        self._run(["api", "-X", "DELETE", f"repos/{self.repo}/git/refs/tags/{name}"])

    def list_tag_refs(self, prefix: str) -> dict[str, str]:
        refs: dict[str, str] = {}
        page = 1
        while True:
            items = (
                self._get(
                    f"git/matching-refs/tags/{prefix}?per_page={_PAGE}&page={page}"
                )
                or []
            )
            for item in items:
                refs[item["ref"].removeprefix("refs/tags/")] = item["object"]["sha"]
            if len(items) < _PAGE:
                return refs
            page += 1

    def compare(self, base: str, head: str) -> tuple[str, list[str]]:
        commits: list[str] = []
        status = ""
        page = 1
        while True:
            data = self._get(f"compare/{base}...{head}?per_page={_PAGE}&page={page}")
            if data is None:
                raise GitHubError(f"compare {base[:7]}...{head[:7]} not found")
            status = data.get("status", status)
            batch = [c["sha"] for c in data.get("commits") or []]
            commits += batch
            if not batch or len(commits) >= int(data.get("total_commits") or 0):
                return status, commits
            page += 1

    def pull_state(self, number: int) -> str:
        """`open`, `merged` or `closed` (closed without merging)."""
        pr = self._get(f"pulls/{number}")
        if pr is None:
            raise GitHubError(f"PR #{number} not found", 404)
        if pr.get("state") == "open":
            return "open"
        return "merged" if pr.get("merged_at") else "closed"

    def commit_pulls(self, sha: str) -> list[dict]:
        pulls: list[dict] = []
        page = 1
        while True:
            batch = self._get(f"commits/{sha}/pulls?per_page={_PAGE}&page={page}") or []
            pulls += batch
            if len(batch) < _PAGE:
                return pulls
            page += 1
