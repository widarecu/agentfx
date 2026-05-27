"""Lifespan recovery hook integration tests.

`recover_queued_evolution_runs` is what the FastAPI lifespan calls at
app startup. It must:
1. Find runs left in RUNNING state from a previous app instance and
   reset them to QUEUED (their child processes died with the previous
   app — the PID is stale).
2. Schedule each QUEUED run via `schedule_run`.

These tests don't actually fork subprocesses — `schedule_run` is patched
so we can verify the recovery logic without spawning real workers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentfx.api import models, schemas
from agentfx.api.services.evolution import create_run
from agentfx.api.workers import evolution_runner


pytestmark = pytest.mark.integration


def _make_run_with_status(
    db_session, status: models.EvolutionRunStatus, *, pid: int | None = None
) -> models.EvolutionRun:
    run = create_run(db_session, schemas.EvolutionRunCreate(instrument="EUR_USD"))
    run.status = status
    run.process_pid = pid
    db_session.commit()
    return run


class TestRecoveryHook:
    def test_resets_orphaned_running_runs_to_queued(self, db_session):
        """A run in RUNNING with a PID from a previous process must be
        reset on recovery — the PID is stale by definition."""
        run = _make_run_with_status(
            db_session, models.EvolutionRunStatus.RUNNING, pid=99999
        )

        # Patch schedule_run so we don't fork real subprocesses
        with patch.object(evolution_runner, "schedule_run") as mock_schedule:
            evolution_runner.recover_queued_evolution_runs(db=db_session)

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.QUEUED
        # PID is cleared — the old PID is no longer the active worker
        assert run.process_pid is None
        # And the recovery scheduled this newly-reset run
        mock_schedule.assert_any_call(run.id)

    def test_schedules_already_queued_runs(self, db_session):
        run = _make_run_with_status(db_session, models.EvolutionRunStatus.QUEUED)

        with patch.object(evolution_runner, "schedule_run") as mock_schedule:
            evolution_runner.recover_queued_evolution_runs(db=db_session)

        mock_schedule.assert_any_call(run.id)

    def test_does_not_schedule_completed_runs(self, db_session):
        completed = _make_run_with_status(
            db_session, models.EvolutionRunStatus.COMPLETED
        )

        with patch.object(evolution_runner, "schedule_run") as mock_schedule:
            evolution_runner.recover_queued_evolution_runs(db=db_session)

        # The completed run must NOT appear in schedule_run calls
        scheduled_ids = [call.args[0] for call in mock_schedule.call_args_list]
        assert completed.id not in scheduled_ids

    def test_does_not_schedule_failed_runs(self, db_session):
        failed = _make_run_with_status(db_session, models.EvolutionRunStatus.FAILED)

        with patch.object(evolution_runner, "schedule_run") as mock_schedule:
            evolution_runner.recover_queued_evolution_runs(db=db_session)

        scheduled_ids = [call.args[0] for call in mock_schedule.call_args_list]
        assert failed.id not in scheduled_ids

    def test_returns_count_of_scheduled_runs(self, db_session):
        # 2 queued + 1 orphaned-running + 1 completed
        _make_run_with_status(db_session, models.EvolutionRunStatus.QUEUED)
        _make_run_with_status(db_session, models.EvolutionRunStatus.QUEUED)
        _make_run_with_status(db_session, models.EvolutionRunStatus.RUNNING)
        _make_run_with_status(db_session, models.EvolutionRunStatus.COMPLETED)

        with patch.object(evolution_runner, "schedule_run"):
            count = evolution_runner.recover_queued_evolution_runs(db=db_session)

        # 3 (2 already queued + 1 reset from RUNNING) but NOT the completed one
        assert count == 3


class TestSignalRunStop:
    def test_returns_false_for_invalid_pid(self):
        assert evolution_runner.signal_run_stop(0) is False
        assert evolution_runner.signal_run_stop(-1) is False

    def test_returns_false_for_nonexistent_pid(self):
        # PID 99999999 is virtually guaranteed not to exist
        result = evolution_runner.signal_run_stop(99_999_999)
        assert result is False
