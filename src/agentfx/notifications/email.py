"""Email helper for paper-run alerts (Wilson 2026-08-31).

Replaces the earlier Slack-webhook path — Slack's mobile push wasn't
reliably reaching Wilson's phone, so paper-run halts went unnoticed.
Email is the interim channel until a proper mobile app ships with
native push.

Structure — three testable layers (mirrors the old slack.py so the
callers, tests, and dedup semantics stay identical):

- `_should_send(...)` — pure gate: master toggle, SMTP config present,
  recipient set, per-run dedup.
- `_build_message(...)` — pure function that builds an
  `email.message.EmailMessage` from a Run row + reason. No I/O.
- `_send_with_one_retry(...)` — SMTP send with ONE retry on transient
  failure (jittered backoff). No SSL fallback for now; STARTTLS on
  the standard submission port covers Gmail, Outlook, SendGrid,
  Mailgun, SES.
- `post_paper_run_alert(...)` — public entry point matching the
  Slack signature so callers don't change.

The delivery function NEVER re-raises. A notification failure must
not affect the live trade.
"""

from __future__ import annotations

import logging
import random
import smtplib
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)

# SMTP submission timeout. Kept tight — paper-runner halts call this
# on a hot-adjacent path; a slow SMTP server can't stall trade
# execution for more than a few seconds.
_SMTP_TIMEOUT_S = 10.0

# One retry on transient failure. Jittered 300-800 ms so parallel
# runs' notifications don't align a thundering herd.
_RETRY_MIN_MS = 300
_RETRY_MAX_MS = 800

_MODULE_LOG_ONCE: set[str] = set()


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────


def _log_once(key: str, msg: str) -> None:
    """Log an INFO line at most once per process for a given key.
    Used for the "SMTP not configured" case so we don't spam it on
    every terminal event.
    """
    if key in _MODULE_LOG_ONCE:
        return
    _MODULE_LOG_ONCE.add(key)
    logger.info(msg)


def _should_send(
    *,
    smtp_host: str,
    email_to: str,
    enabled: bool,
    run: Any,
    reason: str,
) -> tuple[bool, str]:
    """Return (send?, why) — the second element is a short tag used
    for INFO logging on skip. Callers do NOT branch on `why`.

    Skip cases:
    - `enabled == False` (master toggle off, e.g. test env)
    - `smtp_host` empty (SMTP not configured in this deployment)
    - `email_to` empty (no recipient — nothing to send to)
    - `reason` already recorded in `run.parameters['notified']`
      (idempotency guard — re-entrant halt path or watchdog racing
      the subprocess's own `_mark_terminal` call)
    """
    if not enabled:
        return False, "notifications_disabled"
    if not smtp_host:
        return False, "no_smtp_configured"
    if not email_to:
        return False, "no_recipient_configured"
    params = getattr(run, "parameters", None) or {}
    notified = params.get("notified") or {}
    if isinstance(notified, dict) and reason in notified:
        return False, "already_notified"
    return True, "send"


def _mark_notified(run: Any, reason: str) -> None:
    """Write the dedup entry into `run.parameters['notified'][reason]`
    with an ISO timestamp. Re-assigns a fresh dict so SQLAlchemy's
    JSON change detection picks up the mutation without
    `flag_modified`.
    """
    params = dict(getattr(run, "parameters", None) or {})
    notified = dict(params.get("notified") or {})
    notified[reason] = datetime.now(UTC).isoformat()
    params["notified"] = notified
    run.parameters = params


def _fmt_num(x: Any, prefix: str = "") -> str:
    if x is None:
        return "—"
    try:
        return f"{prefix}{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def _build_message(
    *,
    run: Any,
    reason: str,
    extra: dict | None,
    portal_base_url: str | None,
    email_from: str,
    email_to: str,
) -> EmailMessage:
    """Build the outgoing email. Pure — no I/O.

    Fields pulled from the Run row are all optional so a stub-run
    (SimpleNamespace) in tests and a partially-populated row in
    production both serialise cleanly.
    """
    run_id = str(getattr(run, "id", "unknown"))
    status = getattr(run, "status", None)
    pnl = getattr(run, "pnl", None)
    balance = getattr(run, "current_balance", None)
    instrument = getattr(run, "pair", None)
    run_name = getattr(run, "name", None)

    subject = f"[AgentFX] Paper run halted — {reason} ({run_id[:24]}…)"

    body_lines = [
        "AgentFX paper run halted.",
        "",
        f"Run:        {run_id}",
    ]
    if run_name:
        body_lines.append(f"Name:       {run_name}")
    body_lines.extend(
        [
            f"Reason:     {reason}",
            f"Status:     {status}",
            f"Instrument: {instrument or '—'}",
            f"Realized P&L: {_fmt_num(pnl)}",
            f"Balance:    {_fmt_num(balance)}",
            f"When:       {datetime.now(UTC).isoformat(timespec='seconds')}",
        ]
    )
    if extra:
        body_lines.append("")
        body_lines.append("Extra:")
        for k, v in extra.items():
            body_lines.append(f"  {k}: {v}")
    if portal_base_url:
        body_lines.append("")
        body_lines.append(
            f"View in portal: {portal_base_url.rstrip('/')}/paper/{run_id}"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    # SMTP accepts comma-separated recipients directly.
    msg["To"] = email_to
    msg.set_content("\n".join(body_lines))
    return msg


def _send_with_one_retry(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_from: str,
    email_to: str,
    msg: EmailMessage,
    smtp_factory=None,
) -> tuple[bool, str]:
    """Sync SMTP send with ONE retry on transient failure.

    Returns (ok, detail). `detail` is for logging only.

    `smtp_factory` (optional): callable returning an SMTP-like object
    with `starttls`, `login`, `send_message`. Injected by tests so
    they can mock the entire network stack.

    Retryable errors: `SMTPConnectError`, `SMTPServerDisconnected`,
    `SMTPHeloError`, generic `TimeoutError`, `OSError` (connection
    refused, DNS blip). Not retryable: `SMTPAuthenticationError`
    (bad creds — retry won't help), `SMTPRecipientsRefused` (bad
    address), `SMTPDataError` on 5xx.
    """
    if smtp_factory is None:
        smtp_factory = lambda: smtplib.SMTP(  # noqa: E731
            smtp_host, smtp_port, timeout=_SMTP_TIMEOUT_S
        )

    _non_retryable = (
        smtplib.SMTPAuthenticationError,
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
    )
    _transient = (
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPHeloError,
        TimeoutError,
        OSError,
    )

    for attempt in (0, 1):
        try:
            with smtp_factory() as client:
                client.starttls()
                if smtp_user and smtp_password:
                    client.login(smtp_user, smtp_password)
                client.send_message(
                    msg,
                    from_addr=email_from,
                    to_addrs=[a.strip() for a in email_to.split(",") if a.strip()],
                )
            return True, "ok"
        except _non_retryable as exc:
            # Bad config — no point retrying.
            return False, f"non_retryable:{type(exc).__name__}"
        except _transient as exc:
            if attempt == 0:
                _sleep_jitter()
                continue
            return False, f"transient_after_retry:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001 — unknown, retry once
            if attempt == 0:
                _sleep_jitter()
                continue
            return False, f"unknown_after_retry:{type(exc).__name__}"
    return False, "unreachable"  # pragma: no cover


def _sleep_jitter() -> None:
    """Jittered backoff between the initial send and the retry."""
    time.sleep(random.uniform(_RETRY_MIN_MS, _RETRY_MAX_MS) / 1000)


def _infer_portal_base_url() -> str | None:
    """Best-effort portal base URL for the "View in portal" link.
    Derived from ENVIRONMENT so we don't add yet another config key
    that has to be set per-deployment.
    """
    import os

    env = os.getenv("ENVIRONMENT", "development").lower()
    if env in ("production", "staging"):
        return "https://agentfx-liart.vercel.app"
    if env == "uat":
        return "http://localhost:5175"
    return None


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
    """Best-effort email alert on a paper-run terminal / crash event.

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
            smtp_host=settings.SMTP_HOST,
            email_to=settings.EMAIL_TO,
            enabled=settings.NOTIFICATIONS_ENABLED,
            run=run,
            reason=reason,
        )
        if not ok_to_send:
            if why == "no_smtp_configured":
                _log_once(
                    "email.no_smtp",
                    "notifications: SMTP_HOST not set — skipping paper-run "
                    "alerts for the lifetime of this process",
                )
            elif why == "no_recipient_configured":
                _log_once(
                    "email.no_recipient",
                    "notifications: EMAIL_TO not set — skipping paper-run "
                    "alerts for the lifetime of this process",
                )
            elif why == "already_notified":
                logger.debug(
                    "notifications: already sent for run=%s reason=%s — skip",
                    getattr(run, "id", "?"),
                    reason,
                )
            return

        msg = _build_message(
            run=run,
            reason=reason,
            extra=extra,
            portal_base_url=_infer_portal_base_url(),
            email_from=settings.EMAIL_FROM or settings.SMTP_USER,
            email_to=settings.EMAIL_TO,
        )
        ok, detail = _send_with_one_retry(
            smtp_host=settings.SMTP_HOST,
            smtp_port=settings.SMTP_PORT,
            smtp_user=settings.SMTP_USER,
            smtp_password=settings.SMTP_PASSWORD,
            email_from=settings.EMAIL_FROM or settings.SMTP_USER,
            email_to=settings.EMAIL_TO,
            msg=msg,
        )
        if not ok:
            logger.warning(
                "notifications: email send failed for run=%s reason=%s: %s",
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


__all__ = ["post_paper_run_alert"]
