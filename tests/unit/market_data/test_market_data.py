"""Unit tests for the market data router."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import MagicMock, patch

from src.agentfx.api.main import app
from src.agentfx.api import models
from src.agentfx.api.core.database import get_db
from src.agentfx.api.models.user import UserRole
from src.agentfx.api.services.user import get_current_active_user


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    return MagicMock(spec=Session)


@pytest.fixture
def mock_current_user():
    """Create a mock current user."""
    return models.User(
        id=1,
        email="test@example.com",
        hashed_password="hashed_password",
        is_active=True,
        role=UserRole.TRADER,
    )


@pytest.fixture
def market_data_client(mock_db_session, mock_current_user):
    """Create test client with mocked dependencies."""
    app.dependency_overrides[get_db] = lambda: mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: mock_current_user
    client = TestClient(app)
    yield client
    # Clean up overrides after test
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_active_user, None)


class TestMarketDataRouter:
    """Test cases for the market data router."""

    def test_get_historical_data(self, market_data_client, mock_current_user):
        """Test retrieving historical market data using candles endpoint."""
        # Test parameters
        params = {
            "exchange": "oanda",
            "instrument": "EUR_USD",
            "timeframe": "M1",
            "limit": 100,
        }

        # Mock the historical data service
        with patch(
            "src.agentfx.api.services.market_data.MarketDataService.get_candles"
        ) as mock_get_data:
            # Create mock Candle objects that match the database model
            from datetime import datetime

            mock_candle = MagicMock()
            mock_candle.id = 1
            mock_candle.timestamp = datetime(2023, 1, 1, 0, 0, 0)
            mock_candle.open = 1.1
            mock_candle.high = 1.2
            mock_candle.low = 1.0
            mock_candle.close = 1.15
            mock_candle.volume = 1000
            mock_candle.exchange = "oanda"
            mock_candle.instrument = "EUR_USD"
            mock_candle.timeframe = "M1"
            mock_candle.created_at = datetime(2023, 1, 1, 0, 0, 0)

            # Setup mock return value
            mock_get_data.return_value = ([mock_candle], 1)

            response = market_data_client.get(
                "/api/v1/market-data/candles",
                params=params,
                headers={"Authorization": "Bearer test-token"},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert isinstance(data, dict)
            assert "items" in data
            assert "total" in data
            assert len(data["items"]) > 0
            assert "timestamp" in data["items"][0]
            assert "open" in data["items"][0]
            assert "high" in data["items"][0]
            assert "low" in data["items"][0]
            assert "close" in data["items"][0]
