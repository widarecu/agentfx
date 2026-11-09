"""Linear-driven work queue for the PE agent (2026-11-01 rewrite).

The PE loop used to read QA `agent_decisions` rows from the DB and
dispatch HIGH-confidence Opus-generated proposals. That was wrong:
Wilson promotes tickets Triage → Ready in Linear expecting the PE
agent to act on them, but the DB-driven path never looked at Linear
state. CLAUDE.md's rule "agents work Ready only" was enforced
nowhere.

Linear is now the queue. The DB (`agent_workspace_runs`) is just a
local cache tracking individual dispatch attempts and their session
lifecycle.

Flow:
  QA finds divergence → files Linear ticket (Triage, agent-filed)
                              ↓
              Wilson promotes Triage → Ready
                              ↓
  PE scan tick lists Ready issues via `list_ready_dispatch_candidates`
                              ↓
  For each candidate (up to PE_LINEAR_MAX_DISPATCH_PER_SCAN):
    1. `transition_to_in_progress` (atomic lock — second scan sees
        In Progress and skips)
    2. insert AgentWorkspaceRun(PENDING, linear_issue_identifier=…)
    3. sessions.create via Managed Agents adapter
    4a. Success → row=RUNNING; session runs; PR opens → Linear's
         own GitHub integration moves issue to In Review.
    4b. Workspace failure → row=WORKSPACE_FAILED; rollback the
         Linear issue to Ready (via `rollback_to_ready`); next
         scan picks it up unless the retry gate stops it.

Retry gate: if an issue has ≥2 WORKSPACE_FAILED rows and the last
two share a failure signature (same error class + message prefix,
ignoring run ids / timestamps), stop retrying — label it
`needs-wilson` and post a comment with both signatures. See
`should_file_for_attention`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from agentfx.api.core.config import settings
from agentfx.integrations.linear_client import _build_client, _gql

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Shapes
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ReadyIssue:
    """One Ready-state Linear issue PE might dispatch to a workspace.

    Everything the dispatcher needs to build a session prompt — no
    GraphQL noise, no label-id → name resolution — is captured here.
    """

    id: str  # Linear internal uuid
    identifier: str  # "AFX-56" (used as the branch slug + PR link)
    title: str
    description: str  # Markdown body as filed; may be empty
    url: str
    priority: int  # 0 (none) → 4 (urgent)
    labels: list[
        str
    ]  # label NAMES (not ids) — e.g. ["agent-filed", "Bug", "Paper runner"]
    created_at: str  # ISO timestamp, for FIFO tie-breaking

    @property
    def is_money_path(self) -> bool:
        """CLAUDE.md: Money-path PRs never auto-merge; add
        `needs-wilson`. The dispatch prompt tells the session this."""
        return "Money-path" in self.labels

    @property
    def is_feature(self) -> bool:
        """Feature-type tickets may need a design decision — the
        prompt tells the session to open a `[NEEDS DESIGN]` PR
        when it can't resolve ambiguity on its own."""
        return "Feature" in self.labels


# ─────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────


def _linear_enabled() -> tuple[bool, str]:
    """Shared gate — returns (ok, reason). The PE scan loop checks
    this before every Linear API call so a missing key doesn't
    spam logs."""
    if not settings.AGENT_LINEAR_ENABLED:
        return False, "AGENT_LINEAR_ENABLED is False"
    if not settings.LINEAR_API_KEY:
        return False, "LINEAR_API_KEY is empty"
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Public API — called by platform_engineer.scan_and_dispatch
# ─────────────────────────────────────────────────────────────────────


def list_ready_dispatch_candidates(limit: int) -> list[ReadyIssue]:
    """Return Ready-state AFX issues that PE should consider for
    dispatch, sorted by priority desc then createdAt asc.

    Filters:
      - team.key == AGENT_LINEAR_TEAM_KEY
      - state.id == PE_LINEAR_READY_STATE_ID
      - label `agent-filed` present (so manually-filed work is
        left alone unless Wilson adds the label himself)
      - label `needs-wilson` absent (any issue blocked on Wilson
        stays off the PE queue until he removes the label)
      - assignee is null (if Wilson assigns himself, PE skips)

    Never raises; on Linear failure returns [].
    """
    ok, reason = _linear_enabled()
    if not ok:
        logger.info("linear_pe_queue: list skipped — %s", reason)
        return []

    client = _build_client(settings.LINEAR_API_KEY)
    # 2026-11-08 — needs-wilson exclusion moved from the GraphQL filter
    # to a Python post-filter. Linear's `labels: { id: { nin: [...] } }`
    # on a nested collection matches issues where AT LEAST ONE label
    # isn't in the exclude list, not issues where NO label is — since
    # every issue has multiple labels, that filter was effectively a
    # no-op, and already-flagged tickets (AFX-50/52/73) kept coming
    # back every 15 min, triggering the retry gate's comment storm.
    # Filtering in Python is semantically obvious and not dependent on
    # Linear's nested-collection filter edge cases.
    needs_wilson_label_id = settings.PE_LINEAR_NEEDS_WILSON_LABEL_ID
    try:
        query = """
        query ListPEReady(
          $teamKey: String!,
          $stateId: ID!,
          $agentFiledLabelId: ID!,
          $limit: Int!
        ) {
          issues(
            first: $limit,
            orderBy: updatedAt,
            filter: {
              team: { key: { eq: $teamKey } },
              state: { id: { eq: $stateId } },
              labels: { id: { eq: $agentFiledLabelId } },
              assignee: { null: true }
            }
          ) {
            nodes {
              id
              identifier
              title
              description
              url
              priority
              createdAt
              labels(first: 50) {
                nodes { id name }
              }
            }
          }
        }
        """
        data = _gql(
            client,
            query,
            {
                "teamKey": settings.AGENT_LINEAR_TEAM_KEY,
                "stateId": settings.PE_LINEAR_READY_STATE_ID,
                "agentFiledLabelId": settings.PE_LINEAR_AGENT_FILED_LABEL_ID,
                "limit": max(
                    1, min(50, limit * 2)
                ),  # over-fetch; we sort+trim client-side
            },
        )
        nodes = ((data.get("issues") or {}).get("nodes")) or []
    except Exception:
        logger.exception("linear_pe_queue: list_ready failed")
        return []

    # Python-side needs-wilson exclusion — see note above.
    def _label_ids(node: dict) -> set[str]:
        return {
            lbl.get("id")
            for lbl in (node.get("labels") or {}).get("nodes") or []
            if lbl.get("id")
        }

    filtered_nodes = [n for n in nodes if needs_wilson_label_id not in _label_ids(n)]
    excluded_count = len(nodes) - len(filtered_nodes)
    if excluded_count:
        logger.info(
            "linear_pe_queue: %d Ready candidate(s) excluded by needs-wilson "
            "label (post-filter).",
            excluded_count,
        )

    issues = [
        ReadyIssue(
            id=n["id"],
            identifier=n["identifier"],
            title=n["title"] or "",
            description=n["description"] or "",
            url=n["url"],
            priority=n.get("priority") or 0,
            labels=[lbl["name"] for lbl in (n.get("labels") or {}).get("nodes") or []],
            created_at=n["createdAt"],
        )
        for n in filtered_nodes
    ]

    # Priority: 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low.
    # Sort so urgent (1) comes first, then high (2), ...; 0 is treated
    # as the lowest priority (end of list).
    def _sort_key(iss: ReadyIssue) -> tuple[int, str]:
        prio_rank = iss.priority if iss.priority > 0 else 99
        return (prio_rank, iss.created_at)

    issues.sort(key=_sort_key)
    return issues[:limit]


def transition_to_in_progress(issue_id: str) -> bool:
    """Flip an issue's state to In Progress. Returns True on success.

    This is the atomic lock — two parallel scan ticks can't both
    pick the same issue because the second one sees `In Progress`
    on the next list and filters it out.

    Never raises; on failure returns False and the caller skips
    this issue (next scan tick retries).
    """
    ok, reason = _linear_enabled()
    if not ok:
        logger.info("linear_pe_queue: transition_to_in_progress skipped — %s", reason)
        return False

    client = _build_client(settings.LINEAR_API_KEY)
    try:
        mutation = """
        mutation TransitionIssue($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) {
            success
            issue { id identifier state { name } }
          }
        }
        """
        data = _gql(
            client,
            mutation,
            {"id": issue_id, "stateId": settings.PE_LINEAR_IN_PROGRESS_STATE_ID},
        )
        update = data.get("issueUpdate") or {}
        if not update.get("success"):
            logger.warning(
                "linear_pe_queue: issueUpdate reported no success for %s", issue_id
            )
            return False
        return True
    except Exception:
        logger.exception(
            "linear_pe_queue: transition_to_in_progress failed for %s", issue_id
        )
        return False


def rollback_to_ready(issue_id: str, *, reason_summary: str) -> bool:
    """Flip an In Progress issue back to Ready after a
    WORKSPACE_FAILED dispatch, so the next scan tick retries.

    Also posts a short comment describing the failure so Wilson has
    an audit trail without having to hunt in logs.

    Never raises; failure is logged (the DB row carries the full
    error anyway).
    """
    ok, reason = _linear_enabled()
    if not ok:
        logger.info("linear_pe_queue: rollback_to_ready skipped — %s", reason)
        return False

    client = _build_client(settings.LINEAR_API_KEY)
    ok_transition = _issue_update(
        client,
        issue_id=issue_id,
        state_id=settings.PE_LINEAR_READY_STATE_ID,
    )
    _comment(
        client,
        issue_id=issue_id,
        body=(
            "**PE dispatch failed — rolled back to Ready for retry.**\n\n"
            f"Reason: `{reason_summary[:400]}`"
        ),
    )
    return ok_transition


_FILE_FOR_ATTENTION_MARKER = "<!-- pe:file_for_attention -->"


def file_for_attention(
    issue_id: str,
    *,
    signatures: list[str],
) -> bool:
    """Mark an issue as `needs-wilson` + post the two matching
    failure signatures. Called when the retry gate decides a
    proposal has failed twice with the same signature.

    The issue is LEFT in whatever state it is (usually In Progress,
    since this is called before rollback — see `scan_and_dispatch`).
    Wilson picks it up, decides whether the proposal is salvageable,
    and either removes `needs-wilson` to retry or moves it elsewhere.

    **Comment is idempotent** (2026-11-08 fix): tags every post with a
    hidden HTML-comment marker and skips the post if any existing
    comment on the issue carries that marker. Belt-and-braces guard
    for the case where the needs-wilson label-filter fails to exclude
    an already-flagged ticket (which caused the 2026-11-06–08 comment
    storm on AFX-50/52/73). The label-add path stays unconditional —
    `_add_label` is idempotent on its own and is also the contract
    that Wilson's "remove the label to retry" workflow depends on.
    """
    ok, reason = _linear_enabled()
    if not ok:
        logger.info("linear_pe_queue: file_for_attention skipped — %s", reason)
        return False

    client = _build_client(settings.LINEAR_API_KEY)

    if _has_file_for_attention_marker(client, issue_id=issue_id):
        logger.info(
            "linear_pe_queue: file_for_attention comment already present on "
            "%s — skipping comment, ensuring label.",
            issue_id,
        )
    else:
        body = (
            _FILE_FOR_ATTENTION_MARKER
            + "\n**PE dispatch keeps failing with the same error — "
            "needs your eyes.**\n\n"
            "The last two workspace attempts failed with matching "
            "signatures. The proposal probably has a problem the session "
            "can't resolve on its own (SDK schema, missing config, wrong "
            "permissions). I've stopped auto-retrying. Remove the "
            "`needs-wilson` label to resume, or move the issue to decide "
            "its fate.\n\n"
            + "\n".join(
                f"- Attempt {i + 1}: `{sig[:400]}`" for i, sig in enumerate(signatures)
            )
        )
        _comment(client, issue_id=issue_id, body=body)

    return _add_label(
        client,
        issue_id=issue_id,
        label_id=settings.PE_LINEAR_NEEDS_WILSON_LABEL_ID,
    )


def _has_file_for_attention_marker(client: requests.Session, *, issue_id: str) -> bool:
    """Return True if the issue already carries a file_for_attention
    comment (identified by the marker). On Linear read failure returns
    False so we don't silently suppress the first legit post.

    Fetches up to 50 comments — a single issue hit by a long storm
    may exceed this, but any one marker proves prior filing.
    """
    query = """
    query ReadIssueComments($id: String!) {
      issue(id: $id) {
        id
        comments(first: 50) {
          nodes { id body }
        }
      }
    }
    """
    try:
        data = _gql(client, query, {"id": issue_id})
    except Exception:
        logger.exception("linear_pe_queue: comment-marker read failed for %s", issue_id)
        return False
    issue = data.get("issue") or {}
    nodes = ((issue.get("comments") or {}).get("nodes")) or []
    return any(_FILE_FOR_ATTENTION_MARKER in (n.get("body") or "") for n in nodes)


def flag_needs_design(issue_id_or_identifier: str, *, pr_url: str) -> bool:
    """When a session opens a `[NEEDS DESIGN]` PR instead of a
    normal one, surface the handoff on the Linear issue: comment
    with the PR URL + add `needs-wilson` label.

    Called from the workspace poll loop when it detects the title
    prefix. Accepts either a Linear uuid or the human identifier
    (e.g. "AFX-56") — the latter is what the AgentWorkspaceRun
    row carries, so we resolve to uuid first.
    """
    ok, reason = _linear_enabled()
    if not ok:
        logger.info("linear_pe_queue: flag_needs_design skipped — %s", reason)
        return False

    client = _build_client(settings.LINEAR_API_KEY)
    issue_uuid = _resolve_issue_uuid(client, issue_id_or_identifier)
    if issue_uuid is None:
        logger.warning(
            "linear_pe_queue: flag_needs_design — can't resolve %s to a Linear uuid",
            issue_id_or_identifier,
        )
        return False

    body = (
        "**PE session wants design input before writing code.**\n\n"
        f"See the questions in the PR body: {pr_url}\n\n"
        "The PR has no code change yet — the session opened it to surface the "
        "ambiguity rather than guess. Answer in the PR, then either remove "
        "`needs-wilson` so PE picks it up again, or close the PR and the "
        "issue goes back to Ready."
    )
    _comment(client, issue_id=issue_uuid, body=body)
    return _add_label(
        client,
        issue_id=issue_uuid,
        label_id=settings.PE_LINEAR_NEEDS_WILSON_LABEL_ID,
    )


def _resolve_issue_uuid(client: requests.Session, id_or_identifier: str) -> str | None:
    """Linear's `issueUpdate` and `commentCreate` need the uuid, not
    the identifier. If the caller passed a uuid already (contains
    no "-" segment that looks like a team key + number), return it.
    Otherwise look it up by identifier via `issueVcsBranchSearch`.
    """
    if "-" not in id_or_identifier or not any(
        c.isalpha() for c in id_or_identifier.split("-")[0]
    ):
        # Already looks like a uuid.
        return id_or_identifier
    query = """
    query ResolveIssue($identifier: String!) {
      issueVcsBranchSearch(branchName: $identifier) { id identifier }
    }
    """
    try:
        data = _gql(client, query, {"identifier": id_or_identifier})
        issue = data.get("issueVcsBranchSearch") or {}
        return issue.get("id") or None
    except Exception:
        logger.exception(
            "linear_pe_queue: identifier resolve failed for %s", id_or_identifier
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Retry gate
# ─────────────────────────────────────────────────────────────────────


def failure_signature(summary: str | None) -> str:
    """Reduce a dispatch failure summary to a stable signature.

    Strips Anthropic/Linear-shaped ids (`req_...`, `sess_...`,
    `run_...`, etc.), any remaining long alphanumeric blobs
    (session ids, SHA hashes), 4+ digit numbers (timestamps, retry
    counts), and lowercases. Compare two signatures for equality to
    decide whether a second attempt is hitting the same wall as
    the first.
    """
    if not summary:
        return ""
    s = summary[:200].lower()
    # Strip Anthropic/Linear id tokens: req_01abc..., sess_xyz..., etc.
    s = re.sub(
        r"\b(?:req|sess|run|msg|agent|env|vlt|cmp|wflow|evt)_[0-9a-z]+",
        "",
        s,
    )
    # Strip any remaining long alphanumeric blobs (16+ chars) —
    # catches session ids and hashes that don't carry an Anthropic
    # prefix (e.g. GitHub commit SHAs, UUIDs with dashes removed).
    s = re.sub(r"\b[0-9a-z]{16,}\b", "", s)
    # Strip long numbers (timestamps, retry counts).
    s = re.sub(r"\b\d{4,}\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def should_file_for_attention(prior_failed_summaries: list[str]) -> bool:
    """Given the last-first list of a Linear issue's WORKSPACE_FAILED
    summaries, decide whether the retry gate should stop and file for
    attention instead of dispatching again.

    Rule (from Wilson, 2026-11-01):
      "Try twice; if it fails with the same issue, file for attention."

    < 2 prior failures → retry.
    ≥ 2 prior failures AND the latest two share a signature → attention.
    Otherwise → retry (a NEW failure signature is treated as a new
    problem and gets its own 2 attempts).
    """
    if len(prior_failed_summaries) < 2:
        return False
    sig_a = failure_signature(prior_failed_summaries[0])
    sig_b = failure_signature(prior_failed_summaries[1])
    return bool(sig_a) and sig_a == sig_b


# ─────────────────────────────────────────────────────────────────────
# Internal GraphQL plumbing
# ─────────────────────────────────────────────────────────────────────


def _issue_update(client: requests.Session, *, issue_id: str, state_id: str) -> bool:
    mutation = """
    mutation RollbackIssue($id: String!, $stateId: String!) {
      issueUpdate(id: $id, input: { stateId: $stateId }) {
        success
      }
    }
    """
    try:
        data = _gql(client, mutation, {"id": issue_id, "stateId": state_id})
        return bool((data.get("issueUpdate") or {}).get("success"))
    except Exception:
        logger.exception("linear_pe_queue: _issue_update failed for %s", issue_id)
        return False


def _comment(client: requests.Session, *, issue_id: str, body: str) -> bool:
    mutation = """
    mutation CommentOnIssue($issueId: String!, $body: String!) {
      commentCreate(input: { issueId: $issueId, body: $body }) {
        success
      }
    }
    """
    try:
        data = _gql(client, mutation, {"issueId": issue_id, "body": body})
        return bool((data.get("commentCreate") or {}).get("success"))
    except Exception:
        logger.exception("linear_pe_queue: _comment failed for %s", issue_id)
        return False


def _add_label(client: requests.Session, *, issue_id: str, label_id: str) -> bool:
    """Append a single label to the issue's existing label set.

    Linear's `issueUpdate(labelIds: ...)` REPLACES the set, so we
    read the current labels first and merge. If the read fails,
    log + return False — a missing label is better than silently
    stripping all other labels.
    """
    read_q = """
    query ReadLabels($id: String!) {
      issue(id: $id) {
        id
        labels(first: 50) { nodes { id } }
      }
    }
    """
    try:
        data = _gql(client, read_q, {"id": issue_id})
        issue = data.get("issue") or {}
        existing_ids = [n["id"] for n in (issue.get("labels") or {}).get("nodes") or []]
        if label_id in existing_ids:
            return True  # already applied — idempotent
        new_ids = existing_ids + [label_id]
    except Exception:
        logger.exception("linear_pe_queue: _add_label read failed for %s", issue_id)
        return False

    mutation = """
    mutation AddLabel($id: String!, $labelIds: [String!]!) {
      issueUpdate(id: $id, input: { labelIds: $labelIds }) {
        success
      }
    }
    """
    try:
        data = _gql(client, mutation, {"id": issue_id, "labelIds": new_ids})
        return bool((data.get("issueUpdate") or {}).get("success"))
    except Exception:
        logger.exception("linear_pe_queue: _add_label update failed for %s", issue_id)
        return False


__all__ = [
    "ReadyIssue",
    "failure_signature",
    "file_for_attention",
    "flag_needs_design",
    "list_ready_dispatch_candidates",
    "rollback_to_ready",
    "should_file_for_attention",
    "transition_to_in_progress",
]

# Reserved for the dispatch logic: unused import suppressor so
# `requests` keeps a static reference even without a type annotation
# path through it in some code analyzers.
_: Any = requests
