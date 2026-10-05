"""Unit tests for `agentfx.agents.fleet.memory`.

Memory layer is split across two surfaces (DB + markdown files).
Testing each independently so a failure localises immediately: the
DB tests use a MagicMock session (no sqlite), the FS tests use
`tmp_path` + `AGENT_MEMORY_ROOT` env var.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.agents.fleet.memory import (
    DecisionRecord,
    agent_memory_root,
    append_memory_file,
    read_memory_file,
    record_decision,
    record_outcome,
    write_memory_file,
)
from agentfx.api.models.agent_decision import AgentRole

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Filesystem layer
# ─────────────────────────────────────────────────────────────────────


class TestMemoryFilesystem:
    def test_root_respects_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        root = agent_memory_root()
        assert root == tmp_path

    def test_root_auto_creates_directory(self, tmp_path, monkeypatch):
        target = tmp_path / "new_subdir"
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(target))
        assert not target.exists()
        agent_memory_root()
        assert target.exists()

    def test_read_missing_file_returns_empty_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        assert read_memory_file("does_not_exist.md") == ""

    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        write_memory_file("scoreboard.md", "## v0.8.0 pilot\nhello\n")
        assert read_memory_file("scoreboard.md").startswith("## v0.8.0 pilot")

    def test_write_creates_parent_directories(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        write_memory_file("paper_divergence/run_abc.md", "report body")
        assert (tmp_path / "paper_divergence" / "run_abc.md").read_text() == (
            "report body"
        )

    def test_write_is_atomic_via_tmp_rename(self, tmp_path, monkeypatch):
        """A write operation must not leave a half-populated file —
        we use `.tmp` + `replace`. Verify no stale `.tmp` remains."""
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        write_memory_file("x.md", "content")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_append_adds_without_truncating(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))
        append_memory_file("log.md", "first\n")
        append_memory_file("log.md", "second\n")
        assert read_memory_file("log.md") == "first\nsecond\n"


# ─────────────────────────────────────────────────────────────────────
# DB layer
# ─────────────────────────────────────────────────────────────────────


class TestRecordDecision:
    def test_populates_core_fields(self):
        db = MagicMock()
        rec = DecisionRecord(
            agent_role=AgentRole.PM,
            action="morning_brief",
            decision_key="pm_morning_brief_2026-10-05",
            reasoning="phase A pilot",
            model="claude-haiku-4-5",
            input_tokens=100,
            output_tokens=50,
            usd_cost=0.001,
        )
        row = record_decision(db, rec)
        assert row.id and len(row.id) == 32  # uuid4 hex
        assert row.agent_role == "PM"
        assert row.action == "morning_brief"
        assert row.decision_key == "pm_morning_brief_2026-10-05"
        assert row.model == "claude-haiku-4-5"
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert row.usd_cost == 0.001
        assert row.reasoning_text == "phase A pilot"
        # Reflexion fields default to None (filled in later).
        assert row.outcome_json is None
        assert row.outcome_recorded_at is None
        assert row.outcome_grade is None
        db.add.assert_called_once_with(row)
        db.flush.assert_called_once()


class TestRecordOutcome:
    def test_fills_outcome_fields_only(self):
        db = MagicMock()
        existing = MagicMock()
        existing.outcome_json = None
        existing.outcome_grade = None
        existing.reasoning_text = "original reasoning — must not change"
        db.get.return_value = existing

        record_outcome(
            db,
            decision_id="abc",
            outcome={"paper_pnl_30d": 42.1},
            grade="good",
        )

        assert existing.outcome_json == {"paper_pnl_30d": 42.1}
        assert existing.outcome_grade == "good"
        assert existing.outcome_recorded_at is not None
        # Original reasoning untouched.
        assert existing.reasoning_text == "original reasoning — must not change"
        db.flush.assert_called_once()

    def test_missing_row_is_logged_not_raised(self):
        """If an outcome is recorded for a non-existent decision id we
        want to log + continue, not crash the Reflexion loop."""
        db = MagicMock()
        db.get.return_value = None
        # Must not raise.
        record_outcome(
            db,
            decision_id="missing",
            outcome={"x": 1},
            grade="good",
        )
        db.flush.assert_not_called()
