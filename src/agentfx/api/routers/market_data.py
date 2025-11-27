from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core.database import get_db
from ..services import market_data as market_data_service
from ..services import user as user_service

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/candles", response_model=schemas.CandleList)
def get_candles(
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
    run_id: str | None = Query(None, description="Filter by run ID"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    Retrieve OHLCV candles for a specific instrument and timeframe.

    Returns the most recent candles if no time range is specified.
    """
    candles, total = market_data_service.get_candles(
        db=db,
        exchange=exchange,
        instrument=instrument,
        timeframe=timeframe,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        run_id=run_id,
    )

    # Determine the actual time range of the returned candles
    from_ts = min(c.timestamp for c in candles) if candles else None
    to_ts = max(c.timestamp for c in candles) if candles else None

    return {
        "items": candles,
        "total": total,
        "from_time": from_ts,
        "to_time": to_ts,
        "count": len(candles),
    }


@router.get("/candles/latest", response_model=schemas.CandlePublic)
def get_latest_candle(
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
    candle = market_data_service.get_latest_candle(
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
def create_candles(
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
    candles = market_data_service.create_candles(
        db=db,
        exchange=candle_data.exchange,
        instrument=candle_data.instrument,
        timeframe=candle_data.timeframe,
        candles_data=candle_data.candles,
        run_id=run_id,
        update_if_exists=update_if_exists,
    )

    # Update the market data status
    market_data_service.update_market_data_status(
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
def get_market_data_status(
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
    query = db.query(models.MarketDataUpdate)

    if exchange:
        query = query.filter(models.MarketDataUpdate.exchange == exchange)
    if instrument:
        query = query.filter(models.MarketDataUpdate.instrument == instrument.upper())
    if timeframe:
        query = query.filter(models.MarketDataUpdate.timeframe == timeframe)

    return query.all()


@router.get("/instruments", response_model=list[schemas.InstrumentInfo])
def list_instruments(
    exchange: str | None = Query("oanda", description="Filter by exchange"),
    type: str | None = Query(
        None, description="Filter by instrument type (e.g., 'forex', 'crypto')"
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
):
    """
    List available trading instruments.

    This is a simplified implementation that returns a static list of instruments.
    In a production environment, this would query the exchange API or a configuration store.
    """
    # TODO: Replace with actual implementation that queries the exchange
    forex_pairs = [
        "EUR_USD",
        "GBP_USD",
        "USD_JPY",
        "AUD_USD",
        "USD_CAD",
        "NZD_USD",
        "USD_CHF",
    ]

    instruments = []
    for symbol in forex_pairs:
        if exchange and exchange.lower() != "oanda":
            continue
        if type and type.lower() != "forex":
            continue

        instruments.append(
            {
                "symbol": symbol,
                "name": f"{symbol[:3]}/{symbol[4:]}",
                "type": "forex",
                "pip_location": 4 if symbol != "USD_JPY" else 2,
                "display_precision": 5 if symbol != "USD_JPY" else 3,
                "trade_units_precision": 2,
                "minimum_trade_size": 1.0,
                "maximum_trade_size": 10000000.0,
            }
        )

    return instruments


@router.get("/exchanges/{exchange_id}", response_model=schemas.ExchangeInfo)
def get_exchange_info(
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

    return {
        "id": "oanda",
        "name": "OANDA",
        "status": "online",
        "timezone": "UTC",
        "server_time": datetime.utcnow(),
        "supported_timeframes": [
            schemas.Timeframe.M1,
            schemas.Timeframe.M5,
            schemas.Timeframe.M15,
            schemas.Timeframe.M30,
            schemas.Timeframe.H1,
            schemas.Timeframe.H4,
            schemas.Timeframe.D1,
            schemas.Timeframe.W1,
            schemas.Timeframe.MN1,
        ],
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
