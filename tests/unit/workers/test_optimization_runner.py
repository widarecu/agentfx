"""
Unit tests for the optimization runner worker module.

Tests cover:
1. _persist_trial_result() function with various trial data shapes
2. Trial metrics extraction and schema building
3. Extended metrics handling
4. Signal and trade bulk insertion
5. Error handling when persistence fails
6. run_optimization() duplicate execution prevention
7. _execute_optimization() job status validation

These are UNIT tests - all database operations are mocked.

Run with: make test-file FILE=tests/unit/workers/test_optimization_runner.py
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

pytestmark = pytest.mark.unit


class TestPersistTrialResult:
    """Unit tests for _persist_trial_result function."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return MagicMock()

    @pytest.fixture
    def basic_trial_info(self):
        """Basic trial info with minimal metrics."""
        return {
            "params": {"fast": 10, "slow": 50},
            "metrics": {
                "sharpe": 1.5,
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 100,
                "total_return": 0.15,
                "profit_factor": 1.8,
            },
        }

    @pytest.fixture
    def trial_info_with_extended_metrics(self):
        """Trial info including extended pip/dollar metrics."""
        return {
            "params": {"fast": 10, "slow": 50},
            "metrics": {
                "sharpe": 1.5,
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 100,
                "total_return": 0.15,
                "profit_factor": 1.8,
                "total_pips": 250.5,
                "avg_pips_per_trade": 2.5,
                "net_pips": 230.0,
                "gross_pnl_dollars": 2500.0,
                "net_pnl_dollars": 2300.0,
            },
        }

    @pytest.fixture
    def trial_info_with_signals_and_trades(self):
        """Trial info with signals and trades data."""
        return {
            "params": {"fast": 10, "slow": 50},
            "metrics": {
                "sharpe": 1.2,
                "max_drawdown": 0.08,
                "hit_rate": 0.52,
                "n_trades": 5,
                "total_return": 0.10,
                "profit_factor": 1.5,
            },
            "signals": [
                {
                    "timestamp": datetime(2024, 1, 1, 10, 0),
                    "signal": 1,
                    "signal_type": "entry_long",
                    "price": 1.1000,
                },
                {
                    "timestamp": datetime(2024, 1, 1, 14, 0),
                    "signal": 0,
                    "signal_type": "exit",
                    "price": 1.1050,
                    "pnl": 50.0,
                },
            ],
            "trades": [
                {
                    "direction": "long",
                    "entry_timestamp": datetime(2024, 1, 1, 10, 0),
                    "exit_timestamp": datetime(2024, 1, 1, 14, 0),
                    "entry_price": 1.1000,
                    "exit_price": 1.1050,
                    "pips": 50.0,
                    "spread_cost_pips": 1.0,
                    "swap_cost_pips": 0.0,
                    "net_pips": 49.0,
                    "gross_pnl_dollars": 500.0,
                    "spread_cost_dollars": 10.0,
                    "swap_cost_dollars": 0.0,
                    "net_pnl_dollars": 490.0,
                    "duration_minutes": 240,
                    "position_size": 10000,
                },
            ],
        }

    def test_persist_trial_creates_trial_with_basic_metrics(
        self, mock_db, basic_trial_info
    ):
        """Test that _persist_trial_result creates a trial record with basic metrics."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(mock_db, "job_123", 1, basic_trial_info)

            # Verify create_trial was called
            mock_service.create_trial.assert_called_once()
            call_kwargs = mock_service.create_trial.call_args[1]

            assert call_kwargs["job_id"] == "job_123"
            assert call_kwargs["trial_index"] == 1
            assert call_kwargs["params"] == {"fast": 10, "slow": 50}

            # Verify metrics
            metrics = call_kwargs["metrics"]
            assert metrics.sharpe == 1.5
            assert metrics.max_drawdown == 0.05
            assert metrics.hit_rate == 0.55
            assert metrics.trade_count == 100

            # No extended metrics for basic trial
            assert call_kwargs["extended_metrics"] is None

    def test_persist_trial_includes_extended_metrics_when_present(
        self, mock_db, trial_info_with_extended_metrics
    ):
        """Test that extended metrics are included when pip/dollar data is present."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(
                mock_db, "job_123", 1, trial_info_with_extended_metrics
            )

            call_kwargs = mock_service.create_trial.call_args[1]

            # Verify extended metrics are included
            extended = call_kwargs["extended_metrics"]
            assert extended is not None
            assert extended.total_pips == 250.5
            assert extended.net_pips == 230.0
            assert extended.gross_pnl_dollars == 2500.0
            assert extended.net_pnl_dollars == 2300.0

    def test_persist_trial_bulk_inserts_signals(
        self, mock_db, trial_info_with_signals_and_trades
    ):
        """Test that signals are bulk inserted when present."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(
                mock_db, "job_123", 1, trial_info_with_signals_and_trades
            )

            # Verify bulk_create_signals was called with correct data
            mock_service.bulk_create_signals.assert_called_once()
            call_args = mock_service.bulk_create_signals.call_args
            assert call_args[0][1] == "trial_123"  # trial_id
            assert len(call_args[0][2]) == 2  # 2 signals

    def test_persist_trial_bulk_inserts_trades(
        self, mock_db, trial_info_with_signals_and_trades
    ):
        """Test that trades are bulk inserted when present."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(
                mock_db, "job_123", 1, trial_info_with_signals_and_trades
            )

            # Verify bulk_create_trades was called with correct data
            mock_service.bulk_create_trades.assert_called_once()
            call_args = mock_service.bulk_create_trades.call_args
            assert call_args[0][1] == "trial_123"  # trial_id
            assert len(call_args[0][2]) == 1  # 1 trade

    def test_persist_trial_skips_empty_signals_and_trades(
        self, mock_db, basic_trial_info
    ):
        """Test that bulk insert is not called when signals/trades are empty."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(mock_db, "job_123", 1, basic_trial_info)

            # Verify bulk insert methods were NOT called
            mock_service.bulk_create_signals.assert_not_called()
            mock_service.bulk_create_trades.assert_not_called()

    def test_persist_trial_handles_missing_metrics_gracefully(self, mock_db):
        """Test that missing metrics default to sensible values."""
        trial_info = {
            "params": {"fast": 10},
            "metrics": {},  # Empty metrics
        }

        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(mock_db, "job_123", 1, trial_info)

            call_kwargs = mock_service.create_trial.call_args[1]
            metrics = call_kwargs["metrics"]

            # Verify defaults are applied
            assert metrics.sharpe == 0.0
            assert metrics.max_drawdown == 0.0
            assert metrics.hit_rate == 0.0
            assert metrics.trade_count == 0

    def test_persist_trial_handles_exception_without_raising(
        self, mock_db, basic_trial_info
    ):
        """Test that exceptions during persistence are logged but not re-raised."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_service.create_trial.side_effect = Exception("Database error")

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            # Should not raise
            _persist_trial_result(mock_db, "job_123", 1, basic_trial_info)

            # Verify create_trial was attempted
            mock_service.create_trial.assert_called_once()

    def test_persist_trial_handles_missing_params_key(self, mock_db):
        """Test handling of trial_info without params key."""
        trial_info = {
            "metrics": {
                "sharpe": 1.0,
                "max_drawdown": 0.05,
                "hit_rate": 0.5,
                "n_trades": 50,
            },
        }

        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            _persist_trial_result(mock_db, "job_123", 1, trial_info)

            call_kwargs = mock_service.create_trial.call_args[1]
            assert call_kwargs["params"] == {}


class TestRunOptimization:
    """Unit tests for run_optimization async function."""

    @pytest.mark.asyncio
    async def test_prevents_duplicate_execution(self):
        """Test that duplicate job execution is prevented."""
        from src.agentfx.api.workers.optimization_runner import (
            run_optimization,
            _running_jobs,
        )

        job_id = "test_job_duplicate"
        _running_jobs.add(job_id)

        try:
            with patch(
                "src.agentfx.api.workers.optimization_runner._execute_optimization"
            ) as mock_execute:
                await run_optimization(job_id)

                # _execute_optimization should NOT be called due to duplicate check
                mock_execute.assert_not_called()
        finally:
            _running_jobs.discard(job_id)

    @pytest.mark.asyncio
    async def test_adds_and_removes_from_running_jobs(self):
        """Test that job is tracked in _running_jobs during execution."""
        from src.agentfx.api.workers.optimization_runner import (
            run_optimization,
            _running_jobs,
        )

        job_id = "test_job_tracking"
        was_in_running = False

        def mock_execute(jid):
            nonlocal was_in_running
            was_in_running = jid in _running_jobs

        with patch(
            "src.agentfx.api.workers.optimization_runner._execute_optimization",
            side_effect=mock_execute,
        ):
            await run_optimization(job_id)

        # Job should have been in _running_jobs during execution
        assert was_in_running is True
        # Job should be removed after completion
        assert job_id not in _running_jobs

    @pytest.mark.asyncio
    async def test_removes_from_running_jobs_on_completion(self):
        """Test that job is removed from _running_jobs after execution completes."""
        from src.agentfx.api.workers.optimization_runner import (
            run_optimization,
            _running_jobs,
        )

        job_id = "test_job_completion"

        with patch(
            "src.agentfx.api.workers.optimization_runner._execute_optimization",
        ) as mock_execute:
            # Normal completion (no exception)
            mock_execute.return_value = None
            await run_optimization(job_id)

        # Job should be removed after completion
        assert job_id not in _running_jobs


class TestExecuteOptimization:
    """Unit tests for _execute_optimization function."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = MagicMock()
        return session

    @pytest.fixture
    def mock_job(self):
        """Create a mock optimization job."""
        from src.agentfx.api.models.optimization import JobStatus, StrategyType

        job = MagicMock()
        job.id = "job_123"
        job.status = JobStatus.QUEUED
        job.strategy = StrategyType.MA
        job.pair = "EUR_USD"
        job.param_ranges = {
            "fast": {"min": 5, "max": 20},
            "slow": {"min": 30, "max": 60},
        }
        job.data_years = 1
        job.account_balance = 10000.0
        job.position_size = 10000
        job.estimated_iterations = 5
        return job

    def test_skips_if_job_not_found(self):
        """Test that execution is skipped if job is not found."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = None

                from src.agentfx.api.workers.optimization_runner import (
                    _execute_optimization,
                )

                _execute_optimization("nonexistent_job")

                # Should not attempt to start job
                mock_service.start_job.assert_not_called()

    def test_skips_if_job_not_queued(self, mock_job):
        """Test that execution is skipped if job is not in QUEUED status."""
        from src.agentfx.api.models.optimization import JobStatus

        mock_job.status = JobStatus.COMPLETED

        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job

                from src.agentfx.api.workers.optimization_runner import (
                    _execute_optimization,
                )

                _execute_optimization(mock_job.id)

                # Should not attempt to start job
                mock_service.start_job.assert_not_called()

    def test_marks_job_as_running_before_optimization(self, mock_job):
        """Test that job is marked as RUNNING before optimization starts."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job

                with patch(
                    "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
                ) as mock_agent_class:
                    mock_agent = MagicMock()
                    mock_result = MagicMock()
                    mock_result.outputs = {"best_trial": {"sharpe": 1.5}}
                    mock_agent.run.return_value = mock_result
                    mock_agent_class.return_value = mock_agent

                    from src.agentfx.api.workers.optimization_runner import (
                        _execute_optimization,
                    )

                    _execute_optimization(mock_job.id)

                    # Verify start_job was called
                    mock_service.start_job.assert_called_once_with(
                        mock_session, mock_job
                    )

    def test_marks_job_as_failed_on_exception(self, mock_job):
        """Test that job is marked as FAILED when optimization raises exception."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job

                with patch(
                    "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
                ) as mock_agent_class:
                    mock_agent = MagicMock()
                    mock_agent.run.side_effect = RuntimeError("Optimization crashed")
                    mock_agent_class.return_value = mock_agent

                    from src.agentfx.api.workers.optimization_runner import (
                        _execute_optimization,
                    )

                    _execute_optimization(mock_job.id)

                    # Verify fail_job was called
                    mock_service.fail_job.assert_called()
                    fail_call = mock_service.fail_job.call_args
                    assert "Optimization crashed" in fail_call[1]["error_message"]

    def test_completes_job_with_best_sharpe(self, mock_job):
        """Test that job is completed with best_sharpe from results."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job
                mock_top_trial = MagicMock()
                mock_top_trial.id = "best_trial_id"
                mock_service.get_top_trials.return_value = [mock_top_trial]

                with patch(
                    "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
                ) as mock_agent_class:
                    mock_agent = MagicMock()
                    mock_result = MagicMock()
                    mock_result.outputs = {"best_trial": {"sharpe": 2.5}}
                    mock_agent.run.return_value = mock_result
                    mock_agent_class.return_value = mock_agent

                    from src.agentfx.api.workers.optimization_runner import (
                        _execute_optimization,
                    )

                    _execute_optimization(mock_job.id)

                    # Verify complete_job was called with correct best_sharpe
                    mock_service.complete_job.assert_called_once()
                    call_kwargs = mock_service.complete_job.call_args[1]
                    assert call_kwargs["best_sharpe"] == 2.5
                    assert call_kwargs["best_trial_id"] == "best_trial_id"

    def test_closes_session_on_success(self, mock_job):
        """Test that database session is closed after successful execution."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job

                with patch(
                    "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
                ) as mock_agent_class:
                    mock_agent = MagicMock()
                    mock_result = MagicMock()
                    mock_result.outputs = {"best_trial": {"sharpe": 1.0}}
                    mock_agent.run.return_value = mock_result
                    mock_agent_class.return_value = mock_agent

                    from src.agentfx.api.workers.optimization_runner import (
                        _execute_optimization,
                    )

                    _execute_optimization(mock_job.id)

                    # Verify session was closed
                    mock_session.close.assert_called_once()

    def test_closes_session_on_exception(self, mock_job):
        """Test that database session is closed even when exception occurs."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_job.return_value = mock_job

                with patch(
                    "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
                ) as mock_agent_class:
                    mock_agent = MagicMock()
                    mock_agent.run.side_effect = Exception("Crash")
                    mock_agent_class.return_value = mock_agent

                    from src.agentfx.api.workers.optimization_runner import (
                        _execute_optimization,
                    )

                    _execute_optimization(mock_job.id)

                    # Verify session was closed despite exception
                    mock_session.close.assert_called_once()


class TestRecoverQueuedJobs:
    """Unit tests for recover_queued_jobs function."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_jobs_to_recover(self):
        """Test that 0 is returned when no queued or running jobs exist."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_jobs.return_value = ([], 0)

                from src.agentfx.api.workers.optimization_runner import (
                    recover_queued_jobs,
                )

                count = await recover_queued_jobs()

                assert count == 0

    @pytest.mark.asyncio
    async def test_schedules_queued_jobs(self):
        """Test that queued jobs are scheduled for processing."""
        mock_job1 = MagicMock()
        mock_job1.id = "queued_job_1"
        mock_job2 = MagicMock()
        mock_job2.id = "queued_job_2"

        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                # First call returns queued jobs, second returns no running jobs
                mock_service.get_jobs.side_effect = [
                    ([mock_job1, mock_job2], 2),  # QUEUED
                    ([], 0),  # RUNNING
                ]

                with patch(
                    "src.agentfx.api.workers.optimization_runner.run_optimization",
                    new_callable=AsyncMock,
                ):
                    from src.agentfx.api.workers.optimization_runner import (
                        recover_queued_jobs,
                    )

                    count = await recover_queued_jobs()

                    assert count == 2

    @pytest.mark.asyncio
    async def test_resets_running_jobs_to_queued(self):
        """Test that orphaned RUNNING jobs are reset to QUEUED."""
        from src.agentfx.api.models.optimization import JobStatus

        mock_running_job = MagicMock()
        mock_running_job.id = "orphaned_running"

        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                # First call: no queued jobs, second call: one running job
                mock_service.get_jobs.side_effect = [
                    ([], 0),  # QUEUED
                    ([mock_running_job], 1),  # RUNNING
                ]

                with patch(
                    "src.agentfx.api.workers.optimization_runner.run_optimization",
                    new_callable=AsyncMock,
                ):
                    from src.agentfx.api.workers.optimization_runner import (
                        recover_queued_jobs,
                    )

                    await recover_queued_jobs()

                    # Verify job was reset to QUEUED
                    mock_service.update_job.assert_called_once()
                    update_call = mock_service.update_job.call_args
                    assert update_call[1]["status"] == JobStatus.QUEUED
                    assert update_call[1]["progress"] == 0

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        """Test that exceptions during recovery don't crash the system."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_jobs.side_effect = Exception(
                    "Database connection failed"
                )

                from src.agentfx.api.workers.optimization_runner import (
                    recover_queued_jobs,
                )

                # Should not raise
                count = await recover_queued_jobs()

                assert count == 0

    @pytest.mark.asyncio
    async def test_closes_session_after_recovery(self):
        """Test that database session is closed after recovery."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.SessionLocal"
        ) as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value = mock_session

            with patch(
                "src.agentfx.api.workers.optimization_runner.optimization_service"
            ) as mock_service:
                mock_service.get_jobs.return_value = ([], 0)

                from src.agentfx.api.workers.optimization_runner import (
                    recover_queued_jobs,
                )

                await recover_queued_jobs()

                mock_session.close.assert_called_once()


class TestMetricsExtraction:
    """Unit tests for metrics extraction edge cases."""

    def test_handles_none_optional_metrics(self):
        """Test that optional metrics (avg_win, avg_loss, etc.) can be None."""
        trial_info = {
            "params": {"fast": 10},
            "metrics": {
                "sharpe": 1.0,
                "max_drawdown": 0.05,
                "hit_rate": 0.5,
                "n_trades": 50,
                "total_return": 0.10,
                "profit_factor": 1.5,
                # Optional fields not present
            },
        }

        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            mock_db = MagicMock()
            _persist_trial_result(mock_db, "job_123", 1, trial_info)

            call_kwargs = mock_service.create_trial.call_args[1]
            metrics = call_kwargs["metrics"]

            # Optional fields should be None
            assert metrics.avg_win is None
            assert metrics.avg_loss is None
            assert metrics.max_consecutive_losses is None
            assert metrics.avg_trade_duration is None

    def test_handles_zero_values_correctly(self):
        """Test that zero values are preserved, not treated as missing."""
        trial_info = {
            "params": {"fast": 10},
            "metrics": {
                "sharpe": 0.0,  # Zero is valid
                "max_drawdown": 0.0,
                "hit_rate": 0.0,
                "n_trades": 0,
                "total_return": 0.0,
                "profit_factor": 0.0,
            },
        }

        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            mock_db = MagicMock()
            _persist_trial_result(mock_db, "job_123", 1, trial_info)

            call_kwargs = mock_service.create_trial.call_args[1]
            metrics = call_kwargs["metrics"]

            # Zero values should be preserved
            assert metrics.sharpe == 0.0
            assert metrics.max_drawdown == 0.0
            assert metrics.hit_rate == 0.0
            assert metrics.trade_count == 0

    def test_handles_negative_sharpe(self):
        """Test that negative Sharpe ratios are handled correctly."""
        trial_info = {
            "params": {"fast": 10},
            "metrics": {
                "sharpe": -1.5,  # Negative is valid
                "max_drawdown": 0.15,
                "hit_rate": 0.35,
                "n_trades": 50,
                "total_return": -0.10,
                "profit_factor": 0.8,
            },
        }

        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service"
        ) as mock_service:
            mock_trial = MagicMock()
            mock_trial.id = "trial_123"
            mock_service.create_trial.return_value = mock_trial

            from src.agentfx.api.workers.optimization_runner import (
                _persist_trial_result,
            )

            mock_db = MagicMock()
            _persist_trial_result(mock_db, "job_123", 1, trial_info)

            call_kwargs = mock_service.create_trial.call_args[1]
            metrics = call_kwargs["metrics"]

            assert metrics.sharpe == -1.5
            assert metrics.total_return == -0.10
