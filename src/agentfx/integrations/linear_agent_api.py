"""Linear Agent API client — outbound mutations from the AgentFX fleet.

Where `integrations/linear_client.py` is for the fleet's `file_finding`
Triage path (API-key auth, Wilson's identity), this module is for the
**agent-user** actions we take as the "AgentFX Platform Engineer" app:

- `get_app_token()` — OAuth 2.0 `client_credentials` grant, returns
  an `actor=app` access token (~30-day TTL per Linear's docs).
  Cached in-process; re-requested on 401 or expiry.
- `emit_agent_activity()` — GraphQL `agentActivityCreate` mutation,
  posts a message inside a specific agent session. Linear mirrors
  certain activity types (`response`, `elicitation`, `error`) as
  issue comments automatically; `thought` and `action` stay in the
  session UI.

Design rules:

- Never raise. Every function returns `(ok: bool, detail: str)` so
  callers can keep orchestrating even when Linear is down.
- No external deps beyond `requests` (already in the project).
- No globals Wilson has to configure. Credentials come from
  `LINEAR_AGENT_CLIENT_ID` + `LINEAR_AGENT_CLIENT_SECRET` set by
  the AFX-66 Phase 1 PR.
- Secrets never logged (client_secret, access_token).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import requests

from agentfx.api.core.config import settings

logger = logging.getLogger(__name__)

_LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
_LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_SECONDS = 10.0

# Refresh the access token this many seconds BEFORE its documented
# expiry — avoids the "token expired by 1s" race where we send a stale
# token and have to burn an API call to discover it.
_TOKEN_REFRESH_BUFFER_SECONDS = 60


AgentActivityType = Literal["thought", "elicitation", "action", "response", "error"]


@dataclass
class _CachedToken:
    """One module-level cached `actor=app` token + its expiry."""

    token: str
    expires_at: datetime


# Module-level cache. Agent-wide process-local singleton; a Lock
# prevents two concurrent webhook handlers from both racing a fetch
# when the token expires. Simple and sufficient for staging load.
_token_cache: _CachedToken | None = None
_token_lock = threading.Lock()


def get_app_token(*, force_refresh: bool = False) -> tuple[str | None, str]:
    """Return a valid `actor=app` access token, caching across calls.

    `force_refresh=True` discards the cached token and requests a new
    one — used by callers that got a 401 on a previous call.

    Returns `(token, "")` on success or `(None, reason)` on failure.
    Failure reasons are human-readable:
    - `"no_credentials"` — LINEAR_AGENT_CLIENT_ID / _SECRET not set
    - `"token_endpoint:HTTP <code>"` — Linear's /oauth/token returned
      non-200; the status code is in the reason
    - `"token_endpoint:<exception>"` — network / parse failure
    - `"malformed_response"` — 200 but no `access_token` in the body
    """
    global _token_cache

    client_id = settings.LINEAR_AGENT_CLIENT_ID
    client_secret = settings.LINEAR_AGENT_CLIENT_SECRET
    if not client_id or not client_secret:
        return None, "no_credentials"

    now = datetime.now(UTC)
    with _token_lock:
        if (
            not force_refresh
            and _token_cache is not None
            and _token_cache.expires_at
            > now + timedelta(seconds=_TOKEN_REFRESH_BUFFER_SECONDS)
        ):
            return _token_cache.token, ""

        try:
            resp = requests.post(
                _LINEAR_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    # Scope must be subset of what the app is
                    # registered for; "read" is always granted.
                    # `app:assignable` + `app:mentionable` carry
                    # the agent capabilities (per Linear's docs).
                    "scope": "read,app:assignable,app:mentionable,comments:create,issues:create",
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.exception("linear_agent_api: token endpoint request failed")
            return None, f"token_endpoint:{type(exc).__name__}"

        if resp.status_code != 200:
            # Don't log the body — Linear's errors can echo the
            # request including client_id; keep that out of logs.
            logger.warning(
                "linear_agent_api: token endpoint returned HTTP %s",
                resp.status_code,
            )
            return None, f"token_endpoint:HTTP {resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return None, "malformed_response"

        token = data.get("access_token")
        if not token or not isinstance(token, str):
            return None, "malformed_response"

        # Linear returns expires_in in seconds; typically ~2.5M (30d).
        # Fall back to 1 hour if missing so a buggy response doesn't
        # pin an incorrect "never expires" cache.
        expires_in = data.get("expires_in")
        try:
            expires_seconds = int(expires_in) if expires_in is not None else 3600
        except (TypeError, ValueError):
            expires_seconds = 3600

        _token_cache = _CachedToken(
            token=token,
            expires_at=now + timedelta(seconds=expires_seconds),
        )
        return token, ""


_AGENT_ACTIVITY_MUTATION = """
mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) {
    success
    agentActivity { id }
  }
}
"""


def emit_agent_activity(
    *,
    agent_session_id: str,
    content_type: AgentActivityType,
    body: str,
    ephemeral: bool = False,
) -> tuple[bool, str]:
    """Post an activity into a Linear agent session.

    - `thought`: internal note, stays in the session UI only.
    - `elicitation`: asks the user a question. Auto-posts as an issue
      comment and sets session state to `awaitingInput`.
    - `response`: final deliverable. Auto-posts as an issue comment
      and ends the session.
    - `error`: failure. Auto-posts as an issue comment and ends.
    - `action`: tool call — we don't emit this type from Phase 2.

    `ephemeral=True` is only meaningful for `thought` and `action`
    (hint that the activity shouldn't persist in history — useful for
    intermediate "thinking" chatter).

    Returns `(True, "")` on success, `(False, reason)` on failure.
    Never raises; a Linear outage must not break fleet dispatch.

    On 401, retries ONCE with a force-refreshed token — handles the
    rare case where the cached token was revoked mid-flight.
    """
    if not agent_session_id:
        return False, "no_session_id"
    if not body:
        return False, "empty_body"

    # Build input shape per Linear's AgentActivityCreateInput — the
    # content field is a union discriminated by `type`. Phase 2 only
    # uses the body-bearing variants.
    variables = {
        "input": {
            "agentSessionId": agent_session_id,
            "content": {"type": content_type, "body": body},
        }
    }
    if ephemeral and content_type in ("thought", "action"):
        variables["input"]["ephemeral"] = True

    for attempt in (0, 1):
        token, token_err = get_app_token(force_refresh=(attempt == 1))
        if token is None:
            return False, f"token:{token_err}"

        try:
            resp = requests.post(
                _LINEAR_GRAPHQL_URL,
                json={"query": _AGENT_ACTIVITY_MUTATION, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.exception("linear_agent_api: agentActivityCreate request failed")
            return False, f"request:{type(exc).__name__}"

        if resp.status_code == 401 and attempt == 0:
            # Token likely stale — retry once with a forced refresh.
            logger.info(
                "linear_agent_api: 401 on agentActivityCreate — refreshing token"
            )
            continue

        if resp.status_code != 200:
            # Linear responds to GraphQL *validation* errors with 200 +
            # an `errors` array, but reserves actual HTTP non-2xx for
            # request-level problems: missing scope on the token,
            # malformed input shape, authz failures on the mutation.
            # The body usually carries the exact message. Log the first
            # 500 chars so the operator can diagnose from Railway logs
            # instead of having to curl Linear by hand.
            body_snippet = (resp.text or "")[:500]
            logger.warning(
                "linear_agent_api: agentActivityCreate HTTP %s body=%r",
                resp.status_code,
                body_snippet,
            )
            return False, f"HTTP {resp.status_code}:{body_snippet[:200]}"

        try:
            data = resp.json()
        except ValueError:
            return False, "malformed_response"

        errors = data.get("errors") or []
        if errors:
            # Surface the first error's message — usually enough to
            # diagnose (missing scope, invalid session id, rate limit).
            first = errors[0] if isinstance(errors[0], dict) else {}
            msg = first.get("message") or "unknown"
            return False, f"graphql:{msg}"

        result = (data.get("data") or {}).get("agentActivityCreate") or {}
        if not result.get("success"):
            return False, "mutation_returned_success_false"

        return True, ""

    return False, "unreachable"  # pragma: no cover


# Convenience wrappers so call sites don't repeat the content_type
# literal. Thin by design — they exist to make call-site intent
# obvious at a glance.


def emit_thought(agent_session_id: str, body: str) -> tuple[bool, str]:
    """Internal progress note — stays in the session UI, not mirrored
    to the issue as a comment. Use for "picking up X", "session Y
    started" status pings that satisfy Linear's 10s responsiveness
    requirement."""
    return emit_agent_activity(
        agent_session_id=agent_session_id,
        content_type="thought",
        body=body,
    )


def emit_error(agent_session_id: str, body: str) -> tuple[bool, str]:
    """Terminal failure. Auto-posts as an issue comment so Wilson sees
    the reason without digging into logs, and ends the session."""
    return emit_agent_activity(
        agent_session_id=agent_session_id,
        content_type="error",
        body=body,
    )


def emit_response(agent_session_id: str, body: str) -> tuple[bool, str]:
    """Final deliverable — e.g. 'Opened PR #N against feat/agent-fleet'.
    Auto-posts as an issue comment and ends the session."""
    return emit_agent_activity(
        agent_session_id=agent_session_id,
        content_type="response",
        body=body,
    )


# Only exported in tests — never from production code.
def _reset_token_cache_for_tests() -> None:
    """Flush the module-level token cache. Pytest-only helper so
    test cases don't bleed into each other via the singleton."""
    global _token_cache
    with _token_lock:
        _token_cache = None


__all__ = [
    "get_app_token",
    "emit_agent_activity",
    "emit_thought",
    "emit_error",
    "emit_response",
    "AgentActivityType",
]
