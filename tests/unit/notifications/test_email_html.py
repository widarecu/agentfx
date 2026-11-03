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


_GENOME_ID = "genome_gen_evorun_20261027_ab12cd34_003_017"
_APPROVAL_URL = f"https://agentfx-liart.vercel.app/paper/approvals/{_GENOME_ID}"


def _genome_ref(**overrides):
    """Resolved genome reference as PM hands it to the renderer."""
    ref = {
        "id": _GENOME_ID,
        "verified": True,
        "pair": "AUD_USD",
        "granularity": "M30",
        "fitness": 1.264,
        "approval_url": _APPROVAL_URL,
    }
    ref.update(overrides)
    return ref


class TestPMBriefGenomeRefsHTML:
    """AFX-38: a recommendation / evolution action that names a genome
    renders the full id, DB-sourced facts and a deep link to the
    approval row."""

    def _brief(self, *headlines):
        from agentfx.agents.fleet.portfolio_manager import PMBrief, PMRecommendation

        return PMBrief(
            headline="h",
            summary="s",
            recommendations=[
                PMRecommendation(priority="now", headline=h, rationale="r")
                for h in headlines
            ],
        )

    def test_renders_full_id_facts_and_approval_link(self):
        html = email_module.render_pm_brief_html(
            self._brief("Deploy AUD_USD/M30 genome"),
            plaintext_body="",
            recommendation_genome_refs=[[_genome_ref()]],
        )
        assert _GENOME_ID in html
        assert f'href="{_APPROVAL_URL}"' in html
        assert "Open approval row" in html
        assert "AUD_USD M30" in html
        assert "fitness 1.264" in html

    def test_refs_attach_to_the_matching_recommendation(self):
        html = email_module.render_pm_brief_html(
            self._brief("Halt losing run", "Deploy AUD_USD/M30 genome"),
            plaintext_body="",
            recommendation_genome_refs=[[], [_genome_ref()]],
        )
        assert html.index("Halt losing run") < html.index("Deploy AUD_USD/M30")
        assert html.index("Deploy AUD_USD/M30") < html.index(_APPROVAL_URL)

    def test_unverified_ref_warns_instead_of_showing_facts(self):
        html = email_module.render_pm_brief_html(
            self._brief("Deploy something"),
            plaintext_body="",
            recommendation_genome_refs=[
                [_genome_ref(verified=False, pair=None, granularity=None, fitness=None)]
            ],
        )
        assert _GENOME_ID in html
        assert "not in today's snapshot" in html
        assert "AUD_USD M30" not in html

    def test_relative_path_renders_as_text_not_a_broken_link(self):
        """No portal base URL known (dev/test) → a relative href would be
        dead in a mail client; show the path as text instead."""
        path = f"/paper/approvals/{_GENOME_ID}"
        html = email_module.render_pm_brief_html(
            self._brief("Deploy AUD_USD/M30 genome"),
            plaintext_body="",
            recommendation_genome_refs=[[_genome_ref(approval_url=path)]],
        )
        assert path in html
        assert 'href="/paper' not in html

    def test_ref_values_are_escaped(self):
        evil = '"><script>alert(1)</script>'
        html = email_module.render_pm_brief_html(
            self._brief("Deploy"),
            plaintext_body="",
            recommendation_genome_refs=[
                [_genome_ref(id=evil, pair=evil, verified=True)]
            ],
        )
        assert "<script>" not in html

    def test_no_refs_renders_exactly_as_before(self):
        brief = self._brief("Deploy AUD_USD/M30 genome")
        assert email_module.render_pm_brief_html(
            brief, plaintext_body=""
        ) == email_module.render_pm_brief_html(
            brief, plaintext_body="", recommendation_genome_refs=[[]]
        )
        assert "approval row" not in email_module.render_pm_brief_html(
            brief, plaintext_body=""
        )

    def test_scheduling_result_genome_refs_render_link(self):
        results = [
            {
                "pair": "AUD_USD",
                "granularity": "M30",
                "priority": "watch",
                "rationale": f"Champion {_GENOME_ID} already clears Gate B.",
                "expected_profit_impact": "Paper P&L within 7d.",
                "accepted": False,
                "evo_run_id": None,
                "reason": "wrong_priority:watch",
                "genome_refs": [_genome_ref()],
            }
        ]
        html = email_module.render_pm_brief_html(
            self._brief(), plaintext_body="", scheduling_results=results
        )
        assert f'href="{_APPROVAL_URL}"' in html


class TestApprovalRowUrl:
    """AFX-38: single source of truth for the approval-row deep link
    (`/paper/approvals/:genomeId` in the portal router)."""

    def test_absolute_url_with_explicit_base(self):
        assert (
            email_module.approval_row_url(
                _GENOME_ID, portal_base_url="https://agentfx-liart.vercel.app/"
            )
            == _APPROVAL_URL
        )

    def test_base_inferred_from_environment(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert email_module.approval_row_url(_GENOME_ID) == _APPROVAL_URL

    def test_bare_path_when_no_portal_is_known(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "test")
        assert (
            email_module.approval_row_url(_GENOME_ID)
            == f"/paper/approvals/{_GENOME_ID}"
        )

    def test_id_is_url_quoted_as_one_path_segment(self):
        assert (
            email_module.approval_row_url("a/b?c d", portal_base_url="https://p")
            == "https://p/paper/approvals/a%2Fb%3Fc%20d"
        )


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
