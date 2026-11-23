"""Shared Linear client and ticket-state transitions for delivery (AFX-118).

One implementation that both `scripts/afx.py` (agents) and the PE queue
use to claim, block and release tickets, so the board has one owner per
status (AFX-117):

- `start`: Ready -> In Progress. Anything else is refused, so a second
  start cannot launch duplicate work.
- `block`: active ticket -> Blocked, adds `needs-wilson`, posts the
  question once. Never moves an issue to Ready.
- `release_own_claim`: PE-only undo of its own In Progress claim when
  no workspace started. A started workspace or another actor's claim is
  never rolled back.

Standard library only so the CLI runs without the backend environment.
State and label IDs are resolved by name per team, never hard-coded.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LINEAR_URL = "https://api.linear.app/graphql"
DEFAULT_TIMEOUT_SECONDS = 20.0
PAGE_SIZE = 100

READY = "Ready"
IN_PROGRESS = "In Progress"
IN_REVIEW = "In Review"
BLOCKED = "Blocked"
NEEDS_WILSON = "needs-wilson"

BLOCKABLE_STATES = frozenset({IN_PROGRESS, IN_REVIEW, BLOCKED})

Opener = Callable[..., Any]


class LinearError(RuntimeError):
    """Linear API call failed (HTTP, GraphQL errors or bad payload)."""


class NotFoundError(LinearError):
    """The issue, state or label named does not exist."""


@dataclass
class Issue:
    id: str
    identifier: str
    state: str
    team_id: str
    labels: dict[str, str] = field(default_factory=dict)  # name -> id


@dataclass
class TransitionResult:
    ok: bool
    changed: bool
    identifier: str
    from_state: str | None = None
    to_state: str | None = None
    reason: str = ""


class LinearClient:
    """Minimal GraphQL client: errors, pagination and name lookups."""

    def __init__(
        self,
        api_key: str,
        *,
        url: str = LINEAR_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Opener | None = None,
    ) -> None:
        if not api_key:
            raise LinearError("LINEAR_API_KEY is empty")
        self._api_key = api_key
        self._url = url
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._state_ids: dict[tuple[str, str], str] = {}
        self._label_ids: dict[tuple[str, str], str] = {}
        self._viewer_id: str | None = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> LinearClient:
        return cls(os.environ.get("LINEAR_API_KEY", "").strip(), **kwargs)

    def gql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """Run one request and return `data`. Linear puts the real error
        in the body of HTTP 400s, so the body is parsed on errors too."""
        req = urllib.request.Request(
            self._url,
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as err:
            raw = err.read() if err.fp else b""
            try:
                detail = json.loads(raw).get("errors") or raw.decode("utf-8", "replace")
            except ValueError:
                detail = raw.decode("utf-8", "replace")
            raise LinearError(f"linear http {err.code}: {detail}") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise LinearError(f"linear unreachable: {err}") from err
        try:
            payload = json.loads(body)
        except ValueError as err:
            raise LinearError(f"linear non-json response: {body[:200]!r}") from err
        if payload.get("errors"):
            raise LinearError(f"linear graphql errors: {payload['errors']}")
        return payload.get("data") or {}

    def paginate(
        self, query: str, variables: dict[str, Any], path: list[str]
    ) -> list[dict]:
        """Collect every node of the connection at `path`. The query must
        take `$after: String` and select `nodes` and `pageInfo`."""
        nodes: list[dict] = []
        cursor: str | None = None
        while True:
            conn: Any = self.gql(query, {**variables, "after": cursor})
            for key in path:
                conn = (conn or {}).get(key)
            if conn is None:
                raise NotFoundError(f"no data at {'.'.join(path)}")
            nodes.extend(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return nodes
            cursor = info.get("endCursor")

    # Reads

    def issue(self, ident: str) -> Issue:
        data = self.gql(
            "query($id: String!) { issue(id: $id) { id identifier "
            "state { name } team { id } labels(first: 100) { nodes { id name } } } }",
            {"id": ident},
        )
        node = data.get("issue")
        if not node:
            raise NotFoundError(f"issue {ident} not found")
        return Issue(
            id=node["id"],
            identifier=node["identifier"],
            state=(node.get("state") or {}).get("name") or "",
            team_id=(node.get("team") or {}).get("id") or "",
            labels={
                n["name"]: n["id"] for n in (node.get("labels") or {}).get("nodes", [])
            },
        )

    def state_id(self, team_id: str, name: str) -> str:
        key = (team_id, name)
        if key not in self._state_ids:
            nodes = self.paginate(
                "query($teamId: String!, $after: String) { team(id: $teamId) { "
                f"states(first: {PAGE_SIZE}, after: $after) {{ nodes {{ id name }} "
                "pageInfo { hasNextPage endCursor } } } }",
                {"teamId": team_id},
                ["team", "states"],
            )
            for n in nodes:
                self._state_ids.setdefault((team_id, n["name"]), n["id"])
            if key not in self._state_ids:
                raise NotFoundError(f"workflow state {name!r} not found")
        return self._state_ids[key]

    def label_id(self, team_id: str, name: str) -> str:
        key = (team_id, name)
        if key not in self._label_ids:
            nodes = self.paginate(
                "query($teamId: String!, $after: String) { team(id: $teamId) { "
                f"labels(first: {PAGE_SIZE}, after: $after) {{ nodes {{ id name }} "
                "pageInfo { hasNextPage endCursor } } } }",
                {"teamId": team_id},
                ["team", "labels"],
            )
            for n in nodes:
                self._label_ids.setdefault((team_id, n["name"]), n["id"])
            if key not in self._label_ids:
                raise NotFoundError(f"label {name!r} not found")
        return self._label_ids[key]

    def comment_bodies(self, issue_id: str) -> list[str]:
        nodes = self.paginate(
            "query($id: String!, $after: String) { issue(id: $id) { "
            f"comments(first: {PAGE_SIZE}, after: $after) {{ nodes {{ body }} "
            "pageInfo { hasNextPage endCursor } } } }",
            {"id": issue_id},
            ["issue", "comments"],
        )
        return [n.get("body") or "" for n in nodes]

    def viewer_id(self) -> str:
        if self._viewer_id is None:
            viewer = self.gql("query { viewer { id } }").get("viewer") or {}
            if not viewer.get("id"):
                raise LinearError("viewer id unavailable")
            self._viewer_id = viewer["id"]
        return self._viewer_id

    def last_entry_actor(self, issue_id: str, state: str) -> str | None:
        """Actor id of the most recent move into `state`, or None when
        the history has no such move or no human/app actor."""
        nodes = self.paginate(
            "query($id: String!, $after: String) { issue(id: $id) { "
            f"history(first: {PAGE_SIZE}, after: $after) {{ nodes {{ createdAt "
            "actor { id } toState { name } } pageInfo { hasNextPage endCursor } } } }",
            {"id": issue_id},
            ["issue", "history"],
        )
        moves = [n for n in nodes if (n.get("toState") or {}).get("name") == state]
        if not moves:
            return None
        latest = max(moves, key=lambda n: n.get("createdAt") or "")
        return (latest.get("actor") or {}).get("id")

    # Writes

    def set_state(self, issue_id: str, state_id: str) -> None:
        data = self.gql(
            "mutation($id: String!, $stateId: String!) { "
            "issueUpdate(id: $id, input: { stateId: $stateId }) { success } }",
            {"id": issue_id, "stateId": state_id},
        )
        if not (data.get("issueUpdate") or {}).get("success"):
            raise LinearError(f"issueUpdate failed for {issue_id}")

    def add_label(self, issue_id: str, label_id: str) -> None:
        data = self.gql(
            "mutation($id: String!, $labelId: String!) { "
            "issueAddLabel(id: $id, labelId: $labelId) { success } }",
            {"id": issue_id, "labelId": label_id},
        )
        if not (data.get("issueAddLabel") or {}).get("success"):
            raise LinearError(f"issueAddLabel failed for {issue_id}")

    def comment(self, issue_id: str, body: str) -> None:
        data = self.gql(
            "mutation($id: String!, $body: String!) { "
            "commentCreate(input: { issueId: $id, body: $body }) { success } }",
            {"id": issue_id, "body": body},
        )
        if not (data.get("commentCreate") or {}).get("success"):
            raise LinearError(f"commentCreate failed for {issue_id}")

    def comment_once(self, issue_id: str, body: str, marker: str) -> bool:
        """Post `body` unless a comment already carries `marker`. Returns
        True when a new comment was posted."""
        if any(marker in b for b in self.comment_bodies(issue_id)):
            return False
        self.comment(issue_id, body if marker in body else f"{marker}\n{body}")
        return True


# Transitions


def _move(client: LinearClient, issue: Issue, target: str) -> None:
    client.set_state(issue.id, client.state_id(issue.team_id, target))


def start(client: LinearClient, ident: str) -> TransitionResult:
    """Claim a Ready ticket. Refuses every other state, including an
    existing In Progress claim, so callers never start duplicate work."""
    issue = client.issue(ident)
    if issue.state != READY:
        return TransitionResult(
            ok=False,
            changed=False,
            identifier=issue.identifier,
            from_state=issue.state,
            reason=f"{issue.identifier} is {issue.state!r}, not {READY!r}",
        )
    _move(client, issue, IN_PROGRESS)
    return TransitionResult(
        ok=True,
        changed=True,
        identifier=issue.identifier,
        from_state=READY,
        to_state=IN_PROGRESS,
    )


def block_marker(question: str) -> str:
    digest = hashlib.sha256(question.strip().encode()).hexdigest()[:12]
    return f"<!-- afx:block:{digest} -->"


def block(client: LinearClient, ident: str, question: str) -> TransitionResult:
    """Park an active ticket on Wilson: Blocked + needs-wilson + the
    question, posted once. Repeats are no-ops."""
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")
    issue = client.issue(ident)
    if issue.state not in BLOCKABLE_STATES:
        return TransitionResult(
            ok=False,
            changed=False,
            identifier=issue.identifier,
            from_state=issue.state,
            reason=(
                f"{issue.identifier} is {issue.state!r}; only "
                f"{', '.join(sorted(BLOCKABLE_STATES))} can be blocked"
            ),
        )
    changed = False
    if issue.state != BLOCKED:
        _move(client, issue, BLOCKED)
        changed = True
    if NEEDS_WILSON not in issue.labels:
        client.add_label(issue.id, client.label_id(issue.team_id, NEEDS_WILSON))
        changed = True
    marker = block_marker(question)
    body = f"{marker}\n**Blocked: needs Wilson.**\n\n{question}"
    if client.comment_once(issue.id, body, marker):
        changed = True
    return TransitionResult(
        ok=True,
        changed=changed,
        identifier=issue.identifier,
        from_state=issue.state,
        to_state=BLOCKED,
    )


def release_own_claim(
    client: LinearClient, ident: str, *, workspace_started: bool
) -> TransitionResult:
    """Undo this actor's In Progress claim (back to Ready) when no
    workspace started. Anything else is refused and left untouched."""
    issue = client.issue(ident)

    def refuse(reason: str) -> TransitionResult:
        return TransitionResult(
            ok=False,
            changed=False,
            identifier=issue.identifier,
            from_state=issue.state,
            reason=reason,
        )

    if workspace_started:
        return refuse("workspace started; claim is kept")
    if issue.state != IN_PROGRESS:
        return refuse(f"{issue.identifier} is {issue.state!r}, not {IN_PROGRESS!r}")
    claimant = client.last_entry_actor(issue.id, IN_PROGRESS)
    if claimant is None or claimant != client.viewer_id():
        return refuse("In Progress claim belongs to another actor")
    _move(client, issue, READY)
    return TransitionResult(
        ok=True,
        changed=True,
        identifier=issue.identifier,
        from_state=IN_PROGRESS,
        to_state=READY,
    )


__all__ = [
    "BLOCKED",
    "IN_PROGRESS",
    "Issue",
    "LinearClient",
    "LinearError",
    "NotFoundError",
    "READY",
    "TransitionResult",
    "block",
    "block_marker",
    "release_own_claim",
    "start",
]
