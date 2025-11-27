from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .routers import auth, runs, strategies, market_data_ws
from .core.database import engine, Base
from .core.websocket import manager
from .services.market_data_stream import (
    start_market_data_stream,
    stop_market_data_stream,
)
import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager

# Configure root logger
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Add console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)

# Configure file handler
try:
    file_handler = logging.handlers.RotatingFileHandler(
        "agentfx_api.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.root.addHandler(file_handler)
except Exception as e:
    print(f"Failed to set up file logging: {e}")

# Set log levels for specific loggers
logging.getLogger("aiohttp").setLevel(logging.DEBUG)
logging.getLogger("asyncio").setLevel(logging.DEBUG)
logging.getLogger("websockets").setLevel(logging.DEBUG)

# Disable noisy loggers
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

# Configure aiohttp to be less verbose
aiohttp_logger = logging.getLogger("aiohttp.client")
aiohttp_logger.setLevel(logging.WARN)

aiohttp_access_logger = logging.getLogger("aiohttp.access")
aiohttp_access_logger.setLevel(logging.WARN)

logger = logging.getLogger(__name__)
logger.info("Logging configured")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up...")

    # Create database tables
    Base.metadata.create_all(bind=engine)

    # Start market data stream if enabled
    if settings.WEBSOCKET_ENABLED:
        await start_market_data_stream()

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down...")
    if settings.WEBSOCKET_ENABLED:
        await stop_market_data_stream()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AgentFX Trading Platform API",
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Set up CORS and WebSocket middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [str(origin) for origin in settings.BACKEND_CORS_ORIGINS]
        if settings.BACKEND_CORS_ORIGINS
        else ["*"]
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(runs.router, prefix=f"{settings.API_V1_STR}/runs", tags=["runs"])
app.include_router(
    strategies.router, prefix=f"{settings.API_V1_STR}/strategies", tags=["strategies"]
)
app.include_router(
    market_data_ws.router, prefix=settings.API_V1_STR, tags=["market-data-ws"]
)


@app.get("/")
async def root():
    return {"message": "Welcome to AgentFX API"}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "websocket_connections": sum(
            len(conns) for conns in manager.active_connections.values()
        ),
        "active_subscriptions": sum(
            len(subs) for subs in manager.subscriptions.values()
        ),
    }


# WebSocket test endpoint (kept for testing)
@app.websocket("/ws/test")
async def websocket_test(websocket: WebSocket):
    """Simple echo WebSocket for testing connectivity."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        logger.info("WebSocket test connection closed")
