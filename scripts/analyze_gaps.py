#!/usr/bin/env python3
"""
Targeted gap analysis for a single instrument/granularity.

Scans all candle data, detects gaps, classifies each against the market calendar,
then validates unexplained gaps against OANDA.

Usage:
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/analyze_gaps.py AUD_USD M1
"""

import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import requests  # type: ignore[import-untyped]

sys.path.insert(0, "src")

from agentfx.api.core.config import settings
from agentfx.api.core.database import SessionLocal
from agentfx.api.services.market_data import MarketDataService
from agentfx.api.workers.candle_ingestion import (
    GAP_DETECTION_MULTIPLIER,
    GRANULARITY_SECONDS,
)
from agentfx.api.workers.market_calendar import is_market_closure

OANDA_API_KEY = settings.OANDA_API_KEY
OANDA_HOST = settings.OANDA_API_HOST


def fetch_oanda_count(
    instrument: str, granularity: str, from_ts: datetime, to_ts: datetime
) -> int:
    """Ask OANDA how many candles it has for a gap range. Returns candle count."""
    url = f"https://{OANDA_HOST}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    params = {
        "granularity": granularity,
        "from": from_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price": "M",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            candles = r.json().get("candles", [])
            return len([c for c in candles if c.get("complete", True)])
        return -1  # API error
    except Exception:
        return -1


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/analyze_gaps.py <INSTRUMENT> <GRANULARITY>")
        print("Example: python scripts/analyze_gaps.py AUD_USD M1")
        sys.exit(1)

    instrument = sys.argv[1]
    granularity = sys.argv[2]
    gran_seconds = GRANULARITY_SECONDS.get(granularity, 60)
    threshold = gran_seconds * GAP_DETECTION_MULTIPLIER

    print(f"{'=' * 70}")
    print(f"GAP ANALYSIS: {instrument} {granularity}")
    print(f"{'=' * 70}")
    print(
        f"Candle interval: {gran_seconds}s | Gap threshold: {threshold}s ({threshold / 60:.0f} min)"
    )
    print()

    # Step 1: Load timestamps
    db = SessionLocal()
    timestamps = MarketDataService.get_candle_timestamps(
        db, "oanda", instrument, granularity
    )
    db.close()

    print(f"Total candles in DB: {len(timestamps):,}")
    if len(timestamps) < 2:
        print("Not enough data.")
        return

    first = timestamps[0]
    last = timestamps[-1]
    print(f"Date range: {first.date()} to {last.date()}")
    print()

    # Step 2: Detect all gaps
    gaps = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if delta > threshold:
            gaps.append(
                {
                    "from": timestamps[i - 1],
                    "to": timestamps[i],
                    "seconds": delta,
                }
            )

    print(f"Total gaps > {threshold}s: {len(gaps)}")
    print()

    # Step 3: Classify gaps
    weekend = []
    holiday = []
    extended = []
    early_close = []
    low_liq = []
    unexplained = []

    for g in gaps:
        is_closure, reason = is_market_closure(g["from"], g["to"])
        g["closure"] = is_closure
        g["reason"] = reason
        if not is_closure:
            unexplained.append(g)
        elif reason == "weekend":
            weekend.append(g)
        elif reason.startswith("extended_closure"):
            extended.append(g)
        elif reason.startswith("holiday"):
            holiday.append(g)
        elif reason.startswith("early_close"):
            early_close.append(g)
        elif reason == "low_liquidity":
            low_liq.append(g)

    print("--- Classification ---")
    print(f"  Weekend:          {len(weekend)}")
    print(f"  Extended closure:  {len(extended)}")
    print(f"  Holiday:          {len(holiday)}")
    print(f"  Early close:      {len(early_close)}")
    print(f"  Low liquidity:    {len(low_liq)}")
    print(f"  UNEXPLAINED:      {len(unexplained)}")
    print()

    if not unexplained:
        print("No unexplained gaps. Data is clean.")
        return

    # Step 4: Validate unexplained gaps against OANDA
    print(f"--- Validating {len(unexplained)} unexplained gaps against OANDA ---")
    print(f"Estimated time: ~{len(unexplained) * 0.5 / 60:.1f} min")
    print()

    platform_missing = []  # OANDA has data we don't
    oanda_no_data = []  # OANDA also has no data
    partial = []  # OANDA has some but not all
    api_errors = []

    for i, g in enumerate(unexplained):
        if (i + 1) % 100 == 0 or i == 0:
            print(f"  [{i + 1}/{len(unexplained)}]", flush=True)

        expected = max(1, int(g["seconds"] / gran_seconds) - 1)
        oanda_count = fetch_oanda_count(instrument, granularity, g["from"], g["to"])

        if oanda_count < 0:
            api_errors.append(g)
        elif oanda_count == 0:
            oanda_no_data.append(g)
        elif oanda_count >= expected:
            platform_missing.append(g)
            g["oanda_count"] = oanda_count
            g["expected"] = expected
        else:
            partial.append(g)
            g["oanda_count"] = oanda_count
            g["expected"] = expected

        time.sleep(0.3)

    print()
    print(f"{'=' * 70}")
    print(f"RESULTS: {instrument} {granularity}")
    print(f"{'=' * 70}")
    print()
    print(f"Total candles:        {len(timestamps):,}")
    print(f"Total gaps:           {len(gaps)}")
    print(f"Market closure gaps:  {len(gaps) - len(unexplained)}")
    print(f"Unexplained gaps:     {len(unexplained)}")
    print()
    print("--- OANDA Validation ---")
    print(
        f"  PLATFORM_MISSING:   {len(platform_missing):>5}  (OANDA has full data, we're missing it)"
    )
    print(f"  PARTIAL_DATA:       {len(partial):>5}  (OANDA has some, not all)")
    print(
        f"  OANDA_NO_DATA:      {len(oanda_no_data):>5}  (OANDA also has nothing - legitimate)"
    )
    print(f"  API_ERROR:          {len(api_errors):>5}  (couldn't validate)")
    print()

    # Hour distribution of OANDA_NO_DATA
    if oanda_no_data:
        hour_counts = defaultdict(int)
        for g in oanda_no_data:
            hour_counts[g["from"].hour] += 1
        print("--- OANDA_NO_DATA hour distribution (UTC) ---")
        for h in sorted(hour_counts.keys()):
            bar = "#" * hour_counts[h]
            print(f"  {h:02d}:00  {hour_counts[h]:>4}  {bar}")
        print()

    # Hour distribution of PLATFORM_MISSING
    if platform_missing:
        hour_counts = defaultdict(int)
        for g in platform_missing:
            hour_counts[g["from"].hour] += 1
        print("--- PLATFORM_MISSING hour distribution (UTC) ---")
        for h in sorted(hour_counts.keys()):
            bar = "#" * min(hour_counts[h], 60)
            print(f"  {h:02d}:00  {hour_counts[h]:>4}  {bar}")
        print()

    # Duration distribution of PLATFORM_MISSING
    if platform_missing:
        dur_buckets = {
            "<10min": 0,
            "10-30min": 0,
            "30-60min": 0,
            "1-6h": 0,
            "6-24h": 0,
            ">24h": 0,
        }
        for g in platform_missing:
            s = g["seconds"]
            if s < 600:
                dur_buckets["<10min"] += 1
            elif s < 1800:
                dur_buckets["10-30min"] += 1
            elif s < 3600:
                dur_buckets["30-60min"] += 1
            elif s < 21600:
                dur_buckets["1-6h"] += 1
            elif s < 86400:
                dur_buckets["6-24h"] += 1
            else:
                dur_buckets[">24h"] += 1
        print("--- PLATFORM_MISSING duration distribution ---")
        for bucket, count in dur_buckets.items():
            bar = "#" * min(count, 60)
            print(f"  {bucket:>10}  {count:>4}  {bar}")
        print()

    # Sample of PLATFORM_MISSING gaps
    if platform_missing:
        print("--- Sample PLATFORM_MISSING gaps (first 20) ---")
        print(
            f"  {'Gap Start':<24} {'Gap End':<24} {'Duration':>10} {'Expected':>8} {'OANDA':>6}"
        )
        for g in platform_missing[:20]:
            dur = timedelta(seconds=g["seconds"])
            print(
                f"  {str(g['from']):<24} {str(g['to']):<24} {str(dur):>10} {g['expected']:>8} {g['oanda_count']:>6}"
            )
        print()

    # PARTIAL_DATA analysis
    if partial:
        # Coverage ratio distribution
        print("--- PARTIAL_DATA coverage ratio (OANDA count / expected) ---")
        ratio_buckets = {"0-10%": 0, "10-25%": 0, "25-50%": 0, "50-75%": 0, "75-99%": 0}
        for g in partial:
            ratio = g["oanda_count"] / g["expected"] if g["expected"] > 0 else 0
            if ratio < 0.10:
                ratio_buckets["0-10%"] += 1
            elif ratio < 0.25:
                ratio_buckets["10-25%"] += 1
            elif ratio < 0.50:
                ratio_buckets["25-50%"] += 1
            elif ratio < 0.75:
                ratio_buckets["50-75%"] += 1
            else:
                ratio_buckets["75-99%"] += 1
        for bucket, count in ratio_buckets.items():
            bar = "#" * min(count, 60)
            print(f"  {bucket:>8}  {count:>4}  {bar}")
        print()

        # Hour distribution of PARTIAL_DATA (UTC)
        hour_counts = defaultdict(int)
        for g in partial:
            utc_hour = g["from"].astimezone(UTC).hour
            hour_counts[utc_hour] += 1
        print("--- PARTIAL_DATA hour distribution (UTC) ---")
        for h in sorted(hour_counts.keys()):
            bar = "#" * min(hour_counts[h], 60)
            print(f"  {h:02d}:00  {hour_counts[h]:>4}  {bar}")
        print()

        # Duration distribution of PARTIAL_DATA
        dur_buckets = {
            "<10min": 0,
            "10-30min": 0,
            "30-60min": 0,
            "1-6h": 0,
            "6-24h": 0,
            ">24h": 0,
        }
        for g in partial:
            s = g["seconds"]
            if s < 600:
                dur_buckets["<10min"] += 1
            elif s < 1800:
                dur_buckets["10-30min"] += 1
            elif s < 3600:
                dur_buckets["30-60min"] += 1
            elif s < 21600:
                dur_buckets["1-6h"] += 1
            elif s < 86400:
                dur_buckets["6-24h"] += 1
            else:
                dur_buckets[">24h"] += 1
        print("--- PARTIAL_DATA duration distribution ---")
        for bucket, count in dur_buckets.items():
            bar = "#" * min(count, 60)
            print(f"  {bucket:>10}  {count:>4}  {bar}")
        print()

        # Sample of PARTIAL_DATA gaps
        print("--- Sample PARTIAL_DATA gaps (first 30) ---")
        print(
            f"  {'Gap Start (UTC)':<22} {'Gap End (UTC)':<22} {'Duration':>10} {'Expected':>8} {'OANDA':>6} {'Ratio':>7}"
        )
        for g in partial[:30]:
            dur = timedelta(seconds=g["seconds"])
            ratio = g["oanda_count"] / g["expected"] * 100 if g["expected"] > 0 else 0
            start_utc = g["from"].astimezone(UTC).strftime("%Y-%m-%d %H:%M")
            end_utc = g["to"].astimezone(UTC).strftime("%Y-%m-%d %H:%M")
            print(
                f"  {start_utc:<22} {end_utc:<22} {str(dur):>10} {g['expected']:>8} {g['oanda_count']:>6} {ratio:>6.1f}%"
            )
        print()


if __name__ == "__main__":
    main()
