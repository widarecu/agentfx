"""Stdlib Linear client for delivery automation (`scripts/afx.py`).

Runs from GitHub Actions with only `python3` (no repo dependencies), so it
uses `urllib` and never imports `agentfx.api.core.config`. State and label
IDs are looked up by name on the team, never hard-coded.

Errors raise `LinearError`; callers decide whether a failure is fatal.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

LINEAR_URL = "https://api.linear.app/graphql"

Transport = Callable[[str, dict], dict]


class LinearError(RuntimeError):
    """A Linear API call failed (HTTP, network, or GraphQL `errors`)."""


def http_transport(api_key: str, *, url: str = LINEAR_URL, timeout: float = 20):
    """GraphQL over HTTPS. Linear puts the real error in the body of HTTP 400s,
    so the body is parsed on both success and `HTTPError`."""

    def call(query: str, variables: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            try:
                detail: Any = json.loads(raw).get("errors") or raw.decode()
            except ValueError:
                detail = raw.decode("utf-8", "replace")
            raise LinearError(f"linear graphql {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LinearError(f"linear unreachable: {exc}") from exc
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise LinearError(f"linear non-json response: {body[:200]!r}") from exc
        if payload.get("errors"):
            raise LinearError(f"linear graphql error: {payload['errors']}")
        return payload["data"]

    return call


def _is_not_found(exc: LinearError) -> bool:
    return "not found" in str(exc).lower()


class LinearDelivery:
    """Issue state, label and marker-deduplicated comment operations."""

    def __init__(self, transport: Transport, *, team_key: str = "AFX") -> None:
        self._gql = transport
        self.team_key = team_key
        self._issues: dict[str, dict | None] = {}
        self._state_ids: dict[str, str] = {}
        self._label_ids: dict[str, str] = {}

    def _mutate(self, query: str, variables: dict) -> None:
        """Run a mutation; Linear reports a rejected write as `success: false`."""
        data = self._gql(query, variables) or {}
        results = [v for v in data.values() if isinstance(v, dict)]
        if not results or not all(r.get("success") for r in results):
            raise LinearError(f"linear rejected write: {data}")

    def issue(self, identifier: str) -> dict | None:
        """The issue with state (name, type) and labels, or None if missing."""
        if identifier not in self._issues:
            try:
                data = self._gql(
                    "query($id: String!) { issue(id: $id) { id identifier "
                    "state { id name type } labels { nodes { id name } } } }",
                    {"id": identifier},
                )
            except LinearError as exc:
                if not _is_not_found(exc):
                    raise
                data = {"issue": None}
            self._issues[identifier] = data.get("issue")
        return self._issues[identifier]

    def comment_bodies(self, issue: dict) -> list[str]:
        """Every comment body on the issue, all pages. Cached on the issue."""
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

    def state_id(self, name: str) -> str:
        if name not in self._state_ids:
            nodes = self._gql(
                "query($team: String!, $name: String!) { workflowStates(filter: "
                "{ team: { key: { eq: $team } }, name: { eq: $name } }) "
                "{ nodes { id name } } }",
                {"team": self.team_key, "name": name},
            )["workflowStates"]["nodes"]
            if not nodes:
                raise LinearError(f"no state {name!r} on team {self.team_key}")
            self._state_ids[name] = nodes[0]["id"]
        return self._state_ids[name]

    def label_id(self, name: str) -> str:
        """Team label by name, else a workspace label of that name."""
        if name not in self._label_ids:
            nodes = self._gql(
                "query($name: String!) { issueLabels(filter: { name: { eq: $name } }) "
                "{ nodes { id name team { key } } } }",
                {"name": name},
            )["issueLabels"]["nodes"]
            team = [
                n for n in nodes if (n.get("team") or {}).get("key") == self.team_key
            ]
            workspace = [n for n in nodes if not n.get("team")]
            match = (team or workspace or [None])[0]
            if match is None:
                raise LinearError(f"no label {name!r} for team {self.team_key}")
            self._label_ids[name] = match["id"]
        return self._label_ids[name]

    def set_state(self, issue: dict, name: str) -> bool:
        """Move the issue to the named state. False if it was already there."""
        if (issue.get("state") or {}).get("name") == name:
            return False
        state_id = self.state_id(name)
        self._mutate(
            "mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, "
            "input: { stateId: $stateId }) { success } }",
            {"id": issue["id"], "stateId": state_id},
        )
        issue["state"] = {"id": state_id, "name": name, "type": None}
        return True

    @staticmethod
    def has_label(issue: dict, name: str) -> bool:
        nodes = (issue.get("labels") or {}).get("nodes") or []
        return any(n.get("name") == name for n in nodes)

    def add_label(self, issue: dict, name: str) -> bool:
        if self.has_label(issue, name):
            return False
        label_id = self.label_id(name)
        self._mutate(
            "mutation($id: String!, $labelId: String!) { "
            "issueAddLabel(id: $id, labelId: $labelId) { success } }",
            {"id": issue["id"], "labelId": label_id},
        )
        issue.setdefault("labels", {}).setdefault("nodes", []).append(
            {"id": label_id, "name": name}
        )
        return True

    def remove_label(self, issue: dict, name: str) -> bool:
        if not self.has_label(issue, name):
            return False
        label_id = self.label_id(name)
        self._mutate(
            "mutation($id: String!, $labelId: String!) { "
            "issueRemoveLabel(id: $id, labelId: $labelId) { success } }",
            {"id": issue["id"], "labelId": label_id},
        )
        issue["labels"]["nodes"] = [
            n for n in issue["labels"]["nodes"] if n.get("name") != name
        ]
        return True

    def comment_once(self, issue: dict, body: str, marker: str) -> bool:
        """Post `body` unless a comment already carries `marker`."""
        bodies = self.comment_bodies(issue)
        if any(marker in b for b in bodies):
            return False
        self._mutate(
            "mutation($id: String!, $body: String!) { "
            "commentCreate(input: { issueId: $id, body: $body }) { success } }",
            {"id": issue["id"], "body": body},
        )
        bodies.append(body)
        return True
