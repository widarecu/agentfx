"""Unit tests for `agentfx.notifications.slack` (v0.6.2).

Locks the entire edge-case matrix from the plan file: every
short-circuit path, the retry-then-abandon behaviour, the dedup
semantics via `run.parameters['notified']`, and the top-level
belt-and-braces catch that guarantees notification failures cannot
affect the running trade.

Structure:
- Pure helpers (`_should_send`, `_build_slack_payload`,
  `_mark_notified`) exercised deterministically.
- Delivery path (`_post_with_one_retry`) with a mocked
  `requests.Session`, verifying both branches of the retry.
- End-to-end `post_paper_run_alert` via monkeypatched `settings` +
  a stub `requests` module, asserting POST call counts and dedup
  side effects on the run row.

`_sleep_jitter` is monkeypatched to a no-op in every test that
touches the retry path — otherwise each such case would pay
300-800ms.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from agentfx.notifications import slack


pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_run(
    *,
    id: str = "run_test_abc",
    status: str = "HALTED_MANUAL",
    pnl: float = -12.34,
    balance: float = 987.65,
    pair: str = "EUR_USD",
    parameters: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        status=status,
        pnl=pnl,
        current_balance=balance,
        pair=pair,
        parameters=parameters if parameters is not None else {},
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retry-path tests should not pay 300-800ms wall-clock per case.
    Applied globally — every test benefits, none needs the real jitter.
    """
    monkeypatch.setattr(slack, "_sleep_jitter", lambda: None)


@pytest.fixture(autouse=True)
def _reset_log_once():
    """`_log_once` uses a module-level `set` — reset between tests so
    the "webhook not configured" INFO line can be re-observed if a
    later test needs to check for it.
    """
    slack._MODULE_LOG_ONCE.clear()
    yield
    slack._MODULE_LOG_ONCE.clear()


@pytest.fixture
def enable_notifications(monkeypatch):
    """Flip `NOTIFICATIONS_ENABLED` on and set a webhook URL for the
    lifetime of a test. `conftest.py` disables notifications globally
    for safety; opt-in per-test to exercise the send path.
    """
    from agentfx.api.core import config

    monkeypatch.setattr(config.settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(
        config.settings,
        "SLACK_WEBHOOK_URL",
        "https://hooks.slack.example/T00/B00/XYZ",
    )


# ─────────────────────────────────────────────────────────────────────
# _should_send — the pure gate
# ─────────────────────────────────────────────────────────────────────


class TestShouldSend:
    def test_disabled_master_toggle_skips(self):
        ok, why = slack._should_send(
            webhook_url="https://hooks.slack.test/x",
            enabled=False,
            run=_make_run(),
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "notifications_disabled"

    def test_missing_webhook_skips(self):
        ok, why = slack._should_send(
            webhook_url="",
            enabled=True,
            run=_make_run(),
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "no_webhook_configured"

    def test_no_prior_dedup_sends(self):
        ok, why = slack._should_send(
            webhook_url="https://hooks.slack.test/x",
            enabled=True,
            run=_make_run(parameters={}),
            reason="USER_KILL",
        )
        assert ok is True
        assert why == "send"

    def test_same_reason_already_notified_skips(self):
        run = _make_run(
            parameters={"notified": {"USER_KILL": "2026-07-21T12:00:00+00:00"}},
        )
        ok, why = slack._should_send(
            webhook_url="https://hooks.slack.test/x",
            enabled=True,
            run=run,
            reason="USER_KILL",
        )
        assert ok is False
        assert why == "already_notified"

    def test_different_reason_still_sends(self):
        # Wilson kills the run, then it crashes later — TWO distinct
        # events, both deserve an alert.
        run = _make_run(
            parameters={"notified": {"USER_KILL": "2026-07-21T12:00:00+00:00"}},
        )
        ok, why = slack._should_send(
            webhook_url="https://hooks.slack.test/x",
            enabled=True,
            run=run,
            reason="process_crashed",
        )
        assert ok is True
        assert why == "send"

    def test_malformed_notified_key_treated_as_no_dedup(self):
        # Defensive: a legacy row where `parameters['notified']` isn't
        # a dict (some future writer misuse) must not crash the send
        # path. `_should_send` treats it as "not previously notified"
        # and lets the caller proceed.
        run = _make_run(parameters={"notified": "corrupted string value"})
        ok, why = slack._should_send(
            webhook_url="https://hooks.slack.test/x",
            enabled=True,
            run=run,
            reason="USER_KILL",
        )
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# _build_slack_payload — pure payload construction
# ─────────────────────────────────────────────────────────────────────


class TestBuildPayload:
    def test_includes_run_id_status_reason_pnl_balance_instrument(self):
        run = _make_run(pnl=42.5, balance=1_234.56, pair="GBP_USD")
        payload = slack._build_slack_payload(
            run=run,
            reason="DAILY_LOSS_LIMIT",
            extra=None,
            portal_base_url=None,
        )
        assert "🚨 Paper run halted" in payload["text"]
        assert "DAILY_LOSS_LIMIT" in payload["text"]
        # Header block + section fields block only when no portal URL
        assert payload["blocks"][0]["type"] == "header"
        fields = payload["blocks"][1]["fields"]
        joined = " ".join(f["text"] for f in fields)
        assert "run_test_abc" in joined
        assert "DAILY_LOSS_LIMIT" in joined
        assert "HALTED_MANUAL" in joined
        assert "42.50" in joined
        assert "1,234.56" in joined
        assert "GBP_USD" in joined

    def test_appends_extra_fields_verbatim(self):
        run = _make_run()
        payload = slack._build_slack_payload(
            run=run,
            reason="process_crashed",
            extra={"exit_code": -9, "component": "watchdog"},
            portal_base_url=None,
        )
        fields = payload["blocks"][1]["fields"]
        joined = " ".join(f["text"] for f in fields)
        assert "exit_code" in joined
        assert "-9" in joined
        assert "component" in joined
        assert "watchdog" in joined

    def test_adds_portal_link_when_base_url_given(self):
        run = _make_run(id="run_xyz")
        payload = slack._build_slack_payload(
            run=run,
            reason="USER_KILL",
            extra=None,
            portal_base_url="https://portal.example.com/",
        )
        # Trailing slash on base URL should be stripped, then joined.
        link_block = payload["blocks"][-1]
        assert "https://portal.example.com/paper/run_xyz" in link_block["text"]["text"]

    def test_survives_missing_run_attributes(self):
        # A stub run with only an id must not crash the builder — all
        # the numeric formatting paths need to tolerate None.
        run = SimpleNamespace(id="run_bare")
        payload = slack._build_slack_payload(
            run=run,
            reason="BROKER_CIRCUIT",
            extra=None,
            portal_base_url=None,
        )
        joined = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "run_bare" in joined
        assert "—" in joined  # placeholder for missing pnl/balance/pair

    def test_survives_non_numeric_pnl(self):
        # Defensive: if the ORM row's pnl ends up as a string (legacy
        # legacy?) the formatter must fall back rather than raise.
        run = _make_run(pnl="not-a-number")
        payload = slack._build_slack_payload(
            run=run, reason="USER_KILL", extra=None, portal_base_url=None
        )
        joined = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "not-a-number" in joined


# ─────────────────────────────────────────────────────────────────────
# _mark_notified — dedup write
# ─────────────────────────────────────────────────────────────────────


class TestMarkNotified:
    def test_writes_reason_key_with_iso_timestamp(self):
        run = _make_run(parameters={})
        slack._mark_notified(run, "USER_KILL")
        assert "notified" in run.parameters
        assert "USER_KILL" in run.parameters["notified"]
        # ISO 8601 UTC — starts with a 4-digit year, ends with +00:00
        ts = run.parameters["notified"]["USER_KILL"]
        assert ts.startswith("20")
        assert "+00:00" in ts

    def test_preserves_existing_notified_entries(self):
        run = _make_run(
            parameters={"notified": {"USER_KILL": "2026-07-21T12:00:00+00:00"}},
        )
        slack._mark_notified(run, "process_crashed")
        assert set(run.parameters["notified"].keys()) == {
            "USER_KILL",
            "process_crashed",
        }

    def test_reassigns_parameters_dict_for_sqlalchemy_change_tracking(self):
        # Mirrors the `stale_active` pattern shipped this weekend —
        # SQLAlchemy JSON change detection needs a NEW dict object, not
        # an in-place mutation. Verify we're re-assigning.
        original = {}
        run = _make_run(parameters=original)
        slack._mark_notified(run, "USER_KILL")
        assert run.parameters is not original


# ─────────────────────────────────────────────────────────────────────
# _post_with_one_retry — delivery layer with mocked session
# ─────────────────────────────────────────────────────────────────────


def _fake_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    return resp


class TestPostWithOneRetry:
    def test_2xx_succeeds_first_try_no_retry(self):
        session = MagicMock()
        session.post.return_value = _fake_response(200)
        ok, detail = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is True
        assert detail == "ok"
        assert session.post.call_count == 1

    def test_4xx_fails_immediately_no_retry(self):
        # Bad webhook, deleted channel — no point retrying.
        session = MagicMock()
        session.post.return_value = _fake_response(404)
        ok, detail = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is False
        assert "404" in detail
        assert session.post.call_count == 1

    def test_5xx_retries_once_then_succeeds(self):
        session = MagicMock()
        session.post.side_effect = [_fake_response(503), _fake_response(200)]
        ok, detail = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is True
        assert session.post.call_count == 2

    def test_5xx_twice_gives_up(self):
        session = MagicMock()
        session.post.side_effect = [_fake_response(500), _fake_response(502)]
        ok, detail = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is False
        assert "http_5xx" in detail
        assert "502" in detail
        assert session.post.call_count == 2

    def test_connection_error_retries_once(self):
        session = MagicMock()
        session.post.side_effect = [
            requests.ConnectionError("DNS gone"),
            _fake_response(200),
        ]
        ok, _ = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is True
        assert session.post.call_count == 2

    def test_connection_error_twice_gives_up(self):
        session = MagicMock()
        session.post.side_effect = [
            requests.ConnectionError("DNS gone"),
            requests.Timeout("read timeout"),
        ]
        ok, detail = slack._post_with_one_retry(
            webhook_url="https://hooks.slack.test/x",
            payload={"text": "hi"},
            session=session,
        )
        assert ok is False
        assert "exception_after_retry" in detail
        assert session.post.call_count == 2


# ─────────────────────────────────────────────────────────────────────
# post_paper_run_alert — end-to-end with mocked settings + POST
# ─────────────────────────────────────────────────────────────────────


class TestPostPaperRunAlert:
    def test_short_circuits_when_notifications_disabled(self, monkeypatch):
        # No enable_notifications fixture → conftest's default
        # NOTIFICATIONS_ENABLED=false is in effect.
        posted = []
        monkeypatch.setattr(
            slack.requests,
            "post",
            lambda *a, **kw: posted.append((a, kw)) or _fake_response(200),
        )
        run = _make_run()
        slack.post_paper_run_alert(run=run, reason="USER_KILL")
        assert posted == []
        assert "notified" not in (run.parameters or {})

    def test_short_circuits_when_webhook_url_empty(self, monkeypatch):
        from agentfx.api.core import config

        monkeypatch.setattr(config.settings, "NOTIFICATIONS_ENABLED", True)
        monkeypatch.setattr(config.settings, "SLACK_WEBHOOK_URL", "")
        posted = []
        monkeypatch.setattr(
            slack.requests,
            "post",
            lambda *a, **kw: posted.append((a, kw)) or _fake_response(200),
        )
        slack.post_paper_run_alert(run=_make_run(), reason="USER_KILL")
        assert posted == []

    def test_happy_path_posts_and_marks_notified(
        self, enable_notifications, monkeypatch
    ):
        call = {}

        def fake_post(url, json=None, timeout=None):
            call["url"] = url
            call["json"] = json
            call["timeout"] = timeout
            return _fake_response(200)

        monkeypatch.setattr(slack.requests, "post", fake_post)
        run = _make_run(parameters={})
        slack.post_paper_run_alert(run=run, reason="USER_KILL")

        assert call["url"] == "https://hooks.slack.example/T00/B00/XYZ"
        assert call["timeout"] == slack._REQUEST_TIMEOUT_S
        assert "🚨" in call["json"]["text"]
        assert run.parameters["notified"]["USER_KILL"].startswith("20")

    def test_second_call_same_reason_does_not_post_again(
        self, enable_notifications, monkeypatch
    ):
        posts = []
        monkeypatch.setattr(
            slack.requests,
            "post",
            lambda *a, **kw: posts.append(a) or _fake_response(200),
        )
        run = _make_run(parameters={})
        slack.post_paper_run_alert(run=run, reason="USER_KILL")
        slack.post_paper_run_alert(run=run, reason="USER_KILL")
        assert len(posts) == 1  # dedup — second call short-circuits

    def test_second_call_different_reason_does_post(
        self, enable_notifications, monkeypatch
    ):
        posts = []
        monkeypatch.setattr(
            slack.requests,
            "post",
            lambda *a, **kw: posts.append(a) or _fake_response(200),
        )
        run = _make_run(parameters={})
        slack.post_paper_run_alert(run=run, reason="USER_KILL")
        slack.post_paper_run_alert(run=run, reason="process_crashed")
        assert len(posts) == 2

    def test_delivery_failure_does_not_mark_notified(
        self, enable_notifications, monkeypatch
    ):
        # 4xx from Slack → do NOT record dedup, so the next event
        # (potentially with a fresh webhook config) can retry.
        monkeypatch.setattr(
            slack.requests, "post", lambda *a, **kw: _fake_response(404)
        )
        run = _make_run(parameters={})
        slack.post_paper_run_alert(run=run, reason="USER_KILL")
        assert (run.parameters or {}).get("notified", {}).get("USER_KILL") is None

    def test_never_raises_when_post_throws(self, enable_notifications, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("network daemon crashed")

        monkeypatch.setattr(slack.requests, "post", boom)
        run = _make_run(parameters={})
        # Must not raise — a notification failure MUST NOT affect the
        # running trade. Assertion is simply "we got here without an
        # exception propagating".
        slack.post_paper_run_alert(run=run, reason="USER_KILL")

    def test_never_raises_when_config_import_fails(self, monkeypatch):
        # Degenerate case: the config module itself misbehaves. The
        # outer belt-and-braces try/except must catch it.
        def broken_import(name, *a, **kw):
            if name == "agentfx.api.core.config":
                raise ImportError("simulated import failure")
            return __import__(name, *a, **kw)

        monkeypatch.setattr("builtins.__import__", broken_import)
        # Must not raise.
        slack.post_paper_run_alert(run=_make_run(), reason="USER_KILL")

    def test_db_commit_failure_is_swallowed(self, enable_notifications, monkeypatch):
        monkeypatch.setattr(
            slack.requests, "post", lambda *a, **kw: _fake_response(200)
        )

        class BadDB:
            def commit(self):
                raise RuntimeError("db is down")

        run = _make_run(parameters={})
        # Even though db.commit blows up, the outer try must catch it
        # and the terminal-state flow keeps working. The dedup entry
        # in memory is still set (side effect of successful post),
        # but callers depending on persistence should notice via the
        # WARNING log.
        slack.post_paper_run_alert(run=run, reason="USER_KILL", db=BadDB())
        assert run.parameters["notified"]["USER_KILL"].startswith("20")
