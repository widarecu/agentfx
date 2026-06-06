"""Cost-model gap investigation — read-only.

Pulls the 96 OOS trades from `evorun_20260603_9f0b790a`'s winning
genome via the staging API and recomputes each trade's cost under
each candidate correction so we can see the magnitude before
changing any model code:

    Scenario 0  — Current model (continuous swap, 0.1 pip/side slippage)
    Scenario A  — Same rates, swap discrete at 21:00 UTC, skip Sat/Sun
                  (= Sun/Mon AEST). Tests the timing-correction alone.
    Scenario B  — A + slippage = 0. Tests the phantom-slippage finding.
    Scenario C  — B + OANDA-observed long rate (-1.20 AUD/day ≈ -$0.78
                  USD/day per 10,000 long EUR/USD). Tests rate
                  recalibration. Short side kept at current model's
                  rate since we don't have screenshot data for it.

For each scenario we print:
  - aggregate spread / slippage / swap / net P&L in USD
  - per-trade average net pips and average duration
  - gap vs the current model (Scenario 0)

This is read-only — no DB writes, no model changes. Run from your
laptop against the public staging API:

    python scripts/investigate_cost_model_gap.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, UTC

UTC = UTC

BASE = "https://agentfx-staging.up.railway.app/api/v1"
RUN_ID = "evorun_20260603_9f0b790a"
GENOME_ID = "genome_gen_evorun_20260603_9f0b790a_027_024"

# OANDA AU empirical rate for LONG EUR/USD, derived from the user's
# screenshots: ~$1.20 AUD/day per 10,000 units at price ~1.16, in an
# AUD-denominated account. AUD/USD ≈ 0.65 → $0.78 USD/day. Per unit:
# $7.8e-5 USD/day. We hold this constant for Scenario C; refine when
# we wire OANDA's published rates per instrument.
OANDA_LONG_USD_PER_UNIT_PER_DAY = 7.8e-5


def login_get_token() -> str:
    email = os.environ.get("AGENTFX_USER", "claude-readonly@anthropic.com")
    password = os.environ.get("AGENTFX_PWD", "AnalysisRunOnce2026!")
    data = urllib.parse.urlencode({"username": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{BASE}/login",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def parse_ts(s: str) -> datetime:
    """Normalise an ISO timestamp to UTC."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def count_charged_rollovers(entry_utc: datetime, exit_utc: datetime) -> int:
    """Number of OANDA financing rollovers between entry and exit.

    Rule (per the user's screenshots): rollovers happen at 21:00 UTC
    every day, EXCEPT when 21:00 UTC falls on Saturday or Sunday — those
    become Sunday/Monday morning AEST, which OANDA AU skips.

    Counts each rollover the open position crosses. An intraday trade
    that opens and closes between two consecutive 21:00 UTC marks
    crosses zero, and pays no swap.
    """
    if exit_utc <= entry_utc:
        return 0

    # First rollover strictly AFTER entry: same calendar day at 21:00 UTC
    # if that's still in the future, else next day at 21:00 UTC.
    first = entry_utc.replace(hour=21, minute=0, second=0, microsecond=0)
    if first <= entry_utc:
        first = first + timedelta(days=1)

    count = 0
    cur = first
    while cur <= exit_utc:
        # Sat 21:00 UTC = Sun 07:00 AEST → skip
        # Sun 21:00 UTC = Mon 07:00 AEST → skip
        if cur.weekday() not in (5, 6):  # 5=Sat, 6=Sun
            count += 1
        cur = cur + timedelta(days=1)
    return count


def num_days(entry_utc: datetime, exit_utc: datetime) -> float:
    return max((exit_utc - entry_utc).total_seconds(), 0.0) / 86400.0


def fmt_money(v: float, width: int = 10) -> str:
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):.2f}".rjust(width)


def main() -> None:
    token = login_get_token()
    bt = get(
        f"/evolution-runs/{RUN_ID}/genomes/{GENOME_ID}/backtest?window=oos",
        token,
    )
    trades = bt.get("trades") or []
    longs = sum(1 for t in trades if t["direction"] == "long")
    shorts = sum(1 for t in trades if t["direction"] == "short")
    print(f"Pulled {len(trades)} OOS trades for {GENOME_ID}")
    print(
        f"Backtest window: {bt['data_range_start'][:10]} → {bt['data_range_end'][:10]}"
    )
    print(f"Trade directions: {longs} long, {shorts} short")
    print()

    # Aggregate counters per scenario
    scenarios = ["0_current", "A_swap_discrete", "B_no_slippage", "C_oanda_rate"]
    totals: dict[str, dict[str, float]] = {
        s: {
            "spread_cost": 0.0,
            "slippage_cost": 0.0,
            "swap_cost": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "n_trades_with_swap": 0,
            "total_charged_rollovers": 0,
            "total_holding_days": 0.0,
        }
        for s in scenarios
    }

    for t in trades:
        entry = parse_ts(t["entry_timestamp"])
        exit_ = parse_ts(t["exit_timestamp"])
        direction = t["direction"]
        units = float(t["position_size"])
        gross = float(t.get("gross_pnl_dollars") or 0.0)
        cur_spread = float(t.get("spread_cost_dollars") or 0.0)
        cur_slip = float(t.get("slippage_cost_dollars") or 0.0)
        cur_swap = float(t.get("swap_cost_dollars") or 0.0)

        days_held = num_days(entry, exit_)
        rollovers = count_charged_rollovers(entry, exit_)

        # Per-day OANDA observed rate for long EUR/USD. For shorts we
        # don't have empirical data — fall back to scaling the current
        # model's per-trade swap by (rollovers / continuous_days) to
        # isolate the timing effect without inventing a rate.
        if direction == "long":
            oanda_swap = -OANDA_LONG_USD_PER_UNIT_PER_DAY * units * rollovers
        else:
            # Short: keep current model's per-day-equivalent rate
            # (charged amount / days_held), apply over discrete rollovers
            if days_held > 0:
                per_day = cur_swap / days_held
                oanda_swap = per_day * rollovers
            else:
                oanda_swap = 0.0

        # Scenario costs (sign convention: costs are negative; spread is
        # already negative in `cur_spread`, etc.)
        scenario_swap = {
            "0_current": cur_swap,
            # A: current rate, discrete rollovers. Use cur_swap scaled
            # to the rollover count to preserve the current model's
            # per-charged-event amount.
            "A_swap_discrete": (
                cur_swap * (rollovers / max(days_held * 1.0, 1e-9))
                if days_held > 0
                else 0.0
            ),
            "B_no_slippage": (
                cur_swap * (rollovers / max(days_held * 1.0, 1e-9))
                if days_held > 0
                else 0.0
            ),
            "C_oanda_rate": oanda_swap,
        }
        scenario_slip = {
            "0_current": cur_slip,
            "A_swap_discrete": cur_slip,
            "B_no_slippage": 0.0,
            "C_oanda_rate": 0.0,
        }
        scenario_spread = {
            "0_current": cur_spread,
            "A_swap_discrete": cur_spread,
            "B_no_slippage": cur_spread,
            "C_oanda_rate": cur_spread,
        }

        # Sign convention (from backtest.py and verified against the
        # API's reported net_pnl_dollars):
        #   net_pnl_dollars = gross - spread - slippage - swap
        # where `swap` is stored NEGATIVE when the trade was charged
        # financing (so subtracting a negative adds it back to gross).
        # Spread and slippage are stored as POSITIVE values representing
        # the magnitude paid.
        for s in scenarios:
            net = gross - scenario_spread[s] - scenario_slip[s] - scenario_swap[s]
            totals[s]["spread_cost"] += scenario_spread[s]
            totals[s]["slippage_cost"] += scenario_slip[s]
            totals[s]["swap_cost"] += scenario_swap[s]
            totals[s]["gross_pnl"] += gross
            totals[s]["net_pnl"] += net
            if scenario_swap[s] != 0.0:
                totals[s]["n_trades_with_swap"] += 1
            totals[s]["total_holding_days"] += days_held
            if s == "C_oanda_rate":
                totals[s]["total_charged_rollovers"] += rollovers

    # Pull current model's reported totals for sanity (should match
    # Scenario 0 within rounding).
    reported_metrics = bt.get("metrics", {})

    print("=" * 88)
    print("Aggregate (96 OOS trades)")
    print("=" * 88)
    print(
        f"{'Scenario':<22}{'Spread':>12}{'Slippage':>12}{'Swap':>12}{'Net P&L':>12}{'vs current':>12}"
    )
    base_net = totals["0_current"]["net_pnl"]
    for s in scenarios:
        t = totals[s]
        print(
            f"{s:<22}"
            f"{fmt_money(-abs(t['spread_cost'])):>12}"
            f"{fmt_money(-abs(t['slippage_cost'])):>12}"
            f"{fmt_money(t['swap_cost']):>12}"
            f"{fmt_money(t['net_pnl']):>12}"
            f"{fmt_money(t['net_pnl'] - base_net):>12}"
        )
    print()
    print(
        f"Total holding days across all trades: {totals['0_current']['total_holding_days']:.1f}"
    )
    print(
        f"Total OANDA-accurate rollovers charged: {totals['C_oanda_rate']['total_charged_rollovers']}"
    )
    print(
        f"Average rollovers per trade: {totals['C_oanda_rate']['total_charged_rollovers'] / max(len(trades), 1):.2f}"
    )
    print(
        f"Reported aggregate net_pnl_dollars from API: ${reported_metrics.get('net_pnl_dollars', 0):.2f}"
    )
    print()
    print("Per-correction lift in net P&L (vs current):")
    print(
        f"  Just fix swap timing (A)               : {fmt_money(totals['A_swap_discrete']['net_pnl'] - base_net)}"
    )
    print(
        f"  Add slippage = 0 (B)                   : {fmt_money(totals['B_no_slippage']['net_pnl'] - base_net)}"
    )
    print(
        f"  Add OANDA observed long rate (C)       : {fmt_money(totals['C_oanda_rate']['net_pnl'] - base_net)}"
    )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
