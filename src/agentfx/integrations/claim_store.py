"""Where AFX ticket claims are serialized (AFX-142).

Linear has no compare-and-set for issue status, so two takers that both
read Ready could both move a ticket to In Progress. The claim is
serialized on GitHub instead: creating a git ref is atomic, and GitHub
refuses a second create of the same ref with 422 "Reference already
exists". The winner is whoever creates

    refs/tags/afx-claim/<AFX-n>/<when the ticket last entered Ready>

as an annotated tag whose message names the taker. The Ready timestamp
in the name means a ticket Wilson puts back into Ready (failed deploy, a
fleet rollback) gets a fresh claim slot; the old tags are history.

Every taker already holds GitHub credentials for this repo (Wilson's
`gh`, the fleet's `GITHUB_TOKEN`, the Actions token that runs keyless
requests), so this needs no new service, setting or secret.

Standard library only, like `linear_delivery`.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "widarecu/agentfx"
TAG_PREFIX = "afx-claim/"
DEFAULT_TIMEOUT_SECONDS = 20.0

Opener = Callable[..., Any]


class ClaimStoreError(RuntimeError):
    """GitHub call failed (HTTP error, network or bad payload)."""


@dataclass(frozen=True)
class Taker:
    """Who takes a ticket: a name people recognise and a link to the
    session or branch doing the work."""

    name: str
    link: str = ""

    def record(self) -> str:
        return json.dumps({"taker": self.name, "link": self.link}, sort_keys=True)

    @classmethod
    def parse(cls, text: str) -> Taker | None:
        try:
            data = json.loads(text.strip())
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("taker"):
            return None
        return cls(str(data["taker"]), str(data.get("link") or ""))


class ClaimStore(Protocol):
    def create(self, name: str, taker: Taker) -> bool:
        """Atomically create claim `name` for `taker`. False when it
        already exists (someone else, or this taker earlier, holds it)."""

    def holder(self, name: str) -> Taker | None:
        """The taker recorded on claim `name`, or None when absent."""


class GitHubClaims:
    """Claims as annotated tags in the delivery repo, through GitHub REST."""

    def __init__(
        self,
        token: str,
        *,
        repo: str = DEFAULT_REPO,
        api: str = GITHUB_API,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Opener | None = None,
    ) -> None:
        if not token:
            raise ClaimStoreError("no GitHub token (GH_TOKEN, GITHUB_TOKEN or gh auth)")
        self._token = token
        self.repo = repo
        self._api = api.rstrip("/")
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def from_env(cls, **kwargs: Any) -> GitHubClaims:
        token = (
            os.environ.get("GH_TOKEN", "").strip()
            or os.environ.get("GITHUB_TOKEN", "").strip()
            or _gh_auth_token()
        )
        repo = os.environ.get("GITHUB_REPOSITORY", "").strip() or DEFAULT_REPO
        return cls(token, repo=kwargs.pop("repo", repo), **kwargs)

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        req = urllib.request.Request(
            f"{self._api}/repos/{self.repo}/{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raw = err.read() if err.fp else b""
            raise _HttpError(err.code, raw.decode("utf-8", "replace")) from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ClaimStoreError(f"github unreachable: {err}") from err
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as err:
            raise ClaimStoreError(f"github non-json response: {raw[:200]!r}") from err

    def create(self, name: str, taker: Taker) -> bool:
        main = self.call("GET", "git/ref/heads/main")["object"]["sha"]
        tag = self.call(
            "POST",
            "git/tags",
            {"tag": name, "message": taker.record(), "object": main, "type": "commit"},
        )
        try:
            self.call(
                "POST", "git/refs", {"ref": f"refs/tags/{name}", "sha": tag["sha"]}
            )
        except _HttpError as err:
            if err.code == 422 and "already exists" in err.body:
                return False
            raise
        return True

    def holder(self, name: str) -> Taker | None:
        try:
            ref = self.call("GET", f"git/ref/tags/{name}")
        except _HttpError as err:
            if err.code == 404:
                return None
            raise
        obj = ref.get("object") or {}
        if obj.get("type") != "tag":
            return None
        tag = self.call("GET", f"git/tags/{obj['sha']}")
        return Taker.parse(tag.get("message") or "")

    # Keyless requests (`afx.py request run`)

    def set_status(
        self, sha: str, state: str, description: str, target_url: str = ""
    ) -> None:
        body = {
            "state": state,
            "context": REQUEST_STATUS_CONTEXT,
            "description": description[:140],
        }
        if target_url:
            body["target_url"] = target_url
        self.call("POST", f"statuses/{sha}", body)

    def request_status(self, sha: str) -> dict | None:
        statuses = self.call("GET", f"commits/{sha}/statuses") or []
        return next(
            (s for s in statuses if s.get("context") == REQUEST_STATUS_CONTEXT), None
        )

    def delete_branch(self, branch: str) -> None:
        try:
            self.call("DELETE", f"git/refs/heads/{branch}")
        except _HttpError as err:
            if err.code not in (404, 422):
                raise


REQUEST_STATUS_CONTEXT = "afx/request"


class _HttpError(ClaimStoreError):
    def __init__(self, code: int, body: str) -> None:
        super().__init__(f"github http {code}: {body[:300]}")
        self.code = code
        self.body = body


def _gh_auth_token() -> str:
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def claim_tag(identifier: str, ready_since: str) -> str:
    """`afx-claim/AFX-142/20261124063752868`: digits of the ISO time the
    ticket last entered Ready, so every Ready period has one slot."""
    stamp = "".join(c for c in ready_since if c.isdigit()) or "0"
    return f"{TAG_PREFIX}{identifier}/{stamp}"


__all__ = [
    "ClaimStore",
    "ClaimStoreError",
    "GitHubClaims",
    "REQUEST_STATUS_CONTEXT",
    "Taker",
    "claim_tag",
]
