"""
Integration tests for the optimization runner background worker.

Tests cover:
1. Job execution lifecycle (queued -> running -> completed/failed)
2. Recovery of queued jobs on startup
3. Recovery of orphaned running jobs
4. Duplicate job execution prevention

Prerequisites:
- PostgreSQL running with test database
- Test users seeded

Run with: make test-file FILE=tests/integration/workers/test_optimization_runner.py
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agentfx.api.core.config import settings
from src.agentfx.api.models.optimization import OptimizationJob, JobStatus, StrategyType
from src.agentfx.api.services import optimization as optimization_service
from src.agentfx.api.workers.optimization_runner import (
    run_optimization,
    recover_queued_jobs,
    _running_jobs,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    """Create a SQLAlchemy engine for tests."""
    return create_engine(settings.TEST_DATABASE_URL, echo=False)


@pytest.fixture
def session_factory(engine):
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest.fixture
def sample_job_data():
    """Sample job configuration data."""
    return {
        "strategy": StrategyType.MA,
        "pair": "EUR_USD",
        "param_ranges": {
            "fast": {"min": 5, "max": 20, "step": 5},
            "slow": {"min": 20, "max": 50, "step": 10},
        },
        "account_balance": 10000.0,
        "position_size": 10000,
        "data_years": 1,
    }


@pytest.fixture
def create_queued_job(session_factory, sample_job_data):
    """Factory fixture to create a queued optimization job."""
    created_jobs = []

    def _create(status: JobStatus = JobStatus.QUEUED, **overrides):
        session = session_factory()
        try:
            job_data = {**sample_job_data, **overrides}
            job = OptimizationJob(
                id=optimization_service.generate_job_id(),
                strategy=job_data["strategy"],
                pair=job_data["pair"],
                param_ranges=job_data["param_ranges"],
                account_balance=job_data["account_balance"],
                position_size=job_data["position_size"],
                data_years=job_data["data_years"],
                status=status,
                progress=0,
                estimated_iterations=10,
                completed_iterations=0,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            created_jobs.append(job.id)
            return job
        finally:
            session.close()

    yield _create

    # Cleanup created jobs
    session = session_factory()
    try:
        for job_id in created_jobs:
            job = session.query(OptimizationJob).filter_by(id=job_id).first()
            if job:
                session.delete(job)
        session.commit()
    finally:
        session.close()


def get_job_status(session_factory, job_id: str) -> tuple[JobStatus, int, str | None]:
    """Helper to get job status from database."""
    session = session_factory()
    try:
        job = session.query(OptimizationJob).filter_by(id=job_id).first()
        if job:
            return job.status, job.progress, job.error_message
        return None, 0, None
    finally:
        session.close()


class TestJobExecution:
    """Tests for the run_optimization function."""

    @pytest.mark.asyncio
    async def test_job_transitions_to_running(self, session_factory, create_queued_job):
        """Test that a queued job transitions to running status."""
        job = create_queued_job()
        job_id = job.id

        # Mock the BayesianOptimizerAgent to avoid actual optimization
        with patch(
            "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
        ) as mock_agent_class:
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.outputs = {"best_trial": {"sharpe": 1.5}}
            mock_agent.run.return_value = mock_result
            mock_agent_class.return_value = mock_agent

            await run_optimization(job_id)

        # Verify job status
        status, progress, error = get_job_status(session_factory, job_id)
        # Job should be completed since mock returned successfully
        assert status == JobStatus.COMPLETED
        assert progress == 100

    @pytest.mark.asyncio
    async def test_job_completes_with_best_sharpe(
        self, session_factory, create_queued_job
    ):
        """Test that a job completes with best_sharpe set."""
        job = create_queued_job()
        job_id = job.id

        with patch(
            "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
        ) as mock_agent_class:
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.outputs = {"best_trial": {"sharpe": 2.1}}
            mock_agent.run.return_value = mock_result
            mock_agent_class.return_value = mock_agent

            await run_optimization(job_id)

        # Verify job completed with best sharpe
        session = session_factory()
        try:
            job = session.query(OptimizationJob).filter_by(id=job_id).first()
            assert job.status == JobStatus.COMPLETED
            assert job.progress == 100
            assert job.best_sharpe == 2.1
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_job_fails_on_exception(self, session_factory, create_queued_job):
        """Test that a job is marked as failed on exception."""
        job = create_queued_job()
        job_id = job.id

        with patch(
            "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
        ) as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent.run.side_effect = RuntimeError("Optimization failed")
            mock_agent_class.return_value = mock_agent

            await run_optimization(job_id)

        status, progress, error = get_job_status(session_factory, job_id)
        assert status == JobStatus.FAILED
        assert "Optimization failed" in error

    @pytest.mark.asyncio
    async def test_skips_non_queued_job(self, session_factory, create_queued_job):
        """Test that non-queued jobs are skipped."""
        job = create_queued_job(status=JobStatus.COMPLETED)
        job_id = job.id

        with patch(
            "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
        ) as mock_agent_class:
            await run_optimization(job_id)

            # Agent should never be instantiated
            mock_agent_class.assert_not_called()

        status, progress, _ = get_job_status(session_factory, job_id)
        assert status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_prevents_duplicate_execution(
        self, session_factory, create_queued_job
    ):
        """Test that duplicate job execution is prevented."""
        job = create_queued_job()
        job_id = job.id

        # Manually add to running jobs set
        _running_jobs.add(job_id)

        try:
            with patch(
                "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
            ) as mock_agent_class:
                await run_optimization(job_id)

                # Agent should never be instantiated due to duplicate check
                mock_agent_class.assert_not_called()

            # Job should still be queued (not started)
            status, _, _ = get_job_status(session_factory, job_id)
            assert status == JobStatus.QUEUED
        finally:
            _running_jobs.discard(job_id)


class TestJobRecovery:
    """Tests for the recover_queued_jobs function."""

    @pytest.mark.asyncio
    async def test_recovers_queued_jobs(self, session_factory, create_queued_job):
        """Test that queued jobs are recovered on startup."""
        # Create multiple queued jobs
        job1 = create_queued_job()
        job2 = create_queued_job()

        with patch(
            "src.agentfx.api.workers.optimization_runner.run_optimization",
            new_callable=AsyncMock,
        ) as mock_run:
            recovered = await recover_queued_jobs()

        # Should have recovered at least 2 jobs (may be more from other tests)
        assert recovered >= 2
        # Verify both jobs were scheduled
        called_job_ids = {call.args[0] for call in mock_run.call_args_list}
        assert job1.id in called_job_ids
        assert job2.id in called_job_ids

    @pytest.mark.asyncio
    async def test_recovers_orphaned_running_jobs(
        self, session_factory, create_queued_job
    ):
        """Test that orphaned RUNNING jobs are reset and recovered."""
        # Create a job stuck in RUNNING status
        job = create_queued_job(status=JobStatus.RUNNING)
        job_id = job.id

        with patch(
            "src.agentfx.api.workers.optimization_runner.run_optimization",
            new_callable=AsyncMock,
        ) as mock_run:
            recovered = await recover_queued_jobs()

        # Job should have been scheduled
        assert recovered >= 1
        called_job_ids = {call.args[0] for call in mock_run.call_args_list}
        assert job_id in called_job_ids

        # Verify job was reset to QUEUED
        status, progress, _ = get_job_status(session_factory, job_id)
        assert status == JobStatus.QUEUED
        assert progress == 0

    @pytest.mark.asyncio
    async def test_recovery_handles_exception(self, session_factory):
        """Test that recovery handles database exceptions gracefully."""
        with patch(
            "src.agentfx.api.workers.optimization_runner.optimization_service.get_jobs"
        ) as mock_get_jobs:
            mock_get_jobs.side_effect = Exception("Database error")

            # Should not raise, returns 0
            recovered = await recover_queued_jobs()

        assert recovered == 0


class TestProgressCallback:
    """Tests for progress updates during optimization."""

    @pytest.mark.asyncio
    async def test_progress_updates_during_execution(
        self, session_factory, create_queued_job
    ):
        """Test that progress is updated during optimization."""
        job = create_queued_job()
        job_id = job.id

        with patch(
            "src.agentfx.api.workers.optimization_runner.BayesianOptimizerAgent"
        ) as mock_agent_class:
            mock_agent = MagicMock()

            def mock_run(**kwargs):
                # Simulate progress callbacks
                callback = kwargs.get("progress_callback")
                if callback:
                    callback(1, 10, {"metrics": {"sharpe": 0.5}, "params": {}})
                    callback(5, 10, {"metrics": {"sharpe": 1.0}, "params": {}})
                    callback(10, 10, {"metrics": {"sharpe": 1.5}, "params": {}})

                result = MagicMock()
                result.outputs = {"best_trial": {"sharpe": 1.5}}
                return result

            mock_agent.run.side_effect = mock_run
            mock_agent_class.return_value = mock_agent

            # Mock _persist_trial_result to avoid complex setup
            with patch(
                "src.agentfx.api.workers.optimization_runner._persist_trial_result"
            ):
                await run_optimization(job_id)

        # Verify job completed
        status, progress, _ = get_job_status(session_factory, job_id)
        assert status == JobStatus.COMPLETED
        assert progress == 100
