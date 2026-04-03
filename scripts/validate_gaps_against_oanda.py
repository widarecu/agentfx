#!/usr/bin/env python3
"""
Gap Validation Script - Validate database gaps against OANDA API

This script identifies all unexplained candle data gaps in the database and validates
each gap against the live OANDA API to determine which gaps are legitimate (OANDA has
no data) versus platform bugs (OANDA has data we're missing).

Classification types:
- PLATFORM_MISSING: OANDA has data but we're missing it (needs fixing)
- OANDA_NO_DATA: OANDA also has no data (legitimate gap, no fix needed)
- PARTIAL_DATA: OANDA has some but not all expected candles
"""

import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg2
import requests  # type: ignore[import-untyped]

# Add src to path for imports
sys.path.insert(0, "/Users/wilson/Visual Studio/agentfx/src")

from agentfx.api.workers.market_calendar import is_market_closure

# Configuration
DATABASE_URL = "postgresql://test_user:test_password@localhost:5432/agentfx_test"
OANDA_API_KEY = "4c8f32c9b77525bc6475312c87ff6059-7a9b430fa0d6caf77c1de2a32e661997"
OANDA_HOST = "api-fxpractice.oanda.com"

# Instruments and timeframes
INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "USD_CAD"]
TIMEFRAMES = ["M1", "M5", "M10", "M30", "H1", "H4", "H12", "D", "W", "M"]

# Timeframe configurations (interval in seconds, gap threshold in seconds)
TIMEFRAME_CONFIG = {
    "M1": {"interval": 60, "threshold": 300},  # 5 min
    "M5": {"interval": 300, "threshold": 600},  # 10 min
    "M10": {"interval": 600, "threshold": 1200},  # 20 min
    "M30": {"interval": 1800, "threshold": 3600},  # 1 hour
    "H1": {"interval": 3600, "threshold": 7200},  # 2 hours
    "H4": {"interval": 14400, "threshold": 28800},  # 8 hours
    "H12": {"interval": 43200, "threshold": 86400},  # 24 hours
    "D": {"interval": 86400, "threshold": 172800},  # 2 days
    "W": {"interval": 604800, "threshold": 1209600},  # 2 weeks
    "M": {"interval": 2592000, "threshold": 5184000},  # 60 days
}

# M1 sampling limit to keep runtime manageable
M1_SAMPLE_LIMIT = 500


@dataclass
class Gap:
    """Represents a detected gap in candle data"""

    instrument: str
    timeframe: str
    gap_start: datetime
    gap_end: datetime
    duration_seconds: int
    expected_candles: int
    classification: str | None = None
    oanda_candles_count: int | None = None
    closure_reason: str | None = None


class GapValidator:
    """Main gap validation orchestrator"""

    def __init__(self):
        self.conn = None
        self.gaps: list[Gap] = []
        self.stats = {
            "total_gaps_detected": 0,
            "market_closure_gaps": 0,
            "unexplained_gaps": 0,
            "validated_gaps": 0,
            "platform_missing": 0,
            "oanda_no_data": 0,
            "partial_data": 0,
            "api_errors": 0,
        }

    def connect_db(self):
        """Establish database connection"""
        print("Connecting to database...", flush=True)
        self.conn = psycopg2.connect(DATABASE_URL)
        print("✓ Database connected", flush=True)

    def close_db(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def fetch_candle_timestamps(
        self, instrument: str, timeframe: str
    ) -> list[datetime]:
        """Fetch all candle timestamps for instrument/timeframe"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT timestamp
            FROM candles
            WHERE instrument = %s AND timeframe = %s
            ORDER BY timestamp ASC
            """,
            (instrument, timeframe),
        )
        timestamps = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return timestamps

    def detect_gaps(
        self, instrument: str, timeframe: str, timestamps: list[datetime]
    ) -> list[Gap]:
        """Detect gaps in timestamp sequence"""
        if len(timestamps) < 2:
            return []

        config = TIMEFRAME_CONFIG[timeframe]
        threshold = config["threshold"]
        interval = config["interval"]
        gaps = []

        for i in range(len(timestamps) - 1):
            gap_start = timestamps[i]
            gap_end = timestamps[i + 1]
            duration = (gap_end - gap_start).total_seconds()

            if duration > threshold:
                expected_candles = int((duration - interval) / interval)
                gap = Gap(
                    instrument=instrument,
                    timeframe=timeframe,
                    gap_start=gap_start,
                    gap_end=gap_end,
                    duration_seconds=int(duration),
                    expected_candles=expected_candles,
                )
                gaps.append(gap)

        return gaps

    def filter_market_closures(self, gaps: list[Gap]) -> list[Gap]:
        """Filter out gaps explained by market closures"""
        unexplained = []

        for gap in gaps:
            is_closure, reason = is_market_closure(gap.gap_start, gap.gap_end)
            if is_closure:
                gap.closure_reason = reason
                self.stats["market_closure_gaps"] += 1
            else:
                unexplained.append(gap)

        return unexplained

    def identify_all_gaps(self):
        """Step 1: Identify all unexplained gaps from database"""
        print("\n" + "=" * 80, flush=True)
        print("STEP 1: IDENTIFYING UNEXPLAINED GAPS", flush=True)
        print("=" * 80, flush=True)

        for instrument in INSTRUMENTS:
            for timeframe in TIMEFRAMES:
                print(f"\nAnalyzing {instrument} {timeframe}...")

                # Fetch timestamps
                timestamps = self.fetch_candle_timestamps(instrument, timeframe)
                if len(timestamps) < 2:
                    print(f"  ⚠ Insufficient data (only {len(timestamps)} candles)")
                    continue

                # Detect gaps
                gaps = self.detect_gaps(instrument, timeframe, timestamps)
                self.stats["total_gaps_detected"] += len(gaps)
                print(f"  Found {len(gaps)} gaps exceeding threshold")

                # Filter out market closures
                unexplained = self.filter_market_closures(gaps)
                self.stats["unexplained_gaps"] += len(unexplained)

                if len(unexplained) > 0:
                    print(
                        f"  ✓ {len(unexplained)} unexplained gaps (filtered out {len(gaps) - len(unexplained)} market closures)"
                    )

                    # For M1, sample to keep runtime manageable
                    if timeframe == "M1" and len(unexplained) > M1_SAMPLE_LIMIT:
                        print(
                            f"  ℹ Sampling {M1_SAMPLE_LIMIT} gaps from {len(unexplained)} M1 gaps"
                        )
                        # Sample evenly across the timeline
                        step = len(unexplained) // M1_SAMPLE_LIMIT
                        unexplained = unexplained[::step][:M1_SAMPLE_LIMIT]

                    self.gaps.extend(unexplained)
                else:
                    print("  ✓ All gaps explained by market closures")

        print(f"\n{'=' * 80}")
        print(f"TOTAL UNEXPLAINED GAPS TO VALIDATE: {len(self.gaps)}")
        print(f"{'=' * 80}\n")

    def call_oanda_api(
        self,
        instrument: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        retries: int = 3,
    ) -> list[dict] | None:
        """Call OANDA API to fetch candles for a time range"""
        url = f"https://{OANDA_HOST}/v3/instruments/{instrument}/candles"
        headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}

        # Convert to RFC3339 format
        from_time = start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        to_time = end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

        params = {
            "granularity": timeframe,
            "from": from_time,
            "to": to_time,
            "price": "M",
        }

        for attempt in range(retries):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)

                if response.status_code == 200:
                    data = response.json()
                    return data.get("candles", [])
                elif response.status_code == 429:
                    # Rate limited - exponential backoff
                    wait_time = (2**attempt) * 1.0
                    print(f"    ⚠ Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                elif response.status_code == 504:
                    # Gateway timeout - retry
                    print("    ⚠ Gateway timeout, retrying...")
                    time.sleep(2)
                    continue
                else:
                    print(
                        f"    ✗ API error {response.status_code}: {response.text[:100]}"
                    )
                    return None

            except Exception as e:
                print(f"    ✗ Request error: {e}")
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                return None

        return None

    def validate_gap_against_oanda(self, gap: Gap) -> str:
        """Validate a single gap against OANDA API"""
        candles = self.call_oanda_api(
            gap.instrument, gap.timeframe, gap.gap_start, gap.gap_end
        )

        if candles is None:
            self.stats["api_errors"] += 1
            return "API_ERROR"

        # Count complete candles only
        complete_candles = [c for c in candles if c.get("complete", False)]
        gap.oanda_candles_count = len(complete_candles)

        if len(complete_candles) == 0:
            # OANDA has no data for this period
            return "OANDA_NO_DATA"
        elif len(complete_candles) >= gap.expected_candles * 0.9:
            # OANDA has most/all expected candles - we're missing them
            return "PLATFORM_MISSING"
        else:
            # OANDA has some but not all candles
            return "PARTIAL_DATA"

    def validate_all_gaps(self):
        """Step 2: Validate each gap against OANDA API"""
        print("\n" + "=" * 80)
        print("STEP 2: VALIDATING GAPS AGAINST OANDA API")
        print("=" * 80)
        print(f"Validating {len(self.gaps)} unexplained gaps...")
        print(
            f"Estimated time: ~{len(self.gaps) * 0.5 / 60:.1f} minutes (0.5s per gap)"
        )
        print()

        total = len(self.gaps)
        for idx, gap in enumerate(self.gaps, 1):
            # Progress indicator
            if idx % 100 == 0 or idx == 1:
                pct = (idx / total) * 100
                print(
                    f"[{idx}/{total} - {pct:.1f}%] Validating {gap.instrument} {gap.timeframe} gap..."
                )

            # Validate against OANDA
            classification = self.validate_gap_against_oanda(gap)
            gap.classification = classification

            # Update stats
            self.stats["validated_gaps"] += 1
            if classification == "PLATFORM_MISSING":
                self.stats["platform_missing"] += 1
            elif classification == "OANDA_NO_DATA":
                self.stats["oanda_no_data"] += 1
            elif classification == "PARTIAL_DATA":
                self.stats["partial_data"] += 1

            # Rate limiting: 0.5 second delay between calls
            time.sleep(0.5)

        print(
            f"\n✓ Validation complete: {self.stats['validated_gaps']} gaps classified"
        )
        print(f"  - PLATFORM_MISSING: {self.stats['platform_missing']}")
        print(f"  - OANDA_NO_DATA: {self.stats['oanda_no_data']}")
        print(f"  - PARTIAL_DATA: {self.stats['partial_data']}")
        print(f"  - API_ERROR: {self.stats['api_errors']}")

    def generate_report(self) -> str:
        """Step 3: Generate comprehensive classification report"""
        print("\n" + "=" * 80)
        print("STEP 3: GENERATING CLASSIFICATION REPORT")
        print("=" * 80)

        lines = []
        lines.append("=" * 80)
        lines.append("GAP VALIDATION REPORT")
        lines.append(
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        lines.append("=" * 80)
        lines.append("")

        # Section 1: Executive Summary
        lines.append("SECTION 1: EXECUTIVE SUMMARY")
        lines.append("-" * 80)
        lines.append(f"Total gaps detected: {self.stats['total_gaps_detected']:,}")
        lines.append(
            f"Market closure gaps (filtered out): {self.stats['market_closure_gaps']:,}"
        )
        lines.append(
            f"Unexplained gaps (before sampling): {self.stats['unexplained_gaps']:,}"
        )
        lines.append(f"Gaps validated against OANDA: {self.stats['validated_gaps']:,}")
        lines.append("")
        lines.append("Classification Breakdown:")

        total_classified = (
            self.stats["platform_missing"]
            + self.stats["oanda_no_data"]
            + self.stats["partial_data"]
        )

        if total_classified > 0:
            pm_pct = (self.stats["platform_missing"] / total_classified) * 100
            on_pct = (self.stats["oanda_no_data"] / total_classified) * 100
            pd_pct = (self.stats["partial_data"] / total_classified) * 100

            lines.append(
                f"  PLATFORM_MISSING: {self.stats['platform_missing']:,} ({pm_pct:.1f}%) ← NEEDS FIXING"
            )
            lines.append(
                f"  OANDA_NO_DATA:    {self.stats['oanda_no_data']:,} ({on_pct:.1f}%) ← Legitimate gaps"
            )
            lines.append(
                f"  PARTIAL_DATA:     {self.stats['partial_data']:,} ({pd_pct:.1f}%) ← OANDA sparse data"
            )

        if self.stats["api_errors"] > 0:
            lines.append(
                f"  API_ERROR:        {self.stats['api_errors']:,} (could not validate)"
            )

        lines.append("")

        # Section 2: Per-instrument/timeframe breakdown
        lines.append("SECTION 2: PER-INSTRUMENT/TIMEFRAME BREAKDOWN")
        lines.append("-" * 80)

        # Group by instrument and timeframe
        breakdown = defaultdict(lambda: defaultdict(int))
        for gap in self.gaps:
            if gap.classification:
                key = f"{gap.instrument}_{gap.timeframe}"
                breakdown[key][gap.classification] += 1

        # Sort by total PLATFORM_MISSING descending
        sorted_keys = sorted(
            breakdown.keys(),
            key=lambda k: breakdown[k].get("PLATFORM_MISSING", 0),
            reverse=True,
        )

        lines.append(
            f"{'Instrument/TF':<20} {'PLATFORM_MISSING':<20} {'OANDA_NO_DATA':<20} {'PARTIAL_DATA':<20}"
        )
        lines.append("-" * 80)

        for key in sorted_keys:
            stats = breakdown[key]
            pm = stats.get("PLATFORM_MISSING", 0)
            on = stats.get("OANDA_NO_DATA", 0)
            pd = stats.get("PARTIAL_DATA", 0)

            pm_str = f"{pm:,}" if pm > 0 else "-"
            on_str = f"{on:,}" if on > 0 else "-"
            pd_str = f"{pd:,}" if pd > 0 else "-"

            lines.append(f"{key:<20} {pm_str:<20} {on_str:<20} {pd_str:<20}")

        lines.append("")

        # Section 3: PLATFORM_MISSING gaps detail
        lines.append("SECTION 3: PLATFORM_MISSING GAPS DETAIL (THESE NEED FIXING)")
        lines.append("-" * 80)

        platform_missing_gaps = [
            g for g in self.gaps if g.classification == "PLATFORM_MISSING"
        ]

        if platform_missing_gaps:
            lines.append(f"Total PLATFORM_MISSING gaps: {len(platform_missing_gaps):,}")
            lines.append("")

            # Group by instrument/timeframe
            by_inst_tf = defaultdict(list)
            for gap in platform_missing_gaps:
                by_inst_tf[f"{gap.instrument}_{gap.timeframe}"].append(gap)

            for key in sorted(by_inst_tf.keys()):
                gaps_list = by_inst_tf[key]
                # Sort by duration descending
                gaps_list.sort(key=lambda g: g.duration_seconds, reverse=True)

                lines.append(f"\n{key} ({len(gaps_list):,} gaps):")
                lines.append(
                    f"{'Gap Start':<25} {'Gap End':<25} {'Duration':<15} {'Expected':<10} {'OANDA Has':<10}"
                )
                lines.append("-" * 90)

                for gap in gaps_list:
                    start_str = gap.gap_start.strftime("%Y-%m-%d %H:%M:%S")
                    end_str = gap.gap_end.strftime("%Y-%m-%d %H:%M:%S")

                    # Format duration
                    days = gap.duration_seconds // 86400
                    hours = (gap.duration_seconds % 86400) // 3600
                    mins = (gap.duration_seconds % 3600) // 60

                    if days > 0:
                        dur_str = f"{days}d {hours}h"
                    elif hours > 0:
                        dur_str = f"{hours}h {mins}m"
                    else:
                        dur_str = f"{mins}m"

                    lines.append(
                        f"{start_str:<25} {end_str:<25} {dur_str:<15} "
                        f"{gap.expected_candles:<10} {gap.oanda_candles_count or 0:<10}"
                    )
        else:
            lines.append("No PLATFORM_MISSING gaps found! ✓")

        lines.append("")

        # Section 4: OANDA_NO_DATA gaps summary
        lines.append("SECTION 4: OANDA_NO_DATA GAPS SUMMARY (NO FIX NEEDED)")
        lines.append("-" * 80)

        oanda_no_data_gaps = [
            g for g in self.gaps if g.classification == "OANDA_NO_DATA"
        ]

        if oanda_no_data_gaps:
            lines.append(f"Total OANDA_NO_DATA gaps: {len(oanda_no_data_gaps):,}")
            lines.append("")

            # Group by instrument/timeframe and show counts
            by_inst_tf = defaultdict(list)
            for gap in oanda_no_data_gaps:
                by_inst_tf[f"{gap.instrument}_{gap.timeframe}"].append(gap)

            lines.append(
                f"{'Instrument/TF':<20} {'Count':<10} {'Avg Duration':<20} {'Max Duration':<20}"
            )
            lines.append("-" * 80)

            for key in sorted(by_inst_tf.keys()):
                gaps_list = by_inst_tf[key]
                count = len(gaps_list)
                avg_dur = sum(g.duration_seconds for g in gaps_list) / count
                max_dur = max(g.duration_seconds for g in gaps_list)

                # Format durations
                def format_seconds(s):
                    days = s // 86400
                    hours = (s % 86400) // 3600
                    if days > 0:
                        return f"{days}d {hours}h"
                    elif hours > 0:
                        return f"{hours}h"
                    else:
                        return f"{s // 60}m"

                lines.append(
                    f"{key:<20} {count:<10} {format_seconds(int(avg_dur)):<20} {format_seconds(max_dur):<20}"
                )

            # Pattern analysis
            lines.append("")
            lines.append("Common patterns:")

            # Analyze time of day distribution
            hour_dist = defaultdict(int)
            for gap in oanda_no_data_gaps:
                hour_dist[gap.gap_start.hour] += 1

            if hour_dist:
                most_common_hours = sorted(
                    hour_dist.items(), key=lambda x: x[1], reverse=True
                )[:3]
                lines.append(
                    f"  Most common hours (UTC): {', '.join(f'{h}:00 ({c} gaps)' for h, c in most_common_hours)}"
                )

            # Analyze weekday distribution
            weekday_dist = defaultdict(int)
            weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            for gap in oanda_no_data_gaps:
                weekday_dist[gap.gap_start.weekday()] += 1

            if weekday_dist:
                most_common_days = sorted(
                    weekday_dist.items(), key=lambda x: x[1], reverse=True
                )[:3]
                lines.append(
                    f"  Most common days: {', '.join(f'{weekday_names[d]} ({c} gaps)' for d, c in most_common_days)}"
                )
        else:
            lines.append("No OANDA_NO_DATA gaps found.")

        lines.append("")

        # Section 5: Recommendations
        lines.append("SECTION 5: RECOMMENDATIONS")
        lines.append("-" * 80)

        if self.stats["platform_missing"] > 0:
            lines.append("CRITICAL ISSUES FOUND:")
            lines.append("")
            lines.append(
                f"1. {self.stats['platform_missing']:,} gaps where OANDA has data but our platform is missing it."
            )
            lines.append(
                "   These represent actual data quality issues that need to be fixed."
            )
            lines.append("")
            lines.append("   Recommended actions:")
            lines.append(
                "   a) Review backfill logic in src/agentfx/services/ingest.py"
            )
            lines.append(
                "   b) Check for race conditions or timing issues in candle ingestion"
            )
            lines.append(
                "   c) Verify OANDA API pagination handling (5000 candle limit per request)"
            )
            lines.append(
                "   d) Run targeted backfill for affected instrument/timeframe/date ranges"
            )
            lines.append("")

            # Identify most problematic combinations
            pm_by_combo = defaultdict(int)
            for gap in self.gaps:
                if gap.classification == "PLATFORM_MISSING":
                    pm_by_combo[f"{gap.instrument}_{gap.timeframe}"] += 1

            top_combos = sorted(pm_by_combo.items(), key=lambda x: x[1], reverse=True)[
                :5
            ]
            lines.append("   Most affected instrument/timeframe combinations:")
            for combo, count in top_combos:
                lines.append(f"     - {combo}: {count:,} missing gaps")
        else:
            lines.append("✓ No PLATFORM_MISSING gaps found!")
            lines.append(
                "  All gaps in the database are either explained by market closures"
            )
            lines.append(
                "  or confirmed as legitimate gaps where OANDA also has no data."
            )

        lines.append("")

        if self.stats["partial_data"] > 0:
            lines.append(
                f"2. {self.stats['partial_data']:,} gaps with partial OANDA data (sparse data periods)."
            )
            lines.append("   These may represent:")
            lines.append(
                "   - Low liquidity periods where OANDA itself has sparse data"
            )
            lines.append("   - Data quality issues on OANDA's side")
            lines.append(
                "   - Consider flagging these in the database with a 'low_liquidity' marker"
            )
            lines.append("")

        if self.stats["oanda_no_data"] > 0:
            lines.append(
                f"3. {self.stats['oanda_no_data']:,} gaps where OANDA also has no data."
            )
            lines.append("   These are legitimate gaps - no action needed.")
            lines.append(
                "   Consider documenting common patterns for future reference."
            )
            lines.append("")

        lines.append("=" * 80)
        lines.append("END OF REPORT")
        lines.append("=" * 80)

        return "\n".join(lines)

    def run(self):
        """Execute the full validation pipeline"""
        try:
            start_time = time.time()

            # Connect to database
            self.connect_db()

            # Step 1: Identify gaps
            self.identify_all_gaps()

            # Step 2: Validate gaps
            self.validate_all_gaps()

            # Step 3: Generate report
            report = self.generate_report()

            # Write report to file
            report_path = (
                "/Users/wilson/Visual Studio/agentfx/scripts/gap_validation_report.txt"
            )
            with open(report_path, "w") as f:
                f.write(report)

            # Also print to stdout
            print(report)

            elapsed = time.time() - start_time
            print(f"\n✓ Report written to: {report_path}")
            print(f"✓ Total execution time: {elapsed / 60:.1f} minutes")

        finally:
            self.close_db()


if __name__ == "__main__":
    validator = GapValidator()
    validator.run()
