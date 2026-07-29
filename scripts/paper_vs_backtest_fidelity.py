#!/usr/bin/env python3
"""
Paper-vs-backtest fidelity diagnostic.

Reruns the SAME DSL through the backtest engine against the SAME
candle window that the paper runner operated on, and compares
trade-for-trade. Any divergence in trade count, entry timing, or
exit-reason mix points at a runner-vs-backtest execution bug.

Usage:
    ENVIRONMENT=staging PYTHONPATH=src .venv/bin/python \\
      scripts/paper_vs_backtest_fidelity.py --run-id <RUN_ID>
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import UTC, datetime

sys.path.insert(0, "src")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fidelity")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    args = p.parse_args()

    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import Run, StrategyGenome, Trade
    from agentfx.services.backtest import run_backtest_from_db

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == args.run_id).one()
        genome = (
            db.query(StrategyGenome)
            .filter(StrategyGenome.id == run.strategy_genome_id)
            .one()
        )
        approved = run.approved_at.astimezone(UTC)
        now = datetime.now(UTC)
        instrument = run.pair
        granularity = (run.parameters or {}).get("granularity") or "M1"
        log.info("run       : %s (%s %s)", run.id, instrument, granularity)
        log.info("genome    : %s", genome.id)
        log.info("snapshot  : %.4f", float(run.snapshot_balance or 0))
        log.info("window    : %s → %s", approved, now)

        # --- Backtest against the same window using run's snapshot balance ---
        log.info("running backtest engine on the paper window …")
        bt = run_backtest_from_db(
            instrument=instrument,
            granularity=granularity,
            start=approved,
            end=now,
            strategy_name="dsl",
            strategy_params={"spec": genome.dsl_json},
            capture_signals=True,
            account_balance=float(run.snapshot_balance or 10_000.0),
        )
        bt_trades = bt.get("trades", [])
        log.info("backtest emitted %d trades", len(bt_trades))

        # --- Paper trades ---
        pt = (
            db.query(Trade)
            .filter(Trade.run_id == run.id)
            .order_by(Trade.entry_timestamp)
            .all()
        )
        log.info("paper has %d trades", len(pt))

        _summarise(
            "BACKTEST (same window, same DSL, same balance)",
            bt_trades,
            key_pnl="net_pnl_dollars",
        )
        _summarise("PAPER (live)", [_paper_to_dict(t) for t in pt], key_pnl="pnl")

        _timeline(bt_trades, pt)

        _entry_alignment(bt_trades, pt)
        return 0
    finally:
        db.close()


def _paper_to_dict(t) -> dict:
    ep = float(t.entry_price or 0)
    xp = float(t.exit_price) if t.exit_price is not None else None
    dir = "long" if t.side == "buy" else "short"
    pips = ((xp - ep) if dir == "long" else (ep - xp)) / 0.0001 if xp is not None else 0
    return {
        "entry_timestamp": t.entry_timestamp.astimezone(UTC).isoformat(),
        "exit_timestamp": t.exit_timestamp.astimezone(UTC).isoformat()
        if t.exit_timestamp
        else None,
        "direction": dir,
        "entry_price": ep,
        "exit_price": xp,
        "pnl": float(t.pnl or 0),
        "pips": pips,
        "exit_reason": t.exit_reason or "?",
        "quantity": int(t.quantity),
    }


def _summarise(label: str, trades: list[dict], key_pnl: str) -> None:
    closed = [t for t in trades if t.get("exit_timestamp")]
    wins = [t for t in closed if float(t.get(key_pnl, 0)) > 0]
    losses = [t for t in closed if float(t.get(key_pnl, 0)) < 0]
    reasons = Counter(t.get("exit_reason", "?") for t in closed)
    total_pnl = sum(float(t.get(key_pnl, 0)) for t in closed)
    avg_pips = (
        sum(float(t.get("pips", 0)) for t in closed) / len(closed) if closed else 0
    )
    print(f"\n=== {label} ===")
    print(f"  trades: {len(trades)} total, {len(closed)} closed")
    print(
        f"  wins/losses: {len(wins)}/{len(losses)}   "
        f"hit={len(wins) / max(1, len(closed)):.1%}"
    )
    print(f"  Σpnl: {total_pnl:+.2f}   avg pips/trade: {avg_pips:+.2f}")
    print(f"  exit reasons: {dict(reasons)}")


def _timeline(bt: list[dict], pt: list) -> None:
    print("\n=== TIMELINE (first 30 events, sorted) ===")
    events: list[tuple[datetime, str, str]] = []
    for t in bt:
        events.append(
            (
                _parse(t["entry_timestamp"]),
                "BT",
                f"{t['direction']:>5} entry @ {t['entry_price']:.5f}",
            )
        )
        if t.get("exit_timestamp"):
            events.append(
                (
                    _parse(t["exit_timestamp"]),
                    "BT",
                    f"{t['direction']:>5} exit  @ {t['exit_price']:.5f}  "
                    f"pnl={t.get('net_pnl_dollars', 0):+8.2f} "
                    f"reason={t.get('exit_reason', '?')}",
                )
            )
    for t in pt:
        events.append(
            (
                t.entry_timestamp.astimezone(UTC),
                "PP",
                f"{'long' if t.side == 'buy' else 'short':>5} entry @ "
                f"{float(t.entry_price):.5f}",
            )
        )
        if t.exit_timestamp:
            events.append(
                (
                    t.exit_timestamp.astimezone(UTC),
                    "PP",
                    f"{'long' if t.side == 'buy' else 'short':>5} exit  @ "
                    f"{float(t.exit_price or 0):.5f}  pnl={float(t.pnl or 0):+8.2f} "
                    f"reason={t.exit_reason or '?'}",
                )
            )
    events.sort()
    for ts, tag, line in events[:30]:
        print(f"  {ts.isoformat()[:19]}  {tag}  {line}")


def _entry_alignment(bt: list[dict], pt: list) -> None:
    """For each backtest entry, is there a paper entry within 5 min?"""
    print("\n=== ENTRY-ALIGNMENT (are the SAME bars firing?) ===")
    bt_entries = sorted([(_parse(t["entry_timestamp"]), t["direction"]) for t in bt])
    pt_entries = sorted(
        [
            (t.entry_timestamp.astimezone(UTC), "long" if t.side == "buy" else "short")
            for t in pt
        ]
    )
    print(f"  BT entries: {len(bt_entries)}   PP entries: {len(pt_entries)}")
    matched = 0
    from datetime import timedelta

    TOL = timedelta(minutes=5)
    for bt_ts, bt_dir in bt_entries[:60]:
        # find any paper entry within TOL
        hits = [
            (pp_ts, pp_dir)
            for pp_ts, pp_dir in pt_entries
            if abs(pp_ts - bt_ts) <= TOL and pp_dir == bt_dir
        ]
        if hits:
            matched += 1
    print(
        f"  BT entries matched by a PP entry within ±5min & same direction: {matched}/{len(bt_entries[:60])}"
    )


def _parse(x) -> datetime:
    if isinstance(x, datetime):
        return x.astimezone(UTC) if x.tzinfo else x.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


if __name__ == "__main__":
    sys.exit(main())
