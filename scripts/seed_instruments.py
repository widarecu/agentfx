#!/usr/bin/env python3
"""Seed instrument metadata for first-time deployment.

This script populates the instruments table with supported FX pairs.
It does NOT fetch candle data - that is handled by the ingestion worker.

Usage:
    PYTHONPATH=src python scripts/seed_instruments.py

This should be run ONCE during initial deployment.
The candle_ingestion worker handles all candle data (backfill and continuous updates).
"""

import os
import sys
import logging

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentfx.api.core.database import SessionLocal, engine
from agentfx.api.core.config import settings
from agentfx.api.models.instrument import Instrument
from agentfx.api.core.database import Base

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Supported FX pairs per MVP_CONSTRAINTS.md (major pairs only)
SUPPORTED_INSTRUMENTS = [
    {"symbol": "EUR_USD", "display_name": "EUR/USD", "pip_location": -4},
    {"symbol": "GBP_USD", "display_name": "GBP/USD", "pip_location": -4},
    {"symbol": "USD_JPY", "display_name": "USD/JPY", "pip_location": -2},
    {"symbol": "AUD_USD", "display_name": "AUD/USD", "pip_location": -4},
    {"symbol": "USD_CHF", "display_name": "USD/CHF", "pip_location": -4},
    {"symbol": "USD_CAD", "display_name": "USD/CAD", "pip_location": -4},
]


def seed_instruments() -> dict[str, int]:
    """Seed instrument metadata.

    This function is idempotent - it skips instruments that already exist.

    Returns:
        Dictionary with counts of created/skipped instruments.
    """
    # Ensure the instruments table exists
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    created = 0
    skipped = 0

    try:
        for instr_data in SUPPORTED_INSTRUMENTS:
            existing = (
                db.query(Instrument)
                .filter(Instrument.symbol == instr_data["symbol"])
                .first()
            )

            if existing:
                logger.info(f"Skipping {instr_data['symbol']} (already exists)")
                skipped += 1
                continue

            instrument = Instrument(
                symbol=instr_data["symbol"],
                display_name=instr_data["display_name"],
                pip_location=instr_data.get("pip_location", -4),
                instrument_type="CURRENCY",
                is_tradeable=True,
                is_active=True,
            )
            db.add(instrument)
            logger.info(f"Created instrument: {instr_data['symbol']}")
            created += 1

        db.commit()

    finally:
        db.close()

    return {"created": created, "skipped": skipped}


def main():
    """Main entry point for the instrument seeder."""
    logger.info("=" * 60)
    logger.info("AgentFX Instrument Seeder")
    logger.info("=" * 60)
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Database URL: {settings.DATABASE_URL[:50]}...")
    logger.info("")
    logger.info("NOTE: This script seeds INSTRUMENT METADATA only.")
    logger.info("Candle data is handled by the ingestion worker on server startup.")
    logger.info("")

    results = seed_instruments()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Seeding Summary")
    logger.info("=" * 60)
    logger.info(f"  Created: {results['created']} instruments")
    logger.info(f"  Skipped: {results['skipped']} instruments (already exist)")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
