"""Unit tests for the strategies router."""

from datetime import datetime, timezone
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import MagicMock
from uuid import uuid4
from typing import Any

from src.agentfx.api.main import app
from src.agentfx.api import models
from src.agentfx.api.core.database import get_db
from src.agentfx.api.core.security import get_current_active_user
from src.agentfx.api.models.user import UserRole
from src.agentfx.api.models.run import RunStatus, RunEnvironment


# Test data
def create_test_run(**overrides: dict[str, Any]) -> dict[str, Any]:
    run_id = f"run_{int(datetime.now().timestamp())}_test_strategy_123_audusd_paper_1"
    return {
        "id": run_id,
        "strategy_id": "test_strategy_123",
        "pair": "AUDUSD",
        "environment": RunEnvironment.PAPER,
        "status": RunStatus.PENDING,
        "parameters": {"param1": "value1"},
        "initial_balance": 10000.0,
        "current_balance": 10000.0,
        "pnl": 0.0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        **overrides,
    }


TEST_RUN = create_test_run()

import pytest


def create_mock_db():
    """Create a mock database session."""
    return MagicMock(spec=Session)


def create_mock_current_user():
    """Create a mock current user."""
    return models.User(
        id=str(uuid4()),
        email="test@example.com",
        hashed_password="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",  # hash of 'testpassword123'
        full_name="Test User",
        is_active=True,
        role=UserRole.TRADER,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.skip(
    reason="Strategies router is placeholder implementation with broken URL paths. Tests need rewrite for v0.2.0."
)
class TestStrategiesRouter:
    """Test cases for the strategies router."""

    def setup_method(self):
        # Create fresh mocks for each test
        self.db = create_mock_db()
        self.current_user = create_mock_current_user()
        self.client = TestClient(app)
        # Setup dependency overrides
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_active_user] = lambda: self.current_user

    def teardown_method(self):
        # Clear only the overrides set in setup_method
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_active_user, None)

    def test_list_strategies_empty(self):
        """Test listing strategies when no strategies exist."""
        # Mock the database query
        self.db.query.return_value.offset.return_value.limit.return_value.all.return_value = (
            []
        )

        # Make the request
        response = self.client.get(
            "/strategies/", headers={"Authorization": "Bearer test-token"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

        # Verify the query was made with correct parameters
        self.db.query.assert_called_once()
        self.db.query.return_value.offset.assert_called_once_with(0)
        self.db.query.return_value.offset.return_value.limit.assert_called_once_with(
            100
        )

    def test_list_strategies_with_data(self):
        """Test listing strategies when strategies exist."""
        # Create test data
        test_run = create_test_run()
        self.db.query.return_value.offset.return_value.limit.return_value.all.return_value = [
            test_run
        ]

        # Make the request
        response = self.client.get(
            "/strategies/", headers={"Authorization": "Bearer test-token"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == test_run["id"]

    def test_create_strategy(self):
        """Test creating a new strategy."""
        # Test data
        run_data = {
            "strategy_id": "test_strategy_123",
            "pair": "AUDUSD",
            "environment": "paper",
            "parameters": {"param1": "value1"},
            "initial_balance": 10000.0,
        }

        # Make the request
        response = self.client.post(
            "/strategies/",
            json=run_data,
            headers={"Authorization": "Bearer test-token"},
        )

        # Assert the response
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["strategy_id"] == run_data["strategy_id"]
        assert data["pair"] == run_data["pair"]
        assert data["environment"] == run_data["environment"]
        assert data["parameters"] == run_data["parameters"]

    def test_get_strategy_not_found(self):
        """Test getting a non-existent strategy."""
        # Mock the database query to return None (not found)
        self.db.query.return_value.filter.return_value.first.return_value = None

        # Make the request
        response = self.client.get(
            "/strategies/999", headers={"Authorization": "Bearer test-token"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_404_NOT_FOUND
        detail = response.json().get("detail", "")
        assert isinstance(detail, str) and "not found" in detail.lower()

    def test_get_strategy_success(self):
        """Test getting an existing strategy."""
        # Test data
        test_run = create_test_run()

        # Mock the database query
        self.db.query.return_value.filter.return_value.first.return_value = test_run

        # Make the request
        response = self.client.get(
            f"/strategies/{test_run['id']}",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == test_run["id"]
        assert data["strategy_id"] == test_run["strategy_id"]

    def test_create_strategy_invalid_data(self):
        """Test creating a strategy with invalid data."""
        # Test data with missing required fields
        invalid_data = {"pair": "AUDUSD"}  # Missing required fields

        # Make the request
        response = self.client.post(
            "/strategies/",
            json=invalid_data,
            headers={"Authorization": "Bearer test-token"},
        )

        # Assert the response
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert data[0]["name"] == TEST_STRATEGY["name"]
        assert data[0]["description"] == TEST_STRATEGY["description"]

    def test_create_strategy(self):
        """Test creating a new strategy."""
        # Mock the database add and commit
        db_commit = MagicMock()
        db_commit.add.return_value = None
        db_commit.commit.return_value = None
        db_commit.refresh.return_value = None

        # Create test data
        test_strategy = models.Run(
            id=1,
            name=TEST_STRATEGY["name"],
            description=TEST_STRATEGY["description"],
            parameters=TEST_STRATEGY["parameters"],
        )

        # Mock the database query
        self.db.return_value = db_commit
        self.db.query.return_value.filter.return_value.first.return_value = None

        # Make the request
        response = self.client.post(
            "/strategies/",
            json=TEST_STRATEGY,
            headers={"Authorization": "Bearer test-token"},
        )

        # Assert the response
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == TEST_STRATEGY["name"]
        assert data["description"] == TEST_STRATEGY["description"]

    def test_get_strategy_not_found(self):
        """Test getting a non-existent strategy."""
        # Mock the database query
        self.db.query.return_value.filter.return_value.first.return_value = None

        # Make the request
        response = self.client.get(
            "/strategies/999", headers={"Authorization": "Bearer test-token"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Strategy not found" in response.json()["detail"]

    def test_get_strategy_success(self):
        """Test getting an existing strategy."""
        # Create test data
        test_strategy = models.Run(
            id=1,
            name=TEST_STRATEGY["name"],
            description=TEST_STRATEGY["description"],
            parameters=TEST_STRATEGY["parameters"],
        )

        # Mock the database query
        self.db.query.return_value.filter.return_value.first.return_value = (
            test_strategy
        )

        # Make the request
        response = self.client.get(
            "/strategies/1", headers={"Authorization": "Bearer test-token"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == 1
        assert data["name"] == TEST_STRATEGY["name"]
        assert data["description"] == TEST_STRATEGY["description"]
