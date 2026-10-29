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


class TestLinearFiling:
    """QA wire to AFX-11's linear_client — files each work order as a
    Triage finding when the flag is on. Email path keeps running
    regardless."""

    def test_zero_work_orders_is_a_noop(self):
        from unittest.mock import patch
        from agentfx.agents.fleet.quant_analyst import (
            QADivergenceReport,
            _file_work_orders_to_linear,
        )
        from datetime import date

        report = QADivergenceReport(headline="ok", summary="", work_orders=[])
        with patch("agentfx.integrations.linear_client.file_finding") as file_finding:
            _file_work_orders_to_linear(
                report=report, run_id="run_x", today=date(2026, 10, 26)
            )
        file_finding.assert_not_called()

    def test_each_work_order_gets_filed(self):
        from unittest.mock import MagicMock, patch
        from agentfx.agents.fleet.quant_analyst import (
            QADivergenceReport,
            QAWorkOrder,
            _file_work_orders_to_linear,
        )
        from agentfx.integrations.linear_client import FilingResult
        from datetime import date

        report = QADivergenceReport(
            headline="dup orders",
            summary="",
            work_orders=[
                QAWorkOrder(
                    title="Dedupe order submits",
                    target_file="src/execution/order_manager.py",
                    spec="Multiple identical orders fired in a 50ms window.",
                    expected_profit_impact="Avoid ~$1k/wk loss",
                ),
                QAWorkOrder(
                    title="Fix close-reason mapping",
                    target_file="src/agentfx/services/paper_runner.py",
                    spec="Stops record as 'tp' not 'sl'.",
                    expected_profit_impact="Correct reporting; no direct $.",
                ),
            ],
        )
        file_finding_mock = MagicMock(
            return_value=FilingResult(
                filed=True, identifier="AFX-100", action="created"
            )
        )
        with patch(
            "agentfx.integrations.linear_client.file_finding",
            file_finding_mock,
        ):
            _file_work_orders_to_linear(
                report=report, run_id="run_abc", today=date(2026, 10, 26)
            )
        assert file_finding_mock.call_count == 2
        # Each FindingSpec goes in positionally.
        call_specs = [c.args[0] for c in file_finding_mock.call_args_list]
        titles = {s.title for s in call_specs}
        assert titles == {"Dedupe order submits", "Fix close-reason mapping"}
        for s in call_specs:
            assert s.source_label == "QA agent"
            assert s.type_label == "Bug"
            assert s.fingerprint.startswith("qa:")
            assert "src/execution" in s.body or "src/agentfx/services" in s.body
            assert "run_abc" in s.body

    def test_filing_exception_does_not_raise(self):
        from unittest.mock import patch
        from agentfx.agents.fleet.quant_analyst import (
            QADivergenceReport,
            QAWorkOrder,
            _file_work_orders_to_linear,
        )
        from datetime import date

        report = QADivergenceReport(
            headline="x",
            summary="",
            work_orders=[QAWorkOrder(title="t", target_file="x", spec="s")],
        )
        with patch(
            "agentfx.integrations.linear_client.file_finding",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise.
            _file_work_orders_to_linear(
                report=report, run_id="r", today=date(2026, 10, 26)
            )

    def test_fingerprint_stable_across_varying_titles_and_file_paths(self):
        """Regression for AFX-42/44/46 (2026-10-29): the same bug was
        filed three times because Sonnet 5 named the file differently
        on each run and the fingerprint was keyed on
        (title, target_file). Keying on (category, area) means two
        work orders about the same conceptual issue map to one
        Triage issue even when Sonnet 5 varies surface wording."""
        from agentfx.agents.fleet.quant_analyst import (
            _area_label_for,
            _work_order_fingerprint,
        )

        day1_area = _area_label_for("src/agentfx/services/paper_runner.py")
        day2_area = _area_label_for("agentfx/execution/paper_broker.py")
        day3_area = _area_label_for("runner/paper_trading_loop.py")
        # Different filenames (Sonnet 5 hallucinates nonexistent paths),
        # all heuristically map to the same area — "Paper runner".
        assert day1_area == day2_area == day3_area == "Paper runner"

        fp1 = _work_order_fingerprint("idempotency", day1_area)
        fp2 = _work_order_fingerprint("idempotency", day2_area)
        fp3 = _work_order_fingerprint("idempotency", day3_area)
        assert fp1 == fp2 == fp3 == "qa:idempotency:paper_runner"

    def test_fingerprint_differs_by_category_in_same_area(self):
        """A cost_model bug and an execution_timing bug in the same
        area are DIFFERENT issues and must file separately."""
        from agentfx.agents.fleet.quant_analyst import _work_order_fingerprint

        assert _work_order_fingerprint(
            "cost_model", "Paper runner"
        ) != _work_order_fingerprint("execution_timing", "Paper runner")

    def test_fingerprint_tolerates_missing_category(self):
        """A pre-category Sonnet 5 response still gets a fingerprint
        (default 'other') so dedupe doesn't crash."""
        from agentfx.agents.fleet.quant_analyst import _work_order_fingerprint

        assert _work_order_fingerprint("", "Paper runner") == "qa:other:paper_runner"
        assert _work_order_fingerprint("other", "") == "qa:other:paper_runner"

    def test_each_work_order_filed_uses_category_fingerprint(self):
        """End-to-end: a QAWorkOrder with an explicit category produces
        a fingerprint that reflects it."""
        from datetime import date
        from unittest.mock import MagicMock, patch
        from agentfx.agents.fleet.quant_analyst import (
            QADivergenceReport,
            QAWorkOrder,
            _file_work_orders_to_linear,
        )
        from agentfx.integrations.linear_client import FilingResult

        report = QADivergenceReport(
            headline="dup orders",
            summary="",
            work_orders=[
                QAWorkOrder(
                    title="Fix idempotency on broker submit",
                    category="idempotency",
                    target_file="src/agentfx/services/paper_runner.py",
                    spec="Multiple identical orders in a 50ms window.",
                    expected_profit_impact="Avoid duplicate-fill losses.",
                ),
            ],
        )
        file_finding_mock = MagicMock(
            return_value=FilingResult(
                filed=True, identifier="AFX-200", action="created"
            )
        )
        with patch(
            "agentfx.integrations.linear_client.file_finding", file_finding_mock
        ):
            _file_work_orders_to_linear(
                report=report, run_id="run_x", today=date(2026, 10, 29)
            )
        spec = file_finding_mock.call_args_list[0].args[0]
        assert spec.fingerprint == "qa:idempotency:paper_runner"

    def test_area_label_heuristic(self):
        from agentfx.agents.fleet.quant_analyst import _area_label_for

        assert _area_label_for("src/agentfx/services/paper_runner.py") == "Paper runner"
        assert _area_label_for("src/agentfx/services/backtest.py") == "Backtest"
        assert _area_label_for("src/agentfx/adapters/oanda.py") == "OANDA/data"
        assert _area_label_for("src/agentfx/agents/validator.py") == "Validator"
        assert _area_label_for("src/agentfx/core/dsl/compiler.py") == "Evolution/DSL"
        assert _area_label_for("portal/web/src/Dashboard.tsx") == "Portal"
        assert _area_label_for("src/agentfx/notifications/email.py") == "Notifications"
        assert _area_label_for(".github/workflows/ci.yml") == "Infra/CI"
        assert (
            _area_label_for("src/agentfx/agents/fleet/portfolio_manager.py")
            == "Agent fleet"
        )
        # Default fallback
        assert _area_label_for("") == "Paper runner"
        assert _area_label_for("random/path.py") == "Paper runner"
