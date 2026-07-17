from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..core.metrics import market_metrics
from ..core.rate_limiter import market_data_rate_limit
from ..services import user as user_service
from ..services.market_data import MarketDataService

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/candles", response_model=schemas.CandleList)
@market_data_rate_limit(endpoint_type="candles", per_user=True)
async def get_candles(
    request: Request,
    exchange: str = Query(..., description="Exchange identifier (e.g., 'oanda')"),
    instrument: str = Query(..., description="Instrument symbol (e.g., 'AUD_USD')"),
    timeframe: schemas.Timeframe = Query(
        ..., description="Candle timeframe (e.g., 'M1', 'M5', 'H1')"
    ),
    from_time: datetime | None = Query(
        None, description="Filter candles after this timestamp"
    ),
    to_time: datetime | None = Query(
        None, description="Filter candles before this timestamp"
    ),
    limit: int = Query(
        1000, le=10000, description="Maximum number of candles to return"
    ),
    offset: int = Query(
        0, ge=0, description="Number of candles to skip (for pagination)"
    ),
    run_id: str | None = Query(None, description="Filter by run ID"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Retrieve OHLCV candles for a specific instrument and timeframe.

    Returns the most recent candles if no time range is specified.
    """
    try:
        candles, total = MarketDataService.get_candles(
            db=db,
            exchange=exchange,
            instrument=instrument,
            timeframe=timeframe,
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            offset=offset,
            run_id=run_id,
        )

        # Determine the actual time range of the returned candles
        # Use query parameters if provided, otherwise use actual data range
        if from_time is not None and to_time is not None:
            # Use query parameters when both are provided
            response_from_time = from_time
            response_to_time = to_time
        elif candles:
            # Use actual data range when query parameters not provided but data exists
            response_from_time = min(c.timestamp for c in candles)
            response_to_time = max(c.timestamp for c in candles)
        else:
            # Use reasonable defaults when no query params and no data
            from datetime import datetime

            now = datetime.now(UTC)
            response_from_time = now
            response_to_time = now

        # Record metrics
        market_metrics.record_success("candles")
        market_metrics.record_request_time("candles", 0, str(current_user.id))

        return {
            "items": candles,
            "total": total,
            "from_time": response_from_time,
            "to_time": response_to_time,
            "count": len(candles),
        }
    except ValueError as e:
        market_metrics.record_error("candles", "ValueError")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        market_metrics.record_error("candles", type(e).__name__)
        raise


@router.get("/candles/latest", response_model=schemas.CandlePublic)
@market_data_rate_limit(endpoint_type="candles", per_user=True)
async def get_latest_candle(
    request: Request,
    exchange: str = Query(..., description="Exchange identifier (e.g., 'oanda')"),
    instrument: str = Query(..., description="Instrument symbol (e.g., 'AUD_USD')"),
    timeframe: schemas.Timeframe = Query(
        ..., description="Candle timeframe (e.g., 'M1', 'M5', 'H1')"
    ),
    run_id: str | None = Query(None, description="Filter by run ID"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """Get the most recent candle for the specified instrument and timeframe."""
    candle = MarketDataService.get_latest_candle(
        db=db,
        exchange=exchange,
        instrument=instrument,
        timeframe=timeframe,
        run_id=run_id,
    )

    if not candle:
        raise HTTPException(
            status_code=404,
            detail=f"No candles found for {exchange}:{instrument}:{timeframe}",
        )

    return candle


@router.post("/candles", response_model=list[schemas.CandlePublic])
@market_data_rate_limit(endpoint_type="candles", per_user=True)
async def create_candles(
    request: Request,
    candle_data: schemas.CandleBulkCreate,
    run_id: str | None = Query(
        None, description="Associate candles with a specific run"
    ),
    update_if_exists: bool = Query(True, description="Update existing candles"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Create or update multiple candles in bulk.

    This endpoint is typically used by data collection services.
    """
    candles = MarketDataService.create_candles(
        db=db,
        exchange=candle_data.exchange,
        instrument=candle_data.instrument,
        timeframe=candle_data.timeframe,
        candles_data=candle_data.candles,
        run_id=run_id,
        update_if_exists=update_if_exists,
    )

    # Update the market data status
    MarketDataService.update_market_data_status(
        db=db,
        exchange=candle_data.exchange,
        instrument=candle_data.instrument,
        timeframe=candle_data.timeframe,
        last_updated=datetime.utcnow(),
        last_candle_timestamp=max(c.timestamp for c in candles) if candles else None,
        status="idle",
    )

    return candles


@router.get("/status", response_model=list[schemas.MarketDataUpdatePublic])
@market_data_rate_limit(endpoint_type="status", per_user=True)
async def get_market_data_status(
    request: Request,
    exchange: str | None = Query(None, description="Filter by exchange"),
    instrument: str | None = Query(None, description="Filter by instrument"),
    timeframe: schemas.Timeframe | None = Query(
        None, description="Filter by timeframe"
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get the status of market data updates for the specified filters.

    Returns all statuses if no filters are provided.
    """
    return MarketDataService.get_market_data_status(
        db=db,
        exchange=exchange,
        instrument=instrument,
        timeframe=timeframe,
    )


@router.get("/instruments", response_model=list[schemas.InstrumentInfo])
@market_data_rate_limit(endpoint_type="instruments", per_user=True)
async def list_instruments(
    request: Request,
    exchange: str | None = Query("oanda", description="Filter by exchange"),
    type: str | None = Query(
        None, description="Filter by instrument type (e.g., 'forex', 'crypto')"
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List available trading instruments.
    """
    # Get instruments from service
    instrument_symbols = MarketDataService.get_instruments(db=db, exchange=exchange)

    # Convert to InstrumentInfo format
    instruments = []
    for symbol in instrument_symbols:
        symbol_str = str(symbol)  # Ensure it's a string
        if type and type.lower() != "forex":
            continue

        instruments.append(
            {
                "symbol": symbol_str,
                "name": f"{symbol_str[:3]}/{symbol_str[4:]}",
                "type": "forex",
                "pip_location": 4 if symbol_str != "USD_JPY" else 2,
                "display_precision": 5 if symbol_str != "USD_JPY" else 3,
                "trade_units_precision": 2,
                "minimum_trade_size": 1.0,
                "maximum_trade_size": 10000000.0,
                "margin_rate": None,
                "maximum_guaranteed_stop_loss_distance": None,
                "maximum_order_units": None,
            }
        )

    return instruments


@router.get("/timeframes", response_model=list[schemas.TimeframeInfo])
@market_data_rate_limit(endpoint_type="instruments", per_user=True)
async def list_timeframes(
    request: Request,
    exchange: str | None = Query("oanda", description="Filter by exchange"),
    instrument: str | None = Query(None, description="Filter by instrument"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """List available timeframes from ingested candle data."""
    return MarketDataService.get_timeframes(
        db=db, exchange=exchange, instrument=instrument
    )


@router.get("/exchanges/{exchange_id}", response_model=schemas.ExchangeInfo)
@market_data_rate_limit(endpoint_type="instruments", per_user=True)
async def get_exchange_info(
    request: Request,
    exchange_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get information about a specific exchange.

    This is a simplified implementation that returns static data.
    In a production environment, this would query the exchange API or a configuration store.
    """
    if exchange_id.lower() != "oanda":
        raise HTTPException(
            status_code=404, detail=f"Exchange '{exchange_id}' not found"
        )

    # Get timeframes dynamically from DB
    db_timeframes = MarketDataService.get_timeframes(db=db, exchange=exchange_id)
    supported_tfs = (
        [tf["code"] for tf in db_timeframes]
        if db_timeframes
        else [
            "M1",
            "M5",
            "M15",
            "M30",
            "H1",
            "H4",
            "D",
            "W",
            "M",
        ]
    )

    return {
        "id": "oanda",
        "name": "OANDA",
        "status": "online",
        "timezone": "UTC",
        "server_time": datetime.utcnow(),
        "supported_timeframes": supported_tfs,
        "rate_limit_requests_per_second": 100,
        "is_authenticated": True,
        "features": {
            "historical_data": True,
            "streaming": True,
            "fractional_pips": True,
            "guaranteed_stop_loss": False,
            "trailing_stop": True,
        },
    }


@router.get("/exchange-info", response_model=dict)
@market_data_rate_limit(endpoint_type="instruments", per_user=True)
async def get_exchange_info_query(
    request: Request,
    exchange: str = Query(..., description="Exchange identifier"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get exchange information using query parameter.

    This endpoint matches the test expectation for /exchange-info?exchange=oanda
    """
    return MarketDataService.get_exchange_info(db=db, exchange=exchange)


@router.get("/metrics", response_model=dict)
@market_data_rate_limit(endpoint_type="status", per_user=True)
async def get_market_data_metrics(
    request: Request,
    hours: int = Query(1, ge=1, le=24, description="Time window in hours"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Get market data system metrics and health information.

    Returns performance, usage, and data quality metrics.
    """
    # Clean up old metrics
    market_metrics.cleanup_old_metrics()

    # Get various metric types
    endpoint_stats = market_metrics.get_endpoint_stats("candles", hours=hours)
    usage_stats = market_metrics.get_usage_stats(hours=hours)
    quality_stats = market_metrics.get_data_quality_stats(hours=hours)
    health_summary = market_metrics.get_health_summary()

    return {
        "health": health_summary,
        "performance": endpoint_stats,
        "usage": usage_stats,
        "data_quality": quality_stats,
        "system": {
            "active_users": len(
                [k for k in market_metrics.user_usage.keys() if k.startswith("user:")]
            ),
            "total_requests": sum(market_metrics.endpoint_usage.values()),
            "error_rate": endpoint_stats.get("error_rate", 0),
            "avg_response_time_ms": endpoint_stats.get("avg_response_time_ms", 0),
        },
    }
