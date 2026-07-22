"""Outbound notifications for the paper-trading runner.

v0.6.2 — Wilson UAT 2026-07-19. Paper runs are expected to run for
weeks at a time; the portal is not a viable alerting surface for that
horizon. This package delivers Slack-channel notifications on paper-
run terminal events + watchdog-detected crashes.

Design invariants:

- **Never re-raise.** A notification failure (missing config, HTTP
  timeout, bad response) must NEVER affect the live paper trade or
  the runner's decision loop. Log and continue.
- **Idempotent per (run_id, reason).** Callers write a dedup entry
  into `run.parameters['notified']` so re-entrant halt paths + the
  watchdog-vs-subprocess race can't spam the same event.
- **Config-gated.** Empty `SLACK_WEBHOOK_URL` or
  `NOTIFICATIONS_ENABLED=false` short-circuits at the top of every
  call site. Real HTTP only leaves in staging/production where the
  webhook URL is explicitly set.

Callers should use `post_paper_run_alert` and pass the ORM `Run` row
plus a stable `reason` string (one of the `stop_reason` values the
runner emits — USER_KILL, DAILY_LOSS_LIMIT, BROKER_CIRCUIT,
OANDA_AUTH_LOST, process_crashed, crashed_pre_init, ...).
"""

from .slack import post_paper_run_alert

__all__ = ["post_paper_run_alert"]
