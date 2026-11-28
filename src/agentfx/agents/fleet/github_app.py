"""GitHub App installation tokens for the hosted Platform Engineer (AFX-172).

The hosted PE used to push with a personal token owned by Wilson, so
everything it did was recorded as Wilson. A GitHub App (`agentfx-pe`)
acts as its own identity (`agentfx-pe[bot]`). This module turns the
App's client ID + private key into a short-lived (one hour) token
restricted to this repository:

1. sign a JWT (RS256, issuer = client ID, at most 10 minutes of life);
2. `GET /repos/{owner}/{repo}/installation` -> installation id;
3. `POST /app/installations/{id}/access_tokens` -> `ghs_...` token.

Nothing here ever logs or returns the private key, the JWT or the token
in an error reason. Callers receive a `MintResult`; on failure `token`
is empty and `reason` says what went wrong in plain words.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock

import requests
from jose import jwt

from agentfx.api.core.config import settings

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_HTTP_TIMEOUT_SECONDS = 15
_JWT_BACKDATE_SECONDS = 60  # tolerate clock drift (GitHub advises this)
_JWT_LIFETIME_SECONDS = 9 * 60  # GitHub caps a JWT at 10 minutes
_PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}

# Installation tokens live one hour. Sessions can run longer (the sweeper
# only reclaims a RUNNING row after 4h), so the kickoff tells the agent.
TOKEN_LIFETIME_MINUTES = 60

# Tokens minted by this process, so text scrubbing can replace the exact
# value even if it does not match the `ghs_` pattern. Bounded.
_minted: list[str] = []
_minted_lock = Lock()
_MINTED_KEEP = 20


@dataclass(frozen=True)
class MintResult:
    token: str = ""
    expires_at: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.token)


def app_state() -> str:
    """`off` (no App settings), `on` (both set) or `partial` (one set)."""
    client_id = (getattr(settings, "AGENT_PE_GITHUB_APP_CLIENT_ID", "") or "").strip()
    key = (getattr(settings, "AGENT_PE_GITHUB_APP_PRIVATE_KEY", "") or "").strip()
    if client_id and key:
        return "on"
    if client_id or key:
        return "partial"
    return "off"


def minted_tokens() -> list[str]:
    with _minted_lock:
        return list(_minted)


def _remember(token: str) -> None:
    with _minted_lock:
        _minted.append(token)
        del _minted[:-_MINTED_KEEP]


def normalise_pem(raw: str) -> str:
    """A PEM pasted into an env var may carry literal `\\n` sequences."""
    pem = (raw or "").strip()
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\r", "").replace("\\n", "\n")
    return pem


def _github_error(resp: requests.Response) -> str:
    try:
        message = str(resp.json().get("message", ""))[:120]
    except Exception:
        message = ""
    return f"HTTP {resp.status_code}" + (f" ({message})" if message else "")


def build_app_jwt(
    client_id: str, private_key_pem: str, *, now: float | None = None
) -> str:
    issued = int(now if now is not None else time.time())
    claims = {
        "iss": client_id,
        "iat": issued - _JWT_BACKDATE_SECONDS,
        "exp": issued + _JWT_LIFETIME_SECONDS,
    }
    return jwt.encode(claims, private_key_pem, algorithm="RS256")


def mint_installation_token(*, owner: str, repo: str) -> MintResult:
    """Mint a one-hour token for `owner/repo`. Never raises."""
    client_id = (settings.AGENT_PE_GITHUB_APP_CLIENT_ID or "").strip()
    pem = normalise_pem(settings.AGENT_PE_GITHUB_APP_PRIVATE_KEY)
    if not client_id or not pem:
        return MintResult(reason="GitHub App client ID or private key is not set")

    try:
        app_jwt = build_app_jwt(client_id, pem)
    except Exception:
        # Do not include the exception text: it can echo key material.
        return MintResult(
            reason="could not sign the GitHub App JWT: the private key is not a valid RSA PEM"
        )

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = requests.get(
            f"{_API_ROOT}/repos/{owner}/{repo}/installation",
            headers=headers,
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return MintResult(
            reason=f"GitHub installation lookup failed: network error ({type(exc).__name__})"
        )
    if resp.status_code != 200:
        return MintResult(
            reason=(
                f"GitHub installation lookup for {owner}/{repo} failed: "
                f"{_github_error(resp)}. Is the agentfx-pe App installed on the repository?"
            )
        )
    try:
        installation_id = resp.json()["id"]
    except Exception:
        return MintResult(
            reason="GitHub installation lookup returned no installation id"
        )

    try:
        resp = requests.post(
            f"{_API_ROOT}/app/installations/{installation_id}/access_tokens",
            headers=headers,
            json={"repositories": [repo], "permissions": dict(_PERMISSIONS)},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return MintResult(
            reason=f"GitHub token request failed: network error ({type(exc).__name__})"
        )
    if resp.status_code != 201:
        return MintResult(reason=f"GitHub token request failed: {_github_error(resp)}")
    try:
        body = resp.json()
        token = str(body["token"])
        expires_at = str(body.get("expires_at", ""))
    except Exception:
        return MintResult(reason="GitHub token response had no token")

    _remember(token)
    logger.info(
        "github_app: minted installation token for %s/%s (expires %s)",
        owner,
        repo,
        expires_at or "unknown",
    )
    return MintResult(token=token, expires_at=expires_at)


def fleet_token(*, owner: str, repo: str, fallback: str = "") -> tuple[str | None, str]:
    """The token the fleet's own GitHub calls use (claims, PR lookups).

    App set up -> a freshly minted installation token. No App setting at
    all -> `fallback` (the legacy `GITHUB_TOKEN`, possibly empty). App half
    set up, or the mint fails -> `(None, reason)`: callers fail closed and
    never fall back to the personal token once the App is configured.
    """
    state = app_state()
    if state == "off":
        return fallback, ""
    if state != "on":
        return None, (
            "the PE GitHub App is only half configured (client id or private "
            "key missing); not falling back to GITHUB_TOKEN"
        )
    minted = mint_installation_token(owner=owner, repo=repo)
    if not minted.ok:
        return None, f"the PE GitHub App token could not be minted: {minted.reason}"
    return minted.token, ""
