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


class TestWatchdogMarksRunFailed:
    """The watchdog's DB-update half. Verifies the cross-state flip
    (running OR queued → failed) that's the safety net for crashed
    subprocesses. The QUEUED-path test pins the regression that hid
    evorun_20260527_b02d5a08 — child subprocess died during module
    import (RLIMIT_AS hit), the row never transitioned out of QUEUED,
    and the watchdog silently did nothing because it only handled
    RUNNING rows."""

    def test_running_row_becomes_failed_with_process_crashed(self, db_session):
        """Mid-run crash: row was RUNNING when subprocess died."""
        run = _make_run_with_status(
            db_session, models.EvolutionRunStatus.RUNNING, pid=12345
        )
        with patch(
            "agentfx.api.core.database.SessionLocal",
            return_value=db_session,
        ):
            with patch.object(db_session, "close"):
                evolution_runner._mark_run_failed_after_crash(run.id, exit_code=-9)

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.FAILED
        assert run.stop_reason is not None
        assert "process_crashed" in run.stop_reason
        assert "crashed_pre_init" not in run.stop_reason
        assert "exit_code=-9" in run.stop_reason
        assert run.completed_at is not None

    def test_queued_row_becomes_failed_with_pre_init_reason(self, db_session):
        """Pre-init crash: row was QUEUED when subprocess died (e.g.
        RLIMIT_AS hit during numpy/pandas import on Railway). Without
        this branch the row sits in `queued` forever with no error."""
        run = _make_run_with_status(
            db_session, models.EvolutionRunStatus.QUEUED, pid=67890
        )
        with patch(
            "agentfx.api.core.database.SessionLocal",
            return_value=db_session,
        ):
            with patch.object(db_session, "close"):
                evolution_runner._mark_run_failed_after_crash(run.id, exit_code=1)

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.FAILED
        # Specifically marked as pre-init so the portal banner can
        # surface "subprocess crashed during boot, not during a run".
        assert run.stop_reason is not None
        assert "crashed_pre_init" in run.stop_reason
        assert "exit_code=1" in run.stop_reason
        assert run.completed_at is not None

    def test_completed_row_is_not_touched(self, db_session):
        """Watchdog must NEVER overwrite a terminal state. If the
        orchestrator marked the run COMPLETED before exiting, that's
        the truth — even on a non-zero exit (the orchestrator may
        exit non-zero after marking COMPLETED for unrelated reasons)."""
        run = _make_run_with_status(db_session, models.EvolutionRunStatus.COMPLETED)
        original_stop_reason = run.stop_reason
        original_completed_at = run.completed_at

        with patch(
            "agentfx.api.core.database.SessionLocal",
            return_value=db_session,
        ):
            with patch.object(db_session, "close"):
                evolution_runner._mark_run_failed_after_crash(run.id, exit_code=1)

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.COMPLETED
        assert run.stop_reason == original_stop_reason
        assert run.completed_at == original_completed_at

    def test_already_failed_row_is_not_overwritten(self, db_session):
        """Same idempotency for FAILED rows — the orchestrator may
        have marked the run FAILED with its own (more informative)
        stop_reason before exiting non-zero."""
        run = _make_run_with_status(db_session, models.EvolutionRunStatus.FAILED)
        run.stop_reason = "data_window_not_covered"
        db_session.commit()

        with patch(
            "agentfx.api.core.database.SessionLocal",
            return_value=db_session,
        ):
            with patch.object(db_session, "close"):
                evolution_runner._mark_run_failed_after_crash(run.id, exit_code=137)

        db_session.refresh(run)
        assert run.status == models.EvolutionRunStatus.FAILED
        # The more-specific orchestrator-set reason is preserved.
        assert run.stop_reason == "data_window_not_covered"

    def test_vanished_row_is_handled_gracefully(self, db_session):
        """If the run row gets deleted between subprocess exit and
        watchdog firing, the watchdog must not raise."""
        with patch(
            "agentfx.api.core.database.SessionLocal",
            return_value=db_session,
        ):
            with patch.object(db_session, "close"):
                # Should NOT raise even though the row doesn't exist.
                evolution_runner._mark_run_failed_after_crash(
                    "evorun_nonexistent_id", exit_code=1
                )
