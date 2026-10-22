"""Unit tests for the Portfolio Manager Agent (v0.8.0 Phase A).

The PM agent is read-only in Phase A — tests focus on:
- idempotency (brief fires at most once per day)
- time-gating (don't fire before 09:00 UTC)
- never-raises contract (orchestrator depends on this)
- correct hand-off to memory + email layers
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentfx.agents.fleet.portfolio_manager import (
    _brief_key_for,
    run_morning_brief_if_due,
)

pytestmark = pytest.mark.unit


class TestTimeGate:
    """Before 09:00 UTC the agent must be inert — even if nothing
    has run today, we don't want pre-dawn briefs."""

    def test_before_target_hour_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        before = datetime(2026, 10, 5, 7, 30, tzinfo=UTC)

        with patch(
            "agentfx.agents.fleet.portfolio_manager._write_morning_brief"
        ) as write_brief:
            run_morning_brief_if_due(db=db, now=before)

        write_brief.assert_not_called()
        db.query.assert_not_called()

    def test_at_or_after_target_hour_proceeds_to_dedup_check(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        # No prior brief in DB.
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501
        at_target = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

        with patch(
            "agentfx.agents.fleet.portfolio_manager._write_morning_brief"
        ) as write_brief:
            run_morning_brief_if_due(db=db, now=at_target)

        write_brief.assert_called_once()


class TestIdempotency:
    """At most one PM morning brief per calendar day — the orchestrator
    wakes hourly so this is critical to avoid email spam."""

    def test_second_call_same_day_skips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        # Simulate a brief already present for today.
        existing_row = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = existing_row  # noqa: E501

        later = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
        with patch(
            "agentfx.agents.fleet.portfolio_manager._write_morning_brief"
        ) as write_brief:
            run_morning_brief_if_due(db=db, now=later)

        write_brief.assert_not_called()


class TestNeverRaises:
    """The orchestrator calls PM every hour; any uncaught exception
    would be logged but the loop keeps running. Nonetheless the
    agent itself must swallow its own errors defensively."""

    def test_duplicate_check_failure_does_not_raise(self):
        db = MagicMock()
        db.query.side_effect = RuntimeError("db session dead")

        # Must not raise.
        at_target = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
        run_morning_brief_if_due(db=db, now=at_target)

    def test_brief_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501
        at_target = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

        with patch(
            "agentfx.agents.fleet.portfolio_manager._write_morning_brief",
            side_effect=RuntimeError("snapshot crashed"),
        ):
            # Must not raise.
            run_morning_brief_if_due(db=db, now=at_target)


class TestBriefKey:
    """`decision_key` is the dedup field — it must be stable + per-day
    so idempotency works across process restarts."""

    def test_key_is_date_stamped(self):
        from datetime import date

        assert _brief_key_for(date(2026, 10, 5)) == "pm_morning_brief_2026-10-05"

    def test_different_days_different_keys(self):
        from datetime import date

        assert _brief_key_for(date(2026, 10, 5)) != _brief_key_for(date(2026, 10, 6))


class TestDeterministicFallback:
    """Phase-B PM fallback body must surface the needs-attention
    paper runs — the staging 2026-10-06 incident where PM reported
    "zero running" while QA correctly found stuck ERROR runs is
    exactly the state this branch exists to describe."""

    def test_fallback_mentions_needs_attention_count(self):
        from datetime import date
        from agentfx.agents.fleet.portfolio_manager import _deterministic_fallback

        state = {
            "paper_runs": {
                "running": 0,
                "effective_active": 2,
                "needs_attention_count": 2,
                "halted_in_last_7_days": 0,
                "status_counts_last_7_days": {"ERROR": 2},
            },
            "evolution_runs_last_7_days": {"completed": 0, "failed": 1},
        }
        brief = _deterministic_fallback(state, date(2026, 10, 6))
        assert "0 RUNNING" in brief.summary
        assert "2 needing attention" in brief.summary
        assert brief.recommendations == []
        assert brief.concerns == []

    def test_fallback_handles_zero_activity(self):
        from datetime import date
        from agentfx.agents.fleet.portfolio_manager import _deterministic_fallback

        state = {
            "paper_runs": {
                "running": 0,
                "effective_active": 0,
                "needs_attention_count": 0,
                "halted_in_last_7_days": 0,
                "status_counts_last_7_days": {},
            },
            "evolution_runs_last_7_days": {"completed": 0, "failed": 0},
        }
        brief = _deterministic_fallback(state, date(2026, 10, 6))
        assert brief.headline
        assert "2026-10-06" in brief.headline


class TestEvolutionActionExecution:
    """`_execute_evolution_actions` iterates the brief's actions and
    returns a per-action summary. Must never raise, even if the
    scheduler itself raises mid-iteration."""

    def test_iterates_actions_and_summarises(self):
        from unittest.mock import MagicMock, patch
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            _execute_evolution_actions,
        )
        from agentfx.agents.fleet.pm_scheduler import (
            PMEvolutionAction,
            ScheduledEvolutionResult,
        )

        brief = PMBrief(
            headline="x",
            summary="y",
            evolution_actions=[
                PMEvolutionAction(
                    pair="EUR_USD",
                    granularity="H1",
                    rationale="r1",
                    expected_profit_impact="i1",
                    priority="now",
                ),
                PMEvolutionAction(
                    pair="USD_JPY",
                    granularity="M1",
                    rationale="r2",
                    expected_profit_impact="i2",
                    priority="watch",
                ),
            ],
        )
        db = MagicMock()

        with patch(
            "agentfx.agents.fleet.portfolio_manager.schedule_evolution_from_pm_action",
            side_effect=[
                ScheduledEvolutionResult(
                    accepted=True, evo_run_id="evorun_a", reason=""
                ),
                ScheduledEvolutionResult(
                    accepted=False, evo_run_id=None, reason="wrong_priority:watch"
                ),
            ],
        ):
            results = _execute_evolution_actions(
                db=db, brief=brief, pm_decision_key="pm_test"
            )

        assert len(results) == 2
        assert results[0]["accepted"] is True
        assert results[0]["evo_run_id"] == "evorun_a"
        assert results[1]["accepted"] is False
        assert results[1]["reason"] == "wrong_priority:watch"

    def test_scheduler_exception_does_not_crash_iteration(self):
        from unittest.mock import MagicMock, patch
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            _execute_evolution_actions,
        )
        from agentfx.agents.fleet.pm_scheduler import PMEvolutionAction

        brief = PMBrief(
            headline="x",
            summary="y",
            evolution_actions=[
                PMEvolutionAction(
                    pair="EUR_USD",
                    granularity="H1",
                    rationale="r",
                    expected_profit_impact="i",
                    priority="now",
                )
            ],
        )
        db = MagicMock()
        with patch(
            "agentfx.agents.fleet.portfolio_manager.schedule_evolution_from_pm_action",
            side_effect=RuntimeError("completely broken"),
        ):
            # Must not raise.
            results = _execute_evolution_actions(
                db=db, brief=brief, pm_decision_key="pm_test"
            )
        assert len(results) == 1
        assert results[0]["accepted"] is False
        assert results[0]["reason"] == "orchestrator_exception"


class TestMarkdownRendering:
    """The markdown body is what Wilson sees in email + scoreboard.
    The new snapshot shape surfaces the status breakdown + the
    needs-attention line — those must render cleanly for both
    the "stuck runs" case and the "all clear" case."""

    def test_renders_status_breakdown_with_needs_attention(self):
        from datetime import date
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            PMConcern,
            _render_markdown,
        )

        brief = PMBrief(
            headline="Stuck ERROR runs",
            summary="Two paper runs sitting in ERROR; auto-restart is cycling them.",
            recommendations=[],
            concerns=[
                PMConcern(
                    severity="blocker",
                    description="2 paper runs stuck in ERROR, no new trades.",
                )
            ],
        )
        state = {
            "paper_runs": {
                "running": 0,
                "effective_active": 2,
                "needs_attention_count": 2,
                "halted_in_last_7_days": 0,
                "status_counts_last_7_days": {"ERROR": 2},
            },
            "evolution_runs_last_7_days": {"completed": 0, "failed": 0},
            "top_validated_genomes_last_30_days": [],
        }
        md = _render_markdown(brief, date(2026, 10, 6), state)
        assert "ERROR=2" in md
        assert "2 paper run(s) in non-terminal non-RUNNING state" in md
        assert "## Concerns" in md

    def test_renders_cleanly_when_no_paper_activity(self):
        from datetime import date
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            _render_markdown,
        )

        brief = PMBrief(
            headline="All clear",
            summary="Nothing moving but the floor isn't on fire either.",
        )
        state = {
            "paper_runs": {
                "running": 0,
                "effective_active": 0,
                "needs_attention_count": 0,
                "halted_in_last_7_days": 0,
                "status_counts_last_7_days": {},
            },
            "evolution_runs_last_7_days": {"completed": 0, "failed": 0},
            "top_validated_genomes_last_30_days": [],
        }
        md = _render_markdown(brief, date(2026, 10, 6), state)
        # No-activity case renders explanation rather than empty line.
        assert "no activity in last 7 days" in md
        # Should NOT render the needs-attention warning when count is 0.
        assert "non-terminal non-RUNNING" not in md


class TestEvolutionFiller:
    """Filler loop — Wilson 2026-10-22. Must:
    - skip when budget exhausted
    - skip when a Gate-B genome landed today
    - fire LLM + schedule actions otherwise
    - never raise
    """

    def test_skips_when_budget_exhausted(self):
        from agentfx.agents.fleet import portfolio_manager

        db = MagicMock()
        with (
            patch.object(
                portfolio_manager, "evolutions_remaining_today", return_value=0
            ),
            patch("agentfx.agents.fleet.portfolio_manager.structured_call") as llm,
        ):
            fired = portfolio_manager.run_evolution_filler(db=db)
        assert fired == 0
        llm.assert_not_called()

    def test_skips_when_gate_b_cleared_today(self):
        from agentfx.agents.fleet import portfolio_manager

        db = MagicMock()
        with (
            patch.object(
                portfolio_manager, "evolutions_remaining_today", return_value=5
            ),
            patch.object(
                portfolio_manager,
                "_any_genome_cleared_validator_today",
                return_value=True,
            ),
            patch("agentfx.agents.fleet.portfolio_manager.structured_call") as llm,
        ):
            fired = portfolio_manager.run_evolution_filler(db=db)
        assert fired == 0
        llm.assert_not_called()

    def test_fires_actions_when_budget_and_no_gate_b(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        from agentfx.agents.fleet import portfolio_manager
        from agentfx.agents.fleet.portfolio_manager import (
            PMBrief,
            PMEvolutionAction,
        )

        db = MagicMock()

        brief = PMBrief(
            headline="filler tick",
            summary="try new combo",
            evolution_actions=[
                PMEvolutionAction(
                    pair="EUR_USD",
                    granularity="H1",
                    rationale="M1 lineage overfit; try H1",
                    expected_profit_impact="OOS Sharpe > 1.0 on H1",
                    priority="now",
                )
            ],
        )
        llm_result = MagicMock(
            output=brief,
            reasoning="",
            model="claude-haiku-4-5",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            usd_cost=0.001,
            skipped_reason=None,
        )

        with (
            patch.object(
                portfolio_manager, "evolutions_remaining_today", return_value=5
            ),
            patch.object(
                portfolio_manager,
                "_any_genome_cleared_validator_today",
                return_value=False,
            ),
            patch.object(
                portfolio_manager, "_snapshot_platform_state", return_value={}
            ),
            patch(
                "agentfx.agents.fleet.portfolio_manager.structured_call",
                return_value=llm_result,
            ),
            patch.object(
                portfolio_manager,
                "_execute_evolution_actions",
                return_value=[{"outcome": "scheduled"}],
            ),
            patch("agentfx.agents.fleet.portfolio_manager.record_decision"),
        ):
            fired = portfolio_manager.run_evolution_filler(db=db)
        assert fired == 1

    def test_llm_skipped_returns_zero_without_raising(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        from agentfx.agents.fleet import portfolio_manager

        db = MagicMock()
        llm_result = MagicMock(output=None, skipped_reason="api_failure")

        with (
            patch.object(
                portfolio_manager, "evolutions_remaining_today", return_value=5
            ),
            patch.object(
                portfolio_manager,
                "_any_genome_cleared_validator_today",
                return_value=False,
            ),
            patch.object(
                portfolio_manager, "_snapshot_platform_state", return_value={}
            ),
            patch(
                "agentfx.agents.fleet.portfolio_manager.structured_call",
                return_value=llm_result,
            ),
        ):
            fired = portfolio_manager.run_evolution_filler(db=db)
        assert fired == 0

    def test_internal_exception_does_not_raise(self):
        from agentfx.agents.fleet import portfolio_manager

        db = MagicMock()
        with patch.object(
            portfolio_manager,
            "evolutions_remaining_today",
            side_effect=RuntimeError("db exploded"),
        ):
            # Must not raise.
            fired = portfolio_manager.run_evolution_filler(db=db)
        assert fired == 0
