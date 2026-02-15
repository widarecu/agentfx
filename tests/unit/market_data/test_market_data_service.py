"""Unit tests for the market data service."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session
from src.agentfx.api.schemas.market_data import CandleBase, Timeframe
from src.agentfx.api.services.market_data import MarketDataService


@pytest.mark.unit
@pytest.mark.market_data
class TestMarketDataService:
    """Test cases for MarketDataService business logic."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return MagicMock(spec=Session)

    @pytest.fixture
    def sample_candle_data(self):
        """Sample candle data for testing."""
        return [
            CandleBase(
                timestamp=datetime(2023, 1, 1, 12, 0, 0),
                open=1.1000,
                high=1.1005,
                low=1.0995,
                close=1.1002,
                volume=1000,
                exchange="oanda",
                instrument="EUR_USD",
                timeframe=Timeframe.M1,
            ),
            CandleBase(
                timestamp=datetime(2023, 1, 1, 12, 1, 0),
                open=1.1002,
                high=1.1008,
                low=1.1000,
                close=1.1006,
                volume=1200,
                exchange="oanda",
                instrument="EUR_USD",
                timeframe=Timeframe.M1,
            ),
        ]

    def test_get_candles_success(self, mock_db, sample_candle_data):
        """Test successful candle retrieval."""
        # Mock database query
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = sample_candle_data
        mock_query.count.return_value = len(sample_candle_data)

        # Test the service method
        candles, total = MarketDataService.get_candles(
            db=mock_db,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            limit=100,
        )

        # Assertions
        assert len(candles) == 2
        assert total == 2
        assert candles[0].instrument == "EUR_USD"
        assert candles[0].open == 1.1000

    def test_get_candles_with_time_filter(self, mock_db, sample_candle_data):
        """Test candle retrieval with time range filter."""
        from_time = datetime(2023, 1, 1, 12, 0, 0)
        to_time = datetime(2023, 1, 1, 12, 1, 0)

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = sample_candle_data[:1]
        mock_query.count.return_value = 1

        candles, total = MarketDataService.get_candles(
            db=mock_db,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            from_time=from_time,
            to_time=to_time,
            limit=100,
        )

        assert len(candles) == 1
        assert total == 1

    @patch("src.agentfx.api.schemas.market_data.CandlePublic")
    def test_create_candles_validation_success(
        self, mock_candle_public, mock_db, sample_candle_data
    ):
        """Test successful candle creation with validation."""
        # Mock database operations
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock()

        # Mock the query for checking existing candles
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None  # No existing candles

        # Mock CandlePublic.model_validate to return mock objects
        mock_validated_candle = MagicMock()
        mock_validated_candle.instrument = "EUR_USD"
        mock_candle_public.model_validate.return_value = mock_validated_candle

        # Convert CandleBase objects to dictionaries for the service method
        # Note: exchange, instrument, timeframe are passed as separate args, not in candle data
        candles_data = [
            {
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in sample_candle_data
        ]

        # Test candle creation
        result = MarketDataService.create_candles(
            mock_db,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            candles_data=candles_data,
        )

        # Assertions
        assert len(result) == 2
        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_candles_validation_failure(self, mock_db):
        """Test candle creation with invalid data."""
        # Note: exchange, instrument, timeframe are passed as separate args
        invalid_candle_data = [
            {
                "timestamp": datetime(2023, 1, 1, 12, 0, 0),
                "open": 1.1000,
                "high": 1.0995,  # Invalid: high < open
                "low": 1.0990,
                "close": 1.1002,
                "volume": 1000,
            }
        ]

        # Should return empty list and log warnings for invalid data
        result = MarketDataService.create_candles(
            mock_db,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            candles_data=invalid_candle_data,
            update_if_exists=False,
        )

        # Assertions - should return empty list for invalid data
        assert result == []

    def test_get_latest_candle_success(self, mock_db):
        """Test successful latest candle retrieval."""
        # Mock database query
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.first.return_value = {
            "timestamp": datetime(2023, 1, 1, 12, 0, 0),
            "open": 1.1000,
            "high": 1.1005,
            "low": 1.0995,
            "close": 1.1002,
            "volume": 1000,
            "exchange": "oanda",
            "instrument": "EUR_USD",
            "timeframe": "M1",
        }

        # Test the service method
        result = MarketDataService.get_latest_candle(
            db=mock_db, exchange="oanda", instrument="EUR_USD", timeframe="M1"
        )

        # Assertions
        assert result is not None
        assert result["instrument"] == "EUR_USD"
        assert result["open"] == 1.1000

    def test_get_latest_candle_not_found(self, mock_db):
        """Test latest candle retrieval when no candle exists."""
        # Mock database query to return None
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.first.return_value = None

        # Test the service method
        result = MarketDataService.get_latest_candle(
            db=mock_db, exchange="oanda", instrument="EUR_USD", timeframe="M1"
        )

        # Assertions
        assert result is None

    def test_update_market_data_status_success(self, mock_db):
        """Test successful market data status update."""
        # Mock database operations
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock()

        # Test the service method
        result = MarketDataService.update_market_data_status(
            db=mock_db,
            exchange="oanda",
            instrument="EUR_USD",
            timeframe="M1",
            status="active",
            last_updated=datetime.now(),
        )

        # Assertions
        assert result is not None
        mock_db.add.assert_called()
        mock_db.commit.assert_called()
