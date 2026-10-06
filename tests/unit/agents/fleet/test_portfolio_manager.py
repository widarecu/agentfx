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
