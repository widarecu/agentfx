from __future__ import annotations

import os

from pydantic import BaseModel, Field

# Note: Environment settings are read directly via os.environ for simplicity in MVP

MAJOR_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
]


class IngestConfig(BaseModel):
    pairs: list[str] = Field(default_factory=lambda: MAJOR_PAIRS.copy())
    granularity: str = "M1"
    start_days: int = 365
    chunk_size: int = 5000  # OANDA max candles per request typically ~5000
    retry_max: int = 5
    retry_backoff_seconds: float = 1.0
    data_root: str = "data/raw"
    db_path: str = "data/registry.sqlite"
    force_start: bool = False


def env_str(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val


def env_int(name: str, default: int | None = None) -> int | None:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def env_float(name: str, default: float | None = None) -> float | None:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def load_config() -> IngestConfig:
    cfg = IngestConfig()
    pairs = env_str("INGEST_PAIRS")
    if pairs:
        cfg.pairs = [p.strip() for p in pairs.split(",") if p.strip()]
    start_days = env_int("INGEST_START_DAYS")
    if start_days is not None:
        cfg.start_days = start_days
    cfg.force_start = env_bool("INGEST_FORCE_START", False)
    return cfg


# Broker environment helpers (used elsewhere)


def oanda_env() -> str:
    return env_str("OANDA_ENV", "practice")


def oanda_api_host() -> str:
    return env_str("OANDA_API_HOST", "api-fxpractice.oanda.com")


def oanda_api_key() -> str | None:
    return env_str("OANDA_API_KEY")


def oanda_account_id() -> str | None:
    return env_str("OANDA_ACCOUNT_ID")


# Backtest defaults (from env)


def backtest_default_spread_pips() -> float:
    return env_float("BACKTEST_SPREAD_PIPS", 0.5) or 0.5


def backtest_default_slippage_pips() -> float:
    # Default 0.0: OANDA does not bill slippage as a line item, and
    # empirical comparison of our backtests against real OANDA fills on
    # EUR_USD M1 showed our previous 0.1-pip/side default was phantom
    # cost. Override via BACKTEST_SLIPPAGE_PIPS for news-window stress
    # tests or exotic pairs where execution-quality buffer is wanted.
    return env_float("BACKTEST_SLIPPAGE_PIPS", 0.0) or 0.0


def policy_default_max_abs_position() -> int:
    return env_int("POLICY_MAX_ABS_POSITION", 1) or 1


def policy_default_max_trades_per_hour() -> int:
    return env_int("POLICY_MAX_TRADES_PER_HOUR", 60) or 60


def policy_default_daily_loss_threshold() -> float:
    return env_float("POLICY_DAILY_LOSS_THRESHOLD", 0.02) or 0.02
