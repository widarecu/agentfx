"""Unit tests for `agents.fleet.pm_scheduler`.

v0.8.1 Phase C.1 — PM's first write authority. Tests cover:
- Rate-limit enforcement (max 2/week fleet-wide, DB-authoritative).
- Pre-dispatch validation (priority, pair/granularity allow-lists,
  mandatory rationale + expected-impact fields).
- Never-raises contract — scheduler failures must not crash the PM
  loop.
- Decision-log row contents (Reflexion depends on these fields).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agentfx.agents.fleet import pm_scheduler

pytestmark = pytest.mark.unit


def _action(**overrides):
    """Build a PMEvolutionAction with sensible defaults; overrides win."""
    defaults = dict(
        pair="EUR_USD",
        granularity="M1",
        rationale="Champion stale (10 days since last run).",
        expected_profit_impact="Produce a genome with OOS Sharpe > 1.09.",
        priority="now",
    )
    defaults.update(overrides)
    return pm_scheduler.PMEvolutionAction(**defaults)


def _db_with_today_count(count: int) -> MagicMock:
    """DB stub whose rate-limit count query returns `count`."""
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.count.return_value = count  # noqa: E501
    return db


class TestRateLimit:
    def test_zero_prior_runs_leaves_full_quota(self):
        db = _db_with_today_count(0)
        assert pm_scheduler.evolutions_remaining_today(db) == 10

    def test_partial_use_subtracts_from_cap(self):
        db = _db_with_today_count(3)
        assert pm_scheduler.evolutions_remaining_today(db) == 7

    def test_cap_reached_leaves_zero(self):
        db = _db_with_today_count(10)
        assert pm_scheduler.evolutions_remaining_today(db) == 0

    def test_cap_exceeded_still_zero_not_negative(self):
        db = _db_with_today_count(50)
        assert pm_scheduler.evolutions_remaining_today(db) == 0


class TestPreDispatchValidation:
    """Priority / rationale / pair rules reject the action BEFORE any
    DB write happens. All reject paths return a result (never raise)."""

    def test_wrong_priority_rejected(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(0)
        action = _action(priority="watch")

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert out.reason.startswith("wrong_priority")

    def test_missing_rationale_rejected(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(0)
        action = _action(rationale="   ")  # whitespace-only

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert out.reason == "no_rationale"

    def test_missing_expected_impact_rejected(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(0)
        action = _action(expected_profit_impact="")

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert out.reason == "no_expected_impact"

    def test_pair_not_in_allowlist_rejected(self, monkeypatch):
        monkeypatch.setattr(
            pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD,USD_JPY"
        )
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(0)
        action = _action(pair="XAU_USD")

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert "pair_not_allowed" in out.reason

    def test_granularity_not_in_allowlist_rejected(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1,H1")
        db = _db_with_today_count(0)
        action = _action(granularity="M5")

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert "granularity_not_allowed" in out.reason

    def test_rate_limited_rejected_before_db_write(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(10)  # at cap
        action = _action()

        out = pm_scheduler.schedule_evolution_from_pm_action(
            db=db, action=action, pm_decision_key="pm_test"
        )
        assert out.accepted is False
        assert out.reason == "rate_limited"


class TestDispatchSuccess:
    """Happy path: valid action, within rate limit, allowed pair/gran.
    Service.create_run + worker.schedule_run both fire; a decision row
    is recorded; db.commit happens."""

    def test_happy_path_schedules_and_records_decision(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        monkeypatch.setattr(pm_scheduler.settings, "AGENT_FLEET_OWNER_EMAIL", "")
        db = _db_with_today_count(0)
        action = _action()

        fake_run = MagicMock(id="evorun_test_123")

        # Patch at the module path we imported in pm_scheduler. The
        # service + worker imports are lazy (inside the function), so
        # we patch where they're resolved.
        with (
            patch(
                "agentfx.api.services.evolution.create_run", return_value=fake_run
            ) as create,
            patch("agentfx.api.workers.evolution_runner.schedule_run") as sched,
            patch("agentfx.agents.fleet.pm_scheduler.record_decision") as rec,
        ):
            out = pm_scheduler.schedule_evolution_from_pm_action(
                db=db, action=action, pm_decision_key="pm_morning_brief_2026-10-11"
            )

        assert out.accepted is True
        assert out.evo_run_id == "evorun_test_123"
        create.assert_called_once()
        # No owner email set → NULL attribution, fleet-scoped visibility.
        assert create.call_args.kwargs["created_by"] is None
        sched.assert_called_once_with("evorun_test_123")
        rec.assert_called_once()
        # The decision record must capture everything Reflexion needs
        # to later grade the choice.
        call_args = rec.call_args
        decision = call_args[0][1]
        assert decision.action == "started_evolution_run"
        assert decision.decision_key == "pm_evo_evorun_test_123"
        assert decision.outputs["evo_run_id"] == "evorun_test_123"
        assert decision.outputs["attributed_user_id"] is None
        assert decision.reasoning == action.rationale

    def test_attributes_run_to_configured_owner_email(self, monkeypatch):
        """Wilson 2026-10-15: when AGENT_FLEET_OWNER_EMAIL is set,
        PM-authored runs land with `created_by=<that user's id>` so
        they appear in the operator's RBAC-scoped UI listing."""
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        monkeypatch.setattr(
            pm_scheduler.settings,
            "AGENT_FLEET_OWNER_EMAIL",
            "wreyes06@outlook.com",
        )
        db = _db_with_today_count(0)
        # The owner-user lookup query goes through filter/filter/first.
        owner_row = MagicMock(id=42)
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = owner_row  # noqa: E501
        action = _action()

        fake_run = MagicMock(id="evorun_owned_1")
        with (
            patch(
                "agentfx.api.services.evolution.create_run", return_value=fake_run
            ) as create,
            patch("agentfx.api.workers.evolution_runner.schedule_run"),
            patch("agentfx.agents.fleet.pm_scheduler.record_decision") as rec,
        ):
            out = pm_scheduler.schedule_evolution_from_pm_action(
                db=db, action=action, pm_decision_key="pm_test"
            )
        assert out.accepted is True
        assert create.call_args.kwargs["created_by"] == 42
        decision = rec.call_args[0][1]
        assert decision.outputs["attributed_user_id"] == 42

    def test_missing_owner_email_logs_warning_not_crash(self, monkeypatch, caplog):
        """Misconfiguration: owner email set but no matching active
        user. Scheduler must still create the run (falls back to NULL
        attribution) and log a WARNING so QA tech-health picks it up."""
        import logging

        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        monkeypatch.setattr(
            pm_scheduler.settings,
            "AGENT_FLEET_OWNER_EMAIL",
            "unknown@example.com",
        )
        db = _db_with_today_count(0)
        # Owner lookup returns None (no matching active user).
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None  # noqa: E501
        action = _action()

        fake_run = MagicMock(id="evorun_unattributed_1")
        with (
            caplog.at_level(
                logging.WARNING, logger="agentfx.agents.fleet.pm_scheduler"
            ),
            patch(
                "agentfx.api.services.evolution.create_run", return_value=fake_run
            ) as create,
            patch("agentfx.api.workers.evolution_runner.schedule_run"),
            patch("agentfx.agents.fleet.pm_scheduler.record_decision"),
        ):
            out = pm_scheduler.schedule_evolution_from_pm_action(
                db=db, action=action, pm_decision_key="pm_test"
            )
        assert out.accepted is True
        assert create.call_args.kwargs["created_by"] is None
        assert any(
            "does not match any active user" in r.message for r in caplog.records
        )
        db.commit.assert_called_once()


class TestSchedulingContext:
    """`build_scheduling_context` shapes the dict Haiku reads before
    deciding to schedule anything. Verify the key fields are
    present and the per-pair matrix spans every allowed combo."""

    def test_builds_full_coverage_matrix(self, monkeypatch):
        monkeypatch.setattr(
            pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD,USD_JPY"
        )
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1,H1")
        db = MagicMock()
        # last_completed query returns no rows (empty history)
        db.query.return_value.filter.return_value.filter.return_value.group_by.return_value.all.return_value = []  # noqa: E501
        # count query → 0 for rate limit
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.count.return_value = 0  # noqa: E501
        # champion join query also empty
        # Prior decisions query also empty
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []  # noqa: E501

        ctx = pm_scheduler.build_scheduling_context(db)

        assert ctx["allowed_pairs"] == ["EUR_USD", "USD_JPY"]
        assert ctx["allowed_granularities"] == ["M1", "H1"]
        assert ctx["max_per_day"] == 10
        assert ctx["evolutions_remaining_today"] == 10
        # 2 pairs × 2 grans = 4 coverage entries.
        assert len(ctx["per_pair_coverage"]) == 4
        pairs_grans = {(p["pair"], p["granularity"]) for p in ctx["per_pair_coverage"]}
        assert pairs_grans == {
            ("EUR_USD", "M1"),
            ("EUR_USD", "H1"),
            ("USD_JPY", "M1"),
            ("USD_JPY", "H1"),
        }
        # No prior evolution runs → `days_since_last_completed_evo` is None.
        for entry in ctx["per_pair_coverage"]:
            assert entry["days_since_last_completed_evo"] is None
            assert entry["current_best_fitness"] is None
            assert entry["current_best_genome_id"] is None


class TestChampionGenomeId:
    """AFX-38: each pair/granularity champion must carry its genome id,
    or PM can quote "AUD_USD/M30 fitness 1.264" but not name the genome
    Wilson should approve. Real SQL over in-memory SQLite."""

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

    def _seed_run(self, db, run_id, pair, gran, genomes):
        """`genomes`: iterable of (genome_id, fitness, validator_passed)."""
        from agentfx.api import models

        gen_id = f"gen_{run_id}_000"
        db.add(
            models.EvolutionRun(
                id=run_id,
                status=models.EvolutionRunStatus.COMPLETED,
                instrument=pair,
                granularity=gran,
                config_json={},
                stop_conditions_json={},
                current_generation=1,
                llm_cost_usd=0,
            )
        )
        db.add(models.Generation(id=gen_id, run_id=run_id, index=0))
        for genome_id, fitness, passed in genomes:
            db.add(
                models.StrategyGenome(
                    id=genome_id,
                    generation_id=gen_id,
                    dsl_json={"stub": True},
                    parent_ids=[],
                    origin=models.GenomeOrigin.SEED,
                    fitness=fitness,
                    validator_passed=passed,
                    validation_failures=[],
                )
            )
        db.commit()

    def test_champion_id_is_best_validator_passed_genome_per_combo(
        self, db, monkeypatch
    ):
        monkeypatch.setattr(
            pm_scheduler.settings, "CANDLE_INSTRUMENTS", "AUD_USD,EUR_USD"
        )
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M30")
        self._seed_run(
            db,
            "evorun_20261027_ab12cd34",
            "AUD_USD",
            "M30",
            [
                ("genome_gen_evorun_20261027_ab12cd34_000_004", 1.201, True),
                ("genome_gen_evorun_20261027_ab12cd34_000_017", 1.264, True),
                # Higher fitness but validator-failed → not a champion.
                ("genome_gen_evorun_20261027_ab12cd34_000_009", 2.5, False),
                # NULL fitness never wins (max() semantics preserved).
                ("genome_gen_evorun_20261027_ab12cd34_000_001", None, True),
            ],
        )

        ctx = pm_scheduler.build_scheduling_context(db)

        coverage = {(p["pair"], p["granularity"]): p for p in ctx["per_pair_coverage"]}
        aud = coverage[("AUD_USD", "M30")]
        assert aud["current_best_fitness"] == 1.264
        assert (
            aud["current_best_genome_id"]
            == "genome_gen_evorun_20261027_ab12cd34_000_017"
        )
        # No validated genome on EUR_USD → no champion, no id.
        eur = coverage[("EUR_USD", "M30")]
        assert eur["current_best_fitness"] is None
        assert eur["current_best_genome_id"] is None


class TestNeverRaises:
    """The scheduler must never propagate an exception — PM loop
    depends on it. A failure anywhere in create_run / schedule_run /
    the service layer must be caught and reported as a ScheduledEvolutionResult."""

    def test_create_run_exception_returns_rejected_result(self, monkeypatch):
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_INSTRUMENTS", "EUR_USD")
        monkeypatch.setattr(pm_scheduler.settings, "CANDLE_GRANULARITIES", "M1")
        db = _db_with_today_count(0)
        action = _action()

        with patch(
            "agentfx.api.services.evolution.create_run",
            side_effect=RuntimeError("db exploded"),
        ):
            # Must not raise.
            out = pm_scheduler.schedule_evolution_from_pm_action(
                db=db, action=action, pm_decision_key="pm_test"
            )
        assert out.accepted is False
        assert out.reason.startswith("scheduler_error:")
