"""Unit tests for `agentfx.notifications.email` (Wilson 2026-08-31).

Replaces the old test_slack.py — same three-layer test structure
(should-send gate, message builder, send-with-retry) mirroring the
three helpers in the module. Layered so a failure at one layer
points straight at the bug without needing to unpick every layer
above it.

Every send-path test injects a fake `smtp_factory` so no real SMTP
connection is ever attempted. The `NOTIFICATIONS_ENABLED=false`
conftest guard is a second belt around this belt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from smtplib import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPServerDisconnected,
)

from agentfx.notifications import email as email_module

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# _should_send — pure gate
# ─────────────────────────────────────────────────────────────────────


class TestShouldSend:
    def _run(self, parameters: dict | None = None):
        return SimpleNamespace(id="run_x", parameters=parameters)

    def test_disabled_skips(self):
        ok, why = email_module._should_send(
            smtp_host="smtp.example.com",
            email_to="w@x.com",
            enabled=False,
            run=self._run(),
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "notifications_disabled"

    def test_missing_smtp_host_skips(self):
        ok, why = email_module._should_send(
            smtp_host="",
            email_to="w@x.com",
            enabled=True,
            run=self._run(),
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "no_smtp_configured"

    def test_missing_recipient_skips(self):
        ok, why = email_module._should_send(
            smtp_host="smtp.example.com",
            email_to="",
            enabled=True,
            run=self._run(),
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "no_recipient_configured"

    def test_already_notified_skips(self):
        run = self._run(
            parameters={"notified": {"USER_KILL": "2026-08-31T10:00:00+00:00"}}
        )
        ok, why = email_module._should_send(
            smtp_host="smtp.example.com",
            email_to="w@x.com",
            enabled=True,
            run=run,
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "already_notified"

    def test_different_reason_still_sends(self):
        """Dedup is per-(run, reason). A crash after a manual kill is
        a distinct event and must fire."""
        run = self._run(
            parameters={"notified": {"USER_KILL": "2026-08-31T10:00:00+00:00"}}
        )
        ok, why = email_module._should_send(
            smtp_host="smtp.example.com",
            email_to="w@x.com",
            enabled=True,
            run=run,
            reason="LOSS_CAP_HIT",
        )
        assert ok is True
        assert why == "send"

    def test_fresh_run_sends(self):
        ok, why = email_module._should_send(
            smtp_host="smtp.example.com",
            email_to="w@x.com",
            enabled=True,
            run=self._run(),
            reason="BROKER_NETWORK",
        )
        assert ok is True
        assert why == "send"


# ─────────────────────────────────────────────────────────────────────
# _build_message — pure builder
# ─────────────────────────────────────────────────────────────────────


class TestBuildMessage:
    def _run(
        self,
        run_id: str = "run_abc",
        status: str = "ERROR",
        pnl: float = -42.5,
        balance: float = 9_957.5,
        pair: str = "EUR_USD",
        name: str | None = "My Strategy",
    ):
        return SimpleNamespace(
            id=run_id,
            status=status,
            pnl=pnl,
            current_balance=balance,
            pair=pair,
            name=name,
        )

    def test_subject_carries_reason_and_run_id_prefix(self):
        msg = email_module._build_message(
            run=self._run(),
            reason="LOSS_CAP_HIT",
            extra=None,
            portal_base_url=None,
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com",
        )
        assert "LOSS_CAP_HIT" in msg["Subject"]
        assert "run_abc" in msg["Subject"]
        assert "[AgentFX]" in msg["Subject"]

    def test_body_contains_core_facts(self):
        msg = email_module._build_message(
            run=self._run(),
            reason="BROKER_NETWORK",
            extra=None,
            portal_base_url=None,
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com",
        )
        body = msg.get_content()
        assert "run_abc" in body
        assert "BROKER_NETWORK" in body
        assert "EUR_USD" in body
        assert "-42.50" in body
        assert "9,957.50" in body
        assert "My Strategy" in body

    def test_extra_section_rendered(self):
        msg = email_module._build_message(
            run=self._run(),
            reason="process_crashed",
            extra={"exit_code": 1, "traceback_head": "OandaError: timeout"},
            portal_base_url=None,
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com",
        )
        body = msg.get_content()
        assert "exit_code: 1" in body
        assert "traceback_head: OandaError: timeout" in body

    def test_portal_url_added_when_configured(self):
        msg = email_module._build_message(
            run=self._run(run_id="run_xyz"),
            reason="USER_KILL",
            extra=None,
            portal_base_url="https://agentfx-liart.vercel.app",
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com",
        )
        body = msg.get_content()
        assert "https://agentfx-liart.vercel.app/paper/run_xyz" in body

    def test_recipient_and_sender_headers_set(self):
        msg = email_module._build_message(
            run=self._run(),
            reason="USER_KILL",
            extra=None,
            portal_base_url=None,
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com,alt@x.com",
        )
        assert msg["From"] == "alerts@agentfx.com"
        # EmailMessage normalises comma-separated addresses to
        # "addr, addr" with a space after the comma; assert loosely.
        assert "wilson@x.com" in msg["To"]
        assert "alt@x.com" in msg["To"]

    def test_stub_run_missing_fields_renders_dashes(self):
        """A partially-populated run (e.g. never got a balance) must
        still produce a valid message — the alert is more useful than
        a crash."""
        stub = SimpleNamespace(id="run_stub")
        msg = email_module._build_message(
            run=stub,
            reason="crashed_pre_init (exit_code=1)",
            extra=None,
            portal_base_url=None,
            email_from="alerts@agentfx.com",
            email_to="wilson@x.com",
        )
        body = msg.get_content()
        assert "run_stub" in body
        assert "crashed_pre_init" in body
        # Missing pnl / balance render as em-dashes, not KeyError.
        assert "—" in body


# ─────────────────────────────────────────────────────────────────────
# _send_with_one_retry — SMTP send path
# ─────────────────────────────────────────────────────────────────────


class TestSendWithOneRetry:
    def _msg(self):
        m = MagicMock()
        return m

    def test_success_first_try(self):
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)
        ok, detail = email_module._send_with_one_retry(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="apikey",
            smtp_password="s3cret",
            email_from="alerts@example.com",
            email_to="wilson@x.com",
            msg=self._msg(),
            smtp_factory=lambda: smtp,
        )
        assert ok is True
        assert detail == "ok"
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("apikey", "s3cret")
        smtp.send_message.assert_called_once()

    def test_transient_error_recovers_on_retry(self, monkeypatch):
        monkeypatch.setattr(email_module, "_sleep_jitter", lambda: None)

        # First factory returns broken SMTP; second returns healthy.
        broken = MagicMock()
        broken.__enter__ = MagicMock(return_value=broken)
        broken.__exit__ = MagicMock(return_value=False)
        broken.starttls.side_effect = SMTPServerDisconnected("hung up")

        healthy = MagicMock()
        healthy.__enter__ = MagicMock(return_value=healthy)
        healthy.__exit__ = MagicMock(return_value=False)

        factory_calls = iter([broken, healthy])

        ok, detail = email_module._send_with_one_retry(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="apikey",
            smtp_password="s3cret",
            email_from="alerts@example.com",
            email_to="wilson@x.com",
            msg=self._msg(),
            smtp_factory=lambda: next(factory_calls),
        )
        assert ok is True
        assert detail == "ok"

    def test_persistent_transient_gives_up_after_retry(self, monkeypatch):
        monkeypatch.setattr(email_module, "_sleep_jitter", lambda: None)
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)
        smtp.starttls.side_effect = SMTPConnectError(421, "server busy")

        ok, detail = email_module._send_with_one_retry(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="apikey",
            smtp_password="s3cret",
            email_from="alerts@example.com",
            email_to="wilson@x.com",
            msg=self._msg(),
            smtp_factory=lambda: smtp,
        )
        assert ok is False
        assert "transient_after_retry" in detail
        assert "SMTPConnectError" in detail

    def test_auth_error_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(email_module, "_sleep_jitter", lambda: None)
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)
        smtp.login.side_effect = SMTPAuthenticationError(535, "bad credentials")

        factory = MagicMock(return_value=smtp)
        ok, detail = email_module._send_with_one_retry(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="apikey",
            smtp_password="bad",
            email_from="alerts@example.com",
            email_to="wilson@x.com",
            msg=self._msg(),
            smtp_factory=factory,
        )
        assert ok is False
        assert "non_retryable" in detail
        assert "SMTPAuthenticationError" in detail
        # Exactly one attempt — no retry after auth failure.
        assert factory.call_count == 1

    def test_login_skipped_when_no_credentials(self):
        """Some providers accept anonymous submission when the sender
        is pre-authenticated at the network layer (e.g. Postfix
        localhost). Skip the login step if user/password are empty."""
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)

        ok, _ = email_module._send_with_one_retry(
            smtp_host="localhost",
            smtp_port=25,
            smtp_user="",
            smtp_password="",
            email_from="alerts@example.com",
            email_to="wilson@x.com",
            msg=self._msg(),
            smtp_factory=lambda: smtp,
        )
        assert ok is True
        smtp.login.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# post_paper_run_alert — public entry point
# ─────────────────────────────────────────────────────────────────────


class TestPostPaperRunAlert:
    def _run(self, run_id: str = "run_p", parameters: dict | None = None):
        return SimpleNamespace(
            id=run_id,
            parameters=parameters,
            status="ERROR",
            pnl=-42.5,
            current_balance=9_957.5,
            pair="EUR_USD",
            name="Test Strategy",
        )

    def _settings(
        self,
        enabled: bool = True,
        smtp_host: str = "smtp.example.com",
        email_to: str = "wilson@x.com",
    ):
        return SimpleNamespace(
            NOTIFICATIONS_ENABLED=enabled,
            SMTP_HOST=smtp_host,
            SMTP_PORT=587,
            SMTP_USER="apikey",
            SMTP_PASSWORD="s3cret",
            EMAIL_FROM="alerts@agentfx.com",
            EMAIL_TO=email_to,
        )

    def test_short_circuits_when_smtp_not_configured(self):
        """SMTP_HOST unset (default in dev/test) must NOT attempt any
        network call — and the outer catch-all must not fire."""
        run = self._run()
        with patch(
            "agentfx.api.core.config.settings",
            self._settings(smtp_host=""),
        ):
            # The point is that nothing raises + no factory is called.
            email_module.post_paper_run_alert(run=run, reason="USER_KILL")
        # No dedup entry written because we never actually sent.
        assert run.parameters is None or "notified" not in (run.parameters or {})

    def test_short_circuits_when_disabled(self):
        run = self._run()
        with patch(
            "agentfx.api.core.config.settings",
            self._settings(enabled=False),
        ):
            email_module.post_paper_run_alert(run=run, reason="USER_KILL")
        assert run.parameters is None or "notified" not in (run.parameters or {})

    def test_success_writes_dedup_marker(self):
        run = self._run(parameters={})
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)

        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ),
        ):
            email_module.post_paper_run_alert(run=run, reason="LOSS_CAP_HIT", db=None)

        assert "notified" in run.parameters
        assert "LOSS_CAP_HIT" in run.parameters["notified"]

    def test_send_failure_does_not_write_dedup(self):
        """If the send fails we MUST NOT write the dedup marker, so
        the next event has a chance to retry."""
        run = self._run(parameters={})
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module,
                "_send_with_one_retry",
                return_value=(False, "transient_after_retry:SMTPConnectError"),
            ),
        ):
            email_module.post_paper_run_alert(run=run, reason="BROKER_NETWORK")
        assert run.parameters == {}

    def test_second_call_same_reason_does_not_send_again(self):
        """Idempotent per (run, reason) — the classic re-entrant halt
        + watchdog race must not double-send."""
        run = self._run(parameters={})
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ) as sender,
        ):
            email_module.post_paper_run_alert(run=run, reason="USER_KILL")
            email_module.post_paper_run_alert(run=run, reason="USER_KILL")
        assert sender.call_count == 1

    def test_never_raises_on_unexpected_error(self):
        """Belt-and-braces: any exception from anywhere in the chain
        must NOT propagate. The trade is more important than the
        alert."""
        run = self._run()
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module,
                "_build_message",
                side_effect=RuntimeError("something exploded"),
            ),
        ):
            # Must not raise.
            email_module.post_paper_run_alert(run=run, reason="USER_KILL")

    def test_db_commit_called_on_success(self):
        """When `db` is passed and the send succeeds, the dedup entry
        is committed inside the notification helper."""
        run = self._run(parameters={})
        db = MagicMock()
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ),
        ):
            email_module.post_paper_run_alert(run=run, reason="USER_KILL", db=db)
        db.commit.assert_called_once()

    def test_db_commit_failure_does_not_re_raise(self):
        """If the post-send commit blows up (bad session, disconnected
        DB), the caller must still complete."""
        run = self._run(parameters={})
        db = MagicMock()
        db.commit.side_effect = RuntimeError("session busted")
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ),
        ):
            # Must not raise.
            email_module.post_paper_run_alert(run=run, reason="USER_KILL", db=db)
