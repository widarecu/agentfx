"""Fixtures for evolution-pipeline integration tests.

These tests run the orchestrator against real Postgres (the test stack on
port 5433) with synthetic-but-realistic candle data. The point is to
exercise the wiring between orchestrator → fitness → backtest → DB
persistence, not to produce profitable strategies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest


@pytest.fixture
def seed_evolution_candles(db_connection, seed_instruments):
    """Seed ~7 days of M1 candles for EUR_USD with random-walk pricing.

    7 days × 24 × 60 = 10,080 candles — enough for a 4-fold walk-forward
    split where each fold has ~2,500 bars (sufficient for the strategies
    to produce nonzero trade counts on a typical seed).

    Pricing is a random walk with std=0.0002 per minute (realistic FX
    micro-volatility), with intra-bar OHLC spread ~0.0003.
    """
    cursor = db_connection.cursor()
    n_minutes = 7 * 24 * 60  # 10,080
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)

    # Use a fixed seed so test data is reproducible.
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.0002, n_minutes)
    closes = 1.10 * np.exp(np.cumsum(returns))

    spread_half = 0.00015
    rows = []
    for i in range(n_minutes):
        ts = base_time + timedelta(minutes=i)
        c = float(closes[i])
        o = float(closes[i - 1]) if i > 0 else c
        h = max(o, c) + spread_half
        l = min(o, c) - spread_half  # noqa: E741
        vol = int(1000 + rng.integers(0, 200))
        rows.append((ts, o, h, l, c, vol))

    # Bulk insert via executemany for speed
    cursor.executemany(
        """
        INSERT INTO candles (
            exchange, instrument, timeframe, timestamp,
            open, high, low, close, volume, complete
        )
        VALUES ('oanda', 'EUR_USD', 'M1', %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING
        """,
        rows,
    )
    db_connection.commit()
    cursor.close()

    return {
        "instrument": "EUR_USD",
        "timeframe": "M1",
        "count": n_minutes,
        "start": base_time,
        "end": base_time + timedelta(minutes=n_minutes),
    }
