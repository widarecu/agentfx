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


class TestPMBriefSchemaTolerance:
    """Regression for 2026-10-29 production incident: Haiku returned a
    structured PMBrief whose first PMRecommendation omitted
    `priority`, tripping Pydantic strict validation and sending the
    whole brief into deterministic fallback. The schema now defaults
    every nested field so missing values don't blow up the brief."""

    def test_pm_recommendation_builds_from_empty_dict(self):
        from agentfx.agents.fleet.portfolio_manager import PMRecommendation

        rec = PMRecommendation.model_validate({})
        assert rec.priority == "watch"
        assert rec.headline == ""
        assert rec.rationale == ""

    def test_pm_concern_builds_from_empty_dict(self):
        from agentfx.agents.fleet.portfolio_manager import PMConcern

        c = PMConcern.model_validate({})
        assert c.severity == "noted"
        assert c.description == ""

    def test_pm_brief_builds_with_partial_recommendation(self):
        """A single recommendation missing its `priority` must not
        fail the whole brief — this was the exact 2026-10-29 shape."""
        from agentfx.agents.fleet.portfolio_manager import PMBrief

        brief = PMBrief.model_validate(
            {
                "headline": "H",
                "summary": "S",
                "recommendations": [
                    {"headline": "do X"},
                    {"priority": "now", "headline": "do Y", "rationale": "why"},
                ],
                "concerns": [{"description": "oops"}],
            }
        )
        assert len(brief.recommendations) == 2
        assert brief.recommendations[0].priority == "watch"
        assert brief.recommendations[0].headline == "do X"
        assert brief.recommendations[0].rationale == ""
        assert brief.concerns[0].severity == "noted"


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


# ─────────────────────────────────────────────────────────────────────
# AFX-38 — the brief must name the exact genome it recommends
# ─────────────────────────────────────────────────────────────────────
#
# 2026-10-27: PM said "Deploy AUD_USD/M30 genome (fitness 1.264) to paper
# immediately" while the approval queue held 5+ AUD_USD M30 candidates.
# Real-shaped ids below (`genome_{gen_{evorun_{YYYYMMDD}_{hex8}}_{gen}}_{idx}`)
# — three candidates from ONE evolution run. They share their first 38
# characters, so neither pair/granularity nor a 24-char prefix
# (`genome_gen_evorun_202610`, identical for every genome minted in
# October 2026) tells them apart; only the full id does.

_GENOME_A = "genome_gen_evorun_20261027_ab12cd34_003_017"
_GENOME_B = "genome_gen_evorun_20261027_ab12cd34_003_004"
_GENOME_C = "genome_gen_evorun_20261027_ab12cd34_002_011"
_PORTAL = "https://agentfx-liart.vercel.app"


def _aud_usd_m30(gid: str, fitness: float) -> dict:
    """A `top_validated_genomes_last_30_days` entry as the snapshot emits it."""
    return {"id": gid, "fitness": fitness, "pair": "AUD_USD", "granularity": "M30"}


def _afx38_state() -> dict:
    return {
        "as_of_utc": "2026-10-27T09:00:00+00:00",
        "paper_runs": {
            "running": 1,
            "effective_active": 1,
            "halted_in_last_7_days": 0,
            "status_counts_last_7_days": {"RUNNING": 1},
            "needs_attention_count": 0,
        },
        "evolution_runs_last_7_days": {"completed": 4, "failed": 0},
        "top_validated_genomes_last_30_days": [
            _aud_usd_m30(_GENOME_A, 1.264),
            _aud_usd_m30(_GENOME_B, 1.201),
            _aud_usd_m30(_GENOME_C, 1.150),
        ],
        "evolution_scheduling_context": {
            "allowed_pairs": ["AUD_USD"],
            "allowed_granularities": ["M30"],
            "per_pair_coverage": [
                {
                    "pair": "AUD_USD",
                    "granularity": "M30",
                    "days_since_last_completed_evo": 0,
                    "current_best_fitness": 1.264,
                    "current_best_genome_id": _GENOME_A,
                }
            ],
            "evolutions_remaining_today": 10,
            "max_per_day": 10,
            "prior_pm_evolution_decisions": [],
        },
    }


def _send_brief_and_capture_email(brief, state=None) -> dict:
    """Drive `_write_morning_brief` end-to-end — snapshot, LLM, decision
    log and memory file stubbed — and return the kwargs PM handed to
    `send_agent_message` (plain-text `body` + `html_body`)."""
    from datetime import date

    from agentfx.agents.fleet import portfolio_manager as pm
    from agentfx.agents.fleet.llm import LLMCallResult

    llm = LLMCallResult(
        output=brief,
        reasoning="",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        usd_cost=0.0,
    )
    with (
        patch.object(
            pm, "_snapshot_platform_state", return_value=state or _afx38_state()
        ),
        patch.object(pm, "_recent_pm_summaries", return_value=[]),
        patch.object(pm, "_call_haiku", return_value=llm),
        patch.object(pm, "record_decision"),
        patch.object(pm, "write_memory_file"),
        patch(
            "agentfx.notifications.email._infer_portal_base_url",
            return_value=_PORTAL,
        ),
        patch("agentfx.notifications.email.send_agent_message") as send,
    ):
        pm._write_morning_brief(db=MagicMock(), today=date(2026, 10, 27))
    send.assert_called_once()
    return send.call_args.kwargs


class TestGenomeIdInBriefEmail:
    """AFX-38 done-when: a "deploy X" recommendation renders the
    genome_id plus a direct link to its approval row in the email."""

    def _deploy_brief(self, **rec_overrides):
        from agentfx.agents.fleet.portfolio_manager import PMBrief, PMRecommendation

        rec = dict(
            priority="now",
            headline="Deploy AUD_USD/M30 genome (fitness 1.264) to paper immediately",
            rationale="Clears Gate B with the best fitness of the AUD_USD M30 cohort.",
            genome_id=_GENOME_A,
        )
        rec.update(rec_overrides)
        return PMBrief(
            headline="AUD_USD M30 cleared Gate B",
            summary="One candidate is ready for paper.",
            recommendations=[PMRecommendation(**rec)],
        )

    def test_deploy_recommendation_renders_genome_id_and_approval_link(self):
        email = _send_brief_and_capture_email(self._deploy_brief())
        html, body = email["html_body"], email["body"]
        url_a = f"{_PORTAL}/paper/approvals/{_GENOME_A}"

        # HTML part: full id + deep link to the approval row.
        assert _GENOME_A in html
        assert f'href="{url_a}"' in html
        # DB-sourced facts next to the id so Wilson can sanity-check
        # that the id matches PM's prose.
        assert "AUD_USD M30" in html
        assert "fitness 1.264" in html
        # The other AUD_USD M30 candidates are never offered as the action.
        assert _GENOME_B not in html
        assert _GENOME_C not in html

        # Plain-text part (also the scoreboard) carries the same link.
        assert f"_Genome:_ `{_GENOME_A}`" in body
        assert url_a in body
        assert f"/paper/approvals/{_GENOME_B}" not in body
        assert f"/paper/approvals/{_GENOME_C}" not in body

    def test_genome_id_quoted_only_in_rationale_still_resolves(self):
        """Haiku may skip the structured field but follow the prompt's
        'quote the full id in the rationale' rule — still linkable."""
        email = _send_brief_and_capture_email(
            self._deploy_brief(
                genome_id="",
                rationale=f"{_GENOME_A} clears Gate B with the best cohort fitness.",
            )
        )
        url_a = f"{_PORTAL}/paper/approvals/{_GENOME_A}"
        assert f'href="{url_a}"' in email["html_body"]
        assert url_a in email["body"]

    def test_genome_id_not_in_snapshot_is_flagged_not_trusted(self):
        """An id PM was never shown (hallucinated / stale) still renders
        — dropping it would hide what PM said — but with a warning and
        no DB facts attached."""
        ghost = "genome_gen_evorun_20261001_deadbeef_000_000"
        email = _send_brief_and_capture_email(self._deploy_brief(genome_id=ghost))
        html, body = email["html_body"], email["body"]
        assert ghost in html
        assert "not in today's snapshot" in html
        assert "not in today's snapshot" in body
        assert f"{_PORTAL}/paper/approvals/{ghost}" in body

    def test_recommendation_without_a_genome_gets_no_approval_link(self):
        email = _send_brief_and_capture_email(
            self._deploy_brief(
                headline="Halt losing paper run",
                rationale="Draw-down past backtest tolerance.",
                genome_id="",
            )
        )
        assert "/paper/approvals/" not in email["html_body"]
        assert "/paper/approvals/" not in email["body"]

    def test_evolution_action_naming_a_genome_gets_approval_link(self):
        """Ticket scope covers `evolution_actions` too: a (skipped)
        action whose rationale says to deploy a specific genome must
        surface that genome + link — today a skipped action's rationale
        isn't rendered at all."""
        from agentfx.agents.fleet.pm_scheduler import PMEvolutionAction
        from agentfx.agents.fleet.portfolio_manager import PMBrief

        brief = PMBrief(
            headline="h",
            summary="s",
            evolution_actions=[
                PMEvolutionAction(
                    pair="AUD_USD",
                    granularity="M30",
                    priority="watch",
                    rationale=(
                        f"Champion {_GENOME_A} already clears Gate B — deploy "
                        "it to paper before spending budget on a re-run."
                    ),
                    expected_profit_impact="Paper P&L on the champion within 7d.",
                )
            ],
        )
        email = _send_brief_and_capture_email(brief)
        url_a = f"{_PORTAL}/paper/approvals/{_GENOME_A}"
        assert f'href="{url_a}"' in email["html_body"]
        assert url_a in email["body"]
        assert f"/paper/approvals/{_GENOME_B}" not in email["body"]


class TestGenomeIdResolution:
    """Unit-level contract of the resolver behind the email lines."""

    def test_snapshot_index_covers_top_genomes_and_pair_champions(self):
        from agentfx.agents.fleet.portfolio_manager import _snapshot_genome_index

        state = _afx38_state()
        champion_only = "genome_gen_evorun_20261020_0f0f0f0f_005_002"
        state["evolution_scheduling_context"]["per_pair_coverage"].append(
            {
                "pair": "EUR_USD",
                "granularity": "H1",
                "days_since_last_completed_evo": 3,
                "current_best_fitness": 0.91,
                "current_best_genome_id": champion_only,
            }
        )
        index = _snapshot_genome_index(state)
        assert set(index) == {_GENOME_A, _GENOME_B, _GENOME_C, champion_only}
        assert index[champion_only] == {
            "pair": "EUR_USD",
            "granularity": "H1",
            "fitness": 0.91,
        }

    def test_text_match_is_token_bounded(self):
        """`..._003_01` must not match inside `..._003_017` — exact ids
        only, never fuzzy guessing that could point at the wrong row."""
        from agentfx.agents.fleet.portfolio_manager import _genome_refs

        short = "genome_gen_evorun_20261027_ab12cd34_003_01"
        index = {short: {"pair": "AUD_USD", "granularity": "M30", "fitness": 1.0}}
        longer_id_text = f"deploy {_GENOME_A}"
        assert _genome_refs(explicit_id="", texts=[longer_id_text], index=index) == []
        refs = _genome_refs(explicit_id="", texts=[f"deploy ({short})."], index=index)
        assert [r["id"] for r in refs] == [short]

    def test_explicit_id_listed_first_and_not_duplicated(self):
        from agentfx.agents.fleet.portfolio_manager import _genome_refs

        index = {
            g["id"]: g for g in _afx38_state()["top_validated_genomes_last_30_days"]
        }
        refs = _genome_refs(
            explicit_id=_GENOME_A,
            texts=[f"prefer {_GENOME_A} over {_GENOME_B}"],
            index=index,
        )
        assert [r["id"] for r in refs] == [_GENOME_A, _GENOME_B]
        assert all(r["verified"] for r in refs)


class TestPromptAndSchemaNameGenomes:
    """AFX-38: Haiku must be told to name genomes, and given the field
    + the ids to do it with."""

    def test_recommendation_schema_exposes_optional_genome_id(self):
        from agentfx.agents.fleet.portfolio_manager import PMRecommendation

        # Reaches Haiku via the structured-output JSON schema.
        assert "genome_id" in PMRecommendation.model_json_schema()["properties"]
        # Defaults like every other field (2026-10-29 partial-output
        # tolerance) — omitting it must not sink the brief.
        assert PMRecommendation.model_validate({"headline": "x"}).genome_id == ""

    def test_system_prompt_requires_full_verbatim_genome_id(self):
        from agentfx.agents.fleet.portfolio_manager import _SYSTEM_PROMPT

        assert "genome_id" in _SYSTEM_PROMPT
        assert "current_best_genome_id" in _SYSTEM_PROMPT
        assert "top_validated_genomes_last_30_days" in _SYSTEM_PROMPT
        assert "verbatim" in _SYSTEM_PROMPT


class TestSnapshotNamesGenomes:
    """AFX-38 root cause: `StrategyGenome` has no instrument/granularity
    columns, so the snapshot's `getattr(g, "instrument", None)` always
    yielded None — PM saw ids with no pair, and pair champions with no
    id, and could not connect "AUD_USD/M30 fitness 1.264" to a genome.
    Real SQL over in-memory SQLite (same pattern as
    `test_approval_queue_meta.py`)."""

    @pytest.fixture
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from agentfx.api.core.database import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session
        engine.dispose()

    def _seed(self, db):
        from agentfx.api import models

        db.add(
            models.EvolutionRun(
                id="evorun_20261027_ab12cd34",
                status=models.EvolutionRunStatus.COMPLETED,
                instrument="AUD_USD",
                granularity="M30",
                config_json={},
                stop_conditions_json={},
                current_generation=3,
                llm_cost_usd=0,
            )
        )
        db.add(
            models.Generation(
                id="gen_evorun_20261027_ab12cd34_003",
                run_id="evorun_20261027_ab12cd34",
                index=3,
            )
        )
        for gid, fitness, passed in (
            (_GENOME_A, 1.264, True),
            (_GENOME_B, 1.201, True),
            # Higher fitness but failed the validator → never offered.
            ("genome_gen_evorun_20261027_ab12cd34_003_009", 2.5, False),
        ):
            db.add(
                models.StrategyGenome(
                    id=gid,
                    generation_id="gen_evorun_20261027_ab12cd34_003",
                    dsl_json={"stub": True},
                    parent_ids=[],
                    origin=models.GenomeOrigin.SEED,
                    fitness=fitness,
                    validator_passed=passed,
                    validation_failures=[],
                )
            )
        db.commit()

    def test_top_genomes_carry_pair_and_granularity(self, db, monkeypatch):
        from agentfx.agents.fleet import pm_scheduler
        from agentfx.agents.fleet.portfolio_manager import _snapshot_platform_state

        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "AUD_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M30")
        self._seed(db)

        state = _snapshot_platform_state(db)

        top = state["top_validated_genomes_last_30_days"]
        assert [g["id"] for g in top] == [_GENOME_A, _GENOME_B]
        assert [g["fitness"] for g in top] == [1.264, 1.201]
        # The fix: pair/granularity come from the parent EvolutionRun.
        assert all(g["pair"] == "AUD_USD" and g["granularity"] == "M30" for g in top)
        (coverage,) = state["evolution_scheduling_context"]["per_pair_coverage"]
        assert coverage["current_best_fitness"] == 1.264
        assert coverage["current_best_genome_id"] == _GENOME_A
