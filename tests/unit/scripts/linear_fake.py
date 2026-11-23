"""In-memory stand-in for the Linear GraphQL endpoint (AFX-118 tests).

`FakeLinear.opener` replaces `urllib.request.urlopen` in
`linear_delivery.LinearClient`, so tests exercise the real request,
error and pagination code without any network call.
"""

from __future__ import annotations

import io
import json
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


@dataclass
class FakeIssue:
    identifier: str
    state: str
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

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
    ) -> None:
        self.page_size = page_size
        self.viewer = viewer
        self.states = list(STATES if states is None else states)
        self.labels = list(
            ["needs-wilson", "agent-filed"] if labels is None else labels
        )
        self.issues: dict[str, FakeIssue] = {}
        self.calls: list[str] = []
        self.mutations: list[tuple[str, dict]] = []
        self.fail_with: Exception | None = None
        self.http_error: tuple[int, dict] | None = None
        self.clock = 0

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
