"""Platform Engineer Agent — Linear-driven dispatch (2026-11-01).

Pre-rewrite, PE Phase 1 read QA `agent_decisions` from the DB, used
Opus 5 to generate fix proposals, and Phase 2 dispatched HIGH-
confidence proposals to a Managed Agents workspace. That design had
three problems:

1. **Not Linear-aware.** Wilson promotes Triage → Ready in Linear
   expecting PE to act, but the DB-driven path never looked at
   Linear state. CLAUDE.md's rule "agents work Ready only" was
   enforced nowhere in dispatch.
2. **Opus 5 proposals were redundant.** The Managed Agents session
   does its own code reasoning inside the sandbox. Pre-generating a
   proposal duplicated that work and introduced a stranded-proposal
   failure mode (same-day filter orphaned any proposal that didn't
   dispatch the day it was created).
3. **Daily deploy churn.** The 24h scan cadence reset every deploy,
   so the only scan that ever fired was the one on boot.

The rewrite: Linear is the authoritative queue, the DB
(`agent_workspace_runs`) is just a lifecycle cache. Flow:

    QA finds divergence → files Linear ticket (Triage, agent-filed)
                              ↓
              Wilson promotes Triage → Ready
                              ↓
       PE scan tick lists Ready issues via linear_pe_queue
                              ↓
    For each candidate (up to PE_LINEAR_MAX_DISPATCH_PER_SCAN):
      - Retry gate: ≥2 prior WORKSPACE_FAILED rows for this issue
        with matching signatures → file_for_attention + skip
      - Else: transition Ready → In Progress (atomic lock),
        insert AgentWorkspaceRun(PENDING, linear_issue_identifier=…),
        dispatch via Managed Agents adapter.
      - On WORKSPACE_FAILED: rollback_to_ready so next tick retries.
      - On RUNNING: Linear's own GitHub integration moves the issue
        to In Review once a PR opens; poll loop detects the title
        prefix and `[NEEDS DESIGN]` triggers `flag_needs_design`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from agentfx.agents.fleet.linear_pe_queue import (
    ReadyIssue,
    file_for_attention,
    list_ready_dispatch_candidates,
    rollback_to_ready,
    should_file_for_attention,
    transition_to_in_progress,
)
from agentfx.api.core.config import settings
from agentfx.api.models.agent_workspace_run import (
    AgentWorkspaceRun,
    AgentWorkspaceRunStatus,
)

logger = logging.getLogger(__name__)


# The dispatch budget cap lives on the adapter
# (`AGENT_PE_WORKSPACE_MAX_PER_DAY`) and is enforced by
# `check_budget_before_dispatch`. The scan loop uses that single
# source of truth so Scan and Adapter can never disagree.
#
# Legacy constant kept here only for pre-rewrite tests that read it.
# All real decisions go through `check_budget_before_dispatch`.
_MAX_PE_DISPATCHES_PER_DAY = 5


# ─────────────────────────────────────────────────────────────────────
# Public entry point — orchestrator calls this every 15 min.
# ─────────────────────────────────────────────────────────────────────


def scan_and_dispatch(*, db: Session, now: datetime | None = None) -> tuple[int, int]:
    """Scan Linear for Ready AFX issues the fleet filed, apply the
    retry gate, dispatch eligible ones to the Managed Agents
    workspace. Never raises.

    Returns `(0, attempted)` — the first element exists only so the
    caller interface stays stable with the old Phase-1 (produced,
    attempted) signature. There is no "produced" count anymore;
    Opus-generated proposals went away with Phase 1.
    """
    now = now or datetime.now(UTC)

    # Lazy imports — the workspace modules shouldn't be imported
    # unless a dispatch is actually attempted (keeps the orchestrator
    # import graph light).
    from agentfx.agents.fleet.pe_workspace import (
        build_task_slug,
        check_budget_before_dispatch,
        resolve_adapter,
        sweep_stuck_runs,
    )

    # Pre-flight budget check — if the adapter would refuse every
    # candidate anyway, don't touch Linear. Prevents the loop Wilson
    # observed on 2026-11-02: with cap=2 already hit, every scan tick
    # was transitioning Ready → In Progress → rollback → comment, for
    # every eligible issue, every 15 min, with no useful work done.
    sweep_stuck_runs(db, now=now)
    ok, reason = check_budget_before_dispatch(db, now=now)
    if not ok:
        logger.info(
            "pe_agent: adapter budget exhausted (%s) — skipping scan tick, "
            "no Linear state changed",
            reason,
        )
        return (0, 0)

    try:
        max_per_scan = getattr(settings, "PE_LINEAR_MAX_DISPATCH_PER_SCAN", 3)
        candidates = list_ready_dispatch_candidates(limit=max_per_scan)
    except Exception:
        logger.exception("pe_agent: Linear candidate scan failed")
        return (0, 0)

    if not candidates:
        return (0, 0)

    adapter = resolve_adapter()
    attempted = 0

    for issue in candidates:
        try:
            # Per-candidate re-check: a successful dispatch earlier in
            # this scan may have consumed the last budget slot, and
            # the adapter will still refuse at count = cap. We stop
            # the loop cleanly rather than transition-and-rollback.
            ok, reason = check_budget_before_dispatch(db, now=now)
            if not ok:
                logger.info(
                    "pe_agent: budget consumed mid-scan (%s) — stopping",
                    reason,
                )
                break
            if _should_skip_due_to_retry_gate(db, issue):
                continue
            if not transition_to_in_progress(issue.id):
                logger.info(
                    "pe_agent: skipping %s — transition_to_in_progress failed "
                    "(race or Linear error). Next scan retries.",
                    issue.identifier,
                )
                continue
            spec = _build_task_spec(issue=issue, slug_fn=build_task_slug, now=now)
            result = adapter.dispatch(db, spec=spec, now=now)
            attempted += 1
            if not result.accepted:
                # Shouldn't fire for budget any more (checked above),
                # but keep the rollback for other refusal reasons
                # (config missing, adapter unavailable). Still safe.
                logger.info(
                    "pe_agent: adapter refused dispatch for %s — %s. "
                    "Rolling Linear back to Ready.",
                    issue.identifier,
                    result.reason,
                )
                rollback_to_ready(
                    issue.id,
                    reason_summary=f"adapter_refused: {result.reason}",
                )
                continue
            # The adapter writes the row's status (RUNNING on success,
            # WORKSPACE_FAILED on managed-agents SDK failure). Both
            # outcomes return accepted=True — distinguish by status.
            if result.workspace_run_id:
                row = (
                    db.query(AgentWorkspaceRun)
                    .filter(AgentWorkspaceRun.id == result.workspace_run_id)
                    .first()
                )
                if (
                    row is not None
                    and row.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED
                ):
                    rollback_to_ready(
                        issue.id,
                        reason_summary=(row.summary or "")[:400],
                    )
                    logger.info(
                        "pe_agent: dispatch for %s hit WORKSPACE_FAILED (%s); "
                        "rolled back to Ready.",
                        issue.identifier,
                        result.reason,
                    )
                    continue
            logger.info(
                "pe_agent: dispatched Linear %s to workspace "
                "(workspace_run_id=%s, agent_branch=%s)",
                issue.identifier,
                result.workspace_run_id,
                result.agent_branch,
            )
        except Exception:
            logger.exception(
                "pe_agent: dispatch failed unexpectedly for %s", issue.identifier
            )
    return (0, attempted)


# ─────────────────────────────────────────────────────────────────────
# Retry gate
# ─────────────────────────────────────────────────────────────────────


_RETRY_GATE_WINDOW_DAYS = 7


def _should_skip_due_to_retry_gate(db: Session, issue: ReadyIssue) -> bool:
    """Apply Wilson's "try twice; file for attention on same-signature
    failures" rule.

    Reads prior AgentWorkspaceRun rows for this Linear issue, filters
    to WORKSPACE_FAILED ones within the last 7 days, newest first. If
    `should_file_for_attention` says the latest two share a signature,
    flag the issue on Linear with `needs-wilson` + comment and return
    True (skip dispatch).

    **7-day window (2026-11-08)**: a global condition like an Anthropic
    monthly cap can mark every attempt WORKSPACE_FAILED with identical
    signatures. Old failures past the window don't gate NEW attempts
    once the cap clears — so when Wilson removes `needs-wilson`, the
    issue gets a fresh two tries instead of inheriting weeks of
    cap-shaped history. Autonomous recovery when quota returns Dec 1.

    Returns False if the issue should proceed to dispatch.
    """
    window_start = datetime.now(UTC) - timedelta(days=_RETRY_GATE_WINDOW_DAYS)
    prior = (
        db.query(AgentWorkspaceRun)
        .filter(AgentWorkspaceRun.linear_issue_identifier == issue.identifier)
        .filter(AgentWorkspaceRun.status == AgentWorkspaceRunStatus.WORKSPACE_FAILED)
        .filter(AgentWorkspaceRun.created_at >= window_start)
        .order_by(AgentWorkspaceRun.created_at.desc())
        .limit(10)
        .all()
    )
    summaries = [r.summary or "" for r in prior]
    if should_file_for_attention(summaries):
        logger.warning(
            "pe_agent: retry gate — %s has 2 same-signature "
            "WORKSPACE_FAILED attempts; filing for attention.",
            issue.identifier,
        )
        file_for_attention(issue.id, signatures=summaries[:2])
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Dispatch spec from a Linear issue
# ─────────────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """Repo-safe slug: lowercase, hyphen-separated, ASCII only, max
    60 chars. Branch names must be POSIX-friendly."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "pe-task"


def _build_task_spec(
    *,
    issue: ReadyIssue,
    slug_fn: Any,
    now: datetime,
) -> Any:
    """Construct the `WorkspaceTaskSpec` from a Linear issue.

    The dispatch spec carries:
      - `task_slug` — `afx-<identifier>-<title-slug>`, used for the
        agent-side branch name.
      - `pe_proposal_decision_key` — the Linear identifier (e.g.
        AFX-56). The field name is retained for backward compat with
        the AgentWorkspaceRun column; its meaning is now "what
        triggered the dispatch" (previously a Phase-1 proposal id).
      - `linear_issue_identifier` — explicitly the AFX-NN string, so
        the row and the retry gate can group attempts by issue.
      - `narrative` — rendered Linear description + branch-gate
        instructions + `[NEEDS DESIGN]` escape hatch.
    """
    from agentfx.agents.fleet.pe_workspace import WorkspaceTaskSpec

    title_slug = _slugify(issue.title)
    task_slug = slug_fn(f"{issue.identifier.lower()}-{title_slug}", now=now)
    narrative = _render_dispatch_narrative(issue=issue)
    return WorkspaceTaskSpec(
        task_slug=task_slug,
        pe_proposal_decision_key=issue.identifier,  # Linear id in the legacy column
        linear_issue_identifier=issue.identifier,
        title=issue.title,
        narrative=narrative,
        target_files=[],  # the session discovers these — no up-front proposal
        new_test_paths=[],
        estimated_profit_impact="",
    )


def _render_dispatch_narrative(*, issue: ReadyIssue) -> str:
    """Workspace prompt. Includes:
    - The Linear issue identifier, title, body
    - Branch gate (never push to main)
    - Risk-aware instructions (Money-path → needs-wilson label on PR)
    - `[NEEDS DESIGN]` escape hatch for ambiguous requirements
    """
    lines: list[str] = []
    lines.append(f"# {issue.identifier}: {issue.title}")
    lines.append("")
    lines.append(
        "You are the Platform Engineer workspace. This is a Linear "
        "ticket Wilson has approved for autonomous work (promoted to "
        "`Ready`, carries the `agent-filed` label). Deliver a PR."
    )
    lines.append("")
    if issue.description.strip():
        lines.append("## Ticket description")
        lines.append(issue.description)
        lines.append("")
    else:
        lines.append("## Ticket description")
        lines.append(
            "_(The ticket has no description. Infer from the title "
            "or ask for a design decision — see **Escape hatch** below.)_"
        )
        lines.append("")

    lines.append(f"**Linear URL:** {issue.url}")
    lines.append(f"**Labels:** {', '.join(sorted(issue.labels)) or '(none)'}")
    lines.append("")

    lines.append("## Branch gate (NEVER violate)")
    lines.append("")
    lines.append(
        "- You work on a sub-branch named `agent/<task-slug>` off "
        "`feat/agent-fleet`.\n"
        "- **Never push to `main`.** Wilson gates `feat/agent-fleet` → "
        "`main` manually.\n"
        "- Your PR targets `feat/agent-fleet`, never `main`."
    )
    lines.append("")

    if issue.is_money_path:
        lines.append("## ⚠ Money-path risk")
        lines.append("")
        lines.append(
            "- This ticket has the **Risk: Money-path** label (order "
            "execution, sizing, kill switch, credentials, deploy gates).\n"
            "- Add the `needs-wilson` label to your PR "
            "(`gh pr edit --add-label needs-wilson`). The CI "
            "auto-merge workflow will NOT merge a `needs-wilson` PR; "
            "Wilson reviews and merges by hand."
        )
        lines.append("")

    lines.append("## Workflow")
    lines.append("")
    lines.append(
        f"1. Create branch `agent/{_slugify(issue.identifier)}-"
        f"{_slugify(issue.title)}` off `feat/agent-fleet`.\n"
        "2. Read the relevant code; form a hypothesis.\n"
        "3. Write a reproducing test FIRST (it should fail against current code).\n"
        "4. Apply the fix.\n"
        "5. Run `make test-unit` — iterate until green.\n"
        f"6. Commit with `{issue.identifier}` in the message so Linear links the PR.\n"
        f"7. Push the branch and open a PR titled `{issue.identifier}: <short summary>` "
        f"against `feat/agent-fleet`.\n"
        "8. Add label `agent-authored` so the CI auto-merge workflow runs "
        "(`gh pr edit --add-label agent-authored`). "
        "(If this is a Money-path ticket, add `needs-wilson` INSTEAD.)\n"
        "9. If after 3 attempts tests still fail, STOP and report the "
        "failing output in a PR comment."
    )
    lines.append("")

    lines.extend(_render_publishing_section(issue.identifier))

    lines.append("## Escape hatch — `[NEEDS DESIGN]`")
    lines.append("")
    lines.append(
        "**If this task requires a design decision only Wilson can make** "
        "(ambiguous requirements, missing acceptance criteria, a trade-off "
        "between approaches, no clear right answer): **DO NOT GUESS.** "
        "Instead, open a PR with:\n\n"
        "- Branch: `agent/<task-slug>-design-questions` (empty commit is fine)\n"
        f"- Title: `[NEEDS DESIGN] {issue.identifier}: <summary of the question>`\n"
        "- Body: list each question + the trade-offs you see.\n"
        "- **DO NOT** add the `agent-authored` label (auto-merge won't fire anyway).\n\n"
        "Opening the PR tells the fleet poll loop to label the Linear "
        "issue `needs-wilson` and comment with the PR link so Wilson sees "
        "it in both his GitHub and Linear queues."
    )
    lines.append("")

    return "\n".join(lines)


# Worked example of the AFX-39 / PR #65 publish path: create blobs +
# tree + commit + ref through the GitHub REST API when `git push` is
# refused (Managed Agents sandbox has no git-over-HTTPS creds, AFX-73).
_GH_API_PUBLISH_EXAMPLE = """```bash
REPO=repos/widarecu/agentfx
BRANCH="agent/<task-slug>"          # same branch name you tried to push
BASE_SHA=$(git merge-base HEAD origin/feat/agent-fleet)
BASE_TREE=$(gh api "$REPO/git/commits/$BASE_SHA" --jq .tree.sha)

# One blob per added/modified file, one null-sha entry per deleted file.
echo '[]' > /tmp/tree.json
for f in $(git diff --name-only --diff-filter=AM "$BASE_SHA" HEAD); do
  sha=$(jq -n --arg c "$(base64 -w0 "$f")" '{content: $c, encoding: "base64"}' \\
        | gh api "$REPO/git/blobs" --input - --jq .sha)
  jq --arg p "$f" --arg s "$sha" \\
     '. + [{path: $p, mode: "100644", type: "blob", sha: $s}]' \\
     /tmp/tree.json > /tmp/tree.next && mv /tmp/tree.next /tmp/tree.json
done
for f in $(git diff --name-only --diff-filter=D "$BASE_SHA" HEAD); do
  jq --arg p "$f" '. + [{path: $p, mode: "100644", type: "blob", sha: null}]' \\
     /tmp/tree.json > /tmp/tree.next && mv /tmp/tree.next /tmp/tree.json
done

TREE_SHA=$(jq -n --arg b "$BASE_TREE" --slurpfile t /tmp/tree.json \\
             '{base_tree: $b, tree: $t[0]}' \\
           | gh api "$REPO/git/trees" --input - --jq .sha)
COMMIT_SHA=$(jq -n --arg m "$(git log -1 --format=%B)" --arg t "$TREE_SHA" \\
               --arg p "$BASE_SHA" '{message: $m, tree: $t, parents: [$p]}' \\
             | gh api "$REPO/git/commits" --input - --jq .sha)

# Create the branch ref (if it already exists, PATCH it instead:
#   gh api -X PATCH "$REPO/git/refs/heads/$BRANCH" -f sha="$COMMIT_SHA" -F force=true)
gh api "$REPO/git/refs" -f ref="refs/heads/$BRANCH" -f sha="$COMMIT_SHA"

gh pr create --repo widarecu/agentfx --base feat/agent-fleet --head "$BRANCH" ...
```"""


def _render_publishing_section(identifier: str) -> list[str]:
    """Mandatory publish sequence (AFX-72). AFX-40's agent finished
    the work but couldn't `git push` from the sandbox and gave up
    silently; the commit was lost."""
    return [
        "## Publishing (MANDATORY sequence — do not skip steps)",
        "",
        "The sandbox's git credentials are not reliable. Publish in this "
        "exact order and only move to the next step if the previous one "
        "failed (for any reason):",
        "",
        "1. **First try:** `git push -u origin <branch>`.",
        "2. **On failure:** fall back to the GitHub REST API — "
        "`gh api repos/widarecu/agentfx/git/commits` + "
        "`gh api repos/widarecu/agentfx/git/refs` — to create the commit "
        "and branch ref directly (the pattern AFX-39's agent used in "
        "PR #65), then open the PR with `gh pr create`. Worked example:",
        "",
        _GH_API_PUBLISH_EXAMPLE,
        "",
        "3. **If both fail: do NOT die silently.** Open a `[NEEDS WILSON]` "
        "PR against the current HEAD of `feat/agent-fleet` (an empty-commit "
        "branch off it is fine) titled "
        f"`[NEEDS WILSON] {identifier}: publish failed — <short summary>` "
        f"(keep `{identifier}` in the title so the Linear link survives). "
        "Label it `needs-wilson`, NOT `agent-authored`. The PR body must "
        "contain the full diff (`git format-patch --stdout "
        "origin/feat/agent-fleet..HEAD`) and the commands Wilson needs to "
        "apply it from a normal clone, e.g. `git fetch origin && git "
        "checkout -b <branch> origin/feat/agent-fleet && git am < "
        "change.patch && git push -u origin <branch>`.",
        "4. If even that PR cannot be opened, end your final message with "
        "a line that reads exactly `PUBLISH FAILED AFTER FALLBACKS`, "
        "followed by the full patch and the apply commands, so the PE "
        "poll loop flags the Linear ticket for Wilson.",
        "",
        "Never print, echo, or commit tokens or credential files.",
        "",
    ]


# ─────────────────────────────────────────────────────────────────────
# Daily cap bookkeeping
# ─────────────────────────────────────────────────────────────────────


# Note: the old `_dispatches_remaining_today` helper was removed in
# the 2026-11-02 budget-loop fix. Daily cap enforcement now lives on
# the adapter via `check_budget_before_dispatch`, called upfront in
# `scan_and_dispatch` and re-checked per candidate.


__all__ = [
    "scan_and_dispatch",
]
