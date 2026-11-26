"""Unit tests for the Quant Analyst Agent (v0.8.0 Phase B).

Tests focus on:
- idempotency (`decision_key` per run per day)
- activity threshold (≥N trades OR terminal stop_reason)
- never-raises contract (orchestrator scan loop depends on this)
- fallback report shape when LLM is skipped
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
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


def _pool_anomaly() -> qa.TechnicalAnomaly:
    return qa.TechnicalAnomaly(
        category="oanda_pool",
        severity="degraded",
        headline="1 OANDA account IN_USE past lease expiry",
        occurrences=1,
        sample_log="account_id=STUCK run_id=crashed_run",
        recommended_action="investigate",
    )


class TestTechHealthPoolFlags:
    """AFX-8 — OANDA pool-health flags ride along on the tech-health
    report and its email, even when the log window is quiet."""

    def _quiet_buffer(self):
        from agentfx.agents.fleet.log_buffer import LogBuffer

        return LogBuffer()

    def test_pool_flags_fire_report_even_when_logs_quiet(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501
        pool = [_pool_anomaly()]
        with (
            patch.object(qa, "get_global_buffer", return_value=self._quiet_buffer()),
            patch.object(qa, "_pool_health_anomalies", return_value=pool),
            patch.object(qa, "_write_tech_report") as writer,
        ):
            produced = qa.scan_for_technical_anomalies(db=db)
        assert produced is True
        writer.assert_called_once()
        assert writer.call_args.kwargs["pool_anomalies"] == pool

    def test_pool_anomalies_built_from_account_pool_flags(self):
        from agentfx.agents.fleet.account_pool import PoolHealthFlag

        flags = [
            PoolHealthFlag(
                kind="in_use_past_lease",
                headline="1 OANDA account IN_USE past lease expiry",
                account_ids=["STUCK"],
                detail="account_id=STUCK run_id=r lease_expires_at=x",
            ),
            PoolHealthFlag(
                kind="cooling_down_backlog",
                headline="4 OANDA accounts COOLING_DOWN (threshold 3)",
                account_ids=["C0", "C1", "C2", "C3"],
                detail="C0, C1, C2, C3",
            ),
        ]
        with (
            patch.object(qa.settings, "AGENT_QA_POOL_COOLING_DOWN_THRESHOLD", 3),
            patch(
                "agentfx.agents.fleet.account_pool.pool_health_flags",
                return_value=flags,
            ) as checker,
        ):
            anomalies = qa._pool_health_anomalies(MagicMock(), now=datetime.now())
        assert checker.call_args.kwargs["cooling_down_threshold"] == 3
        assert [a.category for a in anomalies] == ["oanda_pool", "oanda_pool"]
        assert anomalies[0].occurrences == 1
        assert anomalies[1].occurrences == 4
        assert "IN_USE past lease" in anomalies[0].headline

    def test_pool_anomaly_check_never_raises(self):
        with patch(
            "agentfx.agents.fleet.account_pool.pool_health_flags",
            side_effect=RuntimeError("db down"),
        ):
            assert qa._pool_health_anomalies(MagicMock(), now=datetime.now()) == []

    def test_pool_flags_only_skip_llm_and_reach_email(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        snapshot = {"window_hours": 24, "record_count": 0, "records": []}
        with (
            patch.object(qa, "_call_sonnet_tech") as llm,
            patch.object(qa, "record_decision"),
            patch.object(qa, "_email_tech_report") as emailer,
        ):
            qa._write_tech_report(
                db=MagicMock(),
                today=date(2026, 11, 11),
                snapshot=snapshot,
                pool_anomalies=[_pool_anomaly()],
            )
        llm.assert_not_called()
        emailer.assert_called_once()
        report = emailer.call_args.kwargs["report"]
        body = emailer.call_args.kwargs["body"]
        assert [a.category for a in report.anomalies] == ["oanda_pool"]
        assert "OANDA" in report.headline
        assert "IN_USE past lease expiry" in body

    def test_pool_flags_merged_with_llm_anomalies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        from agentfx.agents.fleet.llm import LLMCallResult

        llm_report = qa.TechnicalHealthReport(
            headline="No anomalies in window", summary="Quiet.", anomalies=[]
        )
        llm_result = MagicMock(spec=LLMCallResult)
        llm_result.output = llm_report
        llm_result.model = "m"
        llm_result.reasoning = "r"
        llm_result.usd_cost = 0.0
        llm_result.input_tokens = llm_result.output_tokens = 0
        llm_result.cache_read_tokens = 0
        snapshot = {"window_hours": 24, "record_count": 15, "records": []}
        with (
            patch.object(qa, "_call_sonnet_tech", return_value=llm_result) as llm,
            patch.object(qa, "record_decision"),
            patch.object(qa, "_email_tech_report") as emailer,
        ):
            qa._write_tech_report(
                db=MagicMock(),
                today=date(2026, 11, 11),
                snapshot=snapshot,
                pool_anomalies=[_pool_anomaly()],
            )
        llm.assert_called_once()
        emailer.assert_called_once()
        report = emailer.call_args.kwargs["report"]
        assert [a.category for a in report.anomalies] == ["oanda_pool"]
        assert "OANDA" in report.headline

    def test_pool_flag_rendered_in_email_html(self):
        from agentfx.notifications.email import render_qa_tech_health_html

        report = qa.TechnicalHealthReport(
            headline="h", summary="s", anomalies=[_pool_anomaly()]
        )
        html = render_qa_tech_health_html(report, "body")
        assert "oanda_pool" in html
        assert "IN_USE past lease expiry" in html


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


class TestMeaningfulActivity:
    """`_run_has_meaningful_activity` controls what QA looks at.
    Four qualifying paths: terminal stop_reason, ≥N closed trades,
    zero-trade silence past granularity threshold (added 2026-10-29),
    or 0 trades below threshold → skip."""

    def _mk_run(
        self, *, status, created_at, granularity="M30", stop_reason=None, run_id="r1"
    ):
        from agentfx.api.models.run import RunStatus

        run = MagicMock()
        run.id = run_id
        run.status = status if isinstance(status, RunStatus) else RunStatus(status)
        run.created_at = created_at
        run.granularity = granularity
        run.stop_reason = stop_reason
        return run

    def _mk_db_with_closed_count(self, count: int):
        """Mock the SQLAlchemy chain that counts closed trades."""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.count.return_value = count
        return db

    def test_terminal_stop_reason_qualifies(self):
        from datetime import UTC, datetime
        from agentfx.api.models.run import RunStatus

        run = self._mk_run(
            status=RunStatus.HALTED_MANUAL,
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            stop_reason="USER_KILL",
        )
        db = self._mk_db_with_closed_count(0)
        now = datetime(2026, 10, 29, tzinfo=UTC)
        assert qa._run_has_meaningful_activity(db, run=run, now=now) is True

    def test_three_or_more_closed_trades_qualifies(self):
        from datetime import UTC, datetime
        from agentfx.api.models.run import RunStatus

        run = self._mk_run(
            status=RunStatus.RUNNING, created_at=datetime(2026, 10, 29, tzinfo=UTC)
        )
        db = self._mk_db_with_closed_count(qa._MIN_TRADES_FOR_ANALYSIS)
        now = datetime(2026, 10, 29, 1, tzinfo=UTC)
        assert qa._run_has_meaningful_activity(db, run=run, now=now) is True

    def test_zero_trades_m30_past_24h_qualifies(self):
        """2026-10-29 incident path: AUD_USD/M30 running 2 days with
        0 trades. The silence IS the finding."""
        from datetime import UTC, datetime
        from agentfx.api.models.run import RunStatus

        run = self._mk_run(
            status=RunStatus.RUNNING,
            created_at=datetime(2026, 10, 27, 10, tzinfo=UTC),
            granularity="M30",
        )
        db = self._mk_db_with_closed_count(0)
        # 48 hours later — well past the 24h M30 threshold.
        now = datetime(2026, 10, 29, 10, tzinfo=UTC)
        assert qa._run_has_meaningful_activity(db, run=run, now=now) is True

    def test_zero_trades_m30_within_24h_skipped(self):
        from datetime import UTC, datetime
        from agentfx.api.models.run import RunStatus

        run = self._mk_run(
            status=RunStatus.RUNNING,
            created_at=datetime(2026, 10, 29, tzinfo=UTC),
            granularity="M30",
        )
        db = self._mk_db_with_closed_count(0)
        # 12 hours in — not yet suspicious.
        now = datetime(2026, 10, 29, 12, tzinfo=UTC)
        assert qa._run_has_meaningful_activity(db, run=run, now=now) is False

    def test_zero_trades_h4_requires_7_days(self):
        """Lower-frequency timeframes get longer silence windows."""
        from datetime import UTC, datetime, timedelta
        from agentfx.api.models.run import RunStatus

        created_at = datetime(2026, 10, 20, tzinfo=UTC)
        run = self._mk_run(
            status=RunStatus.RUNNING, created_at=created_at, granularity="H4"
        )
        db = self._mk_db_with_closed_count(0)
        # 3 days in — H4's silence threshold is 7 days, not yet triggered.
        assert (
            qa._run_has_meaningful_activity(
                db, run=run, now=created_at + timedelta(days=3)
            )
            is False
        )
        # 8 days in — now qualifies.
        assert (
            qa._run_has_meaningful_activity(
                db, run=run, now=created_at + timedelta(days=8)
            )
            is True
        )

    def test_unknown_granularity_uses_default(self):
        from datetime import UTC, datetime, timedelta
        from agentfx.api.models.run import RunStatus

        created_at = datetime(2026, 10, 20, tzinfo=UTC)
        run = self._mk_run(
            status=RunStatus.RUNNING,
            created_at=created_at,
            granularity="SOMETHING_WEIRD",
        )
        db = self._mk_db_with_closed_count(0)
        # Default is 48h — 1 day isn't enough.
        assert (
            qa._run_has_meaningful_activity(
                db, run=run, now=created_at + timedelta(hours=24)
            )
            is False
        )
        # 72h — now qualifies against the 48h default.
        assert (
            qa._run_has_meaningful_activity(
                db, run=run, now=created_at + timedelta(hours=72)
            )
            is True
        )

    def test_zero_trades_naive_created_at_normalised_to_utc(self):
        """Postgres may hand back a tz-naive datetime depending on
        dialect; the gate must still work without crashing."""
        from datetime import UTC, datetime
        from agentfx.api.models.run import RunStatus

        run = self._mk_run(
            status=RunStatus.RUNNING,
            # TZ-naive on purpose.
            created_at=datetime(2026, 10, 27, 10),
            granularity="M30",
        )
        db = self._mk_db_with_closed_count(0)
        now = datetime(2026, 10, 29, 10, tzinfo=UTC)
        assert qa._run_has_meaningful_activity(db, run=run, now=now) is True

    def test_silence_table_has_all_configured_granularities(self):
        """Sanity: the operator table must at least cover the pairs
        we actually ship."""
        for g in ("M1", "M5", "M30", "H1", "H4", "D"):
            assert g in qa._SILENCE_HOURS_BY_GRANULARITY


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
            assert "run_abc" in s.body
        assert "src/execution/order_manager.py" not in call_specs[0].body
        assert "Area: Paper runner. File was not identified." in call_specs[0].body
        assert (
            "Target file: `src/agentfx/services/paper_runner.py`" in call_specs[1].body
        )

    @staticmethod
    def _file_one(**fields):
        from agentfx.integrations.linear_client import FilingResult

        report = qa.QADivergenceReport(work_orders=[qa.QAWorkOrder(**fields)])
        with patch(
            "agentfx.integrations.linear_client.file_finding",
            return_value=FilingResult(filed=True, action="created"),
        ) as file_finding:
            qa._file_work_orders_to_linear(
                report=report, run_id="run_example", today=date(2026, 10, 26)
            )
        return file_finding.call_args.args[0]

    def test_filing_follows_the_template_in_order(self):
        spec = self._file_one(
            observation="The paper account placed two orders for one signal.",
            evidence="Trades 8 and 9 opened 50 ms apart at 03:40 UTC.",
            expected_profit_impact="Duplicate orders increase exposure.",
            spec="Trades 8 and 9 motivate limiting orders per signal.",
            proposed_fix="Submit at most one order per signal.",
            confidence="high",
            done_when=["One signal cannot create a second order."],
        )
        template = (
            Path(__file__).resolve().parents[4] / "config/linear/templates/finding.md"
        ).read_text(encoding="utf-8")
        headings = r"^#{1,6} .+$"
        assert re.findall(headings, spec.body, flags=re.MULTILINE) == re.findall(
            headings, template, flags=re.MULTILINE
        )
        assert "**Confidence:** High" in spec.body
        assert "## Why\n\nDuplicate orders increase exposure." in spec.body
        assert (
            "## What\n\n"
            "**Observed:** The paper account placed two orders for one signal.\n\n"
            "**Proposed fix:** Submit at most one order per signal.\n\n"
            "Area: Paper runner. File was not identified."
        ) in spec.body
        assert "Trades 8 and 9 motivate" not in spec.body
        assert "## Evidence\n\n" + spec.evidence in spec.body
        assert spec.evidence == (
            "Paper run: `run_example` (2026-10-26)\n\n"
            "Trades 8 and 9 opened 50 ms apart at 03:40 UTC."
        )
        assert "- [ ] One signal cannot create a second order." in spec.body
        assert "- [ ] A test reproduces the problem." in spec.body
        assert "- [ ] `make lint` green." in spec.body
        assert "- [ ] `make test-unit` green." in spec.body

    @pytest.mark.parametrize("full_path", [True, False])
    def test_guessed_target_is_not_echoed_in_filing_prose(self, full_path):
        guess = "src/execution/stop_engine.py"
        reference = guess if full_path else "stop_engine.py"
        spec = self._file_one(
            target_file=guess,
            title=f"Fix {reference}",
            proposed_fix=f"Change {reference}.",
        )
        assert reference not in spec.title
        assert reference not in spec.body
        assert "File was not identified." in spec.body

    @pytest.mark.parametrize(
        ("category", "risk"),
        [
            ("execution_timing", "Money-path"),
            ("idempotency", "Money-path"),
            ("cost_model", "Normal"),
            ("other", "Normal"),
        ],
    )
    def test_analyst_bug_has_risk_and_found_in_labels(self, category, risk):
        spec = self._file_one(category=category)
        assert spec.type_label == "Bug"
        assert spec.risk_label == risk
        assert spec.extra_labels == ["Paper trading"]

    def test_missing_ticket_fields_are_not_stated(self):
        spec = self._file_one()
        assert "**Confidence:** (not stated)" in spec.body
        assert "## Why\n\n(not stated)" in spec.body
        assert (
            "## What\n\n"
            "**Observed:** (not stated)\n\n"
            "**Proposed fix:** (not stated)\n\n"
            "Area: Paper runner. File was not identified."
        ) in spec.body
        assert spec.evidence.endswith("\n\n(not stated)")
        assert "## Done when\n\n(not stated)" in spec.body

    def test_every_filled_section_is_a_template_heading(self):
        """A section keyed by a heading the template no longer has is
        dropped without error, leaving `(not stated)` (AFX-160)."""
        template = (
            Path(__file__).resolve().parents[4] / "config/linear/templates/finding.md"
        ).read_text(encoding="utf-8")
        spec = self._file_one(
            observation="OBSERVED-MARKER",
            evidence="EVIDENCE-MARKER",
            expected_profit_impact="IMPACT-MARKER",
            proposed_fix="FIX-MARKER",
            done_when=["DONE-MARKER"],
        )
        for marker in ("OBSERVED", "EVIDENCE", "IMPACT", "FIX", "DONE"):
            assert f"{marker}-MARKER" in spec.body
        for heading in ("Why", "What", "Evidence", "Done when"):
            assert f"## {heading}\n" in template

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


class TestDivergenceReportTicketWiring:
    """AFX-149: divergence reports file BEFORE rendering/emailing so
    HTML, plain text, and saved markdown all show every work order's
    Linear ticket or skip reason, and survive a Linear outage."""

    def _run_divergence(
        self,
        tmp_path,
        monkeypatch,
        *,
        work_orders,
        filing_return=None,
        filing_side_effect=None,
    ):
        from agentfx.agents.fleet.llm import LLMCallResult

        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        report = qa.QADivergenceReport(
            headline="Spread mismatch",
            summary="Realised spread wider than backtest.",
            classifications=[],
            work_orders=work_orders,
        )
        llm_result = MagicMock(spec=LLMCallResult)
        llm_result.output = report
        llm_result.model = "claude-sonnet"
        llm_result.reasoning = "ok"
        llm_result.usd_cost = 0.01
        llm_result.input_tokens = llm_result.output_tokens = 100
        llm_result.cache_read_tokens = 0

        db = MagicMock()
        file_kwargs = (
            {"side_effect": filing_side_effect}
            if filing_side_effect is not None
            else {"return_value": filing_return}
        )
        with (
            patch.object(qa, "_snapshot_run", return_value={"run_id": "run_1"}),
            patch.object(qa, "_call_sonnet", return_value=llm_result),
            patch.object(qa, "record_decision") as rec_dec,
            patch("agentfx.integrations.linear_client.file_finding", **file_kwargs),
            patch("agentfx.notifications.email.send_agent_message") as send_msg,
        ):
            qa._write_divergence_report(db=db, run_id="run_1", today=date(2026, 11, 25))

        saved_md = (tmp_path / "paper_divergence" / "run_1_2026-11-25.md").read_text()
        return rec_dec, db, send_msg, saved_md

    def test_new_ticket_appears_in_email_html_plaintext_and_saved_file(
        self, tmp_path, monkeypatch
    ):
        from agentfx.integrations.linear_client import FilingResult

        wo = qa.QAWorkOrder(
            title="Tighten M1 spread",
            category="cost_model",
            target_file="src/agentfx/services/paper_runner.py",
            spec="Adjust spread model.",
        )
        _, _, send_msg, saved_md = self._run_divergence(
            tmp_path,
            monkeypatch,
            work_orders=[wo],
            filing_return=FilingResult(
                filed=True,
                identifier="AFX-150",
                url="https://linear.app/agentfx/issue/AFX-150",
                action="created",
            ),
        )
        send_msg.assert_called_once()
        body = send_msg.call_args.kwargs["body"]
        html = send_msg.call_args.kwargs["html_body"]
        assert "New: [AFX-150](https://linear.app/agentfx/issue/AFX-150)" in body
        assert "New: [AFX-150](https://linear.app/agentfx/issue/AFX-150)" in saved_md
        assert "New:" in html
        assert 'href="https://linear.app/agentfx/issue/AFX-150"' in html

    def test_known_issue_and_reopened_appear_with_status_and_link(
        self, tmp_path, monkeypatch
    ):
        from agentfx.integrations.linear_client import FilingResult

        wos = [
            qa.QAWorkOrder(title="WO 1", category="cost_model", spec="s1"),
            qa.QAWorkOrder(title="WO 2", category="idempotency", spec="s2"),
        ]
        _, _, send_msg, saved_md = self._run_divergence(
            tmp_path,
            monkeypatch,
            work_orders=wos,
            filing_side_effect=[
                FilingResult(
                    filed=True,
                    identifier="AFX-51",
                    url="https://linear.app/agentfx/issue/AFX-51",
                    action="commented",
                    state_name="In Progress",
                ),
                FilingResult(
                    filed=True,
                    identifier="AFX-52",
                    url="https://linear.app/agentfx/issue/AFX-52",
                    action="reopened",
                    state_name="Done",
                ),
            ],
        )
        body = send_msg.call_args.kwargs["body"]
        html = send_msg.call_args.kwargs["html_body"]
        assert (
            "Known issue: [AFX-51](https://linear.app/agentfx/issue/AFX-51) "
            "(In Progress), evidence added" in body
        )
        assert (
            "Reopened: [AFX-52](https://linear.app/agentfx/issue/AFX-52) "
            "(was Done), evidence added" in body
        )
        assert "Known issue:" in html and "(In Progress), evidence added" in html
        assert "Reopened:" in html and "(was Done), evidence added" in html
        assert "AFX-51" in saved_md and "AFX-52" in saved_md

    @pytest.mark.parametrize(
        "action,expected",
        [
            ("skipped:flag_off", "Not filed: Linear filing is switched off"),
            ("skipped:cap_hit", "Not filed: daily ticket limit reached"),
            ("skipped:create_failed", "Not filed: Linear error"),
        ],
    )
    def test_not_filed_reasons_shown_and_email_still_sends(
        self, tmp_path, monkeypatch, action, expected
    ):
        from agentfx.integrations.linear_client import FilingResult

        wo = qa.QAWorkOrder(title="WO", category="cost_model", spec="s")
        _, _, send_msg, saved_md = self._run_divergence(
            tmp_path,
            monkeypatch,
            work_orders=[wo],
            filing_return=FilingResult(filed=False, action=action),
        )
        send_msg.assert_called_once()
        assert expected in send_msg.call_args.kwargs["body"]
        assert expected in send_msg.call_args.kwargs["html_body"]
        assert expected in saved_md

    def test_linear_outage_still_sends_email_and_writes_decision_log(
        self, tmp_path, monkeypatch
    ):
        wo = qa.QAWorkOrder(title="WO", category="cost_model", spec="s")
        rec_dec, db, send_msg, saved_md = self._run_divergence(
            tmp_path,
            monkeypatch,
            work_orders=[wo],
            filing_side_effect=RuntimeError("Linear 503"),
        )
        rec_dec.assert_called_once()
        db.commit.assert_called_once()
        send_msg.assert_called_once()
        assert "Not filed: Linear error" in send_msg.call_args.kwargs["body"]
        assert "Not filed: Linear error" in send_msg.call_args.kwargs["html_body"]
        assert "Not filed: Linear error" in saved_md


class TestTechHealthLinearFiling:
    """AFX-149: technical-health anomalies file to Linear Triage, skip
    benign items, group cause+symptom, dedupe across days with varying
    wording, and survive Linear outages."""

    def _snapshot(self):
        return {
            "window_hours": 24,
            "window_end_utc": "2026-11-25T12:00:00+00:00",
            "record_count": 20,
            "records": [],
        }

    def _run_tech(
        self,
        tmp_path,
        monkeypatch,
        *,
        anomalies,
        today=date(2026, 11, 25),
        filing_return=None,
        filing_side_effect=None,
    ):
        from agentfx.agents.fleet.llm import LLMCallResult

        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        report = qa.TechnicalHealthReport(
            headline="Staging anomalies",
            summary="Found issues in staging logs.",
            anomalies=anomalies,
        )
        llm_result = MagicMock(spec=LLMCallResult)
        llm_result.output = report
        llm_result.model = "claude-sonnet"
        llm_result.reasoning = "ok"
        llm_result.usd_cost = 0.01
        llm_result.input_tokens = llm_result.output_tokens = 100
        llm_result.cache_read_tokens = 0

        db = MagicMock()
        file_kwargs = (
            {"side_effect": filing_side_effect}
            if filing_side_effect is not None
            else {"return_value": filing_return}
        )
        with (
            patch.object(qa, "_call_sonnet_tech", return_value=llm_result),
            patch.object(qa, "record_decision") as rec_dec,
            patch(
                "agentfx.integrations.linear_client.file_finding", **file_kwargs
            ) as file_mock,
            patch("agentfx.notifications.email.send_agent_message") as send_msg,
        ):
            qa._write_tech_report(db=db, today=today, snapshot=self._snapshot())
        saved_md = (tmp_path / "tech_health" / f"{today.isoformat()}.md").read_text()
        return rec_dec, db, file_mock, send_msg, saved_md

    def test_degraded_and_critical_anomalies_file_with_template_and_labels(
        self, tmp_path, monkeypatch
    ):
        from agentfx.integrations.linear_client import FilingResult

        anomalies = [
            qa.TechnicalAnomaly(
                category="external_api",
                severity="degraded",
                headline="50 Devin polling failures (HTTP 403)",
                occurrences=50,
                first_seen="2026-11-25T01:00:00Z",
                last_seen="2026-11-25T11:30:00Z",
                component="agentfx.agents.fleet.pe_workspace",
                why_it_matters="PE work orders stall for hours.",
                sample_log="pe_workspace: poll failed HTTP 403",
                recommended_action="file PE work order to fix auth header",
            ),
            qa.TechnicalAnomaly(
                category="resource",
                severity="critical",
                headline="Database connection pool exhausted",
                occurrences=12,
                first_seen="2026-11-25T08:00:00Z",
                last_seen="2026-11-25T09:00:00Z",
                component="agentfx.services.paper_runner",
                why_it_matters="Paper runs stop recording fills.",
                sample_log="paper_runner: QueuePool limit reached",
                recommended_action="increase pool size and audit leaked sessions",
            ),
        ]
        _, _, file_mock, send_msg, saved_md = self._run_tech(
            tmp_path,
            monkeypatch,
            anomalies=anomalies,
            filing_side_effect=[
                FilingResult(
                    filed=True,
                    identifier="AFX-151",
                    url="https://linear.app/agentfx/issue/AFX-151",
                    action="created",
                ),
                FilingResult(
                    filed=True,
                    identifier="AFX-152",
                    url="https://linear.app/agentfx/issue/AFX-152",
                    action="created",
                ),
            ],
        )
        assert file_mock.call_count == 2
        spec0, spec1 = [c.args[0] for c in file_mock.call_args_list]

        # Degraded -> Bug, default Agent fleet area, Staging label
        assert spec0.type_label == "Bug"
        assert spec0.source_label == "QA agent"
        assert spec0.area_label == "Agent fleet"
        assert "Staging" in spec0.extra_labels
        assert spec0.fingerprint == "qa:tech:external_api:pe_workspace"
        for required_fragment in (
            "## What I observed",
            "50 Devin polling failures (HTTP 403)",
            "## Evidence",
            "50",
            "2026-11-25T01:00:00Z",
            "2026-11-25T11:30:00Z",
            "last 24h",
            "pe_workspace",
            "pe_workspace: poll failed HTTP 403",
            "## Why it matters",
            "PE work orders stall for hours.",
            "## Proposed fix",
            "file PE work order to fix auth header",
            "## Done when",
            "The pattern no longer appears in the next technical-health report",
        ):
            assert required_fragment in spec0.body

        # Critical -> Incident, Paper runner area
        assert spec1.type_label == "Incident"
        assert spec1.area_label == "Paper runner"
        assert "Staging" in spec1.extra_labels

        # Email and saved markdown carry the ticket links
        body = send_msg.call_args.kwargs["body"]
        html = send_msg.call_args.kwargs["html_body"]
        assert "New: [AFX-151](https://linear.app/agentfx/issue/AFX-151)" in body
        assert "New: [AFX-152](https://linear.app/agentfx/issue/AFX-152)" in saved_md
        assert 'href="https://linear.app/agentfx/issue/AFX-151"' in html
        assert 'href="https://linear.app/agentfx/issue/AFX-152"' in html

    def test_benign_noted_and_noise_anomalies_are_not_filed(
        self, tmp_path, monkeypatch
    ):
        anomalies = [
            qa.TechnicalAnomaly(
                category="logic_warning",
                severity="noted",
                headline="Routine candle-gap healing ran 3 times",
                occurrences=3,
                sample_log="candle_ingestion: healed 2-bar gap",
                recommended_action="ignore (benign)",
            ),
            qa.TechnicalAnomaly(
                category="noise",
                severity="degraded",
                headline="Transient OANDA 429 burst recovered",
                occurrences=4,
                sample_log="oanda: 429 retry succeeded",
                recommended_action="ignore (benign)",
            ),
        ]
        _, _, file_mock, send_msg, saved_md = self._run_tech(
            tmp_path, monkeypatch, anomalies=anomalies
        )
        file_mock.assert_not_called()
        send_msg.assert_called_once()
        assert "No ticket needed (benign)" in send_msg.call_args.kwargs["body"]
        assert "No ticket needed (benign)" in send_msg.call_args.kwargs["html_body"]
        assert "No ticket needed (benign)" in saved_md

    def test_cause_and_symptom_linked_land_on_one_ticket(self, tmp_path, monkeypatch):
        from agentfx.integrations.linear_client import FilingResult

        anomalies = [
            qa.TechnicalAnomaly(
                category="external_api",
                severity="degraded",
                headline="50 Devin polling failures (HTTP 403)",
                occurrences=50,
                first_seen="2026-11-25T01:00:00Z",
                component="pe_workspace",
                sample_log="pe_workspace: HTTP 403",
                recommended_action="fix Devin token scope",
            ),
            qa.TechnicalAnomaly(
                category="logic_warning",
                severity="degraded",
                headline="Two stuck PE work orders",
                occurrences=2,
                first_seen="2026-11-25T02:00:00Z",
                component="platform_engineer",
                sample_log="platform_engineer: work order stuck >2h",
                recommended_action="resolve polling 403",
                caused_by_index=0,
            ),
        ]
        _, _, file_mock, send_msg, _ = self._run_tech(
            tmp_path,
            monkeypatch,
            anomalies=anomalies,
            filing_return=FilingResult(
                filed=True,
                identifier="AFX-155",
                url="https://linear.app/agentfx/issue/AFX-155",
                action="created",
            ),
        )
        # Only ONE ticket filed for both cause and symptom.
        assert file_mock.call_count == 1
        spec = file_mock.call_args.args[0]
        assert "Two stuck PE work orders" in spec.body
        # Both anomalies in the email show AFX-155.
        html = send_msg.call_args.kwargs["html_body"]
        assert html.count("AFX-155") >= 2

    def test_same_anomaly_on_two_consecutive_days_comments_on_first_ticket(
        self, tmp_path, monkeypatch
    ):
        """Day 1 and Day 2 have different headlines and logger path formats;
        they produce the same fingerprint so Day 2 comments on the first
        ticket instead of creating a duplicate."""
        from agentfx.integrations import linear_client

        day1 = qa.TechnicalAnomaly(
            category="external_api",
            severity="degraded",
            headline="50 Devin polling failures (HTTP 403)",
            occurrences=50,
            first_seen="2026-11-24T01:00:00Z",
            component="agentfx.agents.fleet.pe_workspace",
            sample_log="pe_workspace: HTTP 403",
        )
        day2 = qa.TechnicalAnomaly(
            category="external_api",
            severity="degraded",
            headline="Devin session status poll returned 403 Forbidden 38 times",
            occurrences=38,
            first_seen="2026-11-25T01:00:00Z",
            component="pe_workspace",
            sample_log="pe_workspace: 403 Forbidden",
        )
        assert qa._tech_anomaly_fingerprint(day1) == qa._tech_anomaly_fingerprint(day2)

        # Simulate stateful Linear store across the two consecutive days.
        monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_ENABLED", True)
        monkeypatch.setattr(linear_client.settings, "LINEAR_API_KEY", "lk_test")
        monkeypatch.setattr(linear_client.settings, "AGENT_LINEAR_TEAM_KEY", "AFX")
        stored_open: dict[str, dict] = {}
        created_count = 0
        comments: list[tuple[str, str]] = []

        def fake_search_open(_client, _team, fp):
            return stored_open.get(fp)

        def fake_create(_client, *, team_key, spec, now):
            nonlocal created_count
            created_count += 1
            issue = {
                "id": f"uuid-{created_count}",
                "identifier": f"AFX-{200 + created_count}",
                "url": f"https://linear.app/agentfx/issue/AFX-{200 + created_count}",
                "state": {"name": "Triage", "type": "triage"},
            }
            stored_open[spec.fingerprint] = issue
            return issue

        def fake_comment(_client, *, issue_id, body):
            comments.append((issue_id, body))
            return True

        with (
            patch.object(linear_client, "_build_client", return_value=MagicMock()),
            patch.object(
                linear_client,
                "_search_open_by_fingerprint",
                side_effect=fake_search_open,
            ),
            patch.object(
                linear_client, "_search_closed_by_fingerprint", return_value=[]
            ),
            patch.object(linear_client, "_count_files_today", return_value=0),
            patch.object(
                linear_client, "_create_triage_issue", side_effect=fake_create
            ),
            patch.object(linear_client, "_add_comment", side_effect=fake_comment),
        ):
            r1 = qa._file_tech_anomalies_to_linear(
                report=qa.TechnicalHealthReport(anomalies=[day1]),
                snapshot=self._snapshot(),
            )
            r2 = qa._file_tech_anomalies_to_linear(
                report=qa.TechnicalHealthReport(anomalies=[day2]),
                snapshot=self._snapshot(),
            )

        assert created_count == 1
        assert r1[0].action == "created"
        assert r1[0].identifier == "AFX-201"
        assert r2[0].action == "commented"
        assert r2[0].identifier == "AFX-201"
        assert r2[0].state_name == "Triage"
        assert len(comments) == 1
        assert comments[0][0] == "uuid-1"

    def test_linear_outage_on_tech_report_still_sends_email_and_writes_decision(
        self, tmp_path, monkeypatch
    ):
        anomalies = [
            qa.TechnicalAnomaly(
                category="exception",
                severity="critical",
                headline="Unhandled crash in worker",
                occurrences=5,
                component="paper_runner",
                sample_log="Traceback...",
            )
        ]
        rec_dec, db, _, send_msg, saved_md = self._run_tech(
            tmp_path,
            monkeypatch,
            anomalies=anomalies,
            filing_side_effect=RuntimeError("Linear down"),
        )
        rec_dec.assert_called_once()
        db.commit.assert_called_once()
        send_msg.assert_called_once()
        assert "Not filed: Linear error" in send_msg.call_args.kwargs["body"]
        assert "Not filed: Linear error" in send_msg.call_args.kwargs["html_body"]
        assert "Not filed: Linear error" in saved_md
