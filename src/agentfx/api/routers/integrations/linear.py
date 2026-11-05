"""Linear Agent webhook receiver (AFX-66 Phase 1 — log-only).

Linear POSTs an `AgentSessionEvent` to this endpoint when the PE is
delegated an issue, @mentioned in a comment, or receives a reply
inside an open agent session. Phase 1 verifies the signature + replay
window + idempotency and persists every delivery to
`linear_webhook_deliveries` for observability. **No dispatch wiring
yet** — that lands in Phase 2 once the real webhook payload shape is
confirmed against Linear's docs.

The handler is written to return 200 quickly (Linear's 5s webhook
SLA). It avoids doing any downstream work in the request path; Phase 2
offloads dispatch to a background task.

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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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
    # "duplicate" ACK. Phase 2 will branch on action/session for
    # dispatch; Phase 1 just records.
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
