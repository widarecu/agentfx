"""Unit tests for the transaction-log reconciler (T1)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentfx.services.paper_reconciler import (
    _classify_exit_reason,
    _closed_trade_ids,
    _parse_oanda_time,
    reconcile_broker_transactions,
)

pytestmark = pytest.mark.unit


class TestClassifyExitReason:
    def test_stop_loss(self):
        assert _classify_exit_reason("STOP_LOSS_ORDER") == "stop"

    def test_trailing_stop(self):
        assert _classify_exit_reason("TRAILING_STOP_LOSS_ORDER") == "stop"

    def test_guaranteed_stop(self):
        assert _classify_exit_reason("GUARANTEED_STOP_LOSS_ORDER") == "stop"

    def test_take_profit(self):
        assert _classify_exit_reason("TAKE_PROFIT_ORDER") == "target"

    def test_margin_closeout(self):
        assert _classify_exit_reason("MARGIN_CLOSEOUT") == "broker_other"

    def test_market_order(self):
        assert _classify_exit_reason("MARKET_ORDER") == "broker_other"

    def test_unknown_reason(self):
        assert _classify_exit_reason("SOME_FUTURE_REASON") == "broker_other"

    def test_none(self):
        assert _classify_exit_reason(None) == "broker_other"


class TestParseOandaTime:
    def test_z_suffix(self):
        r = _parse_oanda_time("2026-07-15T13:32:02.123456Z")
        assert r == datetime(2026, 7, 15, 13, 32, 2, 123456, tzinfo=UTC)

    def test_nanosecond_precision_trimmed(self):
        """OANDA sometimes returns 9-digit sub-second precision;
        Python datetime only supports microseconds."""
        r = _parse_oanda_time("2026-07-15T13:32:02.123456789+00:00")
        assert r is not None
        assert r.microsecond == 123456

    def test_none(self):
        assert _parse_oanda_time(None) is None

    def test_bogus(self):
        assert _parse_oanda_time("not a date") is None


class TestClosedTradeIds:
    def test_singular_tradeClosed(self):
        txn = {"tradeClosed": {"tradeID": "42"}}
        assert _closed_trade_ids(txn) == ["42"]

    def test_plural_tradesClosed(self):
        txn = {"tradesClosed": [{"tradeID": "1"}, {"tradeID": "2"}]}
        assert _closed_trade_ids(txn) == ["1", "2"]

    def test_both_shapes_dedup(self):
        txn = {
            "tradeClosed": {"tradeID": "1"},
            "tradesClosed": [{"tradeID": "1"}, {"tradeID": "3"}],
        }
        assert _closed_trade_ids(txn) == ["1", "3"]

    def test_no_closures(self):
        assert _closed_trade_ids({"type": "MARKET_ORDER"}) == []


def _make_run(user_id: int = 1, last_txn_id: str | None = "100"):
    return SimpleNamespace(
        id="run_test",
        user_id=user_id,
        last_transaction_id=last_txn_id,
    )


def _make_trade(
    broker_trade_id: str,
    exit_ts: datetime | None = None,
    financing: float | None = None,
    commission: float | None = None,
):
    return SimpleNamespace(
        id=f"tr_{broker_trade_id}",
        broker_trade_id=broker_trade_id,
        exit_timestamp=exit_ts,
        exit_price=None,
        pnl=None,
        pnl_pct=None,
        exit_reason=None,
        broker_close_transaction_id=None,
        entry_price=1.10000,
        quantity=10_000.0,
        financing=financing,
        commission=commission,
    )


def _wire_trade_lookup(db: MagicMock, by_broker_id: dict):
    """Configure db.query(Trade).filter(...).one_or_none() to return the
    trade matching the broker_trade_id used in the filter. Simulates
    what the reconciler does per-transaction."""
    matcher = MagicMock()

    def _one_or_none():
        # The reconciler filters by run_id + broker_trade_id. We
        # capture the last filter args via the mock chain.
        # For test simplicity, look up by whatever was passed in the
        # SECOND filter arg (broker_trade_id equality).
        # Easier: sniff the .filter() call args.
        call_args = db.query.return_value.filter.call_args
        # SQLAlchemy filter is passed BinaryExpression objects — hard
        # to introspect from a MagicMock. So we take a shortcut: the
        # test always uses one trade at a time, so return by the
        # order of insertion into by_broker_id.
        if not by_broker_id:
            return None
        return next(iter(by_broker_id.values()))

    matcher.one_or_none.side_effect = _one_or_none
    db.query.return_value.filter.return_value = matcher


class TestReconcile:
    def test_no_last_transaction_id_is_noop(self):
        db = MagicMock()
        run = _make_run(last_txn_id=None)
        client = MagicMock()

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 0
        assert result.transactions_seen == 0
        client.get_transactions_since.assert_not_called()

    def test_stop_loss_fill_updates_trade(self):
        db = MagicMock()
        run = _make_run(last_txn_id="100")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "150",
                    "type": "ORDER_FILL",
                    "reason": "STOP_LOSS_ORDER",
                    "time": "2026-07-15T13:35:00.000000Z",
                    "price": "1.14167",
                    "pl": "-22.00",
                    "tradeClosed": {
                        "tradeID": "broker-42",
                        "realizedPL": "-22.00",
                    },
                }
            ],
            "lastTransactionID": "150",
        }
        trade = _make_trade(broker_trade_id="broker-42")
        _wire_trade_lookup(db, {"broker-42": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 1
        assert result.new_last_transaction_id == "150"
        assert trade.exit_reason == "stop"
        assert trade.exit_price == pytest.approx(1.14167)
        assert trade.pnl == pytest.approx(-22.00)
        assert trade.broker_close_transaction_id == "150"
        assert trade.exit_timestamp is not None
        assert run.last_transaction_id == "150"
        db.commit.assert_called()

    def test_take_profit_fill_updates_trade(self):
        db = MagicMock()
        run = _make_run(last_txn_id="200")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "205",
                    "type": "ORDER_FILL",
                    "reason": "TAKE_PROFIT_ORDER",
                    "time": "2026-07-15T13:45:00.000000Z",
                    "price": "1.13760",
                    "pl": "50.00",
                    "tradesClosed": [{"tradeID": "broker-77", "realizedPL": "50.00"}],
                }
            ],
            "lastTransactionID": "205",
        }
        trade = _make_trade(broker_trade_id="broker-77")
        _wire_trade_lookup(db, {"broker-77": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 1
        assert trade.exit_reason == "target"
        assert trade.pnl == pytest.approx(50.00)

    def test_already_closed_trade_not_rewritten(self):
        """If our runner already closed the trade (e.g., via
        opposite_flip), the reconciler must not overwrite the values
        with the broker's later ORDER_FILL for what's essentially
        the same close event."""
        db = MagicMock()
        run = _make_run(last_txn_id="300")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "305",
                    "type": "ORDER_FILL",
                    "reason": "MARKET_ORDER",
                    "time": "2026-07-15T13:50:00Z",
                    "price": "1.14000",
                    "pl": "-10.00",
                    "tradeClosed": {"tradeID": "broker-42"},
                }
            ],
            "lastTransactionID": "305",
        }
        pre_existing_exit = datetime(2026, 7, 15, 13, 40, tzinfo=UTC)
        trade = _make_trade(broker_trade_id="broker-42", exit_ts=pre_existing_exit)
        trade.exit_reason = "opposite_flip"
        trade.pnl = -5.0  # runner-recorded value
        _wire_trade_lookup(db, {"broker-42": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        # No update — trade was already closed.
        assert result.trades_updated == 0
        # Original runner-recorded values preserved.
        assert trade.exit_timestamp == pre_existing_exit
        assert trade.exit_reason == "opposite_flip"
        assert trade.pnl == -5.0

    def test_unknown_broker_trade_id_noted_not_updated(self):
        """A close for a trade we don't have (e.g., manual OANDA-side
        trade) is noted but not treated as an error."""
        db = MagicMock()
        run = _make_run(last_txn_id="400")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "410",
                    "type": "ORDER_FILL",
                    "reason": "STOP_LOSS_ORDER",
                    "time": "2026-07-15T13:55:00Z",
                    "price": "1.14000",
                    "pl": "0.00",
                    "tradeClosed": {"tradeID": "some-other-trade"},
                }
            ],
            "lastTransactionID": "410",
        }
        db.query.return_value.filter.return_value.one_or_none.return_value = None

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 0
        assert any("unknown broker_trade_id" in n for n in result.notes)
        # High-water mark still advances so we don't rescan the same
        # transactions next tick.
        assert run.last_transaction_id == "410"

    def test_order_fill_captures_financing_and_commission(self):
        """v0.6.1 (Wilson 2026-07-16): the close fill's financing
        and commission fields must land on the Trade row alongside
        the price pnl. Prior code only extracted `pl`."""
        db = MagicMock()
        run = _make_run(last_txn_id="600")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "610",
                    "type": "ORDER_FILL",
                    "reason": "STOP_LOSS_ORDER",
                    "time": "2026-07-15T14:00:00Z",
                    "price": "1.14167",
                    "pl": "-22.00",
                    "financing": "-1.50",
                    "commission": "0.30",
                    "tradeClosed": {
                        "tradeID": "broker-99",
                        "realizedPL": "-22.00",
                        "financing": "-1.50",
                    },
                }
            ],
            "lastTransactionID": "610",
        }
        # Pre-existing DAILY_FINANCING accumulated -0.75 during the
        # hold; close fill adds another -1.50.
        trade = _make_trade(broker_trade_id="broker-99", financing=-0.75)
        _wire_trade_lookup(db, {"broker-99": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 1
        # Financing accumulates: prior -0.75 + close -1.50 = -2.25
        assert trade.financing == pytest.approx(-2.25)
        assert trade.commission == pytest.approx(0.30)
        assert trade.pnl == pytest.approx(-22.00)

    def test_daily_financing_accumulates_on_open_trade(self):
        """DAILY_FINANCING transactions are issued nightly at 5pm NY
        for any trade held over the rollover. The reconciler walks
        positionFinancings → openTradeFinancings and adds each
        trade's share to Trade.financing."""
        db = MagicMock()
        run = _make_run(last_txn_id="700")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "701",
                    "type": "DAILY_FINANCING",
                    "time": "2026-07-15T21:00:00Z",
                    "positionFinancings": [
                        {
                            "instrument": "EUR_USD",
                            "financing": "-2.10",
                            "openTradeFinancings": [
                                {"tradeID": "broker-501", "financing": "-2.10"}
                            ],
                        }
                    ],
                }
            ],
            "lastTransactionID": "701",
        }
        trade = _make_trade(broker_trade_id="broker-501")
        assert trade.financing is None  # sanity
        _wire_trade_lookup(db, {"broker-501": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 1
        assert trade.financing == pytest.approx(-2.10)
        # Trade stays open — DAILY_FINANCING doesn't close anything.
        assert trade.exit_timestamp is None

    def test_daily_financing_multiple_rollovers_accumulate(self):
        """Two consecutive DAILY_FINANCING events for the same trade
        both accumulate onto Trade.financing (idempotent because the
        reconciler advances last_transaction_id and never rescans)."""
        db = MagicMock()
        run = _make_run(last_txn_id="800")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "801",
                    "type": "DAILY_FINANCING",
                    "positionFinancings": [
                        {
                            "openTradeFinancings": [
                                {"tradeID": "broker-77", "financing": "-1.00"}
                            ],
                        }
                    ],
                },
                {
                    "id": "802",
                    "type": "DAILY_FINANCING",
                    "positionFinancings": [
                        {
                            "openTradeFinancings": [
                                {"tradeID": "broker-77", "financing": "-1.20"}
                            ],
                        }
                    ],
                },
            ],
            "lastTransactionID": "802",
        }
        trade = _make_trade(broker_trade_id="broker-77")
        _wire_trade_lookup(db, {"broker-77": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 2  # two accumulations
        assert trade.financing == pytest.approx(-2.20)

    def test_non_order_fill_transactions_ignored(self):
        db = MagicMock()
        run = _make_run(last_txn_id="500")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {"id": "501", "type": "MARKET_ORDER"},
                {"id": "502", "type": "STOP_LOSS_ORDER"},
                {"id": "503", "type": "CLIENT_CONFIGURE"},
            ],
            "lastTransactionID": "503",
        }

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 0
        assert result.transactions_seen == 3
