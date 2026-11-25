"""In-memory stand-in for the Linear GraphQL endpoint (AFX-118 tests).

`FakeLinear.opener` replaces `urllib.request.urlopen` in
`linear_delivery.LinearClient`, so tests exercise the real request,
error and pagination code without any network call.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from dataclasses import dataclass, field
from typing import Any

TEAM = "team-afx"
STATES = [
    "Triage",
    "Backlog",
    "Ready",
    "In Progress",
    "In Review",
    "Blocked",
    "Needs Input",
    "In Staging",
    "Done",
    "Canceled",
]


STATE_TYPES = {
    "Triage": "triage",
    "Backlog": "backlog",
    "Ready": "unstarted",
    "Done": "completed",
    "Canceled": "canceled",
}

STATE_TYPES = {
    "Triage": "triage",
    "Backlog": "backlog",
    "Ready": "unstarted",
    "Done": "completed",
    "Canceled": "canceled",
}


@dataclass
class FakeIssue:
    identifier: str
    state: str
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str = "2026-11-23T00:00:00.000Z"
    attachments: list[str] = field(default_factory=list)
    assignee: str | None = None
    delegate: str | None = None
    created_at: str = "2026-11-20T00:00:00.000Z"

    @property
    def id(self) -> str:
        return f"uuid-{self.identifier}"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLinear:
    def __init__(
        self,
        *,
        page_size: int = 2,
        viewer: str = "user-pe",
        states: list[str] | None = None,
        labels: list[str] | None = None,
        workspace_labels: list[str] | None = None,
    ) -> None:
        self.page_size = page_size
        self.viewer = viewer
        self.states = list(STATES if states is None else states)
        self.labels = list(
            ["needs-wilson", "agent-filed"] if labels is None else labels
        )
        self.workspace_labels = list(workspace_labels or [])
        self.issues: dict[str, FakeIssue] = {}
        self.calls: list[str] = []
        self.mutations: list[tuple[str, dict]] = []
        self.fail_with: Exception | None = None
        self.http_error: tuple[int, dict] | None = None
        self.clock = 0
        self._lock = threading.Lock()

    def add(self, identifier: str, state: str, **kw: Any) -> FakeIssue:
        issue = FakeIssue(identifier, state, **kw)
        self.issues[identifier] = issue
        return issue

    def _find(self, ident: str) -> FakeIssue | None:
        for issue in self.issues.values():
            if ident in (issue.id, issue.identifier):
                return issue
        return None

    def _page(self, items: list[dict], after: str | None) -> dict:
        start = int(after) if after else 0
        end = start + self.page_size
        return {
            "nodes": items[start:end],
            "pageInfo": {
                "hasNextPage": end < len(items),
                "endCursor": str(end) if end < len(items) else None,
            },
        }

    def _data(self, query: str, v: dict) -> dict:
        if query.startswith("mutation"):
            return self._mutate(query, v)
        if "viewer" in query:
            return {"viewer": {"id": self.viewer}}
        if "team(id:" in query:
            names = self.states if "states(" in query else self.labels
            prefix = "state" if "states(" in query else "label"
            nodes = [{"id": f"{prefix}-{n}", "name": n} for n in names]
            key = "states" if "states(" in query else "labels"
            return {"team": {key: self._page(nodes, v.get("after"))}}
        if "issueLabels(" in query:
            nodes = [
                {"id": f"label-{n}", "name": n, "team": None}
                for n in self.workspace_labels
                if n == v["name"]
            ]
            return {"issueLabels": self._page(nodes, v.get("after"))}
        if "issues(" in query:
            nodes = [
                {
                    "id": i.id,
                    "identifier": i.identifier,
                    "state": {"name": i.state},
                    "startedAt": i.started_at,
                    "completedAt": i.completed_at,
                    "updatedAt": i.updated_at,
                    "attachments": {"nodes": [{"url": u} for u in i.attachments]},
                }
                for i in self.issues.values()
                if i.state in v["states"] or i.updated_at >= v["since"]
            ]
            return {"issues": self._page(nodes, v.get("after"))}
        issue = self._find(v["id"])
        if issue is None:
            return {"issue": None}
        if "comments(" in query:
            nodes = [{"body": b} for b in issue.comments]
            return {"issue": {"comments": self._page(nodes, v.get("after"))}}
        if "history(" in query:
            return {"issue": {"history": self._page(issue.history, v.get("after"))}}
        return {
            "issue": {
                "id": issue.id,
                "identifier": issue.identifier,
                "createdAt": issue.created_at,
                "state": {
                    "name": issue.state,
                    "type": STATE_TYPES.get(issue.state, "started"),
                },
                "team": {"id": TEAM},
                "labels": {
                    "nodes": [{"id": f"label-{n}", "name": n} for n in issue.labels]
                },
            }
        }

    def _mutate(self, query: str, v: dict) -> dict:
        if "issueUpdate" in query:
            # Claims and blocks only ever write the status (AFX-142).
            assert set(v) == {"id", "stateId"}, v
            assert "assignee" not in query and "delegate" not in query, query
            issue = self._find(v["id"])
            new_state = v["stateId"].removeprefix("state-")
            self.mutations.append(
                ("state", {"issue": issue.identifier, "to": new_state})
            )
            self.clock += 1
            issue.history.append(
                {
                    "createdAt": f"2026-11-23T00:00:{self.clock:02d}Z",
                    "actor": {"id": self.viewer},
                    "toState": {"name": new_state},
                }
            )
            issue.state = new_state
            return {"issueUpdate": {"success": True}}
        if "issueAddLabel" in query:
            issue = self._find(v["id"])
            name = v["labelId"].removeprefix("label-")
            self.mutations.append(("label", {"issue": issue.identifier, "label": name}))
            issue.labels.append(name)
            return {"issueAddLabel": {"success": True}}
        if "issueRemoveLabel" in query:
            issue = self._find(v["id"])
            name = v["labelId"].removeprefix("label-")
            self.mutations.append(
                ("unlabel", {"issue": issue.identifier, "label": name})
            )
            issue.labels.remove(name)
            return {"issueRemoveLabel": {"success": True}}
        if "commentCreate" in query:
            issue = self._find(v["id"])
            self.mutations.append(("comment", {"issue": issue.identifier}))
            issue.comments.append(v["body"])
            return {"commentCreate": {"success": True}}
        raise AssertionError(f"unexpected mutation {query}")

    def opener(self, req, timeout=None):
        with self._lock:
            return self._open(req)

    def _open(self, req):
        payload = json.loads(req.data)
        query, variables = payload["query"], payload["variables"]
        self.calls.append(query)
        if self.fail_with is not None:
            raise self.fail_with
        if self.http_error is not None:
            code, body = self.http_error
            raise urllib.error.HTTPError(
                req.full_url, code, "err", {}, io.BytesIO(json.dumps(body).encode())
            )
        return _Resp(json.dumps({"data": self._data(query, variables)}).encode())


class FakeClaims:
    """In-memory `claim_store.ClaimStore`: `create` is atomic like
    GitHub's ref create. `before_create` lets a test line racers up."""

    def __init__(self) -> None:
        self.tags: dict = {}
        self.before_create = None
        self._lock = threading.Lock()

    def create(self, name, taker) -> bool:
        if self.before_create is not None:
            self.before_create()
        with self._lock:
            if name in self.tags:
                return False
            self.tags[name] = taker
            return True

    def holder(self, name):
        return self.tags.get(name)
