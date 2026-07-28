#!/usr/bin/env python3
"""
One-shot backfill for paper trades whose pnl was locally-estimated.

Wilson UAT 2026-07-26 audit found 2 trades in the live EUR_USD run
with pnl diverging from OANDA's ORDER_FILL.realizedPL by a combined
$271. Root cause: `_reconcile_broker_side_close` wrote a local pnl
estimate (skipping OANDA's homeConversionFactor for non-USD accounts),
and the reconciler's pre-fix idempotency guard skipped these trades
because their exit_timestamp was set.

The idempotency guard was fixed on 2026-07-26 (commit 75b477d) so
new broker-side closes now get overwritten within one reconciler
tick. But the historical rows past `run.last_transaction_id` won't
be re-scanned. This script backfills them by looking up each
suspect trade's ORDER_FILL directly by broker_trade_id and applying
OANDA's authoritative realizedPL / exit_price / financing.

Selection criteria (SUSPECT trade):
    exit_timestamp IS NOT NULL       — closed
    broker_close_transaction_id IS NULL — never reconciled to a txn
    broker_trade_id IS NOT NULL      — has a broker id to query

Usage:
    # Dry run — print what would change without writing:
    ENVIRONMENT=uat PYTHONPATH=src .venv/bin/python \\
      scripts/backfill_broker_side_trade_pnl.py --run-id <RUN_ID> --dry-run

    # Apply the fix:
    ENVIRONMENT=uat PYTHONPATH=src .venv/bin/python \\
      scripts/backfill_broker_side_trade_pnl.py --run-id <RUN_ID>

    # Backfill every paper run for a user:
    ENVIRONMENT=uat PYTHONPATH=src .venv/bin/python \\
      scripts/backfill_broker_side_trade_pnl.py --all-runs
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, "src")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


def _lookup_order_fill_for_trade(client, account_id: str, broker_trade_id: str):
    """Find the ORDER_FILL transaction that closed `broker_trade_id`.

    OANDA doesn't expose "get the close txn for trade X" directly.
    We hit `/accounts/{id}/trades/{tradeID}` which returns the trade
    resource including `financing` and `closingTransactionIDs`. Then
    we resolve the newest closing txn ID to the ORDER_FILL body.
    """
    trade_resp = client._request(
        "GET", f"/accounts/{account_id}/trades/{broker_trade_id}"
    )
    trade = trade_resp.get("trade") or {}
    closing_ids = trade.get("closingTransactionIDs") or []
    if not closing_ids:
        return None
    # OANDA returns oldest→newest for closingTransactionIDs; the last
    # one is the fill that actually flatted the position.
    close_txn_id = str(closing_ids[-1])
    txn_resp = client._request(
        "GET", f"/accounts/{account_id}/transactions/{close_txn_id}"
    )
    return txn_resp.get("transaction")


def _extract_close_details(txn: dict, broker_trade_id: str) -> dict | None:
    """Pull per-trade realizedPL / financing / exit_price / commission
    out of a matching ORDER_FILL, keyed by broker_trade_id.
    """
    if not txn or txn.get("type") != "ORDER_FILL":
        return None
    per_trade_pnl = None
    per_trade_financing = None
    solo = txn.get("tradeClosed") or {}
    if str(solo.get("tradeID")) == broker_trade_id:
        if "realizedPL" in solo:
            per_trade_pnl = float(solo["realizedPL"])
        if "financing" in solo:
            per_trade_financing = float(solo["financing"])
    else:
        for entry in txn.get("tradesClosed") or []:
            if str(entry.get("tradeID")) == broker_trade_id:
                if "realizedPL" in entry:
                    per_trade_pnl = float(entry["realizedPL"])
                if "financing" in entry:
                    per_trade_financing = float(entry["financing"])
                break
    if per_trade_pnl is None and "pl" in txn:
        per_trade_pnl = float(txn["pl"])
    if per_trade_financing is None and "financing" in txn:
        per_trade_financing = float(txn["financing"])
    return {
        "txn_id": str(txn.get("id") or ""),
        "pnl": per_trade_pnl,
        "financing": per_trade_financing,
        "commission": (float(txn["commission"]) if "commission" in txn else None),
        "exit_price": float(txn["price"]) if "price" in txn else None,
        "time": txn.get("time"),
    }


def backfill_run(run_id: str, dry_run: bool = False) -> int:
    """Backfill every suspect trade under `run_id`. Returns the number
    of trades updated (or would-be-updated in dry-run mode)."""
    from agentfx.adapters.oanda import OandaClient
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import Run, Trade

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).one_or_none()
        if run is None:
            logger.error("run %s not found", run_id)
            return 0
        if not run.oanda_account_id:
            logger.error("run %s has no oanda_account_id", run_id)
            return 0

        suspect = (
            db.query(Trade)
            .filter(
                Trade.run_id == run_id,
                Trade.exit_timestamp.isnot(None),
                Trade.broker_close_transaction_id.is_(None),
                Trade.broker_trade_id.isnot(None),
            )
            .all()
        )
        logger.info(
            "run %s: %d suspect trade(s) (exit set, no close-txn id)",
            run_id,
            len(suspect),
        )
        if not suspect:
            return 0

        client = OandaClient()
        updated = 0
        for t in suspect:
            try:
                fill = _lookup_order_fill_for_trade(
                    client,
                    run.oanda_account_id,
                    str(t.broker_trade_id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "  trade %s (broker %s): OANDA lookup FAILED — %s",
                    t.id,
                    t.broker_trade_id,
                    e,
                )
                continue
            if fill is None:
                logger.warning(
                    "  trade %s (broker %s): no closing txn on OANDA",
                    t.id,
                    t.broker_trade_id,
                )
                continue
            details = _extract_close_details(fill, str(t.broker_trade_id))
            if details is None or details["pnl"] is None:
                logger.warning(
                    "  trade %s (broker %s): fill txn missing pnl — skipping",
                    t.id,
                    t.broker_trade_id,
                )
                continue

            old_pnl = t.pnl
            old_exit = t.exit_price
            logger.info(
                "  trade %s (broker %s): pnl %s → %.4f   exit %s → %s   txn %s",
                t.id,
                t.broker_trade_id,
                f"{old_pnl:.4f}" if old_pnl is not None else "NULL",
                details["pnl"],
                f"{old_exit:.5f}" if old_exit is not None else "NULL",
                f"{details['exit_price']:.5f}"
                if details["exit_price"] is not None
                else "NULL",
                details["txn_id"],
            )
            if dry_run:
                updated += 1
                continue

            t.pnl = details["pnl"]
            if t.entry_price and t.quantity:
                t.pnl_pct = (details["pnl"] / (t.entry_price * float(t.quantity))) * 100
            if details["exit_price"] is not None:
                t.exit_price = details["exit_price"]
            if details["financing"] is not None:
                # Overwrite (don't accumulate) — this backfill IS the
                # first authoritative reconciliation for this trade,
                # so it defines the correct value.
                t.financing = details["financing"]
            if details["commission"] is not None:
                t.commission = details["commission"]
            t.broker_close_transaction_id = details["txn_id"]
            updated += 1

        if not dry_run:
            db.commit()
            logger.info("run %s: committed %d update(s)", run_id, updated)
        else:
            logger.info("run %s: DRY RUN — would update %d trade(s)", run_id, updated)
        return updated
    finally:
        db.close()


def _all_paper_run_ids() -> list[str]:
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.models import Run
    from agentfx.api.models.run import RunKind

    db = SessionLocal()
    try:
        rows = (
            db.query(Run.id)
            .filter(Run.kind == RunKind.PAPER)
            .order_by(Run.created_at.desc())
            .all()
        )
        return [r[0] for r in rows]
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Single paper run id to backfill")
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="Backfill every paper run in the DB",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing",
    )
    args = parser.parse_args()

    if not (args.run_id or args.all_runs):
        parser.error("Pass --run-id <ID> or --all-runs")

    run_ids = [args.run_id] if args.run_id else _all_paper_run_ids()
    logger.info("backfill starting: %d run(s), dry_run=%s", len(run_ids), args.dry_run)
    total = 0
    for rid in run_ids:
        total += backfill_run(rid, dry_run=args.dry_run)
    logger.info(
        "backfill done: %d trade(s) %s",
        total,
        "would be updated" if args.dry_run else "updated",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
