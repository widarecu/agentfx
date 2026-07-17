"""Unit tests for the market data router."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.agentfx.api import models
from src.agentfx.api.core.database import get_db
from src.agentfx.api.main import app
from src.agentfx.api.models.user import UserRole
from src.agentfx.api.services.user import get_current_active_user


@pytest.mark.unit
@pytest.mark.market_data
class TestMarketDataRouter:
    """Test cases for market data router endpoints."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return MagicMock()

    @pytest.fixture
    def mock_current_user(self):
        """Create a mock current user."""
        return models.User(
            id=1,
            email="test@example.com",
            hashed_password="hashed_password",
            is_active=True,
            role=UserRole.TRADER,
        )

    @pytest.fixture
    def client(self, mock_db, mock_current_user):
        """Create test client with mocked dependencies."""
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_current_active_user] = lambda: mock_current_user
        client = TestClient(app)
        yield client
        # Clean up overrides after test
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_active_user, None)

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_candles")
    def test_get_candles_success(self, mock_get_candles, client, mock_current_user):
        """Test successful candle retrieval endpoint."""
        # Create mock Candle objects
        mock_candle = MagicMock()
        mock_candle.id = 1
        mock_candle.timestamp = datetime(2023, 1, 1, 12, 0, 0)
        mock_candle.open = 1.1000
        mock_candle.high = 1.1005
        mock_candle.low = 1.0995
        mock_candle.close = 1.1002
        mock_candle.volume = 1000
        mock_candle.exchange = "oanda"
        mock_candle.instrument = "EUR_USD"
        mock_candle.timeframe = "M1"
        mock_candle.created_at = datetime(2023, 1, 1, 12, 0, 0)

        # Setup mocks
        mock_get_candles.return_value = ([mock_candle], 1)

        # Test request
        response = client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["instrument"] == "EUR_USD"

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_candles")
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_get_candles_with_validation_error(
        self, mock_get_user, mock_get_candles, client, mock_current_user
    ):
        """Test candle retrieval with validation error."""
        # Setup mocks
        mock_get_user.return_value = mock_current_user
        mock_get_candles.side_effect = ValueError("Invalid candle data")

        # Test request
        response = client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid candle data" in response.json()["detail"]

    @patch("src.agentfx.api.services.market_data.MarketDataService.create_candles")
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_create_candles_success(
        self, mock_get_user, mock_create_candles, client, mock_current_user
    ):
        """Test successful candle creation endpoint."""
        from src.agentfx.api.schemas.market_data import CandlePublic

        # Setup mocks
        mock_get_user.return_value = mock_current_user

        # Create CandlePublic objects that the service would return
        mock_candle = CandlePublic(
            id=1,
            timestamp=datetime(2023, 1, 1, 12, 0, 0),
            open=1.1000,
            high=1.1005,
            low=1.0995,
            close=1.1002,
            volume=1000,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            created_at=datetime(2023, 1, 1, 12, 0, 0),
            updated_at=None,
        )
        mock_create_candles.return_value = [mock_candle]

        # Format according to CandleBulkCreate schema
        candle_data = {
            "exchange": "oanda",
            "instrument": "EUR_USD",
            "timeframe": "M1",
            "candles": [
                {
                    "timestamp": "2023-01-01T12:00:00Z",
                    "open": 1.1000,
                    "high": 1.1005,
                    "low": 1.0995,
                    "close": 1.1002,
                    "volume": 1000,
                }
            ],
        }

        # Test request
        response = client.post(
            "/api/v1/market-data/candles",
            json=candle_data,
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["instrument"] == "EUR_USD"

    @patch(
        "src.agentfx.api.services.market_data.MarketDataService.get_market_data_status"
    )
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_get_market_data_status(
        self, mock_get_user, mock_get_status, client, mock_current_user
    ):
        """Test market data status endpoint."""
        # Create mock status update objects
        mock_status = MagicMock()
        mock_status.id = 1
        mock_status.exchange = "oanda"
        mock_status.instrument = "EUR_USD"
        mock_status.timeframe = "M1"
        mock_status.last_updated = "2023-01-01T12:00:00Z"
        mock_status.update_status = "idle"
        mock_status.last_candle_timestamp = "2023-01-01T11:59:00Z"
        mock_status.error_message = None

        # Setup mocks
        mock_get_user.return_value = mock_current_user
        mock_get_status.return_value = [mock_status]

        # Test request
        response = client.get(
            "/api/v1/market-data/status", headers={"Authorization": "Bearer test-token"}
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["exchange"] == "oanda"
        assert data[0]["instrument"] == "EUR_USD"
        assert data[0]["update_status"] == "idle"

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_instruments")
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_list_instruments(
        self, mock_get_user, mock_get_instruments, client, mock_current_user
    ):
        """Test instruments listing endpoint."""
        # Setup mocks
        mock_get_user.return_value = mock_current_user
        mock_get_instruments.return_value = ["EUR_USD", "GBP_USD"]

        # Test request
        response = client.get(
            "/api/v1/market-data/instruments?exchange=oanda",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2
        assert data[0]["symbol"] == "EUR_USD"
        assert data[1]["symbol"] == "GBP_USD"

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_exchange_info")
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_get_exchange_info(
        self, mock_get_user, mock_get_exchange_info, client, mock_current_user
    ):
        """Test exchange info endpoint."""
        # Setup mocks
        mock_get_user.return_value = mock_current_user
        mock_get_exchange_info.return_value = {
            "name": "oanda",
            "display_name": "OANDA",
            "enabled": True,
            "description": "OANDA Forex Broker",
            "supported_timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
        }

        # Test request
        response = client.get(
            "/api/v1/market-data/exchange-info?exchange=oanda",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "oanda"
        assert data["enabled"] is True
        assert "M1" in data["supported_timeframes"]

    @patch("src.agentfx.api.core.metrics.market_metrics.get_health_summary")
    @patch("src.agentfx.api.core.metrics.market_metrics.get_endpoint_stats")
    @patch("src.agentfx.api.core.metrics.market_metrics.get_usage_stats")
    @patch("src.agentfx.api.core.metrics.market_metrics.get_data_quality_stats")
    @patch("src.agentfx.api.services.user.get_current_active_user")
    def test_get_metrics(
        self,
        mock_get_user,
        mock_quality_stats,
        mock_usage_stats,
        mock_endpoint_stats,
        mock_health_summary,
        client,
        mock_current_user,
    ):
        """Test metrics endpoint."""
        # Setup mocks
        mock_get_user.return_value = mock_current_user
        mock_health_summary.return_value = {
            "status": "healthy",
            "score": 95.0,
        }
        mock_endpoint_stats.return_value = {
            "avg_response_time": 0.05,
            "success_rate": 99.5,
            "error_rate": 0.5,
        }
        mock_usage_stats.return_value = {
            "total_requests": 1000,
            "requests_per_minute": 10,
        }
        mock_quality_stats.return_value = {
            "validation_errors": 2,
            "data_gaps": 1,
        }

        # Test request
        response = client.get(
            "/api/v1/market-data/metrics",
            headers={"Authorization": "Bearer test-token"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "performance" in data
        assert "usage" in data
        assert "data_quality" in data
        assert "health" in data
        assert data["health"]["status"] == "healthy"

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_candles")
    def test_get_candles_with_custom_limit(
        self, mock_get_candles, client, mock_current_user
    ):
        """Test candle retrieval with custom limit returns correct total count."""
        # Create mock candle objects to simulate 500 of 1500 total
        mock_candles = []
        for i in range(3):
            mock_candle = MagicMock()
            mock_candle.id = i + 1
            mock_candle.timestamp = datetime(2023, 1, 1, 12, i, 0)
            mock_candle.open = 1.1000 + i * 0.0001
            mock_candle.high = 1.1005 + i * 0.0001
            mock_candle.low = 1.0995 + i * 0.0001
            mock_candle.close = 1.1002 + i * 0.0001
            mock_candle.volume = 1000 + i
            mock_candle.exchange = "oanda"
            mock_candle.instrument = "EUR_USD"
            mock_candle.timeframe = "M1"
            mock_candle.created_at = datetime(2023, 1, 1, 12, i, 0)
            mock_candles.append(mock_candle)

        # Return 3 candles but total of 1500 (simulating limit < total)
        mock_get_candles.return_value = (mock_candles, 1500)

        response = client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1&limit=500",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1500
        assert len(data["items"]) == 3

    @patch("src.agentfx.api.services.market_data.MarketDataService.get_timeframes")
    def test_list_timeframes(self, mock_get_timeframes, client, mock_current_user):
        """Test timeframes listing endpoint returns dynamic DB values."""
        mock_get_timeframes.return_value = [
            {"code": "M1", "display_name": "1 Min"},
            {"code": "H1", "display_name": "1 Hour"},
            {"code": "D", "display_name": "Daily"},
        ]

        response = client.get(
            "/api/v1/market-data/timeframes?exchange=oanda",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 3
        assert data[0]["code"] == "M1"
        assert data[0]["display_name"] == "1 Min"
        assert data[1]["code"] == "H1"
        assert data[2]["code"] == "D"
        assert data[2]["display_name"] == "Daily"

    def test_unauthorized_access(self, client):
        """Test that unauthorized requests are rejected."""
        # Test request without authentication (no Authorization header)
        response = client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1"
        )

        # Assertions - should get 400 for missing authentication
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.unit
@pytest.mark.market_data
def test_all_rate_limited_endpoints_are_async():
    """Regression: `@market_data_rate_limit` wraps every endpoint in an
    async wrapper that always `await`s the inner function. A sync `def`
    endpoint under this decorator returns its value (e.g. a Candle
    ORM object) which then fails at `await <object>` with
    ``TypeError: object Candle can't be used in 'await' expression``.

    Wilson UAT 2026-07-16: `/candles/latest` was defined as sync `def`
    and 500'd for every request. Same latent bug on
    `/exchanges/{exchange_id}`. This test enumerates every route callable
    on the market-data router and asserts it's a coroutine — catches the
    regression on any future endpoint the moment it's added.
    """
    import asyncio

    from src.agentfx.api.routers.market_data import router

    offenders = [
        route.endpoint.__name__
        for route in router.routes
        if not asyncio.iscoroutinefunction(route.endpoint)
    ]
    assert not offenders, (
        f"market-data router endpoints defined as sync `def`: {offenders}. "
        "They must be `async def` — the @market_data_rate_limit decorator "
        "awaits the inner function and will 500 with `TypeError: object X "
        "can't be used in 'await' expression` on a sync definition."
    )
