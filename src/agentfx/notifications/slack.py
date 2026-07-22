"""Slack incoming-webhook helper for paper-run alerts.

v0.6.2 — Wilson UAT 2026-07-19. See `agentfx.notifications` module
docstring for the invariants. This module implements the delivery
path.

Structure — three testable layers:

- `_should_send(webhook_url, enabled, run, reason)` — pure gate that
  says whether this call should attempt an HTTP POST. Handles config
  absence, master toggle, and per-run dedup.
- `_build_slack_payload(run, reason, extra, portal_base_url)` — pure
  function that builds the Slack Blocks payload from a Run row + a
  reason string. No I/O, no side effects.
- `post_paper_run_alert(run, reason, extra=None, db=None)` — public
  entry point. Calls `_should_send`, POSTs the payload with one
  retry on transient failure, writes the dedup entry back to
  `run.parameters['notified']` on success, logs and returns silently
  on failure.

The delivery function NEVER re-raises. A notification failure must
not affect the live trade.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Slack POSTs the webhook synchronously. Chosen so a slow Slack
# response doesn't stall the runner's tick more than a couple of
# seconds. `_mark_terminal` is off the hot path anyway (called once
# per run lifetime), but keep this tight so accidental extra calls
# from a future caller stay cheap.
_REQUEST_TIMEOUT_S = 5.0

# One retry on 5xx / ConnectionError. Jittered 300-800 ms so a
# thundering herd from parallel runs can't align.
_RETRY_MIN_MS = 300
_RETRY_MAX_MS = 800

_MODULE_LOG_ONCE: set[str] = set()


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (Vitest-equivalent — no I/O, deterministic)
# ─────────────────────────────────────────────────────────────────────


def _log_once(key: str, msg: str) -> None:
    """Log an INFO line at most once per process for a given key.
    Used for the "webhook not configured" case so we don't spam it
    on every terminal event.
    """
    if key in _MODULE_LOG_ONCE:
        return
    _MODULE_LOG_ONCE.add(key)
    logger.info(msg)


def _should_send(
    *,
    webhook_url: str,
    enabled: bool,
    run: Any,
    reason: str,
) -> tuple[bool, str]:
    """Return (send?, why) — the second element is a short tag used
    only for INFO logging on skip. Callers do NOT branch on `why`.

    Skip cases:
    - `enabled == False` (master toggle off, e.g. test env)
    - `webhook_url` empty (unset in this deployment)
    - `reason` already recorded in `run.parameters['notified']`
      (idempotency guard — re-entrant halt path or watchdog racing
      the subprocess's own `_mark_terminal` call)
    """
    if not enabled:
        return False, "notifications_disabled"
    if not webhook_url:
        return False, "no_webhook_configured"
    params = getattr(run, "parameters", None) or {}
    notified = params.get("notified") or {}
    if isinstance(notified, dict) and reason in notified:
        return False, "already_notified"
    return True, "send"


def _mark_notified(run: Any, reason: str) -> None:
    """Write the dedup entry into `run.parameters['notified'][reason]`
    with an ISO timestamp. Mirrors the `stale_active` pattern from
    the signal-tick — re-assign a fresh dict so SQLAlchemy's JSON
    change detection picks up the mutation without `flag_modified`.
    """
    params = dict(getattr(run, "parameters", None) or {})
    notified = dict(params.get("notified") or {})
    notified[reason] = datetime.now(UTC).isoformat()
    params["notified"] = notified
    run.parameters = params


def _build_slack_payload(
    *,
    run: Any,
    reason: str,
    extra: dict | None,
    portal_base_url: str | None,
) -> dict:
    """Build the Slack Blocks payload. Pure — no I/O.

    Fields pulled from the Run row are all optional so this survives
    a stub-run (SimpleNamespace) in tests and a partially-populated
    row in production.
    """
    run_id = str(getattr(run, "id", "unknown"))
    status = getattr(run, "status", None)
    pnl = getattr(run, "pnl", None)
    balance = getattr(run, "current_balance", None)
    instrument = getattr(run, "pair", None)

    def _fmt_num(x: Any, prefix: str = "") -> str:
        if x is None:
            return "—"
        try:
            return f"{prefix}{float(x):,.2f}"
        except (TypeError, ValueError):
            return str(x)

    fields = [
        {"type": "mrkdwn", "text": f"*Run:* `{run_id}`"},
        {"type": "mrkdwn", "text": f"*Reason:* `{reason}`"},
        {"type": "mrkdwn", "text": f"*Status:* {status}"},
        {"type": "mrkdwn", "text": f"*Realized P&L:* {_fmt_num(pnl)}"},
        {"type": "mrkdwn", "text": f"*Balance:* {_fmt_num(balance)}"},
        {"type": "mrkdwn", "text": f"*Instrument:* {instrument or '—'}"},
    ]
    if extra:
        for k, v in extra.items():
            fields.append({"type": "mrkdwn", "text": f"*{k}:* `{v}`"})

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Paper run halted"},
        },
        {"type": "section", "fields": fields},
    ]
    if portal_base_url:
        url = f"{portal_base_url.rstrip('/')}/paper/{run_id}"
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{url}|View in portal>"},
            }
        )
    return {
        "text": f"🚨 Paper run halted — {run_id} ({reason})",
        "blocks": blocks,
    }


def _post_with_one_retry(
    *,
    webhook_url: str,
    payload: dict,
    session: requests.Session | None = None,
) -> tuple[bool, str]:
    """Sync POST with ONE retry on 5xx / ConnectionError / Timeout.

    Returns (ok, detail). `detail` is for logging only.

    - 2xx → (True, "ok")
    - 4xx → (False, "http_4xx_<status>") no retry (bad webhook, etc.)
    - 5xx or exception → retry once with 300-800ms jitter; if that
      also fails, (False, "http_5xx_after_retry" / "exception_after_retry")
    """
    http = session or requests
    for attempt in (0, 1):
        try:
            resp = http.post(
                webhook_url,
                json=payload,
                timeout=_REQUEST_TIMEOUT_S,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == 0:
                _sleep_jitter()
                continue
            return False, f"exception_after_retry: {type(exc).__name__}"
        if 200 <= resp.status_code < 300:
            return True, "ok"
        if 400 <= resp.status_code < 500:
            return False, f"http_4xx_{resp.status_code}"
        # 5xx
        if attempt == 0:
            _sleep_jitter()
            continue
        return False, f"http_5xx_after_retry_{resp.status_code}"
    return False, "unreachable"  # pragma: no cover


def _sleep_jitter() -> None:
    """Jittered backoff between the initial call and the retry.
    Extracted so tests can monkeypatch it to a no-op — otherwise the
    test suite pays 300-800ms per retry-covering case.
    """
    time.sleep(random.uniform(_RETRY_MIN_MS, _RETRY_MAX_MS) / 1000)


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def post_paper_run_alert(
    *,
    run: Any,
    reason: str,
    extra: dict | None = None,
    db: Any | None = None,
) -> None:
    """Best-effort Slack alert on a paper-run terminal / crash event.

    Idempotent per `(run.id, reason)` — safe to call multiple times.
    Never raises.

    `db` (optional): SQLAlchemy session. When provided AND delivery
    succeeds, the dedup entry is committed. Callers that already
    commit after `_mark_terminal` can pass `db=None` and the runner-
    side commit will pick up the `run.parameters` mutation via the
    normal ORM change tracking.
    """
    try:
        from agentfx.api.core.config import settings

        ok_to_send, why = _should_send(
            webhook_url=settings.SLACK_WEBHOOK_URL,
            enabled=settings.NOTIFICATIONS_ENABLED,
            run=run,
            reason=reason,
        )
        if not ok_to_send:
            if why == "no_webhook_configured":
                _log_once(
                    "slack.no_webhook",
                    "notifications: SLACK_WEBHOOK_URL not set — skipping "
                    "paper-run alerts for the lifetime of this process",
                )
            elif why == "already_notified":
                logger.debug(
                    "notifications: already sent for run=%s reason=%s — skip",
                    getattr(run, "id", "?"),
                    reason,
                )
            return

        payload = _build_slack_payload(
            run=run,
            reason=reason,
            extra=extra,
            portal_base_url=_infer_portal_base_url(),
        )
        ok, detail = _post_with_one_retry(
            webhook_url=settings.SLACK_WEBHOOK_URL,
            payload=payload,
        )
        if not ok:
            logger.warning(
                "notifications: Slack POST failed for run=%s reason=%s: %s",
                getattr(run, "id", "?"),
                reason,
                detail,
            )
            return

        _mark_notified(run, reason)
        if db is not None:
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — never let commit failure re-raise
                logger.exception(
                    "notifications: post-send db.commit failed for run=%s "
                    "reason=%s — dedup entry lost, next event may re-send",
                    getattr(run, "id", "?"),
                    reason,
                )
    except Exception:  # noqa: BLE001 — outermost catch-all
        # Belt-and-braces: whatever explodes above, do NOT let it
        # propagate. The paper trade is more important than the alert.
        logger.exception(
            "notifications: unexpected error posting alert for run=%s reason=%s",
            getattr(run, "id", "?"),
            reason,
        )


def _infer_portal_base_url() -> str | None:
    """Best-effort portal base URL for the "View in portal" link. Not
    a Settings field — derived from ENVIRONMENT so we don't add yet
    another config key that has to be set per-deployment.
    """
    import os

    env = os.getenv("ENVIRONMENT", "development").lower()
    if env in ("production", "staging"):
        return "https://agentfx-liart.vercel.app"
    if env == "uat":
        return "http://localhost:5175"
    return None


__all__ = ["post_paper_run_alert"]
