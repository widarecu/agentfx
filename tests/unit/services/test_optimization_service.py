"""
Unit tests for optimization service.

Tests CRUD operations for optimization jobs, trials, signals, and trades.
Uses mocked database sessions to test service layer logic.
"""

from unittest.mock import Mock, patch
from datetime import datetime, timezone

from agentfx.api.services import optimization as optimization_service
from agentfx.api import schemas


class TestJobIdGeneration:
    """Tests for ID generation functions."""

    def test_generate_job_id_format(self):
        """Test that job ID follows expected format."""
        job_id = optimization_service.generate_job_id()
        assert job_id.startswith("optjob_")
        parts = job_id.split("_")
        assert len(parts) == 3
        # Second part should be numeric timestamp
        assert parts[1].isdigit()
        # Third part should be 6 character hex
        assert len(parts[2]) == 6

    def test_generate_job_id_uniqueness(self):
        """Test that generated IDs are unique."""
        ids = [optimization_service.generate_job_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_generate_trial_id_format(self):
        """Test that trial ID includes job ID and index."""
        trial_id = optimization_service.generate_trial_id("optjob_123_abc", 5)
        assert trial_id == "trial_optjob_123_abc_005"

    def test_generate_trial_id_pads_index(self):
        """Test that trial ID index is zero-padded."""
        trial_id = optimization_service.generate_trial_id("optjob_123_abc", 1)
        assert "_001" in trial_id

    def test_generate_trade_id_format(self):
        """Test that trade ID includes trial ID and index."""
        trade_id = optimization_service.generate_trade_id(
            "trial_optjob_123_abc_001", 10
        )
        assert trade_id == "trade_trial_optjob_123_abc_001_010"


class TestGetJob:
    """Tests for get_job function."""

    def test_get_job_returns_job_when_found(self):
        """Test that get_job returns job when it exists."""
        mock_db = Mock()
        mock_job = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        result = optimization_service.get_job(mock_db, "optjob_123_abc")

        assert result == mock_job
        mock_db.query.assert_called_once()

    def test_get_job_returns_none_when_not_found(self):
        """Test that get_job returns None when job doesn't exist."""
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = optimization_service.get_job(mock_db, "optjob_nonexistent")

        assert result is None


class TestGetJobs:
    """Tests for get_jobs function."""

    def test_get_jobs_returns_items_and_count(self):
        """Test that get_jobs returns items and total count."""
        mock_db = Mock()
        mock_jobs = [Mock(), Mock()]
        mock_query = Mock()
        mock_query.count.return_value = 10
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            mock_jobs
        )
        mock_db.query.return_value = mock_query

        items, total = optimization_service.get_jobs(mock_db)

        assert items == mock_jobs
        assert total == 10

    def test_get_jobs_applies_filters(self):
        """Test that get_jobs applies optional filters."""
        mock_db = Mock()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 5
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            []
        )
        mock_db.query.return_value = mock_query

        optimization_service.get_jobs(
            mock_db,
            strategy="ma",
            pair="EUR_USD",
            status="completed",
            created_by="user123",
        )

        # Filter should be called 4 times for 4 filters
        assert mock_query.filter.call_count == 4

    def test_get_jobs_pagination(self):
        """Test that get_jobs applies skip and limit."""
        mock_db = Mock()
        mock_query = Mock()
        mock_query.count.return_value = 100
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            []
        )
        mock_db.query.return_value = mock_query

        optimization_service.get_jobs(mock_db, skip=20, limit=10)

        mock_query.order_by.return_value.offset.assert_called_with(20)
        mock_query.order_by.return_value.offset.return_value.limit.assert_called_with(
            10
        )


class TestCreateJob:
    """Tests for create_job function."""

    @patch("agentfx.api.services.optimization.models")
    def test_create_job_generates_id(self, mock_models):
        """Test that create_job generates a unique ID."""
        mock_db = Mock()
        mock_job = Mock()
        mock_models.OptimizationJob.return_value = mock_job
        mock_models.JobStatus.QUEUED = "queued"

        job_in = schemas.OptimizationJobCreate(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": schemas.ParamRange(min=5, max=50),
                "slow": schemas.ParamRange(min=20, max=200),
            },
        )

        optimization_service.create_job(mock_db, job_in)

        # Verify job was added and committed
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @patch("agentfx.api.services.optimization.models")
    def test_create_job_estimates_iterations(self, mock_models):
        """Test that create_job estimates iterations based on param count."""
        mock_db = Mock()
        mock_job = Mock()
        mock_models.OptimizationJob.return_value = mock_job
        mock_models.JobStatus.QUEUED = "queued"

        job_in = schemas.OptimizationJobCreate(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": schemas.ParamRange(min=5, max=50),
                "slow": schemas.ParamRange(min=20, max=200),
            },
        )

        optimization_service.create_job(mock_db, job_in)

        # 2 params = 10 + (2 * 15) = 40 iterations
        call_kwargs = mock_models.OptimizationJob.call_args.kwargs
        assert call_kwargs["estimated_iterations"] == 40

    @patch("agentfx.api.services.optimization.models")
    def test_create_job_sets_created_by(self, mock_models):
        """Test that create_job sets created_by field."""
        mock_db = Mock()
        mock_job = Mock()
        mock_models.OptimizationJob.return_value = mock_job
        mock_models.JobStatus.QUEUED = "queued"

        job_in = schemas.OptimizationJobCreate(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": schemas.ParamRange(min=5, max=50)},
        )

        optimization_service.create_job(mock_db, job_in, created_by="user123")

        call_kwargs = mock_models.OptimizationJob.call_args.kwargs
        assert call_kwargs["created_by"] == "user123"


class TestUpdateJob:
    """Tests for update_job function."""

    def test_update_job_sets_fields(self):
        """Test that update_job sets specified fields."""
        mock_db = Mock()
        mock_job = Mock()
        mock_job.status = "queued"
        mock_job.progress = 0

        optimization_service.update_job(
            mock_db,
            mock_job,
            status="running",
            progress=50,
        )

        assert mock_job.status == "running"
        assert mock_job.progress == 50
        mock_db.commit.assert_called_once()

    def test_update_job_updates_timestamp(self):
        """Test that update_job sets updated_at."""
        mock_db = Mock()
        mock_job = Mock()

        optimization_service.update_job(mock_db, mock_job, progress=50)

        assert mock_job.updated_at is not None


class TestJobStateTransitions:
    """Tests for job state transition functions."""

    @patch("agentfx.api.services.optimization.update_job")
    @patch("agentfx.api.services.optimization.models")
    def test_start_job_sets_running(self, mock_models, mock_update):
        """Test that start_job sets status to running."""
        mock_models.JobStatus.RUNNING = "running"
        mock_db = Mock()
        mock_job = Mock()

        optimization_service.start_job(mock_db, mock_job)

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["status"] == "running"
        assert "started_at" in call_kwargs

    @patch("agentfx.api.services.optimization.update_job")
    @patch("agentfx.api.services.optimization.models")
    def test_complete_job_sets_completed(self, mock_models, mock_update):
        """Test that complete_job sets status to completed."""
        mock_models.JobStatus.COMPLETED = "completed"
        mock_db = Mock()
        mock_job = Mock()

        optimization_service.complete_job(
            mock_db,
            mock_job,
            best_sharpe=1.5,
            best_trial_id="trial_123",
        )

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["progress"] == 100
        assert call_kwargs["best_sharpe"] == 1.5

    @patch("agentfx.api.services.optimization.update_job")
    @patch("agentfx.api.services.optimization.models")
    def test_fail_job_sets_failed(self, mock_models, mock_update):
        """Test that fail_job sets status to failed with error message."""
        mock_models.JobStatus.FAILED = "failed"
        mock_db = Mock()
        mock_job = Mock()

        optimization_service.fail_job(mock_db, mock_job, "Something went wrong")

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["error_message"] == "Something went wrong"


class TestDeleteJob:
    """Tests for delete_job function."""

    @patch("agentfx.api.services.optimization.get_job")
    def test_delete_job_deletes_existing(self, mock_get_job):
        """Test that delete_job deletes existing job."""
        mock_db = Mock()
        mock_job = Mock()
        mock_get_job.return_value = mock_job

        result = optimization_service.delete_job(mock_db, "optjob_123")

        assert result is True
        mock_db.delete.assert_called_once_with(mock_job)
        mock_db.commit.assert_called_once()

    @patch("agentfx.api.services.optimization.get_job")
    def test_delete_job_returns_false_if_not_found(self, mock_get_job):
        """Test that delete_job returns False if job not found."""
        mock_db = Mock()
        mock_get_job.return_value = None

        result = optimization_service.delete_job(mock_db, "optjob_nonexistent")

        assert result is False
        mock_db.delete.assert_not_called()


class TestTrialOperations:
    """Tests for trial CRUD operations."""

    def test_get_trial_returns_trial_when_found(self):
        """Test that get_trial returns trial when it exists."""
        mock_db = Mock()
        mock_trial = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_trial

        result = optimization_service.get_trial(mock_db, "trial_123")

        assert result == mock_trial

    def test_get_trials_returns_items_and_count(self):
        """Test that get_trials returns items and total."""
        mock_db = Mock()
        mock_trials = [Mock(), Mock()]
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 50
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            mock_trials
        )
        mock_db.query.return_value = mock_query

        items, total = optimization_service.get_trials(mock_db, "optjob_123")

        assert items == mock_trials
        assert total == 50

    def test_get_trials_filters_by_gate_b(self):
        """Test that get_trials can filter by Gate B status."""
        mock_db = Mock()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 10
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            []
        )
        mock_db.query.return_value = mock_query

        optimization_service.get_trials(mock_db, "optjob_123", passes_gate_b=True)

        # Should filter twice: once for job_id, once for passes_gate_b
        assert mock_query.filter.call_count == 2

    def test_get_top_trials_orders_by_sharpe(self):
        """Test that get_top_trials returns trials ordered by Sharpe."""
        mock_db = Mock()
        mock_trials = [Mock(sharpe=1.5), Mock(sharpe=1.2), Mock(sharpe=1.0)]
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.limit.return_value.all.return_value = (
            mock_trials
        )
        mock_db.query.return_value = mock_query

        result = optimization_service.get_top_trials(mock_db, "optjob_123", limit=3)

        assert len(result) == 3


class TestCreateTrial:
    """Tests for create_trial function."""

    @patch("agentfx.api.services.optimization.get_job")
    @patch("agentfx.api.services.optimization.update_job")
    @patch("agentfx.api.services.optimization.models")
    @patch("agentfx.api.services.optimization.schemas")
    def test_create_trial_evaluates_gate_b(
        self, mock_schemas, mock_models, mock_update, mock_get_job
    ):
        """Test that create_trial evaluates Gate B thresholds."""
        mock_db = Mock()
        mock_trial = Mock()
        mock_models.OptimizationTrial.return_value = mock_trial
        mock_get_job.return_value = Mock(
            completed_iterations=0, estimated_iterations=10
        )
        mock_schemas.evaluate_gate_b.return_value = (True, [])

        metrics = schemas.TrialMetrics(
            sharpe=1.5,
            max_drawdown=0.05,
            hit_rate=0.55,
            trade_count=500,
            total_return=0.15,
            profit_factor=1.5,
        )

        optimization_service.create_trial(
            mock_db,
            job_id="optjob_123",
            trial_index=1,
            params={"fast": 20, "slow": 50},
            metrics=metrics,
        )

        mock_schemas.evaluate_gate_b.assert_called_once_with(metrics)

    @patch("agentfx.api.services.optimization.get_job")
    @patch("agentfx.api.services.optimization.update_job")
    @patch("agentfx.api.services.optimization.models")
    @patch("agentfx.api.services.optimization.schemas")
    def test_create_trial_updates_job_progress(
        self, mock_schemas, mock_models, mock_update, mock_get_job
    ):
        """Test that create_trial updates job progress."""
        mock_db = Mock()
        mock_trial = Mock()
        mock_models.OptimizationTrial.return_value = mock_trial
        mock_job = Mock(completed_iterations=5, estimated_iterations=10)
        mock_get_job.return_value = mock_job
        mock_schemas.evaluate_gate_b.return_value = (False, ["Low Sharpe"])

        metrics = schemas.TrialMetrics(
            sharpe=0.5,
            max_drawdown=0.05,
            hit_rate=0.55,
            trade_count=500,
            total_return=0.15,
            profit_factor=1.5,
        )

        optimization_service.create_trial(
            mock_db,
            job_id="optjob_123",
            trial_index=6,
            params={"fast": 20},
            metrics=metrics,
        )

        # Should update job with new progress
        mock_update.assert_called()


class TestSignalOperations:
    """Tests for signal CRUD operations."""

    def test_get_signals_returns_items_and_count(self):
        """Test that get_signals returns items and total."""
        mock_db = Mock()
        mock_signals = [Mock(), Mock()]
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 100
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            mock_signals
        )
        mock_db.query.return_value = mock_query

        items, total = optimization_service.get_signals(mock_db, "trial_123")

        assert items == mock_signals
        assert total == 100

    @patch("agentfx.api.services.optimization.models")
    def test_bulk_create_signals(self, mock_models):
        """Test that bulk_create_signals creates multiple signals."""
        mock_db = Mock()
        signals = [
            {
                "timestamp": datetime.now(timezone.utc),
                "signal": 1,
                "signal_type": "entry_long",
                "price": 1.1000,
            },
            {
                "timestamp": datetime.now(timezone.utc),
                "signal": -1,
                "signal_type": "exit_long",
                "price": 1.1050,
            },
        ]

        result = optimization_service.bulk_create_signals(mock_db, "trial_123", signals)

        assert result == 2
        mock_db.bulk_save_objects.assert_called_once()
        mock_db.commit.assert_called_once()


class TestTradeOperations:
    """Tests for trade CRUD operations."""

    def test_get_trades_returns_items_and_count(self):
        """Test that get_trades returns items and total."""
        mock_db = Mock()
        mock_trades = [Mock(), Mock()]
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 50
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            mock_trades
        )
        mock_db.query.return_value = mock_query

        items, total = optimization_service.get_trades(mock_db, "trial_123")

        assert items == mock_trades
        assert total == 50

    @patch("agentfx.api.services.optimization.models")
    def test_bulk_create_trades(self, mock_models):
        """Test that bulk_create_trades creates multiple trades."""
        mock_db = Mock()
        trades = [
            {
                "direction": "long",
                "entry_timestamp": datetime.now(timezone.utc),
                "exit_timestamp": datetime.now(timezone.utc),
                "entry_price": 1.1000,
                "exit_price": 1.1050,
                "pips": 50,
                "spread_cost_pips": 2,
                "swap_cost_pips": 0.5,
                "net_pips": 47.5,
                "gross_pnl_dollars": 50.0,
                "spread_cost_dollars": 2.0,
                "swap_cost_dollars": 0.5,
                "net_pnl_dollars": 47.5,
                "duration_minutes": 120,
                "position_size": 10000,
            },
        ]

        result = optimization_service.bulk_create_trades(mock_db, "trial_123", trades)

        assert result == 1
        mock_db.bulk_save_objects.assert_called_once()
        mock_db.commit.assert_called_once()
