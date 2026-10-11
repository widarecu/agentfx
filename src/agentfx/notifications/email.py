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


_ENV_TAG = {
    "development": "DEV",
    "test": "TEST",
    "uat": "UAT",
    "staging": "STAGING",
    "production": "PROD",
}


def _build_message(
    *,
    run: Any,
    reason: str,
    extra: dict | None,
    portal_base_url: str | None,
    email_from: str,
    email_to: str,
    environment: str | None = None,
) -> EmailMessage:
    """Build the outgoing email. Pure — no I/O.

    Fields pulled from the Run row are all optional so a stub-run
    (SimpleNamespace) in tests and a partially-populated row in
    production both serialise cleanly.

    `environment` (optional) — one of the ENVIRONMENT literal values
    (`development`, `test`, `uat`, `staging`, `production`). Tagged
    into the subject prefix so a UAT smoke alert reads instantly
    different from a real production halt in a mobile push preview.
    Unknown values are dropped (no tag) rather than surfacing garbage.
    """
    run_id = str(getattr(run, "id", "unknown"))
    status = getattr(run, "status", None)
    pnl = getattr(run, "pnl", None)
    balance = getattr(run, "current_balance", None)
    instrument = getattr(run, "pair", None)
    run_name = getattr(run, "name", None)

    env_tag = _ENV_TAG.get((environment or "").lower())
    subject_prefix = f"[AgentFX {env_tag}]" if env_tag else "[AgentFX]"
    subject = f"{subject_prefix} Paper run halted — {reason} ({run_id[:24]}…)"

    body_lines = ["AgentFX paper run halted.", ""]
    if env_tag:
        body_lines.append(f"Environment: {env_tag}")
    body_lines.append(f"Run:        {run_id}")
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

    Classification:
    - 5xx SMTP responses (permanent — 550 unverified sender, 552 msg
      too big, 535 auth failed) → NOT retried. A retry will fail the
      same way and only adds latency to the halt path.
    - 4xx SMTP responses (transient — 421 service unavailable, 450
      mailbox busy) → retried once.
    - Socket-level failures (TimeoutError, generic OSError,
      SMTPServerDisconnected mid-conversation) → retried once.
    - Unknown → retried once, tagged `unknown_after_retry`.

    Ordering matters: SMTP-family exceptions must be caught before the
    generic OSError arm, because `smtplib.SMTPException` inherits from
    OSError in Python 3 — an untyped OSError catch would swallow every
    SMTP error and mis-label them all as transient. `_non_retryable`
    stays as an explicit tuple so the log tag names the specific
    subclass (clearer than "SMTPResponseException(535)") and because
    `SMTPRecipientsRefused` wraps a per-recipient dict rather than
    subclassing `SMTPResponseException`.
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
            return False, f"non_retryable:{type(exc).__name__}"
        except smtplib.SMTPResponseException as exc:
            if 500 <= exc.smtp_code < 600:
                return (
                    False,
                    f"non_retryable:{type(exc).__name__}({exc.smtp_code})",
                )
            if attempt == 0:
                _sleep_jitter()
                continue
            return (
                False,
                f"transient_after_retry:{type(exc).__name__}({exc.smtp_code})",
            )
        except (smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
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
            environment=settings.ENVIRONMENT,
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


__all__ = [
    "post_paper_run_alert",
    "send_agent_message",
    "render_pm_brief_html",
    "render_qa_divergence_html",
    "render_qa_tech_health_html",
    "render_pe_proposal_html",
]


# ─────────────────────────────────────────────────────────────────────
# Agent Fleet channel (v0.8.x) — tagged email for PM/QA/PE/RA output
# ─────────────────────────────────────────────────────────────────────


def send_agent_message(
    *,
    agent_role: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    """Send a tagged email from a fleet agent.

    Subject format: `[AgentFX AGENT <ROLE>] <ENV> — <subject>`. The
    ENV tag matches `post_paper_run_alert` so Wilson can filter
    staging vs production vs UAT at a glance even inside the fleet
    channel.

    `body` is plain text (always sent). `html_body` is optional; when
    provided, the message goes as `multipart/alternative` so modern
    mail clients render HTML while plain-text fallback stays
    available for stripped forwards and copy-paste.

    Never raises. SMTP failures are logged; the orchestrator keeps
    running.

    `agent_role` is a short tag (`"PM"`, `"QA"`, `"PE"`, `"RA"`).
    """
    try:
        from agentfx.api.core.config import settings

        if not settings.AGENT_FLEET_ENABLED:
            # Belt + braces: even if a caller invokes this with the
            # flag off, we don't want to leak email.
            return

        env_tag = _ENV_TAG.get((settings.ENVIRONMENT or "").lower())
        tag = f"[AgentFX AGENT {agent_role}"
        tag += f" {env_tag}" if env_tag else ""
        tag += "]"
        full_subject = f"{tag} {subject}"

        ok_to_send, why = _should_send(
            smtp_host=settings.SMTP_HOST,
            email_to=settings.EMAIL_TO,
            enabled=settings.NOTIFICATIONS_ENABLED,
            # No dedup for agent messages — they're expected to
            # be unique per decision_key already. Pass a fresh
            # stub so `_should_send`'s notified-set check is a
            # no-op.
            run=type("Stub", (), {"id": "agent-stub", "parameters": None})(),
            reason=full_subject,
        )
        if not ok_to_send:
            logger.debug("send_agent_message: skipped (%s)", why)
            return

        msg = EmailMessage()
        msg["Subject"] = full_subject
        msg["From"] = settings.EMAIL_FROM or settings.SMTP_USER
        msg["To"] = settings.EMAIL_TO
        msg.set_content(body)
        if html_body:
            # multipart/alternative — mail clients pick HTML; plain
            # text stays as fallback for forwards, archive, copy.
            msg.add_alternative(html_body, subtype="html")

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
                "send_agent_message: failed role=%s subject=%r detail=%s",
                agent_role,
                subject,
                detail,
            )
    except Exception:  # noqa: BLE001 — never let email break the fleet
        logger.exception(
            "send_agent_message: unexpected failure role=%s subject=%r",
            agent_role,
            subject,
        )


# ─────────────────────────────────────────────────────────────────────
# HTML templates (Wilson 2026-10-05: fleet emails need polish)
# ─────────────────────────────────────────────────────────────────────


# Inline CSS only — many email clients strip <style> blocks. Max
# width 600px for mobile safety; no external images (deliverability
# + privacy); monospace for code / IDs.
_EMAIL_SHELL = """<!doctype html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7f7f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1f2937;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f7f8;">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden;">
      <tr><td style="padding:16px 20px;background:#111827;color:#f9fafb;font-weight:600;letter-spacing:0.5px;">{env_pill} AgentFX &middot; {role_label}</td></tr>
      <tr><td style="padding:20px;">{inner}</td></tr>
      <tr><td style="padding:12px 20px;background:#f9fafb;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;">v0.8.0 Phase B &middot; feat/agent-fleet &middot; <a href="https://github.com/widarecu/agentfx" style="color:#4b5563;">repo</a></td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _env_pill_html() -> str:
    try:
        from agentfx.api.core.config import settings

        env = (settings.ENVIRONMENT or "").upper()
    except Exception:
        env = ""
    if not env:
        return ""
    color_map = {
        "PRODUCTION": "#dc2626",
        "STAGING": "#d97706",
        "UAT": "#2563eb",
        "DEVELOPMENT": "#6b7280",
        "TEST": "#6b7280",
    }
    bg = color_map.get(env, "#6b7280")
    return (
        f'<span style="background:{bg};color:#fff;padding:2px 8px;'
        f'border-radius:12px;font-size:11px;margin-right:8px;">{env}</span>'
    )


def _escape(text: str) -> str:
    """Minimal HTML escape — enough for prose content from the LLM."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_pm_brief_html(
    brief: Any,
    plaintext_body: str,
    scheduling_results: list[dict[str, Any]] | None = None,
) -> str:
    """Render a `PMBrief` as styled HTML for the fleet email.

    Takes `Any` on the type signature to avoid an import cycle —
    the caller is `agents.fleet.portfolio_manager` and the model
    class lives there. The function only reads duck-typed
    attributes (`headline`, `summary`, `recommendations`,
    `concerns`).

    `scheduling_results` (v0.8.1): the per-action outcomes from
    PM's evolution-scheduling tool invocations this tick. When
    non-empty, rendered as a dedicated section with accept/reject
    badges and the launched `evo_run_id` so Wilson can trace back
    to the actual evolution.

    `plaintext_body` is accepted but unused in the main render
    path — only shown as fallback if an exception breaks the
    structured render.
    """
    try:
        headline = _escape(getattr(brief, "headline", "") or "")
        summary = _escape(getattr(brief, "summary", "") or "")
        recs = getattr(brief, "recommendations", []) or []
        concerns = getattr(brief, "concerns", []) or []

        rec_html_parts: list[str] = []
        for r in recs:
            prio = _escape(getattr(r, "priority", "") or "")
            head = _escape(getattr(r, "headline", "") or "")
            why = _escape(getattr(r, "rationale", "") or "")
            prio_color = {
                "now": "#dc2626",
                "this_week": "#d97706",
                "watch": "#6b7280",
            }.get(prio, "#6b7280")
            rec_html_parts.append(
                f'<li style="margin:0 0 10px 0;">'
                f'<span style="background:{prio_color};color:#fff;'
                f"padding:1px 7px;border-radius:10px;font-size:11px;"
                f'margin-right:6px;">{prio}</span>'
                f"<strong>{head}</strong>"
                f'<div style="color:#6b7280;font-size:13px;margin-top:2px;">{why}</div>'
                f"</li>"
            )
        rec_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Recommendations</h3>"
            f'<ul style="margin:0;padding-left:18px;">{"".join(rec_html_parts)}</ul>'
            if rec_html_parts
            else ""
        )

        concern_html_parts: list[str] = []
        for c in concerns:
            sev = _escape(getattr(c, "severity", "") or "")
            desc = _escape(getattr(c, "description", "") or "")
            sev_color = {
                "blocker": "#dc2626",
                "degraded": "#d97706",
                "noted": "#6b7280",
            }.get(sev, "#6b7280")
            concern_html_parts.append(
                f'<li style="margin:0 0 8px 0;">'
                f'<span style="background:{sev_color};color:#fff;'
                f"padding:1px 7px;border-radius:10px;font-size:11px;"
                f'margin-right:6px;">{sev}</span>'
                f"{desc}</li>"
            )
        concern_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Concerns</h3>"
            f'<ul style="margin:0;padding-left:18px;">{"".join(concern_html_parts)}</ul>'
            if concern_html_parts
            else ""
        )

        scheduling_html = _render_scheduling_results_html(scheduling_results)

        inner = (
            f'<h2 style="margin:0 0 8px 0;font-size:20px;color:#111827;">{headline}</h2>'
            f'<p style="margin:0 0 16px 0;line-height:1.5;color:#374151;">{summary}</p>'
            f"{rec_html}"
            f"{concern_html}"
            f"{scheduling_html}"
        )

        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Portfolio Manager",
            inner=inner,
        )
    except Exception:
        # Rendering must not break the send — fall back to a plain
        # <pre> dump of the plaintext body wrapped in the shell.
        safe = _escape(plaintext_body)
        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Portfolio Manager",
            inner=f'<pre style="white-space:pre-wrap;margin:0;">{safe}</pre>',
        )


def _render_scheduling_results_html(
    results: list[dict[str, Any]] | None,
) -> str:
    """Render the "evolution actions executed this tick" section.

    Returns an empty string when PM scheduled nothing — the
    section only appears when it actually has content.
    """
    if not results:
        return ""
    parts: list[str] = []
    for r in results:
        pair = _escape(r.get("pair") or "")
        gran = _escape(r.get("granularity") or "")
        priority = _escape(r.get("priority") or "")
        rationale = _escape(r.get("rationale") or "")
        impact = _escape(r.get("expected_profit_impact") or "")
        if r.get("accepted"):
            evo_id = _escape(r.get("evo_run_id") or "")
            parts.append(
                f'<li style="margin:0 0 10px 0;">'
                f'<span style="background:#059669;color:#fff;padding:1px 7px;'
                f'border-radius:10px;font-size:11px;margin-right:6px;">started</span>'
                f"<strong>{pair}/{gran}</strong> "
                f'<code style="background:#f3f4f6;padding:1px 4px;font-size:11px;">'
                f"{evo_id}</code>"
                f'<div style="color:#6b7280;font-size:13px;margin-top:4px;">'
                f"<em>Why:</em> {rationale}</div>"
                f'<div style="color:#6b7280;font-size:13px;">'
                f"<em>Expected:</em> {impact}</div>"
                f"</li>"
            )
        else:
            reason = _escape(r.get("reason") or "")
            parts.append(
                f'<li style="margin:0 0 10px 0;">'
                f'<span style="background:#6b7280;color:#fff;padding:1px 7px;'
                f'border-radius:10px;font-size:11px;margin-right:6px;">skipped</span>'
                f"<strong>{pair}/{gran}</strong> "
                f'<span style="color:#6b7280;">(priority={priority}, '
                f"reason: <code>{reason}</code>)</span>"
                f"</li>"
            )
    return (
        '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
        "Evolution actions executed this tick</h3>"
        f'<ul style="margin:0;padding-left:18px;">{"".join(parts)}</ul>'
    )


def render_qa_divergence_html(report: Any, plaintext_body: str) -> str:
    """Render a QA divergence report as styled HTML.

    Mirrors `render_pm_brief_html`'s shape so future refactoring
    can collapse them if we grow more agents. Duck-typed on
    `headline`, `summary`, `classifications`, `work_orders`.
    """
    try:
        headline = _escape(getattr(report, "headline", "") or "")
        summary = _escape(getattr(report, "summary", "") or "")
        classifications = getattr(report, "classifications", []) or []
        work_orders = getattr(report, "work_orders", []) or []

        class_html_parts: list[str] = []
        for c in classifications:
            cat = _escape(getattr(c, "category", "") or "")
            conf = getattr(c, "confidence", 0.0) or 0.0
            desc = _escape(getattr(c, "description", "") or "")
            class_html_parts.append(
                f'<li style="margin:0 0 8px 0;">'
                f'<span style="background:#4f46e5;color:#fff;padding:1px 7px;'
                f'border-radius:10px;font-size:11px;margin-right:6px;">{cat}</span>'
                f"<strong>confidence {conf:.0%}</strong><br/>"
                f'<span style="color:#374151;font-size:13px;">{desc}</span>'
                f"</li>"
            )
        class_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Divergence classifications</h3>"
            f'<ul style="margin:0;padding-left:18px;">{"".join(class_html_parts)}</ul>'
            if class_html_parts
            else ""
        )

        wo_html_parts: list[str] = []
        for w in work_orders:
            title = _escape(getattr(w, "title", "") or "")
            file_target = _escape(getattr(w, "target_file", "") or "")
            spec = _escape(getattr(w, "spec", "") or "")
            wo_html_parts.append(
                f'<li style="margin:0 0 10px 0;">'
                f"<strong>{title}</strong>"
                f'<div style="color:#6b7280;font-size:12px;">target: '
                f'<code style="background:#f3f4f6;padding:1px 4px;">{file_target}</code></div>'
                f'<div style="color:#374151;font-size:13px;margin-top:2px;">{spec}</div>'
                f"</li>"
            )
        wo_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Work orders for Platform Engineer</h3>"
            f'<ul style="margin:0;padding-left:18px;">{"".join(wo_html_parts)}</ul>'
            if wo_html_parts
            else ""
        )

        inner = (
            f'<h2 style="margin:0 0 8px 0;font-size:20px;color:#111827;">{headline}</h2>'
            f'<p style="margin:0 0 16px 0;line-height:1.5;color:#374151;">{summary}</p>'
            f"{class_html}"
            f"{wo_html}"
        )

        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Quant Analyst",
            inner=inner,
        )
    except Exception:
        safe = _escape(plaintext_body)
        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Quant Analyst",
            inner=f'<pre style="white-space:pre-wrap;margin:0;">{safe}</pre>',
        )


def render_qa_tech_health_html(report: Any, plaintext_body: str) -> str:
    """Render a QA technical-health report as styled HTML.

    Mirrors the divergence template's shape. Duck-typed on
    `headline`, `summary`, `anomalies` with per-anomaly fields
    `category`, `severity`, `headline`, `occurrences`, `first_seen`,
    `sample_log`, `recommended_action`.

    One extra tile vs the divergence template: a `<pre>` sample log
    line per anomaly so Wilson sees the raw signal without having
    to open the markdown file.
    """
    try:
        headline = _escape(getattr(report, "headline", "") or "")
        summary = _escape(getattr(report, "summary", "") or "")
        anomalies = getattr(report, "anomalies", []) or []

        anomaly_html_parts: list[str] = []
        for a in anomalies:
            sev = _escape(getattr(a, "severity", "") or "")
            cat = _escape(getattr(a, "category", "") or "")
            head = _escape(getattr(a, "headline", "") or "")
            occ = getattr(a, "occurrences", 0) or 0
            first = _escape(getattr(a, "first_seen", "") or "")
            sample = _escape((getattr(a, "sample_log", "") or "")[:400])
            action = _escape(getattr(a, "recommended_action", "") or "")
            sev_color = {
                "critical": "#dc2626",
                "degraded": "#d97706",
                "noted": "#6b7280",
            }.get(sev, "#6b7280")
            anomaly_html_parts.append(
                f'<li style="margin:0 0 14px 0;list-style:none;'
                f'padding:10px 12px;background:#f9fafb;border-left:3px solid {sev_color};">'
                f'<div><span style="background:{sev_color};color:#fff;'
                f"padding:1px 7px;border-radius:10px;font-size:11px;"
                f'margin-right:6px;">{sev}</span>'
                f'<span style="background:#4f46e5;color:#fff;padding:1px 7px;'
                f'border-radius:10px;font-size:11px;margin-right:6px;">{cat}</span>'
                f"<strong>{head}</strong></div>"
                f'<div style="color:#6b7280;font-size:12px;margin-top:4px;">'
                f"{occ}× &middot; first seen {first} &middot; action: {action}</div>"
                f'<pre style="margin:6px 0 0 0;padding:8px 10px;'
                f"background:#111827;color:#f9fafb;font-size:11px;"
                f'overflow-x:auto;border-radius:4px;white-space:pre-wrap;">'
                f"{sample}</pre>"
                f"</li>"
            )
        anomaly_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Anomalies</h3>"
            f'<ul style="margin:0;padding:0;list-style:none;">'
            f"{''.join(anomaly_html_parts)}</ul>"
            if anomaly_html_parts
            else (
                '<p style="color:#059669;margin:16px 0 0 0;">'
                "No anomalies classified in this window.</p>"
            )
        )

        inner = (
            f'<h2 style="margin:0 0 8px 0;font-size:20px;color:#111827;">{headline}</h2>'
            f'<p style="margin:0 0 16px 0;line-height:1.5;color:#374151;">{summary}</p>'
            f"{anomaly_html}"
        )

        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Quant Analyst &middot; Technical Health",
            inner=inner,
        )
    except Exception:
        safe = _escape(plaintext_body)
        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Quant Analyst &middot; Technical Health",
            inner=f'<pre style="white-space:pre-wrap;margin:0;">{safe}</pre>',
        )


def render_pe_proposal_html(
    proposal: Any,
    work_order: dict[str, Any],
    plaintext_body: str,
) -> str:
    """Render a PE fix proposal as styled HTML.

    Duck-typed on `investigation_summary`, `proposed_changes`,
    `new_tests`, `risk_assessment`, `estimated_profit_impact`,
    `confidence`.

    Shape mirrors the PM/QA templates: shell + title + sections.
    Diffs render in a dark <pre> block so a glance tells Wilson
    whether the change is tractable without opening the markdown
    on disk.
    """
    try:
        confidence = _escape(getattr(proposal, "confidence", "") or "")
        confidence_color = {
            "high": "#059669",
            "medium": "#d97706",
            "low": "#6b7280",
        }.get(confidence, "#6b7280")
        wo_title = _escape(work_order.get("title") or "(untitled)")
        wo_target = _escape(work_order.get("target_file") or "(unspecified)")
        investigation = _escape(getattr(proposal, "investigation_summary", "") or "")
        proposed_changes = getattr(proposal, "proposed_changes", []) or []
        new_tests = getattr(proposal, "new_tests", []) or []
        risk = _escape(getattr(proposal, "risk_assessment", "") or "")
        impact = _escape(getattr(proposal, "estimated_profit_impact", "") or "")

        changes_html_parts: list[str] = []
        for ch in proposed_changes:
            ch_type = _escape(getattr(ch, "change_type", "") or "")
            ch_path = _escape(getattr(ch, "file_path", "") or "")
            ch_why = _escape(getattr(ch, "rationale", "") or "")
            ch_diff = _escape((getattr(ch, "diff", "") or "")[:2000])
            type_color = {
                "add": "#059669",
                "modify": "#2563eb",
                "delete": "#dc2626",
            }.get(ch_type, "#6b7280")
            changes_html_parts.append(
                f'<li style="margin:0 0 14px 0;list-style:none;">'
                f'<div><span style="background:{type_color};color:#fff;'
                f"padding:1px 7px;border-radius:10px;font-size:11px;"
                f'margin-right:6px;">{ch_type}</span>'
                f'<code style="background:#f3f4f6;padding:1px 6px;">'
                f"{ch_path}</code></div>"
                f'<div style="color:#6b7280;font-size:13px;margin-top:4px;">'
                f"<em>Why:</em> {ch_why}</div>"
                f'<pre style="margin:6px 0 0 0;padding:8px 10px;'
                f"background:#111827;color:#f9fafb;font-size:11px;"
                f'overflow-x:auto;border-radius:4px;white-space:pre-wrap;">'
                f"{ch_diff}</pre>"
                f"</li>"
            )
        changes_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "Proposed changes</h3>"
            f'<ul style="margin:0;padding:0;list-style:none;">'
            f"{''.join(changes_html_parts)}</ul>"
            if changes_html_parts
            else (
                '<p style="color:#6b7280;margin:16px 0 0 0;">'
                "No code changes — see investigation summary.</p>"
            )
        )

        tests_html_parts: list[str] = []
        for t in new_tests:
            tests_html_parts.append(
                f'<li style="margin:0 0 8px 0;">'
                f"<strong>{_escape(getattr(t, 'name', '') or '')}</strong> "
                f'in <code style="background:#f3f4f6;padding:1px 4px;">'
                f"{_escape(getattr(t, 'file_path', '') or '')}</code>"
                f'<div style="color:#6b7280;font-size:13px;">'
                f"{_escape(getattr(t, 'purpose', '') or '')}</div>"
                f"</li>"
            )
        tests_html = (
            '<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            "New tests</h3>"
            f'<ul style="margin:0;padding-left:18px;">'
            f"{''.join(tests_html_parts)}</ul>"
            if tests_html_parts
            else ""
        )

        inner = (
            f'<h2 style="margin:0 0 8px 0;font-size:20px;color:#111827;">{wo_title}</h2>'
            f'<div style="margin:0 0 16px 0;color:#6b7280;font-size:13px;">'
            f'<span style="background:{confidence_color};color:#fff;'
            f"padding:1px 7px;border-radius:10px;font-size:11px;"
            f'margin-right:6px;">confidence: {confidence}</span>'
            f'Target: <code style="background:#f3f4f6;padding:1px 4px;">{wo_target}</code>'
            f"</div>"
            f'<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            f"Investigation</h3>"
            f'<p style="margin:0 0 16px 0;line-height:1.5;color:#374151;">'
            f"{investigation}</p>"
            f"{changes_html}"
            f"{tests_html}"
            f'<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">Risk</h3>'
            f'<p style="margin:0 0 16px 0;color:#374151;">{risk}</p>'
            f'<h3 style="margin:20px 0 10px 0;font-size:14px;color:#374151;">'
            f"Expected profit impact</h3>"
            f'<p style="margin:0 0 16px 0;color:#374151;">{impact}</p>'
        )
        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Platform Engineer",
            inner=inner,
        )
    except Exception:
        safe = _escape(plaintext_body)
        return _EMAIL_SHELL.format(
            env_pill=_env_pill_html(),
            role_label="Platform Engineer",
            inner=f'<pre style="white-space:pre-wrap;margin:0;">{safe}</pre>',
        )
