#!/usr/bin/env python3
"""
Inspect a single gap in detail: show our DB timestamps vs OANDA timestamps.

Usage:
    ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/inspect_gap.py AUD_USD M1 [gap_index]
    gap_index defaults to 0 (first unexplained gap)
"""

import sys
from datetime import UTC, timedelta

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


def fetch_oanda_candles(instrument, granularity, from_ts, to_ts):
    """Fetch actual candle timestamps from OANDA for a range."""
    url = f"https://{OANDA_HOST}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    params = {
        "granularity": granularity,
        "from": from_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price": "M",
    }
    r = requests.get(url, headers=headers, params=params, timeout=15)
    if r.status_code != 200:
        print(f"  OANDA API error: {r.status_code} {r.text[:200]}")
        return []
    return r.json().get("candles", [])


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: python scripts/inspect_gap.py <INSTRUMENT> <GRANULARITY> [gap_index]"
        )
        sys.exit(1)

    instrument = sys.argv[1]
    granularity = sys.argv[2]
    gap_index = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    gran_seconds = GRANULARITY_SECONDS.get(granularity, 60)
    threshold = gran_seconds * GAP_DETECTION_MULTIPLIER

    # Load timestamps
    db = SessionLocal()
    timestamps = MarketDataService.get_candle_timestamps(
        db, "oanda", instrument, granularity
    )
    db.close()

    # Find unexplained gaps
    unexplained = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if delta > threshold:
            is_closure, reason = is_market_closure(timestamps[i - 1], timestamps[i])
            if not is_closure:
                unexplained.append(
                    {
                        "from": timestamps[i - 1],
                        "to": timestamps[i],
                        "seconds": delta,
                        "index": i,
                    }
                )

    if not unexplained:
        print("No unexplained gaps found.")
        return

    if gap_index >= len(unexplained):
        print(f"Gap index {gap_index} out of range (0-{len(unexplained) - 1})")
        return

    g = unexplained[gap_index]
    gap_start = g["from"]
    gap_end = g["to"]
    gap_start_utc = gap_start.astimezone(UTC)
    gap_end_utc = gap_end.astimezone(UTC)

    print(f"{'=' * 70}")
    print(
        f"GAP INSPECTION: {instrument} {granularity} — gap #{gap_index} of {len(unexplained)}"
    )
    print(f"{'=' * 70}")
    print()
    print(
        f"Gap start (DB):   {gap_start}  →  {gap_start_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    print(
        f"Gap end (DB):     {gap_end}  →  {gap_end_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    print(f"Duration:         {timedelta(seconds=g['seconds'])} ({g['seconds']:.0f}s)")
    print(f"Expected candles: {max(1, int(g['seconds'] / gran_seconds) - 1)}")
    print()

    # Show DB candles around the gap (5 before, 5 after)
    idx = g["index"]
    before_start = max(0, idx - 5)
    after_end = min(len(timestamps), idx + 5)

    print("--- DB candles around gap (5 before / 5 after) ---")
    for j in range(before_start, after_end):
        ts = timestamps[j]
        ts_utc = ts.astimezone(UTC)
        marker = ""
        if j == idx - 1:
            marker = "  ← LAST BEFORE GAP"
        elif j == idx:
            marker = "  ← FIRST AFTER GAP"

        if j == idx and j > before_start:
            gap_dur = timedelta(
                seconds=(timestamps[idx] - timestamps[idx - 1]).total_seconds()
            )
            print(f"  {'':>4}  ... GAP: {gap_dur} ...")

        print(f"  [{j:>4}]  {ts}  ({ts_utc.strftime('%H:%M:%S')} UTC){marker}")
    print()

    # Query OANDA for the gap range
    print("--- OANDA candles in gap range ---")
    print(
        f"  Querying: {gap_start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')} to {gap_end_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    candles = fetch_oanda_candles(instrument, granularity, gap_start, gap_end)
    complete = [c for c in candles if c.get("complete", True)]

    print(f"  Total returned: {len(candles)}  (complete: {len(complete)})")
    print()

    if candles:
        print(
            f"  {'#':>4}  {'Timestamp (UTC)':<22}  {'Complete':>8}  {'Open':>8}  {'Close':>8}  {'Volume':>6}"
        )
        for i, c in enumerate(candles):
            ts = c["time"][:19]  # trim to seconds
            comp = "yes" if c.get("complete", True) else "NO"
            mid = c.get("mid", {})
            o = mid.get("o", "?")
            cl = mid.get("c", "?")
            vol = c.get("volume", "?")
            print(f"  {i + 1:>4}  {ts:<22}  {comp:>8}  {o:>8}  {cl:>8}  {vol:>6}")
    else:
        print("  (no candles returned)")
    print()


if __name__ == "__main__":
    main()
