"""Unit tests for the Quant Analyst Agent (v0.8.0 Phase B).

Tests focus on:
- idempotency (`decision_key` per run per day)
- activity threshold (≥N trades OR terminal stop_reason)
- never-raises contract (orchestrator scan loop depends on this)
- fallback report shape when LLM is skipped
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentfx.agents.fleet import quant_analyst as qa

pytestmark = pytest.mark.unit


class TestReportKey:
    def test_key_is_run_and_date_stamped(self):
        key = qa._report_key_for("run_abc", date(2026, 10, 5))
        assert key == "qa_divergence_run_abc_2026-10-05"

    def test_different_runs_different_keys(self):
        assert qa._report_key_for("a", date(2026, 10, 5)) != qa._report_key_for(
            "b", date(2026, 10, 5)
        )


class TestNeverRaises:
    def test_scan_failure_returns_zero(self):
        db = MagicMock()
        # Make `_candidate_runs` crash hard.
        with patch.object(qa, "_candidate_runs", side_effect=RuntimeError("boom")):
            # Must not raise.
            produced = qa.scan_and_run_reports(db=db)
        assert produced == 0

    def test_per_run_failure_logged_and_scan_continues(self):
        db = MagicMock()
        with patch.object(qa, "_candidate_runs", return_value=["r1", "r2", "r3"]):
            with patch.object(
                qa,
                "_write_divergence_report",
                side_effect=[RuntimeError("fail1"), None, RuntimeError("fail3")],
            ):
                # Must not raise; one of the three succeeded.
                produced = qa.scan_and_run_reports(db=db)
        # The one in the middle completed without raising; the other
        # two were caught per-run.
        assert produced == 1


class TestFallbackReport:
    def test_fallback_has_valid_shape(self):
        snapshot = {
            "run_id": "run_x",
            "stop_reason": "USER_KILL",
            "trades_recent": [{"pnl": -1.0}, {"pnl": -2.0}],
        }
        report = qa._deterministic_fallback(snapshot, "run_x")
        assert report.headline
        assert "deterministic fallback" in report.summary.lower()
        # Fallback never fabricates classifications or work orders.
        assert report.classifications == []
        assert report.work_orders == []


class TestTechHealthKeys:
    def test_key_is_date_stamped(self):
        from datetime import date

        assert (
            qa._tech_health_key_for(date(2026, 10, 6))
            == "qa_technical_health_2026-10-06"
        )


class TestTechHealthScan:
    """The scan should skip the LLM call when the log buffer is
    quiet (below the threshold), and also skip when today's report
    has already been written (idempotency)."""

    def test_skips_when_log_count_below_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        # No existing report row.
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501
        # Buffer returns 2 records (below the 10-record threshold).
        from agentfx.agents.fleet.log_buffer import CapturedLog, LogBuffer
        from datetime import UTC, datetime

        buf = LogBuffer()
        for i in range(2):
            buf.append(
                CapturedLog(
                    timestamp=datetime.now(UTC),
                    level="WARNING",
                    logger_name="l",
                    message=f"warn-{i}",
                )
            )
        with patch(
            "agentfx.agents.fleet.quant_analyst.get_global_buffer",
            return_value=buf,
        ):
            with patch(
                "agentfx.agents.fleet.quant_analyst._write_tech_report"
            ) as writer:
                produced = qa.scan_for_technical_anomalies(db=db)
        assert produced is False
        writer.assert_not_called()

    def test_skips_when_todays_report_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        # Simulate an existing report row.
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = MagicMock()  # noqa: E501
        with patch("agentfx.agents.fleet.quant_analyst._write_tech_report") as writer:
            produced = qa.scan_for_technical_anomalies(db=db)
        assert produced is False
        writer.assert_not_called()

    def test_fires_when_threshold_crossed_and_no_prior_report(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501

        from agentfx.agents.fleet.log_buffer import CapturedLog, LogBuffer
        from datetime import UTC, datetime

        buf = LogBuffer()
        # 15 records = above the 10 threshold.
        for i in range(15):
            buf.append(
                CapturedLog(
                    timestamp=datetime.now(UTC),
                    level="WARNING",
                    logger_name="l",
                    message=f"warn-{i}",
                )
            )
        with patch(
            "agentfx.agents.fleet.quant_analyst.get_global_buffer",
            return_value=buf,
        ):
            with patch(
                "agentfx.agents.fleet.quant_analyst._write_tech_report"
            ) as writer:
                produced = qa.scan_for_technical_anomalies(db=db)
        assert produced is True
        writer.assert_called_once()

    def test_never_raises_on_internal_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501

        from agentfx.agents.fleet.log_buffer import CapturedLog, LogBuffer
        from datetime import UTC, datetime

        buf = LogBuffer()
        for i in range(15):
            buf.append(
                CapturedLog(
                    timestamp=datetime.now(UTC),
                    level="WARNING",
                    logger_name="l",
                    message=f"m-{i}",
                )
            )
        with patch(
            "agentfx.agents.fleet.quant_analyst.get_global_buffer",
            return_value=buf,
        ):
            with patch(
                "agentfx.agents.fleet.quant_analyst._write_tech_report",
                side_effect=RuntimeError("something broke"),
            ):
                # Must not raise — orchestrator loop depends on this.
                produced = qa.scan_for_technical_anomalies(db=db)
        assert produced is False


class TestMarkdownRender:
    def test_renders_sections_present_in_report(self):
        report = qa.QADivergenceReport(
            headline="Costs are too low",
            summary="Backtest spread 0.3 pips; realised 1.5 pips.",
            classifications=[
                qa.DivergenceClassification(
                    category="cost_model",
                    confidence=0.9,
                    description="Spread 5x expected.",
                )
            ],
            work_orders=[
                qa.QAWorkOrder(
                    title="Recalibrate EUR_USD typical_spread_pips",
                    target_file="instruments table",
                    spec="Update spread to 1.5 pips.",
                    expected_profit_impact="Removes 1% drag per trade.",
                )
            ],
        )
        md = qa._render_markdown(report, "run_x", date(2026, 10, 5))
        assert "QA divergence report" in md
        assert "## Classifications" in md
        assert "## Work orders" in md
        assert "Spread 5x expected" in md
        assert "Recalibrate EUR_USD" in md

    def test_omits_empty_sections(self):
        report = qa.QADivergenceReport(
            headline="No divergence",
            summary="Live matches backtest within statistical bounds.",
            classifications=[],
            work_orders=[],
        )
        md = qa._render_markdown(report, "run_x", date(2026, 10, 5))
        assert "## Classifications" not in md
        assert "## Work orders" not in md
