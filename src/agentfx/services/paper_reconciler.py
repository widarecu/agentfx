"""Transaction-log-based reconciliation for the paper runner (T1).

Wilson UAT 2026-07-15/16: our runner trusted the immediate response
body of every OANDA call. When a broker-side event happened between
our polls (SL fires, TP fires, margin close), we had no way to know
about it — the next `get_position` returned 0 units, but we couldn't
correlate that to a specific Trade row with the correct pnl and exit
reason. Trade rows silently drifted from OANDA reality; forensics
required screenshotting the broker's activity feed.

This module reconciles our local Trade rows against OANDA's
authoritative transaction stream. Called from the runner's health
tick every few iterations. Idempotent — safe to call multiple times.

Design:
- Every OANDA account has a monotonic transaction log. We store the
  last-observed ID on the run row (`runs.last_transaction_id`) and
  poll `/transactions/sinceid` for anything newer.
- For each ORDER_FILL transaction that closes a trade WE opened
  (matched via `Trade.broker_trade_id`), update:
    - exit_timestamp = txn.time
    - exit_price = txn.price
    - pnl = txn.pl
    - exit_reason inferred from txn.reason (STOP_LOSS_ORDER,
      TAKE_PROFIT_ORDER, MARKET_ORDER, etc.)
    - broker_close_transaction_id = txn.id
- Update run.last_transaction_id to response's lastTransactionID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# OANDA v20 fill `reason` → our exit_reason taxonomy.
# See docs section on ORDER_FILL.reason enum.
_REASON_TO_EXIT: dict[str, str] = {
    "STOP_LOSS_ORDER": "stop",
    "TAKE_PROFIT_ORDER": "target",
    "TRAILING_STOP_LOSS_ORDER": "stop",
    "GUARANTEED_STOP_LOSS_ORDER": "stop",
    "MARKET_ORDER_POSITION_CLOSEOUT": "broker_other",
    "MARKET_ORDER_TRADE_CLOSE": "broker_other",
    "MARKET_ORDER_MARGIN_CLOSEOUT": "broker_other",
    "MARGIN_CLOSEOUT": "broker_other",
    "MARKET_ORDER": "broker_other",  # a market order we placed to close
}


@dataclass(frozen=True)
class ReconcileResult:
    """What `reconcile_broker_transactions` did on one invocation."""

    transactions_seen: int
    trades_updated: int
    new_last_transaction_id: str | None
    notes: list[str]


def _classify_exit_reason(txn_reason: str | None) -> str:
    """Map an OANDA fill `reason` to our exit_reason taxonomy."""
    if not txn_reason:
        return "broker_other"
    return _REASON_TO_EXIT.get(txn_reason, "broker_other")


def _parse_oanda_time(raw: Any) -> datetime | None:
    """OANDA returns ISO8601 with sub-microsecond precision. Python's
    fromisoformat is strict — trim the sub-microsecond digits and
    normalize the trailing Z.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim to microseconds (6 digits after the dot); anything beyond
    # is nanoseconds which datetime can't hold.
    if "." in s and "+" in s:
        head, tail = s.split("+", 1)
        if "." in head:
            base, frac = head.split(".", 1)
            frac = frac[:6]
            head = f"{base}.{frac}"
        s = f"{head}+{tail}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _closed_trade_ids(txn: dict) -> list[str]:
    """Extract the trade IDs that this ORDER_FILL transaction closed.

    OANDA v20 puts closures in `tradesClosed` (array) and full/partial
    close details in `tradeClosed` (singular). We collect both — some
    responses use one shape and some the other depending on how the
    close was initiated.
    """
    ids: list[str] = []
    solo = txn.get("tradeClosed") or {}
    if solo.get("tradeID"):
        ids.append(str(solo["tradeID"]))
    for entry in txn.get("tradesClosed") or []:
        tid = entry.get("tradeID") if isinstance(entry, dict) else None
        if tid:
            ids.append(str(tid))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def reconcile_broker_transactions(*, db, run, client) -> ReconcileResult:
    """Poll OANDA transactions since `run.last_transaction_id`, update
    any of our open Trade rows that OANDA has closed broker-side, and
    advance the high-water mark.

    Idempotent: only closes trades whose `exit_timestamp` is still
    NULL. Safe to call after every health tick.

    Returns a `ReconcileResult` so the caller can log a one-liner
    ("processed 3 txns, updated 1 trade") without depending on
    logger config.
    """
    from agentfx.api.models import Trade

    if run.last_transaction_id is None:
        # Not yet initialized — nothing to reconcile against. The
        # runner sets this on first successful signal-tick.
        return ReconcileResult(
            transactions_seen=0,
            trades_updated=0,
            new_last_transaction_id=None,
            notes=["last_transaction_id not initialised"],
        )

    resp = client.get_transactions_since(run.last_transaction_id)
    txns = resp.get("transactions") or []
    new_last = resp.get("lastTransactionID") or run.last_transaction_id
    notes: list[str] = []
    updated = 0

    for txn in txns:
        txn_type = txn.get("type")
        if txn_type == "DAILY_FINANCING":
            # Overnight rollover — walk positionFinancings[]/
            # openTradeFinancings[] and accumulate the per-trade
            # financing charge onto our Trade rows. Idempotent by
            # transaction id — high-water advancement below prevents
            # double-application on subsequent reconciler runs.
            for pf in txn.get("positionFinancings") or []:
                for otf in pf.get("openTradeFinancings") or []:
                    broker_tid = str(otf.get("tradeID") or "")
                    if not broker_tid:
                        continue
                    trade = (
                        db.query(Trade)
                        .filter(
                            Trade.run_id == run.id,
                            Trade.broker_trade_id == broker_tid,
                        )
                        .one_or_none()
                    )
                    if trade is None:
                        continue  # not our trade
                    fin = float(otf.get("financing", 0) or 0)
                    trade.financing = float(trade.financing or 0.0) + fin
                    updated += 1
                    notes.append(
                        f"daily_financing trade {trade.id} broker_trade_id="
                        f"{broker_tid} +financing={fin}"
                    )
            continue
        if txn_type != "ORDER_FILL":
            continue
        closed_ids = _closed_trade_ids(txn)
        if not closed_ids:
            continue
        # Look up each closed trade in our DB by broker_trade_id.
        for broker_tid in closed_ids:
            trade = (
                db.query(Trade)
                .filter(
                    Trade.run_id == run.id,
                    Trade.broker_trade_id == broker_tid,
                )
                .one_or_none()
            )
            if trade is None:
                notes.append(f"unknown broker_trade_id={broker_tid}")
                continue
            # Idempotency: skip only if we've ALREADY reconciled this
            # trade against this exact OANDA transaction. `exit_timestamp
            # is not None` was too broad — it also skipped trades that
            # the runner had marked closed with a LOCAL estimate (e.g.
            # `_reconcile_broker_side_close` populates exit_timestamp +
            # exit_price + a locally-computed pnl the moment it detects
            # OANDA has flatted the position). Under the old guard the
            # authoritative OANDA `realizedPL` never overwrote that
            # estimate. Wilson UAT 2026-07-26 audit found 2 trades in
            # his EUR_USD run with pnl off by a combined $271: one
            # with pnl=0 (broker_other classification during weekend
            # rollover) and one with pnl=$742 vs OANDA's $1094 (local
            # multiplication in the wrong currency for a non-USD
            # account). Switching to txn-id-based idempotency lets the
            # reconciler overwrite the estimate on its next tick.
            already_reconciled = trade.broker_close_transaction_id and str(
                trade.broker_close_transaction_id
            ) == str(txn.get("id") or "")
            if already_reconciled:
                continue
            # Prefer `tradeClosed`/`tradesClosed` entry's pnl + financing
            # if it matches this specific trade — falls back to the txn's
            # top-level `pl`/`financing`.
            per_trade_pnl: float | None = None
            per_trade_financing: float | None = None
            solo = txn.get("tradeClosed") or {}
            if str(solo.get("tradeID")) == broker_tid:
                if "realizedPL" in solo:
                    per_trade_pnl = float(solo["realizedPL"])
                if "financing" in solo:
                    per_trade_financing = float(solo["financing"])
            else:
                for entry in txn.get("tradesClosed") or []:
                    if (
                        isinstance(entry, dict)
                        and str(entry.get("tradeID")) == broker_tid
                    ):
                        if "realizedPL" in entry:
                            per_trade_pnl = float(entry["realizedPL"])
                        if "financing" in entry:
                            per_trade_financing = float(entry["financing"])
                        break
            if per_trade_pnl is None and "pl" in txn:
                per_trade_pnl = float(txn["pl"])
            if per_trade_financing is None and "financing" in txn:
                per_trade_financing = float(txn["financing"])

            trade.exit_timestamp = _parse_oanda_time(txn.get("time")) or datetime.now(
                UTC
            )
            if "price" in txn:
                trade.exit_price = float(txn["price"])
            if per_trade_pnl is not None:
                trade.pnl = per_trade_pnl
                if trade.entry_price and trade.quantity:
                    trade.pnl_pct = (
                        per_trade_pnl / (trade.entry_price * float(trade.quantity))
                    ) * 100
            # Add the close-time financing on top of any accumulated
            # DAILY_FINANCING already recorded.
            if per_trade_financing is not None:
                trade.financing = float(trade.financing or 0.0) + per_trade_financing
            # Commission on the close fill. Entry commission would
            # have been captured at trade-entry time by the runner.
            if "commission" in txn:
                trade.commission = float(trade.commission or 0.0) + float(
                    txn["commission"]
                )
            trade.exit_reason = _classify_exit_reason(txn.get("reason"))
            trade.broker_close_transaction_id = str(txn.get("id") or "")
            updated += 1
            notes.append(
                f"reconciled trade {trade.id} broker_trade_id={broker_tid} "
                f"exit_reason={trade.exit_reason} pnl={per_trade_pnl} "
                f"financing={per_trade_financing}"
            )

    if new_last != run.last_transaction_id:
        run.last_transaction_id = new_last
    db.commit()

    # Wilson 2026-08-24: position-mismatch check. On top of the
    # transaction-log reconciliation above, compare the CURRENT OANDA
    # position against the sum of our OPEN Trade rows. This catches:
    #
    #   * Stranded broker positions: DB thinks flat, OANDA holds units.
    #     Symptom of the Aug 21 event where `_flatten_and_halt` marked
    #     trades closed even though `close_all_positions` failed.
    #   * Ghost DB open trades: DB has open trade, OANDA holds nothing.
    #     Can happen if a close txn was missed and we haven't yet
    #     processed the resulting fill.
    #   * Direction/size drift: both have positions but they don't
    #     agree on units.
    #
    # Idempotent: uses `run.parameters['position_mismatch_alerted_at']`
    # so the alarm fires ONCE per mismatch episode, not every 60s.
    try:
        mismatch_note = _check_position_mismatch(db=db, run=run, client=client)
        if mismatch_note:
            notes.append(mismatch_note)
    except Exception:  # noqa: BLE001 — never let a check crash the reconciler
        logger.exception(
            "reconciler: position-mismatch check failed on %s (continuing)",
            run.id,
        )

    return ReconcileResult(
        transactions_seen=len(txns),
        trades_updated=updated,
        new_last_transaction_id=new_last,
        notes=notes,
    )


def _check_position_mismatch(*, db, run, client) -> str | None:
    """Compare OANDA net position vs sum of open DB Trade units.

    Returns a short human note if mismatch alarm fired this tick;
    None if in-sync or if already-alerted state is unchanged.
    """
    from agentfx.api.models import Trade
    from sqlalchemy.orm.attributes import flag_modified

    # OANDA-side: net units for this instrument. `get_position` returns
    # {"long_units": N, "short_units": M} — subtract to get signed.
    try:
        pos = client.get_position(run.pair)
    except Exception:  # noqa: BLE001
        # Broker call failed — can't verify. Skip this tick.
        logger.warning(
            "reconciler: get_position failed for %s (%s) — skipping mismatch check",
            run.id,
            run.pair,
        )
        return None
    long_units = int(pos.get("long_units", 0) or 0)
    short_units = int(pos.get("short_units", 0) or 0)
    oanda_signed = long_units - short_units

    # DB-side: sum of open trade units (with side sign).
    open_trades = (
        db.query(Trade)
        .filter(Trade.run_id == run.id, Trade.exit_timestamp.is_(None))
        .all()
    )
    db_signed = 0
    for t in open_trades:
        side_sign = 1 if (t.side or "").lower() in ("buy", "long") else -1
        db_signed += side_sign * int(t.quantity or 0)

    in_sync = oanda_signed == db_signed
    params = dict(run.parameters or {})
    alerted_at = params.get("position_mismatch_alerted_at")

    if in_sync:
        # Clear any prior alert marker.
        if alerted_at is not None:
            params.pop("position_mismatch_alerted_at", None)
            params.pop("position_mismatch_details", None)
            run.parameters = params
            flag_modified(run, "parameters")
            db.commit()
            logger.info(
                "reconciler: position-mismatch RESOLVED on %s (OANDA=%d, DB=%d)",
                run.id,
                oanda_signed,
                db_signed,
            )
            return f"position_mismatch_resolved OANDA={oanda_signed} DB={db_signed}"
        return None

    # Mismatch. Idempotent: only alarm once per episode.
    if alerted_at is not None:
        return None

    now = datetime.now(UTC)
    diff = oanda_signed - db_signed
    kind = (
        "stranded_broker_position"
        if not open_trades and oanda_signed != 0
        else "ghost_db_trade"
        if oanda_signed == 0 and open_trades
        else "size_or_direction_drift"
    )
    logger.error(
        "reconciler: POSITION MISMATCH on %s (%s) — "
        "OANDA=%d, DB=%d, diff=%+d, kind=%s. Requires operator review.",
        run.id,
        run.pair,
        oanda_signed,
        db_signed,
        diff,
        kind,
    )

    from agentfx.services.paper_runner import _write_decision  # local to avoid cycle

    _write_decision(
        db,
        run,
        action="USER_POSITION_MISMATCH",
        reason=(
            f"OANDA position ({oanda_signed} units) does not match DB open "
            f"trades ({db_signed} units, {len(open_trades)} rows). "
            f"Diff={diff:+d}. Kind={kind}. "
            f"Broker-side state may be stranded — manual review required."
        ),
        outputs={
            "paper_run_id": run.id,
            "instrument": run.pair,
            "oanda_signed_units": oanda_signed,
            "db_signed_units": db_signed,
            "diff_units": diff,
            "kind": kind,
            "open_db_trades": [
                {
                    "id": t.id,
                    "side": t.side,
                    "units": t.quantity,
                    "entry_price": t.entry_price,
                }
                for t in open_trades
            ],
        },
    )

    params["position_mismatch_alerted_at"] = now.isoformat()
    params["position_mismatch_details"] = {
        "oanda_signed_units": oanda_signed,
        "db_signed_units": db_signed,
        "kind": kind,
    }
    run.parameters = params
    flag_modified(run, "parameters")
    db.commit()

    return f"position_mismatch_alerted OANDA={oanda_signed} DB={db_signed} kind={kind}"


__all__ = [
    "ReconcileResult",
    "reconcile_broker_transactions",
]
