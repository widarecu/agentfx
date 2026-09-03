"""Outbound notifications for the paper-trading runner.

Wilson 2026-08-31: replaced Slack incoming-webhook path with email.
Slack mobile push wasn't reliably reaching Wilson's phone, so
paper-run halts (which run for weeks at a time and can strand
positions on OANDA) were going unnoticed for hours. Email via SMTP
is the interim channel until a proper mobile app ships with native
push notifications.

Design invariants (unchanged from the Slack version):

- **Never re-raise.** A notification failure (missing config, SMTP
  timeout, bad response) must NEVER affect the live paper trade or
  the runner's decision loop. Log and continue.
- **Idempotent per (run_id, reason).** Callers write a dedup entry
  into `run.parameters['notified']` so re-entrant halt paths + the
  watchdog-vs-subprocess race can't spam the same event.
- **Config-gated.** Empty `SMTP_HOST` or empty `EMAIL_TO` or
  `NOTIFICATIONS_ENABLED=false` short-circuits at the top of every
  call site. Real email only leaves in staging/production where the
  SMTP config is explicitly set.

Callers should use `post_paper_run_alert` and pass the ORM `Run` row
plus a stable `reason` string (one of the `stop_reason` values the
runner emits — USER_KILL, LOSS_CAP_HIT, BROKER_CIRCUIT,
BROKER_NETWORK, OANDA_AUTH_LOST, process_crashed,
crashed_pre_init, ...).
"""

from .email import post_paper_run_alert

__all__ = ["post_paper_run_alert"]
