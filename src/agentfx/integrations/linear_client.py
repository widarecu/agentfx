"""Linear GraphQL client for fleet agents to file findings (AFX-11).

Agents call `file_finding(...)` from this module when they want Wilson
to see an actionable item on his phone instead of in email. The
module:

- Dedupes against open issues carrying the same `Fingerprint:` string
  in their description (so repeated QA reports comment on one issue
  instead of flooding Triage).
- Enforces a per-agent daily cap (default 5 new issues/day) so a
  misbehaving agent can't bury Triage.
- Files every new issue into **Triage** with the right Type / Area /
  Source / `agent-filed` labels. Never sets priority — Wilson owns
  that when he approves out of Triage.
- Never raises. Any error (flag off, no key, network, GraphQL) is
  swallowed and logged; the agent code calling it keeps working.

Not in scope here (follow-ups):

- Distinct OAuth app identity (`actor=app`) for the fleet. Right now
  the client uses a plain `LINEAR_API_KEY` which must belong to the
  fleet's dedicated Linear user, not Wilson's personal key.
- Daily email digest for cap-exceeded findings. Today a cap miss is
  logged WARNING and the agent falls back to its own email path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from agentfx.api.core.config import settings

logger = logging.getLogger(__name__)

_LINEAR_API_URL = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_SECONDS = 10.0

# Marker Line we embed in every filed description so dedupe can
# match against it with a simple substring search. Kept intentionally
# verbose so a human reading the issue in Linear sees why it's there.
_FINGERPRINT_MARKER = "**Fingerprint:**"
_CLOSED_STATE_TYPES = ("completed", "canceled", "duplicate")
_MAX_DUPLICATE_CHAIN_HOPS = 5


# ─────────────────────────────────────────────────────────────────────
# Public shapes
# ─────────────────────────────────────────────────────────────────────


@dataclass
class FindingSpec:
    """What an agent hands to `file_finding` — everything needed to
    land a Triage issue (or comment on the existing one)."""

    fingerprint: str  # Stable id, e.g. "qa:pool-stuck-lease" or "qa:<work-order-hash>"
    title: str  # Issue title — one short line
    body: str  # Markdown body (Agent finding template content)
    source_label: str  # "QA agent" / "PE agent" / "PM agent" / "Research agent"
    type_label: (
        str  # "Bug" / "Feature" / "Improvement" / "Chore" / "Incident" / "Test gap"
    )
    area_label: str  # "Backtest" / "Paper runner" / "Agent fleet" / etc.
    risk_label: str = "Normal"  # "Normal" or "Money-path"
    extra_labels: list[str] = field(default_factory=list)  # e.g. "Found in: Staging"


@dataclass
class FilingResult:
    """What `file_finding` returns. Never raises; failure cases are
    expressed as `filed=False` with a `reason`."""

    filed: bool
    issue_id: str | None = None  # Linear's internal uuid
    identifier: str | None = None  # Human-readable, e.g. "AFX-42"
    url: str | None = None
    action: str = (
        ""  # "created" / "commented" / "reopened" / "benign" / "skipped:<why>"
    )
    reason: str = ""  # Human-readable detail (success → "" / "ok")
    state_name: str | None = (
        None  # Existing or prior Linear state (e.g. "In Progress", "Done")
    )


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def file_finding(spec: FindingSpec, *, now: datetime | None = None) -> FilingResult:
    """Agents call this. Dedupes, caps, and files. Never raises.

    - If `AGENT_LINEAR_ENABLED=False` → returns `filed=False, action="skipped:flag_off"`.
    - If `LINEAR_API_KEY` empty → returns `filed=False, action="skipped:no_key"`.
    - If an open issue carries the same `Fingerprint:` → comments on
      it with the new evidence; returns `filed=True, action="commented"`.
    - If the per-agent daily cap is already hit → returns
      `filed=False, action="skipped:cap_hit"`. Caller should log /
      email the finding itself.
    - Else creates a Triage issue, returns `filed=True, action="created"`.
    """
    now = now or datetime.now(UTC)
    if not bool(getattr(settings, "AGENT_LINEAR_ENABLED", False)):
        return FilingResult(filed=False, action="skipped:flag_off")

    api_key = (getattr(settings, "LINEAR_API_KEY", "") or "").strip()
    if not api_key:
        logger.warning(
            "linear_client: AGENT_LINEAR_ENABLED=true but LINEAR_API_KEY empty; "
            "skipping finding %r",
            spec.fingerprint,
        )
        return FilingResult(filed=False, action="skipped:no_key")

    client = _build_client(api_key)
    team_key = getattr(settings, "AGENT_LINEAR_TEAM_KEY", "AFX") or "AFX"

    # 1. Dedupe search — if an open issue already carries this
    #    fingerprint, comment on it instead of filing again.
    try:
        existing = _search_open_by_fingerprint(client, team_key, spec.fingerprint)
    except Exception:
        logger.exception(
            "linear_client: dedupe search failed for fingerprint=%s",
            spec.fingerprint,
        )
        existing = None

    if existing is not None:
        return _comment_on_open_issue(client, existing, spec, now=now)

    # 1b. Closed-issue lookup (AFX-149) — if this finding matches a
    #     closed ticket (Done / Canceled / Duplicate), reopen it to
    #     Triage (or follow Duplicate to its canonical ticket and
    #     comment or reopen) instead of filing a brand new ticket.
    #     Reopens do not count against the daily cap.
    try:
        closed_nodes = _search_closed_by_fingerprint(client, team_key, spec.fingerprint)
    except Exception:
        logger.exception(
            "linear_client: closed-issue search failed for fingerprint=%s",
            spec.fingerprint,
        )
        closed_nodes = []

    closed_match = _pick_closed_candidate(closed_nodes)
    if closed_match is not None:
        target = (
            _resolve_duplicate_target(client, closed_match)
            if _is_duplicate_issue(closed_match)
            else closed_match
        )
        if not _is_closed_issue(target):
            return _comment_on_open_issue(client, target, spec, now=now)
        return _reopen_closed_issue(
            client,
            team_key=team_key,
            issue=target,
            spec=spec,
            now=now,
        )

    # 2. Per-agent daily cap — bail out before creating if the cap
    #    is already hit so repeated low-signal findings don't bury
    #    Triage.
    cap = _daily_cap_per_agent()
    try:
        files_today = _count_files_today(
            client, team_key, source_label=spec.source_label, now=now
        )
    except Exception:
        logger.exception(
            "linear_client: cap count failed for source=%s", spec.source_label
        )
        files_today = 0
    if files_today >= cap:
        logger.warning(
            "linear_client: cap hit for source=%s (%d/%d today); finding %r not filed",
            spec.source_label,
            files_today,
            cap,
            spec.fingerprint,
        )
        return FilingResult(filed=False, action="skipped:cap_hit")

    # 3. Create new Triage issue.
    try:
        created = _create_triage_issue(
            client,
            team_key=team_key,
            spec=spec,
            now=now,
        )
    except Exception:
        logger.exception(
            "linear_client: issue create failed for fingerprint=%s",
            spec.fingerprint,
        )
        return FilingResult(filed=False, action="skipped:create_failed")

    if created is None:
        return FilingResult(filed=False, action="skipped:create_failed")

    logger.info(
        "linear_client: filed Triage issue %s for fingerprint=%s (source=%s)",
        created["identifier"],
        spec.fingerprint,
        spec.source_label,
    )
    return FilingResult(
        filed=True,
        issue_id=created["id"],
        identifier=created["identifier"],
        url=created.get("url"),
        action="created",
    )


# ─────────────────────────────────────────────────────────────────────
# GraphQL helpers (small, no abstraction overhead)
# ─────────────────────────────────────────────────────────────────────


def _build_client(api_key: str) -> requests.Session:
    """Session with the Linear auth header. Linear uses the raw key,
    not `Bearer`. Keep-alive is fine; a stale token causes a 401 which
    callers handle (never raised to agent code)."""
    sess = requests.Session()
    sess.headers.update(
        {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
    )
    return sess


def _gql(client: requests.Session, query: str, variables: dict[str, Any]) -> dict:
    """Execute a GraphQL request. Returns the `data` field; raises on
    HTTP error / GraphQL errors so callers can log + swallow uniformly.

    Linear uses HTTP 400 for schema violations + complexity-gate
    rejections, with the actual error in the response body — so we
    parse the body BEFORE `raise_for_status()` and surface the real
    error message. Without this, a 400 only shows "Bad Request" in
    logs, which cost us ~15 min debugging on 2026-10-27.
    """
    resp = client.post(
        _LINEAR_API_URL,
        json={"query": query, "variables": variables},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if payload.get("errors"):
        raise RuntimeError(
            f"linear graphql errors (status={resp.status_code}): {payload['errors']}"
        )
    resp.raise_for_status()
    return payload.get("data") or {}


def _search_open_by_fingerprint(
    client: requests.Session, team_key: str, fingerprint: str
) -> dict | None:
    """Return the first open issue whose description contains
    `Fingerprint: <fingerprint>`, or None.

    "Open" = any non-terminal state (Triage / Backlog / Ready /
    In Progress / In Review / Blocked). Done / Canceled / Duplicate
    are handled separately by `_search_closed_by_fingerprint`.

    AFX-76: the marker must mirror exactly what `_render_body_with_fingerprint`
    writes, including the backticks around the fingerprint value.
    `description: { contains }` is a substring filter and a backtick
    mismatch here is why dedupe never fired between 2026-10-27 and
    2026-11-03 (12 duplicate QA tickets filed on the same 3 findings).
    """
    marker = f"{_FINGERPRINT_MARKER} `{fingerprint}`"
    query = """
    query SearchByFingerprint($query: String!, $teamKey: String!) {
      issues(
        first: 10,
        filter: {
          team: { key: { eq: $teamKey } },
          state: { type: { nin: ["completed", "canceled", "duplicate"] } },
          description: { contains: $query }
        }
      ) {
        nodes { id identifier url state { name type } }
      }
    }
    """
    data = _gql(client, query, {"query": marker, "teamKey": team_key})
    nodes = (data.get("issues") or {}).get("nodes") or []
    for node in nodes:
        if not _is_closed_issue(node):
            return node
    return None


def _search_closed_by_fingerprint(
    client: requests.Session, team_key: str, fingerprint: str
) -> list[dict]:
    """Return closed issues (Done / Canceled / Duplicate) whose
    description contains the fingerprint marker, including duplicate
    relations so a Duplicate can be resolved to its canonical ticket."""
    marker = f"{_FINGERPRINT_MARKER} `{fingerprint}`"
    query = """
    query SearchClosedByFingerprint($query: String!, $teamKey: String!) {
      issues(
        first: 10,
        filter: {
          team: { key: { eq: $teamKey } },
          state: { type: { in: ["completed", "canceled", "duplicate"] } },
          description: { contains: $query }
        }
      ) {
        nodes {
          id
          identifier
          url
          completedAt
          canceledAt
          updatedAt
          state { name type }
          relations(first: 5) {
            nodes {
              type
              relatedIssue {
                id
                identifier
                url
                completedAt
                canceledAt
                updatedAt
                state { name type }
              }
            }
          }
        }
      }
    }
    """
    data = _gql(client, query, {"query": marker, "teamKey": team_key})
    return (data.get("issues") or {}).get("nodes") or []


def _fetch_issue_with_relations(client: requests.Session, issue_id: str) -> dict | None:
    """Fetch one issue with its duplicate relations (used only when
    following a multi-hop Duplicate chain)."""
    query = """
    query IssueWithRelations($id: String!) {
      issue(id: $id) {
        id
        identifier
        url
        completedAt
        canceledAt
        updatedAt
        state { name type }
        relations(first: 5) {
          nodes {
            type
            relatedIssue {
              id
              identifier
              url
              completedAt
              canceledAt
              updatedAt
              state { name type }
            }
          }
        }
      }
    }
    """
    data = _gql(client, query, {"id": issue_id})
    return data.get("issue") or None


def _is_closed_issue(issue: dict) -> bool:
    state = issue.get("state") or {}
    state_type = (state.get("type") or "").strip().lower()
    state_name = (state.get("name") or "").strip().lower()
    return state_type in _CLOSED_STATE_TYPES or state_name in (
        "done",
        "canceled",
        "cancelled",
        "duplicate",
    )


def _is_duplicate_issue(issue: dict) -> bool:
    state = issue.get("state") or {}
    if (state.get("type") or "").strip().lower() == "duplicate":
        return True
    if (state.get("name") or "").strip().lower() == "duplicate":
        return True
    return _duplicate_target(issue) is not None


def _duplicate_target(issue: dict) -> dict | None:
    relations = (issue.get("relations") or {}).get("nodes") or []
    for rel in relations:
        if (rel.get("type") or "").strip().lower() == "duplicate":
            target = rel.get("relatedIssue")
            if isinstance(target, dict) and target.get("id"):
                return target
    return None


def _closed_timestamp(issue: dict) -> str:
    return (
        issue.get("completedAt")
        or issue.get("canceledAt")
        or issue.get("updatedAt")
        or ""
    )


def _pick_closed_candidate(nodes: list[dict]) -> dict | None:
    """Choose the best closed ticket to reopen from matching nodes.

    Preference order (AFX-149):
    1. Non-duplicate Done (`completed`), most recently closed first.
    2. Non-duplicate Canceled, most recently closed first.
    3. Duplicate (or Canceled-as-duplicate), most recently closed first
       — caller resolves this to its canonical target.
    """
    if not nodes:
        return None
    non_dup_done: list[dict] = []
    non_dup_canceled: list[dict] = []
    dups: list[dict] = []
    for node in nodes:
        if _is_duplicate_issue(node):
            dups.append(node)
        else:
            state = node.get("state") or {}
            st_type = (state.get("type") or "").strip().lower()
            st_name = (state.get("name") or "").strip().lower()
            if st_type == "completed" or st_name == "done":
                non_dup_done.append(node)
            else:
                non_dup_canceled.append(node)
    for bucket in (non_dup_done, non_dup_canceled, dups):
        if bucket:
            bucket.sort(key=_closed_timestamp, reverse=True)
            return bucket[0]
    return None


def _resolve_duplicate_target(client: requests.Session, issue: dict) -> dict:
    """Follow `duplicate` relations to the canonical ticket, guarded
    against cycles and bounded to `_MAX_DUPLICATE_CHAIN_HOPS`."""
    current = issue
    visited: set[str] = {str(current.get("id") or "")}
    for _ in range(_MAX_DUPLICATE_CHAIN_HOPS):
        target = _duplicate_target(current)
        if target is None:
            break
        target_id = str(target.get("id") or "")
        if not target_id or target_id in visited:
            break
        visited.add(target_id)
        current = target
        if _is_duplicate_issue(current) and "relations" not in current:
            try:
                fetched = _fetch_issue_with_relations(client, target_id)
                if fetched:
                    current = fetched
            except Exception:
                logger.exception(
                    "linear_client: failed to follow duplicate chain from %s",
                    current.get("identifier"),
                )
                break
    return current


def _comment_on_open_issue(
    client: requests.Session,
    issue: dict,
    spec: FindingSpec,
    *,
    now: datetime,
) -> FilingResult:
    state_name = (issue.get("state") or {}).get("name") or None
    comment_body = _render_dedupe_comment(spec, now=now)
    try:
        ok = _add_comment(client, issue_id=issue["id"], body=comment_body)
        if ok:
            logger.info(
                "linear_client: appended evidence to open issue=%s (fingerprint=%s)",
                issue["identifier"],
                spec.fingerprint,
            )
            return FilingResult(
                filed=True,
                issue_id=issue["id"],
                identifier=issue["identifier"],
                url=issue.get("url"),
                action="commented",
                state_name=state_name,
            )
    except Exception:
        logger.exception(
            "linear_client: comment append failed for issue=%s",
            issue.get("identifier"),
        )
    return FilingResult(
        filed=False,
        issue_id=issue["id"],
        identifier=issue["identifier"],
        url=issue.get("url"),
        action="skipped:dedupe_comment_failed",
        state_name=state_name,
    )


def _resolve_triage_state_id(client: requests.Session, team_key: str) -> str | None:
    """Look up the team's Triage workflow state ID."""
    query = """
    query ResolveTriageState($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          id
          states(first: 25) {
            nodes { id name type }
          }
        }
      }
    }
    """
    data = _gql(client, query, {"teamKey": team_key})
    teams = (data.get("teams") or {}).get("nodes") or []
    if not teams:
        return None
    states = (teams[0].get("states") or {}).get("nodes") or []
    for st in states:
        if (st.get("type") or "").strip().lower() == "triage":
            return st.get("id")
    for st in states:
        if (st.get("name") or "").strip().lower() == "triage":
            return st.get("id")
    return None


def _update_issue_state(
    client: requests.Session, *, issue_id: str, state_id: str
) -> bool:
    mutation = """
    mutation UpdateIssueState($id: String!, $stateId: String!) {
      issueUpdate(id: $id, input: { stateId: $stateId }) {
        success
      }
    }
    """
    data = _gql(client, mutation, {"id": issue_id, "stateId": state_id})
    return bool((data.get("issueUpdate") or {}).get("success"))


def _reopen_closed_issue(
    client: requests.Session,
    *,
    team_key: str,
    issue: dict,
    spec: FindingSpec,
    now: datetime,
) -> FilingResult:
    """Move a closed issue back to Triage and append a recurrence comment."""
    prev_state = (issue.get("state") or {}).get("name") or "Closed"
    closed_at = _closed_timestamp(issue)
    try:
        triage_state_id = _resolve_triage_state_id(client, team_key)
        if not triage_state_id:
            logger.error(
                "linear_client: Triage state not found on team %s; cannot reopen %s",
                team_key,
                issue.get("identifier"),
            )
            return FilingResult(
                filed=False,
                issue_id=issue["id"],
                identifier=issue["identifier"],
                url=issue.get("url"),
                action="skipped:reopen_failed",
                state_name=prev_state,
            )
        moved = _update_issue_state(
            client, issue_id=issue["id"], state_id=triage_state_id
        )
        if not moved:
            logger.error(
                "linear_client: issueUpdate failed reopening %s",
                issue.get("identifier"),
            )
            return FilingResult(
                filed=False,
                issue_id=issue["id"],
                identifier=issue["identifier"],
                url=issue.get("url"),
                action="skipped:reopen_failed",
                state_name=prev_state,
            )
        comment_body = _render_reopen_comment(
            spec,
            closed_state=prev_state,
            closed_at=closed_at,
            now=now,
        )
        commented = _add_comment(client, issue_id=issue["id"], body=comment_body)
        if not commented:
            logger.error(
                "linear_client: reopen comment failed for %s",
                issue.get("identifier"),
            )
            return FilingResult(
                filed=False,
                issue_id=issue["id"],
                identifier=issue["identifier"],
                url=issue.get("url"),
                action="skipped:reopen_failed",
                state_name=prev_state,
            )
        logger.info(
            "linear_client: reopened closed issue=%s (was %s) to Triage "
            "(fingerprint=%s)",
            issue["identifier"],
            prev_state,
            spec.fingerprint,
        )
        return FilingResult(
            filed=True,
            issue_id=issue["id"],
            identifier=issue["identifier"],
            url=issue.get("url"),
            action="reopened",
            state_name=prev_state,
        )
    except Exception:
        logger.exception(
            "linear_client: failed to reopen closed issue=%s",
            issue.get("identifier"),
        )
        return FilingResult(
            filed=False,
            issue_id=issue["id"],
            identifier=issue["identifier"],
            url=issue.get("url"),
            action="skipped:reopen_failed",
            state_name=prev_state,
        )


def _count_files_today(
    client: requests.Session,
    team_key: str,
    *,
    source_label: str,
    now: datetime,
) -> int:
    """Count issues created today in this UTC day that carry the
    agent-filed flag AND this source agent's label. Used to enforce
    the per-agent daily cap."""
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC).isoformat()
    query = """
    query CountToday($teamKey: String!, $after: DateTimeOrDuration!, $src: String!) {
      issues(
        first: 50,
        filter: {
          team: { key: { eq: $teamKey } },
          createdAt: { gte: $after },
          labels: { name: { in: ["agent-filed", $src] } }
        }
      ) {
        nodes { id labels { nodes { name } } }
      }
    }
    """
    data = _gql(
        client,
        query,
        {"teamKey": team_key, "after": day_start, "src": source_label},
    )
    nodes = (data.get("issues") or {}).get("nodes") or []
    # Linear's label filter is OR-of-names. Count only rows that have
    # BOTH labels so we don't over-count issues merely labelled
    # `agent-filed` by a different agent.
    count = 0
    for n in nodes:
        names = {lab["name"] for lab in (n.get("labels") or {}).get("nodes", [])}
        if "agent-filed" in names and source_label in names:
            count += 1
    return count


def _create_triage_issue(
    client: requests.Session,
    *,
    team_key: str,
    spec: FindingSpec,
    now: datetime,
) -> dict | None:
    """Resolve team + label ids, then create a Triage issue. Returns
    the created {id, identifier, url} or None on API failure."""
    team_id, label_ids = _resolve_team_and_labels(
        client,
        team_key=team_key,
        names=[
            "agent-filed",
            spec.source_label,
            spec.type_label,
            spec.area_label,
            spec.risk_label,
            *spec.extra_labels,
        ],
    )
    if team_id is None:
        logger.error("linear_client: team %r not found", team_key)
        return None

    description = _render_body_with_fingerprint(spec, now=now)
    mutation = """
    mutation CreateTriage($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier url }
      }
    }
    """
    data = _gql(
        client,
        mutation,
        {
            "input": {
                "teamId": team_id,
                "title": spec.title[:255],
                "description": description,
                "labelIds": label_ids,
                # Explicit stateId lookup would be nicer but Linear
                # routes newly-created issues through the team's
                # triage flow when no stateId is provided AND the
                # team has triage enabled (confirmed for AFX team).
            }
        },
    )
    created = ((data.get("issueCreate") or {}).get("issue")) or None
    if created is None or not (data.get("issueCreate") or {}).get("success"):
        logger.error("linear_client: issueCreate reported failure for %r", spec.title)
        return None
    return created


def _add_comment(client: requests.Session, *, issue_id: str, body: str) -> bool:
    mutation = """
    mutation AddComment($input: CommentCreateInput!) {
      commentCreate(input: $input) { success }
    }
    """
    data = _gql(client, mutation, {"input": {"issueId": issue_id, "body": body}})
    return bool((data.get("commentCreate") or {}).get("success"))


def _resolve_team_and_labels(
    client: requests.Session, *, team_key: str, names: list[str]
) -> tuple[str | None, list[str]]:
    """Fetch team id + the ids of the named labels in one query.

    Labels that don't exist on the team are silently dropped — Linear
    rejects unknown label ids on `issueCreate`, so attaching only
    recognised ones keeps the filing path robust against a label
    being renamed/retired.

    `first: 50` is chosen deliberately: Linear's GraphQL complexity
    gate rejects anything above ~100 nested label rows (seen
    2026-10-27 as HTTP 400 "Query too complex. Complexity: 15055.
    Maximum: 10000" when using 250). AFX currently has 37 labels, so
    50 is comfortable headroom. If a team ever exceeds 50 labels,
    label resolution would silently miss the overflow — unlikely in
    practice, and `_resolve_team_and_labels` already warns about
    unresolved labels.
    """
    query = """
    query ResolveTeamAndLabels($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          id
          labels(first: 50) { nodes { id name } }
        }
      }
    }
    """
    data = _gql(client, query, {"teamKey": team_key})
    nodes = (data.get("teams") or {}).get("nodes") or []
    if not nodes:
        return None, []
    team = nodes[0]
    team_id = team["id"]
    requested = {n for n in names if n}
    found: dict[str, str] = {}
    for lab in (team.get("labels") or {}).get("nodes", []):
        if lab["name"] in requested:
            found[lab["name"]] = lab["id"]
    # Preserve caller's order; drop unknowns silently.
    label_ids = [found[n] for n in names if n in found]
    missing = requested - set(found.keys())
    if missing:
        logger.warning(
            "linear_client: label(s) not found on team %s, dropping: %s",
            team_key,
            sorted(missing),
        )
    return team_id, label_ids


# ─────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────


def _render_body_with_fingerprint(spec: FindingSpec, *, now: datetime) -> str:
    """Agent finding template body + the fingerprint marker so dedupe
    can match later. The marker lives at the very end so humans see
    the content first."""
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    sections = [
        spec.body.strip(),
        "",
        "---",
        f"*Filed by {spec.source_label} at {ts}.*",
        "",
        f"{_FINGERPRINT_MARKER} `{spec.fingerprint}`",
    ]
    return "\n".join(sections)


def _render_dedupe_comment(spec: FindingSpec, *, now: datetime) -> str:
    """What we post as a new comment when dedupe matches an existing
    open issue — the fresh evidence, not the whole template again."""
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"**{spec.source_label} saw this again at {ts}.**\n\n"
        f"{spec.body.strip()}\n\n"
        f"_(Dedupe fingerprint: `{spec.fingerprint}`)_"
    )


def _render_reopen_comment(
    spec: FindingSpec,
    *,
    closed_state: str,
    closed_at: str,
    now: datetime,
) -> str:
    """Comment posted when a previously closed issue recurs and is
    reopened to Triage (AFX-149)."""
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    closed_date = closed_at[:10] if len(closed_at) >= 10 else "an earlier date"
    return (
        f"**This issue has come back** (previously closed as {closed_state} on "
        f"{closed_date}). Reopened to Triage by {spec.source_label} at {ts}.\n\n"
        f"{spec.body.strip()}\n\n"
        f"_(Dedupe fingerprint: `{spec.fingerprint}`)_"
    )


def _daily_cap_per_agent() -> int:
    val = getattr(settings, "AGENT_LINEAR_DAILY_CAP_PER_AGENT", 5)
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 5


__all__ = [
    "FindingSpec",
    "FilingResult",
    "file_finding",
]
