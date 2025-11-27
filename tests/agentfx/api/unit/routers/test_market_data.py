"""Unit tests for the market data router."""
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import MagicMock, patch

from fastapi import Depends
from src.agentfx.api.main import app
from src.agentfx.api import models
from src.agentfx.api.core.database import get_db
from src.agentfx.api.models.user import UserRole

# Mock the database session and dependencies
db = MagicMock(spec=Session)
app.dependency_overrides[get_db] = lambda: db

# Mock the current user
current_user = models.User(
    id=1,
    email="test@example.com",
    hashed_password="hashed_password",
    is_active=True,
    role=UserRole.TRADER
)

# Create test client
client = TestClient(app)

class TestMarketDataRouter:
    """Test cases for the market data router."""
    
    @patch('src.agentfx.api.routers.market_data_ws.get_current_user')
    def test_websocket_connection(self, mock_get_current_user):
        """Test WebSocket connection to market data stream."""
        # Setup the mock current user
        mock_get_current_user.return_value = current_user
        
        # Mock the WebSocket connection
        with patch('src.agentfx.api.routers.market_data_ws.websocket_endpoint') as mock_ws:
            # This is a simple test to check if the endpoint is properly set up
            # Actual WebSocket testing would require a more complex setup
            response = client.get("/ws/market-data/")
            assert response.status_code == 426  # Should be a WebSocket upgrade request
    
    @patch('src.agentfx.api.routers.market_data_ws.get_current_user')
    def test_subscribe_to_instruments(self, mock_get_current_user):
        """Test subscribing to market data for instruments."""
        # Setup the mock current user
        mock_get_current_user.return_value = current_user
        
        # Test data
        subscription_data = {
            "instruments": ["EUR_USD", "GBP_USD"],
            "granularity": "M1"
        }
        
        # Mock the WebSocket connection and subscription
        with patch('src.agentfx.api.routers.market_data_ws.manager.subscribe') as mock_subscribe:
            response = client.post(
                "/market-data/subscribe/",
                json=subscription_data,
                headers={"Authorization": "Bearer test-token"}
            )
            
            assert response.status_code == status.HTTP_200_OK
            mock_subscribe.assert_called_once_with(
                user_id=current_user.id,
                instruments=subscription_data["instruments"],
                granularity=subscription_data["granularity"]
            )
    
    @patch('src.agentfx.api.routers.market_data_ws.get_current_user')
    def test_unsubscribe_from_instruments(self, mock_get_current_user):
        """Test unsubscribing from market data."""
        # Setup the mock current user
        mock_get_current_user.return_value = current_user
        
        # Test data
        unsubscribe_data = {
            "instruments": ["EUR_USD", "GBP_USD"]
        }
        
        # Mock the WebSocket unsubscription
        with patch('src.agentfx.api.routers.market_data_ws.manager.unsubscribe') as mock_unsubscribe:
            response = client.post(
                "/market-data/unsubscribe/",
                json=unsubscribe_data,
                headers={"Authorization": "Bearer test-token"}
            )
            
            assert response.status_code == status.HTTP_200_OK
            mock_unsubscribe.assert_called_once_with(
                user_id=current_user.id,
                instruments=unsubscribe_data["instruments"]
            )

    @patch('src.agentfx.api.routers.market_data_ws.get_current_user')
    def test_get_historical_data(self, mock_get_current_user):
        """Test retrieving historical market data."""
        # Setup the mock current user
        mock_get_current_user.return_value = current_user
        
        # Test parameters
        params = {
            "instrument": "EUR_USD",
            "granularity": "M1",
            "count": 100
        }
        
        # Mock the historical data service
        with patch('src.agentfx.api.routers.market_data_ws.get_historical_data') as mock_get_data:
            # Setup mock return value
            mock_get_data.return_value = [{"time": "2023-01-01T00:00:00Z", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15}]
            
            response = client.get(
                "/market-data/historical/",
                params=params,
                headers={"Authorization": "Bearer test-token"}
            )
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert isinstance(data, list)
            assert len(data) > 0
            assert "time" in data[0]
            assert "open" in data[0]
            assert "high" in data[0]
            assert "low" in data[0]
            assert "close" in data[0]
