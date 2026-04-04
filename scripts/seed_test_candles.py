#!/usr/bin/env python3
"""Seed synthetic candle data for E2E tests.

Creates a small set of M1 candles for EUR_USD so that E2E tests can:
- Load and display candle data in the Market Data page
- Render candlestick charts
- Populate the timeframes dropdown (derived from ingested data)

This script is for CI/E2E environments ONLY. Production and staging
use real OANDA data via the ingestion worker.

Usage:
    ENVIRONMENT=test PYTHONPATH=src python scripts/seed_test_candles.py
"""

import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentfx.api.core.database import SessionLocal
from agentfx.api.models.market_data import Candle


def seed_candles():
    db = SessionLocal()
    try:
        # Check if candles already exist
        existing = (
            db.query(Candle)
            .filter(
                Candle.instrument == "EUR_USD",
                Candle.timeframe == "M1",
            )
            .count()
        )

        if existing > 0:
            print(f"Candles already exist ({existing} EUR_USD M1). Skipping seed.")
            return

        # Generate 200 M1 candles for EUR_USD starting from 1 day ago
        base_time = datetime.now(UTC) - timedelta(days=1)
        base_price = 1.1000
        candles = []

        for i in range(200):
            timestamp = base_time + timedelta(minutes=i)
            # Simulate small price movements
            drift = (i % 20 - 10) * 0.0001
            open_price = base_price + drift
            close_price = open_price + ((i % 7) - 3) * 0.00005
            high_price = max(open_price, close_price) + 0.0002
            low_price = min(open_price, close_price) - 0.0002

            candles.append(
                Candle(
                    exchange="oanda",
                    instrument="EUR_USD",
                    timeframe="M1",
                    timestamp=timestamp,
                    open=round(open_price, 5),
                    high=round(high_price, 5),
                    low=round(low_price, 5),
                    close=round(close_price, 5),
                    volume=100.0,
                    complete=True,
                )
            )

        db.bulk_save_objects(candles)
        db.commit()
        print(f"Seeded {len(candles)} M1 candles for EUR_USD")

    except Exception as e:
        print(f"Error seeding candles: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    seed_candles()
