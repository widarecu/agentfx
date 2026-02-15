#!/usr/bin/env python3
"""
One-time repair script for candle data gaps.

Scans all candle data in the database, detects gaps using a tight threshold,
and heals them by fetching missing candles from OANDA.

Usage:
    # Repair all instruments and granularities:
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/repair_candle_data.py

    # Repair specific instrument:
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/repair_candle_data.py -i EUR_USD

    # Repair specific granularity:
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/repair_candle_data.py -g M1

    # Dry run (detect gaps without healing):
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/repair_candle_data.py --dry-run
"""

import argparse
import logging
import sys
import time

sys.path.insert(0, "src")

from agentfx.api.core.config import settings


def run_dry(instruments: list[str], granularities: list[str]) -> None:
    """Detect gaps and report counts without healing."""
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.services.market_data import MarketDataService
    from agentfx.api.workers.candle_ingestion import (
        GAP_DETECTION_MULTIPLIER,
        GRANULARITY_SECONDS,
    )
    from agentfx.api.workers.market_calendar import is_market_closure

    logger = logging.getLogger("repair")
    db = SessionLocal()

    try:
        total_gaps = 0
        for instrument in instruments:
            for granularity in granularities:
                granularity_seconds = GRANULARITY_SECONDS.get(granularity, 60)
                max_gap_seconds = granularity_seconds * GAP_DETECTION_MULTIPLIER

                timestamps = MarketDataService.get_candle_timestamps(
                    db, "oanda", instrument, granularity
                )
                if len(timestamps) < 2:
                    logger.info(f"{instrument}:{granularity} - no data")
                    continue

                gaps = 0
                for i in range(1, len(timestamps)):
                    delta = (timestamps[i] - timestamps[i - 1]).total_seconds()
                    if delta > max_gap_seconds:
                        is_closure, _ = is_market_closure(
                            timestamps[i - 1], timestamps[i]
                        )
                        if not is_closure:
                            gaps += 1

                if gaps > 0:
                    logger.info(f"{instrument}:{granularity} - {gaps} healable gaps")
                    total_gaps += gaps
                else:
                    logger.info(f"{instrument}:{granularity} - clean")

        logger.info(f"Total healable gaps found: {total_gaps}")
    finally:
        db.close()


def run_repair(instruments: list[str], granularities: list[str]) -> None:
    """Run the actual repair."""
    from agentfx.api.workers.candle_ingestion import repair_candle_data

    logger = logging.getLogger("repair")

    start = time.time()
    stats = repair_candle_data(
        instruments=instruments,
        granularities=granularities,
    )
    elapsed = time.time() - start

    logger.info(f"Repair completed in {elapsed / 60:.1f} minutes")
    logger.info(f"Total candles healed: {stats['total_healed']}")

    for instrument, granularities_data in stats["by_instrument"].items():
        for gran, count in granularities_data.items():
            if count > 0:
                logger.info(f"  {instrument}:{gran} = {count} candles healed")
            elif count == -1:
                logger.error(f"  {instrument}:{gran} = FAILED")


def main():
    parser = argparse.ArgumentParser(description="Repair candle data gaps")
    parser.add_argument(
        "--instrument",
        "-i",
        help="Specific instrument to repair (e.g., EUR_USD). Default: all configured.",
    )
    parser.add_argument(
        "--granularity",
        "-g",
        help="Specific granularity to repair (e.g., M1). Default: all configured.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect gaps and report but don't heal them.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("repair")

    instruments = [args.instrument] if args.instrument else settings.instruments_list
    granularities = (
        [args.granularity] if args.granularity else settings.granularities_list
    )

    logger.info("Starting candle data repair...")
    logger.info(f"Instruments: {instruments}")
    logger.info(f"Granularities: {granularities}")
    logger.info(f"Dry run: {args.dry_run}")

    if args.dry_run:
        run_dry(instruments, granularities)
    else:
        run_repair(instruments, granularities)


if __name__ == "__main__":
    main()
