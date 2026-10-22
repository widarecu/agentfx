"""Unit tests for `agentfx.services.agent_orchestrator`.

Focus on:
- Budget accounting (daily cap + monthly alert threshold).
- Lifespan task gating (`AGENT_FLEET_ENABLED` false → no tasks).
- One-shot monthly alert (sentinel file + idempotence).

The async lifespan task itself is covered via the smaller
`register_lifespan_tasks` + `shutdown_lifespan_tasks` surface —
testing the sleep loop directly would be slow and brittle.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentfx.services import agent_orchestrator

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Budget accounting
# ─────────────────────────────────────────────────────────────────────


class TestDailyBudget:
    def _db_returning(self, total: float) -> MagicMock:
        """Build a MagicMock session whose daily spend query returns
        the given scalar."""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.scalar.return_value = total  # noqa: E501
        return db

    def test_zero_spend_is_within_budget(self):
        db = self._db_returning(0.0)
        assert agent_orchestrator.is_within_daily_budget(db=db) is True

    def test_spend_below_cap_is_within_budget(self):
        db = self._db_returning(1.00)
        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_DAILY_USD_CAP", 2.00
        ):
            assert agent_orchestrator.is_within_daily_budget(db=db) is True

    def test_spend_above_cap_is_rejected(self):
        db = self._db_returning(5.00)
        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_DAILY_USD_CAP", 2.00
        ):
            assert agent_orchestrator.is_within_daily_budget(db=db) is False

    def test_spend_at_cap_is_rejected(self):
        """Hard cap is exclusive — spending exactly the cap halts the
        next run, so a budget breach can't start a run that pushes
        us over."""
        db = self._db_returning(2.00)
        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_DAILY_USD_CAP", 2.00
        ):
            assert agent_orchestrator.is_within_daily_budget(db=db) is False


class TestMonthlyAlert:
    def _db_returning_total(self, total: float) -> MagicMock:
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = total
        return db

    def test_below_threshold_does_not_alert(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = self._db_returning_total(5.00)
        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_MONTHLY_USD_ALERT", 20.00
        ):
            assert agent_orchestrator.should_alert_monthly_overspend(db=db) is False

    def test_crossing_threshold_fires_once(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = self._db_returning_total(25.00)
        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_MONTHLY_USD_ALERT", 20.00
        ):
            first = agent_orchestrator.should_alert_monthly_overspend(db=db)
            second = agent_orchestrator.should_alert_monthly_overspend(db=db)

        assert first is True
        assert second is False  # sentinel prevented double-fire

    def test_sentinel_is_per_month(self, tmp_path, monkeypatch):
        """Crossing the threshold in a new calendar month must fire
        the alert again — Wilson wants a monthly tripwire, not a
        once-per-deploy one."""
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        db = self._db_returning_total(25.00)

        oct_date = datetime(2026, 10, 15, tzinfo=UTC).date()
        nov_date = datetime(2026, 11, 15, tzinfo=UTC).date()

        with patch.object(
            agent_orchestrator.settings, "AGENT_FLEET_MONTHLY_USD_ALERT", 20.00
        ):
            fired_oct = agent_orchestrator.should_alert_monthly_overspend(
                db=db, today=oct_date
            )
            fired_oct_again = agent_orchestrator.should_alert_monthly_overspend(
                db=db, today=oct_date
            )
            fired_nov = agent_orchestrator.should_alert_monthly_overspend(
                db=db, today=nov_date
            )

        assert fired_oct is True
        assert fired_oct_again is False
        assert fired_nov is True


# ─────────────────────────────────────────────────────────────────────
# Lifespan gating
# ─────────────────────────────────────────────────────────────────────


class TestLifespanGating:
    def test_disabled_flag_registers_no_tasks(self):
        with patch.object(agent_orchestrator.settings, "AGENT_FLEET_ENABLED", False):
            tasks = agent_orchestrator.register_lifespan_tasks()
        assert tasks == []

    async def test_enabled_flag_starts_all_fleet_tasks(self):
        """`create_task` requires a running loop — pytest-asyncio
        `auto` mode (see conftest) provides one when the test itself
        is async. We patch all three loops to no-ops and cleanly
        shutdown so leftover pending tasks don't pollute other tests."""

        async def _noop():
            return None

        with (
            patch.object(agent_orchestrator.settings, "AGENT_FLEET_ENABLED", True),
            patch("agentfx.services.agent_orchestrator._pm_daily_loop", _noop),
            patch(
                "agentfx.services.agent_orchestrator._evolution_filler_loop",
                _noop,
            ),
            patch("agentfx.services.agent_orchestrator._qa_scan_loop", _noop),
            patch("agentfx.services.agent_orchestrator._pe_scan_loop", _noop),
            patch(
                "agentfx.services.agent_orchestrator._pe_workspace_poll_loop",
                _noop,
            ),
        ):
            tasks = agent_orchestrator.register_lifespan_tasks()
            assert len(tasks) == 5
            names = {t.get_name() for t in tasks}
            assert names == {
                "fleet_pm_daily",
                "fleet_evolution_filler",
                "fleet_qa_scan",
                "fleet_pe_scan",
                "fleet_pe_workspace_poll",
            }
            await agent_orchestrator.shutdown_lifespan_tasks()
