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
    action: str = ""  # "created" / "commented" / "skipped:<why>"
    reason: str = ""  # Human-readable detail (success → "" / "ok")


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
        comment_body = _render_dedupe_comment(spec, now=now)
        try:
            ok = _add_comment(client, issue_id=existing["id"], body=comment_body)
            if ok:
                logger.info(
                    "linear_client: appended evidence to open issue=%s "
                    "(fingerprint=%s)",
                    existing["identifier"],
                    spec.fingerprint,
                )
                return FilingResult(
                    filed=True,
                    issue_id=existing["id"],
                    identifier=existing["identifier"],
                    url=existing.get("url"),
                    action="commented",
                )
        except Exception:
            logger.exception(
                "linear_client: comment append failed for issue=%s",
                existing.get("identifier"),
            )
        # Comment attempt failed — fall through so the finding is at
        # least logged at the caller rather than lost. We return
        # action="skipped:dedupe_comment_failed" so the caller can
        # retry via its email path if desired.
        return FilingResult(
            filed=False,
            issue_id=existing["id"],
            identifier=existing["identifier"],
            action="skipped:dedupe_comment_failed",
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
    HTTP error / GraphQL errors so callers can log + swallow uniformly."""
    resp = client.post(
        _LINEAR_API_URL,
        json={"query": query, "variables": variables},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"linear graphql errors: {payload['errors']}")
    return payload.get("data") or {}


def _search_open_by_fingerprint(
    client: requests.Session, team_key: str, fingerprint: str
) -> dict | None:
    """Return the first open issue whose description contains
    `Fingerprint: <fingerprint>`, or None.

    "Open" = any non-terminal state (Triage / Backlog / Ready /
    In Progress / In Review). We treat Done / Canceled / Duplicate
    as closed — a historical Done issue should not block a fresh
    report of the same problem."""
    marker = f"{_FINGERPRINT_MARKER} {fingerprint}"
    query = """
    query SearchByFingerprint($query: String!, $teamKey: String!) {
      issues(
        first: 10,
        filter: {
          team: { key: { eq: $teamKey } },
          state: { type: { nin: ["completed", "canceled"] } },
          description: { contains: $query }
        }
      ) {
        nodes { id identifier url state { name } }
      }
    }
    """
    data = _gql(client, query, {"query": marker, "teamKey": team_key})
    nodes = (data.get("issues") or {}).get("nodes") or []
    return nodes[0] if nodes else None


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
    being renamed/retired."""
    query = """
    query ResolveTeamAndLabels($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          id
          labels(first: 250) { nodes { id name } }
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
