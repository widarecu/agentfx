"""
Unit tests for optimization API router.

Tests REST endpoints for optimization jobs, trials, signals, and trades.
Uses FastAPI TestClient with mocked dependencies.
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from agentfx.api import models, schemas
from agentfx.api.routers import optimization as optimization_router

# Fixtures for mocking


@pytest.fixture
def mock_current_user():
    """Create a mock current user for authentication."""
    user = Mock(spec=models.User)
    user.id = "user_123"
    user.email = "test@example.com"
    user.role = models.UserRole.TRADER
    return user


@pytest.fixture
def mock_viewer_user():
    """Create a mock viewer user."""
    user = Mock(spec=models.User)
    user.id = "viewer_123"
    user.email = "viewer@example.com"
    user.role = models.UserRole.VIEWER
    return user


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user."""
    user = Mock(spec=models.User)
    user.id = "admin_123"
    user.email = "admin@example.com"
    user.role = models.UserRole.ADMIN
    return user


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return Mock()


@pytest.fixture
def sample_job():
    """Create a sample optimization job."""
    job = Mock(spec=models.OptimizationJob)
    job.id = "optjob_123_abc"
    job.strategy = "ma"
    job.pair = "EUR_USD"
    job.status = models.JobStatus.COMPLETED
    job.progress = 100
    job.estimated_iterations = 40
    job.completed_iterations = 40
    job.best_sharpe = 1.5
    job.best_trial_id = "trial_optjob_123_abc_001"
    job.account_balance = 10000.0
    job.position_size = 10000
    job.data_years = 5
    job.param_ranges = {"fast": {"min": 5, "max": 50}, "slow": {"min": 20, "max": 200}}
    job.data_range_start = None
    job.data_range_end = None
    job.error_message = None
    job.created_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    job.started_at = datetime(2024, 1, 15, 10, 0, 1, tzinfo=UTC)
    job.completed_at = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
    job.created_by = "user_123"
    return job


@pytest.fixture
def sample_trial():
    """Create a sample optimization trial."""
    trial = Mock(spec=models.OptimizationTrial)
    trial.id = "trial_optjob_123_abc_001"
    trial.job_id = "optjob_123_abc"
    trial.params = {"fast": 20, "slow": 50}
    trial.sharpe = 1.5
    trial.max_drawdown = 0.05
    trial.hit_rate = 0.55
    trial.trade_count = 500
    trial.total_return = 0.15
    trial.profit_factor = 1.5
    trial.avg_win = 25.0
    trial.avg_loss = 15.0
    trial.max_consecutive_losses = 5
    trial.avg_trade_duration = 120.0
    trial.total_pips = 500.0
    trial.net_pips = 480.0
    trial.gross_pnl_dollars = 500.0
    trial.net_pnl_dollars = 480.0
    trial.passes_gate_b = True
    trial.gate_b_failures = None
    trial.created_at = datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC)
    return trial


class TestListOptimizationJobs:
    """Tests for GET /optimization-jobs endpoint."""

    def test_list_jobs_returns_paginated_results(
        self, mock_db, mock_current_user, sample_job
    ):
        """Test that list endpoint returns paginated results."""
        with (
            patch(
                "agentfx.api.routers.optimization.optimization_service"
            ) as mock_service,
            patch("agentfx.api.routers.optimization.get_db", return_value=mock_db),
            patch(
                "agentfx.api.routers.optimization.user_service.get_current_active_user",
                return_value=mock_current_user,
            ),
        ):
            mock_service.get_jobs.return_value = ([sample_job], 1)

            result = optimization_router.list_optimization_jobs(
                page=1,
                page_size=10,
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["total"] == 1
            assert result["page"] == 1
            assert result["page_size"] == 10
            mock_service.get_jobs.assert_called_once()

    def test_list_jobs_applies_filters(self, mock_db, mock_current_user):
        """Test that list endpoint applies optional filters."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_jobs.return_value = ([], 0)

            optimization_router.list_optimization_jobs(
                page=1,
                page_size=10,
                strategy="ma",
                pair="EUR_USD",
                status="completed",
                db=mock_db,
                current_user=mock_current_user,
            )

            mock_service.get_jobs.assert_called_once_with(
                mock_db,
                skip=0,
                limit=10,
                strategy="ma",
                pair="EUR_USD",
                status="completed",
                created_by=mock_current_user.id,
            )


class TestCreateOptimizationJob:
    """Tests for POST /optimization-jobs endpoint."""

    def test_create_job_returns_202_accepted(self, mock_db, mock_current_user):
        """Test that create job returns 202 with job ID."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_job = Mock()
            mock_job.id = "optjob_new_123"
            mock_job.status = models.JobStatus.QUEUED
            mock_job.estimated_iterations = 40
            mock_job.created_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_service.create_job.return_value = mock_job

            job_in = schemas.OptimizationJobCreate(
                strategy="ma",
                pair="EUR_USD",
                param_ranges={
                    "fast": schemas.ParamRange(min=5, max=50),
                    "slow": schemas.ParamRange(min=20, max=200),
                },
            )

            result = optimization_router.create_optimization_job(
                job_in=job_in,
                background_tasks=Mock(),
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["job_id"] == "optjob_new_123"
            assert result["status"] == models.JobStatus.QUEUED
            assert "status_url" in result

    def test_create_job_requires_trader_role(self, mock_db, mock_viewer_user):
        """Test that create job requires TRADER role."""
        job_in = schemas.OptimizationJobCreate(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": schemas.ParamRange(min=5, max=50)},
        )

        with pytest.raises(HTTPException) as exc_info:
            optimization_router.create_optimization_job(
                job_in=job_in,
                background_tasks=Mock(),
                db=mock_db,
                current_user=mock_viewer_user,
            )

        assert exc_info.value.status_code == 403
        assert "TRADER or ADMIN" in str(exc_info.value.detail)

    def test_create_job_allowed_for_admin(self, mock_db, mock_admin_user):
        """Test that create job is allowed for ADMIN role."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_job = Mock()
            mock_job.id = "optjob_admin_123"
            mock_job.status = models.JobStatus.QUEUED
            mock_job.estimated_iterations = 25
            mock_job.created_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_service.create_job.return_value = mock_job

            job_in = schemas.OptimizationJobCreate(
                strategy="ma",
                pair="EUR_USD",
                param_ranges={"fast": schemas.ParamRange(min=5, max=50)},
            )

            result = optimization_router.create_optimization_job(
                job_in=job_in,
                background_tasks=Mock(),
                db=mock_db,
                current_user=mock_admin_user,
            )

            assert result["job_id"] == "optjob_admin_123"


class TestGetOptimizationJob:
    """Tests for GET /optimization-jobs/{job_id} endpoint."""

    def test_get_job_returns_job(self, mock_db, mock_current_user, sample_job):
        """Test that get job returns job details."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_job.return_value = sample_job

            result = optimization_router.get_optimization_job(
                job_id="optjob_123_abc",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result == sample_job
            mock_service.get_job.assert_called_once_with(
                mock_db, job_id="optjob_123_abc"
            )

    def test_get_job_returns_404_if_not_found(self, mock_db, mock_current_user):
        """Test that get job returns 404 if job not found."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.get_optimization_job(
                    job_id="optjob_nonexistent",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 404


class TestDeleteOptimizationJob:
    """Tests for DELETE /optimization-jobs/{job_id} endpoint."""

    def test_delete_own_job_succeeds(self, mock_db, mock_current_user, sample_job):
        """Test that user can delete their own job."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            sample_job.created_by = mock_current_user.id
            mock_service.get_job.return_value = sample_job

            result = optimization_router.delete_optimization_job(
                job_id="optjob_123_abc",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["success"] is True
            mock_service.delete_job.assert_called_once()

    def test_delete_other_user_job_forbidden(
        self, mock_db, mock_current_user, sample_job
    ):
        """Test that user cannot delete another user's job."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            sample_job.created_by = "other_user"
            mock_service.get_job.return_value = sample_job

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.delete_optimization_job(
                    job_id="optjob_123_abc",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 403

    def test_admin_can_delete_any_job(self, mock_db, mock_admin_user, sample_job):
        """Test that admin can delete any job."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            sample_job.created_by = "other_user"
            mock_service.get_job.return_value = sample_job

            result = optimization_router.delete_optimization_job(
                job_id="optjob_123_abc",
                db=mock_db,
                current_user=mock_admin_user,
            )

            assert result["success"] is True


class TestListJobTrials:
    """Tests for GET /optimization-jobs/{job_id}/trials endpoint."""

    def test_list_trials_returns_trials(
        self, mock_db, mock_current_user, sample_job, sample_trial
    ):
        """Test that list trials returns trial list."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_job.return_value = sample_job
            mock_service.get_trials.return_value = ([sample_trial], 1)

            result = optimization_router.list_job_trials(
                job_id="optjob_123_abc",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["total"] == 1
            assert result["job_id"] == "optjob_123_abc"

    def test_list_trials_returns_404_if_job_not_found(self, mock_db, mock_current_user):
        """Test that list trials returns 404 if job not found."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.list_job_trials(
                    job_id="optjob_nonexistent",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 404


class TestGetTopTrials:
    """Tests for GET /optimization-jobs/{job_id}/trials/top endpoint."""

    def test_get_top_trials_returns_top_n(
        self, mock_db, mock_current_user, sample_job, sample_trial
    ):
        """Test that get top trials returns top N trials."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_job.return_value = sample_job
            mock_service.get_top_trials.return_value = [sample_trial]

            result = optimization_router.get_top_trials(
                job_id="optjob_123_abc",
                limit=3,
                db=mock_db,
                current_user=mock_current_user,
            )

            assert len(result) == 1
            mock_service.get_top_trials.assert_called_with(
                mock_db, job_id="optjob_123_abc", limit=3
            )


class TestGetTrial:
    """Tests for GET /optimization-jobs/{job_id}/trials/{trial_id} endpoint."""

    def test_get_trial_returns_trial(
        self, mock_db, mock_current_user, sample_job, sample_trial
    ):
        """Test that get trial returns trial details."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_trial.return_value = sample_trial
            # RBAC ownership check requires loading the parent job
            mock_service.get_job.return_value = sample_job

            result = optimization_router.get_trial(
                job_id="optjob_123_abc",
                trial_id="trial_optjob_123_abc_001",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result == sample_trial

    def test_get_trial_returns_404_if_not_found(self, mock_db, mock_current_user):
        """Test that get trial returns 404 if trial not found."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_trial.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.get_trial(
                    job_id="optjob_123_abc",
                    trial_id="trial_nonexistent",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 404

    def test_get_trial_returns_404_if_wrong_job(
        self, mock_db, mock_current_user, sample_trial
    ):
        """Test that get trial returns 404 if trial belongs to different job."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            sample_trial.job_id = "optjob_different"
            mock_service.get_trial.return_value = sample_trial

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.get_trial(
                    job_id="optjob_123_abc",
                    trial_id="trial_optjob_123_abc_001",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 404


class TestListTrialSignals:
    """Tests for GET /optimization-jobs/{job_id}/trials/{trial_id}/signals endpoint."""

    def test_list_signals_returns_signals(
        self, mock_db, mock_current_user, sample_job, sample_trial
    ):
        """Test that list signals returns signal list."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_trial.return_value = sample_trial
            # RBAC ownership check on the parent job
            mock_service.get_job.return_value = sample_job
            mock_signals = [Mock(), Mock()]
            mock_service.get_signals.return_value = (mock_signals, 2)

            result = optimization_router.list_trial_signals(
                job_id="optjob_123_abc",
                trial_id="trial_optjob_123_abc_001",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["total"] == 2
            assert result["trial_id"] == "trial_optjob_123_abc_001"


class TestListTrialTrades:
    """Tests for GET /optimization-jobs/{job_id}/trials/{trial_id}/trades endpoint."""

    def test_list_trades_returns_trades(
        self, mock_db, mock_current_user, sample_job, sample_trial
    ):
        """Test that list trades returns trade list."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_trial.return_value = sample_trial
            # RBAC ownership check on the parent job
            mock_service.get_job.return_value = sample_job
            mock_trades = [Mock(), Mock()]
            mock_service.get_trades.return_value = (mock_trades, 2)

            result = optimization_router.list_trial_trades(
                job_id="optjob_123_abc",
                trial_id="trial_optjob_123_abc_001",
                db=mock_db,
                current_user=mock_current_user,
            )

            assert result["total"] == 2
            assert result["trial_id"] == "trial_optjob_123_abc_001"

    def test_list_trades_returns_404_if_trial_not_found(
        self, mock_db, mock_current_user
    ):
        """Test that list trades returns 404 if trial not found."""
        with patch(
            "agentfx.api.routers.optimization.optimization_service"
        ) as mock_service:
            mock_service.get_trial.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                optimization_router.list_trial_trades(
                    job_id="optjob_123_abc",
                    trial_id="trial_nonexistent",
                    db=mock_db,
                    current_user=mock_current_user,
                )

            assert exc_info.value.status_code == 404
