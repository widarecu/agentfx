import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import aiohttp
import pytest
from dotenv import load_dotenv

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class WebSocketTestClient:
    def __init__(self):
        self.ws_url = f"wss://{os.getenv('OANDA_STREAMING_HOST')}/v3/accounts/{os.getenv('OANDA_ACCOUNT_ID')}/pricing/stream"
        self.instruments = "EUR_USD,USD_JPY,GBP_USD"
        self.headers = {
            "Authorization": f"Bearer {os.getenv('OANDA_API_KEY')}",
            "Content-Type": "application/json"
        }
        self.session = None
        self.ws = None
        self.received_messages = []

    async def connect(self):
        """Establish WebSocket connection to OANDA"""
        try:
            # Create a new session with detailed timeout settings
            timeout = aiohttp.ClientTimeout(
                total=30,  # Total request timeout
                connect=10,  # Connection timeout
                sock_connect=10,  # Socket connect timeout
                sock_read=30  # Socket read timeout (increased for WebSocket)
            )
            
            # Create SSL context with more detailed error reporting
            ssl_context = aiohttp.TCPConnector(
                ssl=True,
                enable_cleanup_closed=True,
                force_close=True
            )
            
            # Create a new session for this connection attempt
            self.session = aiohttp.ClientSession(
                connector=ssl_context,
                timeout=timeout
            )
            
            # Add required WebSocket headers
            ws_headers = {
                **self.headers,
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "x3JJHMbDL1EzLkh9GBhXDw==",
                "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits"
            }
            
            # Add instruments to the URL
            full_url = f"{self.ws_url}?instruments={self.instruments}&snapshot=True"
            logger.info(f"Connecting to WebSocket at {full_url}")
            
            # Create the WebSocket connection
            self.ws = await self.session.ws_connect(
                full_url,
                headers=ws_headers,
                heartbeat=30.0,
                compress=15,
                max_msg_size=10 * 1024 * 1024,
                receive_timeout=30.0,
                autoclose=True,
                autoping=True
            )
            
            logger.info("WebSocket connection established")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {str(e)}")
            if hasattr(e, 'status'):
                logger.error(f"Status code: {e.status}")
            if hasattr(e, 'headers'):
                logger.error(f"Response headers: {dict(e.headers)}")
            if hasattr(e, 'message') and hasattr(e.message, 'decode'):
                logger.error(f"Response body: {e.message.decode()}")
            return False
    
    async def listen_for_messages(self, timeout=30):
        """Listen for messages from the WebSocket"""
        if not self.ws:
            logger.error("WebSocket is not connected")
            return
            
        logger.info("Listening for messages...")
        start_time = datetime.now()
        
        try:
            while (datetime.now() - start_time).total_seconds() < timeout:
                msg = await self.ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    self.received_messages.append(data)
                    logger.info(f"Received message: {json.dumps(data, indent=2)}")
                    
                    # If we've received at least one message, we can consider the test successful
                    if len(self.received_messages) >= 1:
                        return True
                        
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket connection closed by server")
                    break
                    
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self.ws.exception()}")
                    break
                    
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for messages")
            
        return len(self.received_messages) > 0
    
    async def close(self):
        """Close the WebSocket connection"""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        logger.info("WebSocket connection closed")

@pytest.mark.asyncio
async def test_websocket_connection():
    """Test WebSocket connection to OANDA"""
    # Check if we have the required environment variables
    required_vars = ['OANDA_API_KEY', 'OANDA_ACCOUNT_ID', 'OANDA_STREAMING_HOST']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        pytest.skip(f"Skipping WebSocket test. Missing required environment variables: {', '.join(missing_vars)}")
    
    client = WebSocketTestClient()
    
    try:
        # Test connection
        connected = await client.connect()
        assert connected, "Failed to connect to WebSocket"
        
        # Test receiving messages
        received_messages = await client.listen_for_messages(timeout=15)
        assert received_messages, "Did not receive any messages from WebSocket"
        
        logger.info("WebSocket test completed successfully")
        
    finally:
        # Clean up
        await client.close()

if __name__ == "__main__":
    # This allows running the test directly with: python -m tests.test_websocket_connection
    import asyncio
    asyncio.run(test_websocket_connection())
