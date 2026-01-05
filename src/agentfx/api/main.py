from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from .core.config import settings
from .routers import auth, runs, strategies, market_data, optimization
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
logging.getLogger("aiohttp").setLevel(logging.INFO)
logging.getLogger("asyncio").setLevel(logging.INFO)

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
    logger.info("Starting up AgentFX API...")

    # Start candle ingestion worker
    from .workers.candle_ingestion import start_ingestion_worker, stop_ingestion_worker

    await start_ingestion_worker()
    logger.info("Candle ingestion worker started")

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down...")
    await stop_ingestion_worker()
    logger.info("Candle ingestion worker stopped")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AgentFX Trading Platform API",
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Parse allowed origins once
_allowed_origins = (
    [origin.strip() for origin in settings.BACKEND_CORS_ORIGINS.split(",")]
    if settings.BACKEND_CORS_ORIGINS
    else ["*"]
)


class CORSPreflightMiddleware(BaseHTTPMiddleware):
    """
    Handle CORS preflight OPTIONS requests before they reach route dependencies.

    This is necessary because FastAPI evaluates route dependencies (like OAuth2PasswordBearer)
    before CORSMiddleware can short-circuit OPTIONS requests. Since OPTIONS requests don't
    include Authorization headers (per CORS spec), the auth dependencies fail with 400/401.

    This middleware intercepts OPTIONS requests early and returns proper CORS headers.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "")
            # Use origin if provided, otherwise use wildcard
            allow_origin = origin if origin else "*"

            logger.debug(
                f"CORS preflight: method={request.method}, origin={origin}, path={request.url.path}"
            )

            # For CORS preflight, we need to respond with appropriate headers
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": allow_origin,
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Origin, X-Requested-With",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Max-Age": "600",
                },
            )

        return await call_next(request)


# Set up CORS middleware (still needed for actual requests with credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add OPTIONS handler AFTER CORSMiddleware so it runs FIRST (LIFO order)
app.add_middleware(CORSPreflightMiddleware)

# Include routers
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(market_data.router, prefix=settings.API_V1_STR, tags=["market-data"])
app.include_router(runs.router, prefix=f"{settings.API_V1_STR}/runs", tags=["runs"])
app.include_router(
    strategies.router, prefix=f"{settings.API_V1_STR}/strategies", tags=["strategies"]
)
app.include_router(
    optimization.router, prefix=settings.API_V1_STR, tags=["optimization"]
)


@app.get("/")
async def root():
    return {"message": "Welcome to AgentFX API"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
