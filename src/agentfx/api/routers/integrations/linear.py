"""Linear Agent integration — webhook receiver + OAuth install callback.

**Webhook (AFX-66 Phase 1 — log-only).** Linear POSTs an
`AgentSessionEvent` to `/agent-webhook` when the PE is delegated an
issue, @mentioned in a comment, or receives a reply inside an open
agent session. The handler verifies the signature + replay window +
idempotency and persists every delivery to `linear_webhook_deliveries`.
No dispatch wiring yet — that lands in Phase 2 once the real webhook
payload shape is confirmed against Linear's docs.

**OAuth callback.** Linear installs a private `actor=app` application
through the standard OAuth authorization-code flow: the admin visits
the authorize URL, approves, Linear redirects to our callback with
a `code`, and **we must POST that code to Linear's token endpoint**
for the install to finalize. Without the exchange, Linear rolls the
install back (seen 2026-11-05: install consent screen approved, but
`Installation status: No installations` in the app page because the
callback returned 404 instead of exchanging the code).

The returned token belongs to the authorizing user and we discard
it; subsequent API calls use the `client_credentials` grant for
`actor=app` tokens (Phase 2).

Hard rules from Linear's docs — see the design doc on AFX-66:

- HMAC-SHA256 of the **raw request body** against the webhook's
  signing secret, hex-encoded, compared to the `Linear-Signature`
  header. `hmac.compare_digest` for constant-time comparison.
- `webhookTimestamp` in the body (ms since epoch) must be within
  60 s of now — replay guard.
- `Linear-Delivery` header is a UUID per delivery; primary key of
  the persistence table so duplicate deliveries hit an
  IntegrityError on insert and short-circuit without reprocessing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.database import get_db
from ...models.linear_webhook_delivery import LinearWebhookDelivery

logger = logging.getLogger(__name__)

router = APIRouter()

# Linear's documented replay window on webhookTimestamp. See the
# AFX-66 design doc + https://linear.app/developers/webhooks.
_REPLAY_WINDOW_SECONDS = 60

# Linear's OAuth token endpoint — standard across both
# authorization-code and client-credentials grants.
_LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"

# Token-exchange HTTP timeout. Kept tight because the browser is
# waiting on this callback and 5+ s on a hanging Linear request would
# look like our page crashed. Linear's token endpoint is usually
# <500ms in practice.
_TOKEN_EXCHANGE_TIMEOUT_S = 8.0


@router.post("/agent-webhook", status_code=status.HTTP_200_OK)
async def linear_agent_webhook(
    request: Request,
    db: Session = Depends(get_db),
    linear_signature: str | None = Header(default=None, alias="Linear-Signature"),
    linear_delivery: str | None = Header(default=None, alias="Linear-Delivery"),
) -> dict[str, str]:
    """Receive one Linear AgentSessionEvent delivery.

    Verify → replay-check → idempotent persist → log. Returns:

    - `{"status": "flag_off"}` if the master toggle is off.
    - `{"status": "duplicate"}` if we've already processed this
      `Linear-Delivery` value.
    - `{"status": "logged"}` on first-time receipt.

    Raises 400 for malformed payloads, 401 for signature failures,
    500 only if the signing secret is unset (misconfiguration).
    """
    if not settings.LINEAR_AGENT_ENABLED:
        # Flag off is a valid, silent state — we still ACK so Linear
        # doesn't retry forever against a disabled receiver.
        return {"status": "flag_off"}

    if not settings.LINEAR_AGENT_WEBHOOK_SECRET:
        # Misconfiguration: flag on but no signing secret. Fail loud
        # with a 500 so operators catch it rather than silently trust
        # every POST.
        logger.error(
            "linear_webhook: LINEAR_AGENT_ENABLED=true but "
            "LINEAR_AGENT_WEBHOOK_SECRET is empty — cannot verify requests"
        )
        raise HTTPException(
            status_code=500,
            detail="linear webhook secret not configured",
        )

    if not linear_signature:
        raise HTTPException(status_code=401, detail="missing Linear-Signature header")
    if not linear_delivery:
        raise HTTPException(status_code=400, detail="missing Linear-Delivery header")

    # Signature verification uses the raw body bytes exactly as
    # received. If we parsed JSON first and re-serialised, whitespace
    # / key-ordering could flip the HMAC.
    raw_body = await request.body()
    expected = hmac.new(
        settings.LINEAR_AGENT_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, linear_signature):
        logger.warning("linear_webhook: invalid signature delivery=%s", linear_delivery)
        raise HTTPException(status_code=401, detail="invalid signature")

    # Parse after verification so a bad-JSON attacker can't short-circuit
    # the signature check.
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning(
            "linear_webhook: body is not valid JSON delivery=%s err=%s",
            linear_delivery,
            exc,
        )
        raise HTTPException(status_code=400, detail="body is not valid JSON") from exc

    # Replay-window check on the body's webhookTimestamp (ms since
    # epoch). Absent timestamp is a soft-skip — log and continue —
    # rather than reject, because Linear's docs mention the field but
    # don't make every event payload include it; we'd rather accept
    # than drop valid traffic over a parse-defensive check.
    wh_ts_ms = payload.get("webhookTimestamp")
    if isinstance(wh_ts_ms, (int, float)):
        wh_ts = datetime.fromtimestamp(wh_ts_ms / 1000.0, tz=UTC)
        now = datetime.now(tz=UTC)
        drift = abs((now - wh_ts).total_seconds())
        if drift > _REPLAY_WINDOW_SECONDS:
            logger.warning(
                "linear_webhook: outside replay window delivery=%s drift=%.1fs",
                linear_delivery,
                drift,
            )
            raise HTTPException(status_code=400, detail="replay window exceeded")

    action = _as_str(payload.get("action")) or ""
    agent_session_id = _extract_session_id(payload)

    # Idempotent persist. If this Linear-Delivery was already received,
    # the PK collision raises IntegrityError and we return a
    # "duplicate" ACK. Phase 2 branches on `action=created` to
    # dispatch via a background task (see below); Phase 1 was
    # purely log-only.
    row = LinearWebhookDelivery(
        delivery_id=linear_delivery,
        event_action=action[:32] if action else None,
        agent_session_id=agent_session_id[:64] if agent_session_id else None,
        raw_body=payload,
        processing_status="logged",
    )
    try:
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(
            "linear_webhook: duplicate delivery=%s — acking without reprocess",
            linear_delivery,
        )
        return {"status": "duplicate"}

    logger.info(
        "linear_webhook: logged delivery=%s action=%s session=%s",
        linear_delivery,
        action or "<unknown>",
        agent_session_id or "<unknown>",
    )

    # AFX-66 Phase 2 — on `action=created`, fire a background task
    # that emits the responsiveness `thought` activity and dispatches
    # to the Managed Agents workspace. The handler itself returns 200
    # immediately (Linear's 5s SLA); the dispatcher has its own 10s
    # budget to emit the first activity.
    #
    # `asyncio.create_task` + `asyncio.to_thread` keeps the (sync)
    # dispatcher off the event loop so a slow adapter can't stall
    # other webhooks.
    if action == "created":
        import asyncio

        from agentfx.agents.fleet.linear_webhook_dispatch import (
            dispatch_from_linear_webhook,
        )

        # Shallow-copy the payload so later mutations can't race.
        payload_snapshot = dict(payload)

        async def _bg() -> None:
            try:
                await asyncio.to_thread(
                    dispatch_from_linear_webhook,
                    delivery_id=linear_delivery,
                    payload=payload_snapshot,
                )
            except Exception:  # noqa: BLE001 — bg tasks mustn't bubble
                logger.exception(
                    "linear_webhook: dispatch background task crashed delivery=%s",
                    linear_delivery,
                )

        asyncio.create_task(_bg())

    return {"status": "logged"}


def _as_str(value: Any) -> str | None:
    """Coerce arbitrary JSON scalar to str if it is one, else None.
    Avoids the `str(None)` / `str(dict)` footguns on untrusted input."""
    if isinstance(value, str):
        return value
    return None


def _extract_session_id(payload: dict[str, Any]) -> str | None:
    """Best-effort agent session id lookup across likely payload shapes.

    Linear's docs name the mutation input field (`agentSessionId`) but
    not the exact webhook field that carries it. We probe the common
    shapes rather than guess one — Phase 1 persists `raw_body` so we
    can confirm the actual field against real traffic and tighten this
    helper in Phase 2.

    Returns None rather than raising; a missing id is informational,
    not an auth problem.
    """
    # Shape A: top-level agentSession object
    agent_session = payload.get("agentSession")
    if isinstance(agent_session, dict):
        sid = agent_session.get("id")
        if isinstance(sid, str):
            return sid

    # Shape B: nested under data.agentSession (common for Linear webhooks)
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("agentSession")
        if isinstance(nested, dict):
            sid = nested.get("id")
            if isinstance(sid, str):
                return sid

        # Shape C: data itself is the session (type-tagged payloads)
        if data.get("type") == "AgentSession":
            sid = data.get("id")
            if isinstance(sid, str):
                return sid

    return None


# ─────────────────────────────────────────────────────────────────────
# OAuth install callback — finalizes the app install on Linear's side
# ─────────────────────────────────────────────────────────────────────


@router.get("/oauth/callback")
def linear_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """OAuth 2.0 authorization-code callback for Linear app install.

    Linear redirects the browser here with `?code=...&state=...` after
    the admin approves the authorize screen. We **must** exchange the
    code at Linear's token endpoint for the install to commit — a
    naked 404 at this URL rolls the install back on Linear's side.

    The exchange returns an access token tied to the authorizing user.
    We **discard** it: subsequent agent API calls use the
    `client_credentials` grant (Phase 2) which issues its own
    `actor=app` token. This endpoint's job is purely to close the
    install loop.

    Error semantics:

    - `?error=access_denied` (user clicked Cancel) → 400 with a human
      page explaining they can retry the install URL.
    - Missing `code` with no explicit `error` → 400 (malformed
      redirect).
    - Missing client credentials in settings → 500 (misconfig).
    - Linear's token endpoint returns non-200 → 502 with the response
      body in the page so operators can diagnose from the browser.
    - All success paths return a human-friendly "install complete"
      page the user can close.
    """
    if error:
        logger.warning(
            "linear_oauth_callback: provider returned error=%s desc=%s",
            error,
            error_description,
        )
        return HTMLResponse(
            _render_install_page(
                title="Install failed",
                body=(
                    f"Linear returned <code>{error}</code>"
                    + (f": {error_description}" if error_description else "")
                    + ". You can try again by revisiting the install URL."
                ),
            ),
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            _render_install_page(
                title="Install failed",
                body=(
                    "No authorization code in the redirect. Something "
                    "went wrong at the consent step — try the install "
                    "URL again."
                ),
            ),
            status_code=400,
        )

    client_id = settings.LINEAR_AGENT_CLIENT_ID
    client_secret = settings.LINEAR_AGENT_CLIENT_SECRET
    if not client_id or not client_secret:
        logger.error(
            "linear_oauth_callback: LINEAR_AGENT_CLIENT_ID or "
            "LINEAR_AGENT_CLIENT_SECRET not set — cannot exchange code"
        )
        return HTMLResponse(
            _render_install_page(
                title="Install failed",
                body=(
                    "Server not configured for OAuth yet "
                    "(LINEAR_AGENT_CLIENT_ID / _CLIENT_SECRET missing). "
                    "Set the env vars on Railway and try again."
                ),
            ),
            status_code=500,
        )

    # Derive the redirect_uri from the incoming request so the value
    # sent to Linear's token endpoint matches the value in the
    # original authorize URL exactly — Linear rejects mismatches.
    redirect_uri = _derive_redirect_uri(request)

    try:
        resp = requests.post(
            _LINEAR_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=_TOKEN_EXCHANGE_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.exception("linear_oauth_callback: token exchange request failed")
        return HTMLResponse(
            _render_install_page(
                title="Install failed",
                body=(
                    f"Could not reach Linear's token endpoint: "
                    f"<code>{type(exc).__name__}</code>. Check Railway "
                    f"network egress and retry."
                ),
            ),
            status_code=502,
        )

    if resp.status_code != 200:
        # Include the body in the page — Linear's error responses
        # usually name the specific problem (invalid_grant, redirect
        # URI mismatch, etc.) and an operator reading this in-browser
        # shouldn't have to tail logs to see it.
        snippet = resp.text[:500]
        logger.error(
            "linear_oauth_callback: token exchange failed status=%s body=%s",
            resp.status_code,
            snippet,
        )
        return HTMLResponse(
            _render_install_page(
                title="Install failed",
                body=(
                    f"Linear's token endpoint returned "
                    f"<code>HTTP {resp.status_code}</code>:"
                    f"<pre>{snippet}</pre>"
                    f"Common causes: redirect_uri mismatch (must be "
                    f"exactly <code>{redirect_uri}</code>), expired "
                    f"code, revoked client secret."
                ),
            ),
            status_code=502,
        )

    # Success. We discard the body — the token itself isn't needed
    # (actor=app uses client_credentials for subsequent calls). The
    # install is now committed on Linear's side.
    logger.info(
        "linear_oauth_callback: install committed (state=%s)",
        state or "<none>",
    )
    return HTMLResponse(
        _render_install_page(
            title="Install complete",
            body=(
                "<strong>AgentFX Platform Engineer</strong> is now "
                "installed in your Linear workspace. You can close "
                "this tab.<br><br>"
                "Next: delegate a Linear issue to the agent or "
                "<code>@</code>-mention it in a comment to trigger "
                "the first webhook delivery."
            ),
        ),
        status_code=200,
    )


def _derive_redirect_uri(request: Request) -> str:
    """Reconstruct the callback URL exactly as Linear knows it.

    Linear's token exchange rejects a `redirect_uri` that differs
    even cosmetically (trailing slash, query string, scheme case)
    from the one it saw on the authorize request.

    **Behind a TLS-terminating proxy** (Railway, Heroku, nginx) the
    request arrives at FastAPI with `scheme='http'` because TLS was
    already terminated at the edge. `request.url` reflects that
    inbound scheme — in our case `http://agentfx-staging.up.railway.app/...`
    — but Linear's registered redirect is `https://...` and the
    token endpoint responds `invalid_request: redirect_uri mismatch`
    (observed 2026-11-05).

    Standard fix: trust the proxy's `X-Forwarded-Proto` header and
    use that for the scheme. Railway always sets this to `https`
    for TLS-terminated traffic. Local dev stays correct because
    there's no X-Forwarded-Proto when nothing's proxying.

    If multiple proxies chain the header (comma-separated values),
    the leftmost is the original client-facing scheme per RFC 7239.
    """
    parsed = urlparse(str(request.url))
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        scheme = forwarded_proto.split(",")[0].strip().lower()
        if scheme in ("http", "https"):
            parsed = parsed._replace(scheme=scheme)
    return urlunparse(parsed._replace(query="", fragment=""))


def _render_install_page(*, title: str, body: str) -> str:
    """Minimal self-contained HTML — no external assets so it works
    even if the operator's browser has blockers. Dark theme matches
    Linear's own UI so the page doesn't flash white when the
    redirect lands."""
    return (
        "<!doctype html>"
        "<html lang='en'><head>"
        "<meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "sans-serif;background:#0f1114;color:#e6e9ef;margin:0;padding:48px;}"
        ".card{max-width:560px;margin:60px auto;background:#1a1d24;"
        "border:1px solid #2a2e37;border-radius:12px;padding:32px;"
        "line-height:1.55;}"
        "h1{margin:0 0 16px;font-size:20px;font-weight:600;}"
        "code{background:#2a2e37;padding:2px 6px;border-radius:4px;"
        "font-family:'SF Mono',Menlo,monospace;font-size:13px;}"
        "pre{background:#2a2e37;padding:12px;border-radius:6px;"
        "overflow-x:auto;font-size:12px;}"
        "</style></head>"
        f"<body><div class='card'><h1>{title}</h1><div>{body}</div></div></body>"
        "</html>"
    )
