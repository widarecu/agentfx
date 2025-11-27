from typing import Dict, List, Set, Optional
from fastapi import WebSocket
import json
import asyncio
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.subscriptions: Dict[WebSocket, Set[tuple]] = {}

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = set()
        self.active_connections[channel].add(websocket)
        self.subscriptions[websocket] = set()
        logger.info(f"New WebSocket connection to channel: {channel}")

    def disconnect(self, websocket: WebSocket):
        for channel, connections in self.active_connections.items():
            if websocket in connections:
                connections.remove(websocket)
        if websocket in self.subscriptions:
            del self.subscriptions[websocket]
        logger.info("WebSocket disconnected")

    async def subscribe(self, websocket: WebSocket, instrument: str, timeframe: str):
        if websocket in self.subscriptions:
            self.subscriptions[websocket].add((instrument, timeframe))
            logger.info(f"Subscribed to {instrument} {timeframe}")

    async def unsubscribe(self, websocket: WebSocket, instrument: str, timeframe: str):
        if websocket in self.subscriptions:
            self.subscriptions[websocket].discard((instrument, timeframe))
            logger.info(f"Unsubscribed from {instrument} {timeframe}")

    async def broadcast(self, channel: str, message: dict):
        if channel not in self.active_connections:
            return
            
        disconnected = set()
        for connection in self.active_connections[channel]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_to_subscribers(self, instrument: str, timeframe: str, data: dict):
        message = {
            "type": "candle_update",
            "instrument": instrument,
            "timeframe": timeframe,
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        for websocket, subscriptions in self.subscriptions.items():
            if (instrument, timeframe) in subscriptions:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending to subscriber: {e}")
                    self.disconnect(websocket)

# Global WebSocket manager instance
manager = ConnectionManager()
