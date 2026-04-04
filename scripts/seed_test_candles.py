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

        # Generate candles for multiple timeframes so the timeframe dropdown populates
        base_time = datetime.now(UTC) - timedelta(days=1)
        base_price = 1.1000
        candles = []

        timeframes = [
            ("M1", 1, 200),
            ("M5", 5, 50),
            ("M10", 10, 30),
            ("M30", 30, 20),
            ("H1", 60, 10),
            ("H4", 240, 5),
            ("D", 1440, 3),
        ]

        for tf_name, tf_minutes, count in timeframes:
            for i in range(count):
                timestamp = base_time + timedelta(minutes=i * tf_minutes)
                drift = (i % 20 - 10) * 0.0001
                open_price = base_price + drift
                close_price = open_price + ((i % 7) - 3) * 0.00005
                high_price = max(open_price, close_price) + 0.0002
                low_price = min(open_price, close_price) - 0.0002

                candles.append(
                    Candle(
                        exchange="oanda",
                        instrument="EUR_USD",
                        timeframe=tf_name,
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
        print(
            f"Seeded {len(candles)} candles across {len(timeframes)} timeframes for EUR_USD"
        )

    except Exception as e:
        print(f"Error seeding candles: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    seed_candles()
