#!/usr/bin/env python3
"""Seed instrument metadata for first-time deployment.

This script populates the instruments table with supported FX pairs and,
when OANDA credentials are available, refreshes the per-instrument
financing (swap) rates used by the backtest engine.

Usage:
    PYTHONPATH=src python scripts/seed_instruments.py
        Seed missing instruments and refresh financing rates.

    PYTHONPATH=src python scripts/seed_instruments.py --refresh-financing
        Skip metadata seed; only refresh financing rates for existing rows.

    PYTHONPATH=src python scripts/seed_instruments.py --skip-financing
        Seed metadata only; do not call OANDA for financing rates.

The candle_ingestion worker handles all candle data (backfill and continuous
updates) — this script only owns metadata and financing rates.
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentfx.adapters.oanda import OandaClient, OandaError
from agentfx.api.core.config import settings
from agentfx.api.core.database import Base, SessionLocal, engine
from agentfx.api.models.instrument import Instrument

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


_FALLBACK_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "financing_fallback.yaml"
)


def _load_financing_fallback() -> dict[str, dict[str, float]]:
    """Load the static fallback rates keyed by symbol.

    Returns an empty dict if the file is missing or malformed (the refresh
    will then simply skip the fallback and persist whatever OANDA returned).
    """
    try:
        with _FALLBACK_PATH.open() as f:
            data = yaml.safe_load(f) or {}
        return data.get("financing_fallback", {}) or {}
    except FileNotFoundError:
        logger.warning(f"Financing fallback file not found at {_FALLBACK_PATH}")
        return {}
    except Exception as e:
        logger.warning(f"Could not parse {_FALLBACK_PATH}: {e}")
        return {}


def refresh_financing_rates() -> dict[str, int]:
    """Fetch latest OANDA financing rates and persist them on instruments.

    Failures (OANDA unreachable, missing credentials, malformed response) are
    logged but do not raise — they leave existing rows untouched and allow
    deploys to proceed even when OANDA is intermittently unavailable.

    Returns:
        Dictionary with counts: refreshed, failed, skipped (no row in DB yet).
    """
    if not settings.OANDA_API_KEY or not settings.OANDA_ACCOUNT_ID:
        logger.warning(
            "OANDA_API_KEY or OANDA_ACCOUNT_ID not set — skipping financing refresh."
        )
        return {"refreshed": 0, "failed": 0, "skipped": len(SUPPORTED_INSTRUMENTS)}

    try:
        client = OandaClient()
    except OandaError as e:
        logger.warning(f"Could not initialise OandaClient: {e}")
        return {"refreshed": 0, "failed": len(SUPPORTED_INSTRUMENTS), "skipped": 0}

    fallback_table = _load_financing_fallback()

    db = SessionLocal()
    refreshed = 0
    fallback_used = 0
    failed = 0
    skipped = 0

    try:
        for instr_data in SUPPORTED_INSTRUMENTS:
            symbol = instr_data["symbol"]
            row = db.query(Instrument).filter(Instrument.symbol == symbol).first()
            if row is None:
                logger.warning(
                    f"{symbol}: no row in instruments table — run seed first."
                )
                skipped += 1
                continue

            try:
                rates = client.get_instrument_financing(symbol)
            except OandaError as e:
                logger.warning(f"{symbol}: financing fetch failed ({e})")
                failed += 1
                continue

            # OANDA's practice account returns "0"/"0" for GBP- and JPY-quoted
            # pairs (verified 2026-05-04 across all 127 instruments). Treat
            # 0/0 as missing-data and substitute from the static fallback so
            # backtests don't silently underestimate carry-trade strategies.
            if (
                rates["long_rate"] == 0.0
                and rates["short_rate"] == 0.0
                and symbol in fallback_table
            ):
                fb = fallback_table[symbol]
                logger.warning(
                    f"{symbol}: OANDA returned 0/0; using fallback "
                    f"long={fb['long_rate']:.6f}, short={fb['short_rate']:.6f}"
                )
                rates = {
                    "long_rate": float(fb["long_rate"]),
                    "short_rate": float(fb["short_rate"]),
                    "fetched_at": rates.get("fetched_at", datetime.now(UTC)),
                }
                fallback_used += 1

            row.financing_long_rate = rates["long_rate"]
            row.financing_short_rate = rates["short_rate"]
            row.financing_updated_at = rates.get("fetched_at", datetime.now(UTC))
            logger.info(
                f"{symbol}: long={rates['long_rate']:.6f}, "
                f"short={rates['short_rate']:.6f}"
            )
            refreshed += 1

        db.commit()

    finally:
        db.close()

    return {
        "refreshed": refreshed,
        "fallback_used": fallback_used,
        "failed": failed,
        "skipped": skipped,
    }


def main():
    """Main entry point for the instrument seeder."""
    parser = argparse.ArgumentParser(
        description="Seed instrument metadata and refresh financing rates."
    )
    parser.add_argument(
        "--refresh-financing",
        action="store_true",
        help="Skip metadata seed; only refresh financing rates for existing rows.",
    )
    parser.add_argument(
        "--skip-financing",
        action="store_true",
        help="Seed metadata only; do not call OANDA for financing rates.",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("AgentFX Instrument Seeder")
    logger.info("=" * 60)
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Database URL: {settings.DATABASE_URL[:50]}...")
    logger.info("")

    if not args.refresh_financing:
        logger.info("Step 1: seeding instrument metadata")
        results = seed_instruments()
        logger.info("")
        logger.info("Metadata Summary")
        logger.info(f"  Created: {results['created']} instruments")
        logger.info(f"  Skipped: {results['skipped']} instruments (already exist)")
        logger.info("")

    if not args.skip_financing:
        logger.info("Step 2: refreshing OANDA financing rates")
        fin_results = refresh_financing_rates()
        logger.info("")
        logger.info("Financing Summary")
        logger.info(f"  Refreshed:     {fin_results['refreshed']} instruments")
        logger.info(
            f"  Fallback used: {fin_results.get('fallback_used', 0)} instruments"
        )
        logger.info(f"  Failed:        {fin_results['failed']} instruments")
        logger.info(f"  Skipped:       {fin_results['skipped']} instruments")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
