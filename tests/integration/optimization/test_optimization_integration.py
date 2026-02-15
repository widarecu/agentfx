"""
Optimization Integration Tests

These tests validate the optimization API endpoints with a real database:
1. Job CRUD operations
2. Trial data persistence
3. Role-based authorization
4. Pagination and filtering

Prerequisites:
- PostgreSQL running with test database
- Test users seeded in database

Run with: ENVIRONMENT=test pytest tests/integration/optimization/test_optimization_integration.py -v
"""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def generate_job_id() -> str:
    """Generate a unique job ID in the expected format."""
    short_uuid = str(uuid4())[:8]
    timestamp = int(time.time() * 1000)
    return f"optjob_{timestamp}_{short_uuid}"


def generate_trial_id() -> str:
    """Generate a unique trial ID in the expected format."""
    short_uuid = str(uuid4())[:8]
    timestamp = int(time.time() * 1000)
    return f"trial_{timestamp}_{short_uuid}"


# Note: Uses test_client_with_db from integration conftest.py which overrides get_db


@pytest.fixture
def test_users(db_session: Session):
    """Ensure test users exist in the database."""
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    users = {
        "viewer": {
            "email": "viewer@example.com",
            "password": "secretpassword",
            "role": "viewer",
        },
        "trader": {
            "email": "trader@example.com",
            "password": "traderpassword",
            "role": "trader",
        },
        "admin": {
            "email": "admin@example.com",
            "password": "adminpassword",
            "role": "admin",
        },
    }

    created_users = {}
    for key, data in users.items():
        user = db_session.query(User).filter_by(email=data["email"]).first()
        if not user:
            user = User(
                email=data["email"],
                hashed_password=get_password_hash(data["password"]),
                role=data["role"],
                is_active=True,
            )
            db_session.add(user)
        created_users[key] = {**data, "user": user}

    db_session.commit()
    return created_users


def get_auth_token(client: TestClient, email: str, password: str) -> str:
    """Get JWT token for user."""
    response = client.post(
        "/api/v1/login",
        data={"username": email, "password": password},
    )
    if response.status_code != 200:
        pytest.skip(f"Could not authenticate user {email}: {response.json()}")
    return response.json()["access_token"]


class TestOptimizationJobCRUD:
    """Test CRUD operations for optimization jobs."""

    def test_create_job_persists_to_db(
        self, test_client_with_db, test_users, db_session
    ):
        """Test that creating a job persists it to the database."""
        from src.agentfx.api.models.optimization import OptimizationJob

        token = get_auth_token(
            test_client_with_db,
            test_users["trader"]["email"],
            test_users["trader"]["password"],
        )

        response = test_client_with_db.post(
            "/api/v1/optimization-jobs",
            json={
                "strategy": "ma",
                "pair": "EUR_USD",
                "param_ranges": {
                    "fast": {"min": 5, "max": 50},
                    "slow": {"min": 20, "max": 200},
                },
                "account_balance": 10000,
                "position_size": 10000,
                "data_years": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 202, f"Failed to create job: {response.json()}"
        data = response.json()
        assert "job_id" in data

        # Verify job was persisted
        job = db_session.query(OptimizationJob).filter_by(id=data["job_id"]).first()
        assert job is not None
        assert job.strategy == "ma"
        assert job.pair == "EUR_USD"
        assert job.status == "queued"

        # Cleanup
        db_session.delete(job)
        db_session.commit()

    def test_get_job_returns_details(self, test_client_with_db, test_users, db_session):
        """Test that getting a job returns its details."""
        from src.agentfx.api.models.optimization import OptimizationJob

        # Create a job directly in DB
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="rsi",
            pair="GBP_USD",
            status="completed",
            progress=100,
            param_ranges={"period": {"min": 7, "max": 21}},
            account_balance=10000,
            position_size=10000,
            data_years=2,
            estimated_iterations=34,
            completed_iterations=34,
            best_sharpe=1.5,
        )
        db_session.add(job)
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        response = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{job.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(job.id)
        assert data["strategy"] == "rsi"
        assert data["pair"] == "GBP_USD"
        assert data["status"] == "completed"

        # Cleanup
        db_session.delete(job)
        db_session.commit()

    def test_list_jobs_pagination(self, test_client_with_db, test_users, db_session):
        """Test that job list supports pagination."""
        from src.agentfx.api.models.optimization import OptimizationJob

        # Create multiple jobs
        jobs = []
        for i in range(15):
            job = OptimizationJob(
                id=generate_job_id(),
                strategy="ma",
                pair="EUR_USD",
                status="queued",
                progress=0,
                param_ranges={"fast": {"min": 5, "max": 50}},
                account_balance=10000,
                position_size=10000,
                data_years=1,
                estimated_iterations=20,
            )
            db_session.add(job)
            jobs.append(job)
            time.sleep(0.001)  # Ensure unique timestamps
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        # Get first page
        response = test_client_with_db.get(
            "/api/v1/optimization-jobs?page=1&page_size=10",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 15
        assert len(data["items"]) == 10
        assert data["page"] == 1

        # Get second page
        response = test_client_with_db.get(
            "/api/v1/optimization-jobs?page=2&page_size=10",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 5
        assert data["page"] == 2

        # Cleanup
        for job in jobs:
            db_session.delete(job)
        db_session.commit()

    def test_delete_job_cascades_to_trials(
        self, test_client_with_db, test_users, db_session
    ):
        """Test that deleting a job also deletes its trials."""
        from src.agentfx.api.models.optimization import (
            OptimizationJob,
            OptimizationTrial,
        )

        # Get the trader user's ID for the created_by field
        trader_user = test_users["trader"]["user"]
        db_session.refresh(trader_user)

        # Create a job with trials (owned by trader)
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="ma",
            pair="EUR_USD",
            status="completed",
            progress=100,
            param_ranges={"fast": {"min": 5, "max": 50}},
            account_balance=10000,
            position_size=10000,
            data_years=1,
            estimated_iterations=10,
            completed_iterations=10,
            created_by=trader_user.id,  # Set owner so trader can delete
        )
        db_session.add(job)
        db_session.commit()

        # Add trials
        for i in range(5):
            trial = OptimizationTrial(
                id=generate_trial_id(),
                job_id=job.id,
                params={"fast": 10 + i, "slow": 50 + i * 10},
                sharpe=0.5 + i * 0.2,
                max_drawdown=0.05,
                hit_rate=0.52,
                trade_count=150,
                total_return=0.08,
                profit_factor=1.5,
                passes_gate_b=i >= 3,
            )
            db_session.add(trial)
            time.sleep(0.001)  # Ensure unique timestamps
        db_session.commit()

        job_id = job.id

        # Verify trials exist
        trial_count = (
            db_session.query(OptimizationTrial).filter_by(job_id=job_id).count()
        )
        assert trial_count == 5

        token = get_auth_token(
            test_client_with_db,
            test_users["trader"]["email"],
            test_users["trader"]["password"],
        )

        # Delete the job
        response = test_client_with_db.delete(
            f"/api/v1/optimization-jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify job is deleted
        job = db_session.query(OptimizationJob).filter_by(id=job_id).first()
        assert job is None

        # Verify trials are also deleted (cascade)
        trial_count = (
            db_session.query(OptimizationTrial).filter_by(job_id=job_id).count()
        )
        assert trial_count == 0


class TestOptimizationTrials:
    """Test trial-related operations."""

    def test_get_trials_for_job(self, test_client_with_db, test_users, db_session):
        """Test retrieving trials for a specific job."""
        from src.agentfx.api.models.optimization import (
            OptimizationJob,
            OptimizationTrial,
        )

        # Create job with trials
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="donchian",
            pair="USD_JPY",
            status="completed",
            progress=100,
            param_ranges={"period": {"min": 10, "max": 55}},
            account_balance=10000,
            position_size=10000,
            data_years=1,
            estimated_iterations=10,
            completed_iterations=10,
        )
        db_session.add(job)
        db_session.commit()

        for i in range(5):
            trial = OptimizationTrial(
                id=generate_trial_id(),
                job_id=job.id,
                params={"period": 20 + i * 5},
                sharpe=0.8 + i * 0.3,
                max_drawdown=0.08 - i * 0.01,
                hit_rate=0.50 + i * 0.02,
                trade_count=100 + i * 50,
                total_return=0.05 + i * 0.02,
                profit_factor=1.3 + i * 0.1,
                passes_gate_b=i >= 2,
            )
            db_session.add(trial)
            time.sleep(0.001)
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        response = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{job.id}/trials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 5

        # Cleanup
        db_session.query(OptimizationTrial).filter_by(job_id=job.id).delete()
        db_session.delete(job)
        db_session.commit()

    def test_get_top_trials(self, test_client_with_db, test_users, db_session):
        """Test retrieving top N trials sorted by Sharpe ratio."""
        from src.agentfx.api.models.optimization import (
            OptimizationJob,
            OptimizationTrial,
        )

        # Create job with trials of varying Sharpe ratios
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="ma",
            pair="AUD_USD",
            status="completed",
            progress=100,
            param_ranges={"fast": {"min": 5, "max": 30}},
            account_balance=10000,
            position_size=10000,
            data_years=1,
            estimated_iterations=10,
            completed_iterations=10,
        )
        db_session.add(job)
        db_session.commit()

        sharpe_values = [0.5, 1.8, 1.2, 0.9, 1.5]  # Unsorted
        for i, sharpe in enumerate(sharpe_values):
            trial = OptimizationTrial(
                id=generate_trial_id(),
                job_id=job.id,
                params={"fast": 10 + i * 2},
                sharpe=sharpe,
                max_drawdown=0.05,
                hit_rate=0.52,
                trade_count=200,
                total_return=0.10,
                profit_factor=1.5,
                passes_gate_b=sharpe >= 1.0,
            )
            db_session.add(trial)
            time.sleep(0.001)
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        response = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{job.id}/trials/top?limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

        # Verify sorted by Sharpe (descending)
        assert data[0]["sharpe"] >= data[1]["sharpe"] >= data[2]["sharpe"]
        assert data[0]["sharpe"] == 1.8  # Highest
        assert data[1]["sharpe"] == 1.5  # Second
        assert data[2]["sharpe"] == 1.2  # Third

        # Cleanup
        db_session.query(OptimizationTrial).filter_by(job_id=job.id).delete()
        db_session.delete(job)
        db_session.commit()


class TestOptimizationAuthorization:
    """Test role-based authorization for optimization endpoints."""

    def test_viewer_cannot_create_job(self, test_client_with_db, test_users):
        """Test that viewer role cannot create optimization jobs."""
        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        response = test_client_with_db.post(
            "/api/v1/optimization-jobs",
            json={
                "strategy": "ma",
                "pair": "EUR_USD",
                "param_ranges": {"fast": {"min": 5, "max": 50}},
                "account_balance": 10000,
                "position_size": 10000,
                "data_years": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    def test_trader_can_create_job(self, test_client_with_db, test_users, db_session):
        """Test that trader role can create optimization jobs."""
        from src.agentfx.api.models.optimization import OptimizationJob

        token = get_auth_token(
            test_client_with_db,
            test_users["trader"]["email"],
            test_users["trader"]["password"],
        )

        response = test_client_with_db.post(
            "/api/v1/optimization-jobs",
            json={
                "strategy": "ma",
                "pair": "EUR_USD",
                "param_ranges": {"fast": {"min": 5, "max": 50}},
                "account_balance": 10000,
                "position_size": 10000,
                "data_years": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # API returns 202 Accepted for async job creation
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data

        # Cleanup
        job = db_session.query(OptimizationJob).filter_by(id=data["job_id"]).first()
        if job:
            db_session.delete(job)
            db_session.commit()

    def test_viewer_cannot_delete_job(
        self, test_client_with_db, test_users, db_session
    ):
        """Test that viewer role cannot delete optimization jobs."""
        from src.agentfx.api.models.optimization import OptimizationJob

        # Create a job
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="ma",
            pair="EUR_USD",
            status="queued",
            progress=0,
            param_ranges={"fast": {"min": 5, "max": 50}},
            account_balance=10000,
            position_size=10000,
            data_years=1,
            estimated_iterations=20,
        )
        db_session.add(job)
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        response = test_client_with_db.delete(
            f"/api/v1/optimization-jobs/{job.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

        # Cleanup
        db_session.delete(job)
        db_session.commit()

    def test_viewer_can_read_jobs(self, test_client_with_db, test_users, db_session):
        """Test that viewer role can read optimization jobs."""
        from src.agentfx.api.models.optimization import OptimizationJob

        # Create a job
        job = OptimizationJob(
            id=generate_job_id(),
            strategy="ma",
            pair="EUR_USD",
            status="completed",
            progress=100,
            param_ranges={"fast": {"min": 5, "max": 50}},
            account_balance=10000,
            position_size=10000,
            data_years=1,
            estimated_iterations=20,
            completed_iterations=20,
        )
        db_session.add(job)
        db_session.commit()

        token = get_auth_token(
            test_client_with_db,
            test_users["viewer"]["email"],
            test_users["viewer"]["password"],
        )

        # List jobs
        response = test_client_with_db.get(
            "/api/v1/optimization-jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Get specific job
        response = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{job.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Cleanup
        db_session.delete(job)
        db_session.commit()

    def test_unauthenticated_cannot_access(self, test_client_with_db):
        """Test that unauthenticated requests are rejected."""
        response = test_client_with_db.get("/api/v1/optimization-jobs")
        assert response.status_code == 401

        response = test_client_with_db.post(
            "/api/v1/optimization-jobs",
            json={
                "strategy": "ma",
                "pair": "EUR_USD",
                "param_ranges": {"fast": {"min": 5, "max": 50}},
            },
        )
        assert response.status_code == 401
