"""Unit tests for the HTML email rendering (Phase B).

The actual SMTP path is covered by `test_email.py`. These tests
exercise `render_pm_brief_html` + `render_qa_divergence_html` as
pure functions, plus the `html_body` extension to
`send_agent_message` end-to-end against a mocked SMTP factory.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentfx.notifications import email as email_module

pytestmark = pytest.mark.unit


class TestPMBriefHTML:
    def _brief(self, **overrides):
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            PMConcern,
            PMRecommendation,
        )

        defaults = dict(
            headline="Fleet is healthy",
            summary="All systems nominal.",
            recommendations=[
                PMRecommendation(
                    priority="now",
                    headline="Halt losing paper run",
                    rationale="Draw-down past backtest tolerance.",
                )
            ],
            concerns=[
                PMConcern(severity="noted", description="Spread wider than expected.")
            ],
        )
        defaults.update(overrides)
        return PMBrief(**defaults)

    def test_includes_headline_and_summary(self):
        html = email_module.render_pm_brief_html(self._brief(), plaintext_body="")
        assert "Fleet is healthy" in html
        assert "All systems nominal" in html

    def test_recommendations_render_with_priority_badge(self):
        html = email_module.render_pm_brief_html(self._brief(), plaintext_body="")
        assert "now" in html  # priority chip
        assert "Halt losing paper run" in html
        assert "Draw-down past backtest tolerance" in html

    def test_concerns_render_with_severity_badge(self):
        html = email_module.render_pm_brief_html(self._brief(), plaintext_body="")
        assert "noted" in html
        assert "Spread wider than expected" in html

    def test_empty_sections_hidden_when_no_data(self):
        brief = self._brief(recommendations=[], concerns=[])
        html = email_module.render_pm_brief_html(brief, plaintext_body="")
        assert "Recommendations" not in html
        assert "Concerns" not in html

    def test_rendering_failure_falls_back_to_pre_block(self):
        """A busted brief must not break the email send. We simulate a
        real template failure by making an attribute access raise;
        the function must swallow the exception and return the
        <pre>-wrapped plaintext fallback."""

        class _ExplodingBrief:
            @property
            def headline(self):
                raise RuntimeError("renderer bug")

        html = email_module.render_pm_brief_html(
            _ExplodingBrief(), plaintext_body="raw body"
        )
        assert "<pre" in html
        assert "raw body" in html

    def test_escapes_html_special_chars(self):
        brief = self._brief(headline="<script>alert(1)</script>")
        html = email_module.render_pm_brief_html(brief, plaintext_body="")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestQADivergenceHTML:
    def _report(self):
        from agentfx.agents.fleet.quant_analyst import (
            DivergenceClassification,
            QADivergenceReport,
            QAWorkOrder,
        )

        return QADivergenceReport(
            headline="Cost model wrong",
            summary="Realized spread 5x backtest.",
            classifications=[
                DivergenceClassification(
                    category="cost_model",
                    confidence=0.9,
                    description="5x the modelled spread.",
                )
            ],
            work_orders=[
                QAWorkOrder(
                    title="Update typical_spread_pips",
                    target_file="DB table",
                    spec="Set EUR_USD M1 to 1.5 pips.",
                    expected_profit_impact="Removes 1% drag per trade.",
                )
            ],
        )

    def test_renders_full_report(self):
        html = email_module.render_qa_divergence_html(self._report(), plaintext_body="")
        assert "Cost model wrong" in html
        assert "cost_model" in html
        assert "90%" in html  # confidence
        assert "Update typical_spread_pips" in html


class TestSendAgentMessageHTML:
    """`send_agent_message` must send multipart/alternative when
    `html_body` is passed."""

    def _settings(self):
        return SimpleNamespace(
            NOTIFICATIONS_ENABLED=True,
            AGENT_FLEET_ENABLED=True,
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_USER="apikey",
            SMTP_PASSWORD="s3cret",
            EMAIL_FROM="alerts@agentfx.com",
            EMAIL_TO="wilson@example.com",
            ENVIRONMENT="test",
        )

    def test_html_body_added_as_alternative(self):
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ) as sender,
        ):
            email_module.send_agent_message(
                agent_role="PM",
                subject="Hi",
                body="plain text",
                html_body="<p>html body</p>",
            )
        sender.assert_called_once()
        msg = sender.call_args.kwargs["msg"]
        # EmailMessage with add_alternative gives it a multipart shape.
        assert msg.is_multipart() is True
        parts = list(msg.iter_parts())
        content_types = {p.get_content_type() for p in parts}
        assert "text/plain" in content_types
        assert "text/html" in content_types

    def test_plain_body_only_stays_single_part(self):
        with (
            patch("agentfx.api.core.config.settings", self._settings()),
            patch.object(
                email_module, "_send_with_one_retry", return_value=(True, "ok")
            ) as sender,
        ):
            email_module.send_agent_message(
                agent_role="PM",
                subject="Hi",
                body="plain only",
            )
        sender.assert_called_once()
        msg = sender.call_args.kwargs["msg"]
        assert msg.is_multipart() is False
