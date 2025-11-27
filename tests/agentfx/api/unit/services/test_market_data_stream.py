"""Unit tests for the market data stream service."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.agentfx.api.services.market_data_stream import OandaMarketDataStream, market_data_stream

class TestOandaMarketDataStream:
    """Test cases for the OandaMarketDataStream class."""
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket client."""
        mock = AsyncMock()
        mock.recv = AsyncMock(return_value=json.dumps({"type": "HEARTBEAT"}))
        return mock

    @pytest.fixture
    def mock_session(self, mock_websocket):
        """Create a mock aiohttp client session."""
        session = MagicMock()
        ws = AsyncMock()
        ws.__aenter__.return_value = mock_websocket
        session.ws_connect.return_value = ws
        return session

    @pytest.mark.asyncio
    async def test_connect_success(self, mock_session):
        """Test successful WebSocket connection."""
        stream = OandaMarketDataStream()
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await stream.connect()
            
        assert result is True
        assert stream.active is True
        
    @pytest.mark.asyncio
    async def test_connect_failure(self, mock_session):
        """Test WebSocket connection failure."""
        stream = OandaMarketDataStream()
        mock_session.ws_connect.side_effect = Exception("Connection failed")
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await stream.connect()
            
        assert result is False
        assert stream.active is False
        
    @pytest.mark.asyncio
    async def test_disconnect(self, mock_session):
        """Test disconnecting the WebSocket."""
        stream = OandaMarketDataStream()
        stream.active = True
        stream.ws = AsyncMock()
        
        await stream.disconnect()
        
        assert stream.active is False
        stream.ws.close.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_listen_message_processing(self, mock_session, mock_websocket):
        """Test processing of incoming WebSocket messages."""
        # Setup test data
        test_message = {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "bids": [{"price": "1.1000", "liquidity": 1000}],
            "asks": [{"price": "1.1002", "liquidity": 1000}],
            "time": "2023-01-01T00:00:00.000000000Z"
        }
        
        # Configure mock
        mock_websocket.recv.side_effect = [
            json.dumps(test_message),
            asyncio.CancelledError()  # To break the loop
        ]
        
        # Create and test
        stream = OandaMarketDataStream()
        stream.active = True
        stream.ws = mock_websocket
        
        # Mock the process_message method
        with patch.object(stream, '_process_message') as mock_process:
            with pytest.raises(asyncio.CancelledError):
                await stream._listen()
            
            # Verify the message was processed
            mock_process.assert_called_once_with(test_message)
            
    @pytest.mark.asyncio
    async def test_reconnect_logic(self, mock_session):
        """Test the reconnection logic."""
        stream = OandaMarketDataStream()
        stream.active = True
        
        # First connection fails, second succeeds
        mock_session.ws_connect.side_effect = [
            Exception("First failure"),
            mock_session.ws_connect.return_value
        ]
        
        with patch('aiohttp.ClientSession', return_value=mock_session), \
             patch('asyncio.sleep', return_value=None) as mock_sleep:
                
            result = await stream._reconnect()
            
            assert result is True
            assert mock_sleep.call_count == 1  # Should have slept once before retry
            assert mock_session.ws_connect.call_count == 2  # Two connection attempts
