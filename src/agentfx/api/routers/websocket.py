from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Optional
import json
import logging
from ...api.core.websocket import manager
from ...api.core.security import get_current_user_ws
from ...api.schemas.websocket import WebSocketMessage, WebSocketCommand, WebSocketSubscription
from ...api.services.market_data import MarketDataService

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/ws/market-data")
async def websocket_market_data(
    websocket: WebSocket,
    token: Optional[str] = None
):
    """
    WebSocket endpoint for real-time market data streaming.
    
    Expected commands:
    - {"command": "subscribe", "instrument": "EUR_USD", "timeframe": "M1"}
    - {"command": "unsubscribe", "instrument": "EUR_USD", "timeframe": "M1"}
    """
    try:
        # Authenticate user
        user = await get_current_user_ws(token)
        if not user:
            await websocket.close(code=4001)
            return
            
        # Accept the connection
        await manager.connect(websocket, "market-data")
        logger.info(f"New WebSocket connection from user {user.email}")
        
        # Send welcome message
        await websocket.send_json({
            "type": "connection_established",
            "message": "Connected to market data feed",
            "user_id": str(user.id)
        })
        
        # Handle incoming messages
        while True:
            try:
                data = await websocket.receive_text()
                await handle_websocket_message(websocket, data)
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
            except Exception as e:
                logger.error(f"WebSocket error: {str(e)}")
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })
                
    except Exception as e:
        logger.error(f"WebSocket connection error: {str(e)}")
    finally:
        manager.disconnect(websocket)

async def handle_websocket_message(websocket: WebSocket, message: str):
    """Handle incoming WebSocket messages."""
    try:
        data = json.loads(message)
        command = data.get("command")
        
        if command == "subscribe":
            instrument = data.get("instrument")
            timeframe = data.get("timeframe")
            if not all([instrument, timeframe]):
                await websocket.send_json({
                    "type": "error",
                    "message": "Missing instrument or timeframe"
                })
                return
                
            await manager.subscribe(websocket, instrument, timeframe)
            await websocket.send_json({
                "type": "subscription_update",
                "status": "subscribed",
                "instrument": instrument,
                "timeframe": timeframe
            })
            
        elif command == "unsubscribe":
            instrument = data.get("instrument")
            timeframe = data.get("timeframe")
            if not all([instrument, timeframe]):
                await websocket.send_json({
                    "type": "error",
                    "message": "Missing instrument or timeframe"
                })
                return
                
            await manager.unsubscribe(websocket, instrument, timeframe)
            await websocket.send_json({
                "type": "subscription_update",
                "status": "unsubscribed",
                "instrument": instrument,
                "timeframe": timeframe
            })
            
        else:
            await websocket.send_json({
                "type": "error",
                "message": f"Unknown command: {command}"
            })
            
    except json.JSONDecodeError:
        await websocket.send_json({
            "type": "error",
            "message": "Invalid JSON format"
        })
    except Exception as e:
        logger.error(f"Error handling WebSocket message: {str(e)}")
        await websocket.send_json({
            "type": "error",
            "message": "Internal server error"
        })
