"""Unit tests for the transaction-log reconciler (T1)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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

    def test_already_reconciled_same_txn_not_rewritten(self):
        """Idempotency: if the trade's `broker_close_transaction_id`
        already matches this ORDER_FILL's id, the reconciler must
        not re-write. Wilson UAT 2026-07-26 changed this guard from
        "any exit_timestamp present" to "same txn id" so that trades
        the runner marked closed with a LOCAL estimate still get
        overwritten with OANDA's authoritative realizedPL — while
        genuine repeat reconciliations of the same close event stay
        idempotent.
        """
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
        trade.pnl = -5.0
        trade.broker_close_transaction_id = "305"  # already reconciled this txn
        _wire_trade_lookup(db, {"broker-42": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 0
        assert trade.exit_timestamp == pre_existing_exit
        assert trade.exit_reason == "opposite_flip"
        assert trade.pnl == -5.0

    def test_local_estimate_gets_overwritten_by_oanda_realizedpl(self):
        """Wilson UAT 2026-07-26 regression: OANDA's SL/TP closed a
        position while the runner's local `_reconcile_broker_side_close`
        wrote a placeholder pnl (either 0 for the "broker_other"
        classification path, or an in-wrong-currency estimate for
        non-USD accounts). The reconciler MUST overwrite that
        placeholder when the ORDER_FILL with matching broker_trade_id
        arrives, because OANDA's realizedPL is the only authoritative
        source. Under the pre-fix guard (`exit_timestamp is not None
        → skip`) the placeholder stuck around and the tooltip's
        Realised line was off by a combined $271 across Wilson's run.
        """
        db = MagicMock()
        run = _make_run(last_txn_id="900")
        client = MagicMock()
        client.get_transactions_since.return_value = {
            "transactions": [
                {
                    "id": "918",
                    "type": "ORDER_FILL",
                    "reason": "MARKET_ORDER_POSITION_CLOSEOUT",
                    "time": "2026-07-20T00:01:09.820Z",
                    "price": "1.14286",
                    "pl": "-81.1604",
                    "financing": "0.0000",
                    "tradesClosed": [{"tradeID": "914", "realizedPL": "-81.1604"}],
                }
            ],
            "lastTransactionID": "918",
        }
        # Trade already marked "closed" by the runner's local
        # estimator with the placeholder values Wilson saw.
        placeholder_exit = datetime(2026, 7, 20, 0, 1, 9, tzinfo=UTC)
        trade = _make_trade(broker_trade_id="914", exit_ts=placeholder_exit)
        trade.exit_price = 1.1425  # runner defaulted to entry_price
        trade.pnl = 0.0  # placeholder
        trade.exit_reason = "broker_other"
        trade.broker_close_transaction_id = None  # NOT yet reconciled
        _wire_trade_lookup(db, {"914": trade})

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert result.trades_updated == 1
        assert trade.pnl == -81.1604  # OANDA truth now stored
        assert trade.exit_price == 1.14286  # OANDA fill price replaces estimate
        assert trade.exit_reason == "broker_other"  # already classified — kept
        assert trade.broker_close_transaction_id == "918"  # idempotency stamp

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


class TestPositionMismatchCheck:
    """Wilson 2026-08-24: reconciler compares OANDA net position
    against sum of open DB Trade rows every tick. Detects the Aug 21
    stranded-broker-position scenario within one health cycle instead
    of silently drifting for hours."""

    def _run(self, parameters=None):
        return SimpleNamespace(
            id="run_mismatch",
            pair="EUR_USD",
            last_transaction_id="900",
            parameters=parameters or {},
            user_id=42,
        )

    def _client(self, long_units=0, short_units=0, txn_response=None):
        c = MagicMock()
        c.get_position.return_value = {
            "long_units": long_units,
            "short_units": short_units,
        }
        c.get_transactions_since.return_value = txn_response or {
            "transactions": [],
            "lastTransactionID": "900",
        }
        return c

    def _db_with_open_trades(self, trades):
        db = MagicMock()
        # Two query call sites: the reconciler's own (ORDER_FILL match)
        # and the mismatch check's (open-trade sum). Both hit .filter(...).all().
        db.query.return_value.filter.return_value.all.return_value = trades
        db.query.return_value.filter.return_value.one_or_none.return_value = None
        return db

    def test_stranded_broker_position_fires_alarm(self):
        """OANDA has open units but DB thinks flat → alarm."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run()
        client = self._client(long_units=16_987)
        db = self._db_with_open_trades([])  # DB says no open trades

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert any("position_mismatch_alerted" in n for n in result.notes), result.notes
        assert "position_mismatch_alerted_at" in run.parameters
        details = run.parameters["position_mismatch_details"]
        assert details["kind"] == "stranded_broker_position"
        assert details["oanda_signed_units"] == 16_987
        assert details["db_signed_units"] == 0

    def test_ghost_db_trade_fires_alarm(self):
        """DB has open trade but OANDA has 0 units."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run()
        client = self._client(long_units=0, short_units=0)
        open_trade = SimpleNamespace(
            id="tr_ghost",
            side="buy",
            quantity=10_000,
            entry_price=1.17,
            exit_timestamp=None,
        )
        db = self._db_with_open_trades([open_trade])

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert any("position_mismatch_alerted" in n for n in result.notes)
        assert run.parameters["position_mismatch_details"]["kind"] == "ghost_db_trade"

    def test_in_sync_no_alarm(self):
        """Both sides agree — nothing fires."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run()
        client = self._client(long_units=15_000)
        open_trade = SimpleNamespace(
            id="tr_ok",
            side="buy",
            quantity=15_000,
            entry_price=1.17,
            exit_timestamp=None,
        )
        db = self._db_with_open_trades([open_trade])

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert not any("position_mismatch" in n for n in result.notes)
        assert "position_mismatch_alerted_at" not in run.parameters

    def test_already_alerted_stays_silent(self):
        """Idempotent: mismatch persists across ticks — alarm fires
        once, then stays silent until it clears."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run(
            parameters={
                "position_mismatch_alerted_at": "2026-08-24T10:00:00+00:00",
                "position_mismatch_details": {
                    "oanda_signed_units": 16_987,
                    "db_signed_units": 0,
                    "kind": "stranded_broker_position",
                },
            }
        )
        client = self._client(long_units=16_987)
        db = self._db_with_open_trades([])

        result = reconcile_broker_transactions(db=db, run=run, client=client)

        # No new alert note this tick — already flagged.
        assert not any("position_mismatch_alerted" in n for n in result.notes)

    def test_resolved_clears_marker(self):
        """When mismatch is fixed (operator manually closed broker
        position), the marker is cleared on the next tick."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run(
            parameters={
                "position_mismatch_alerted_at": "2026-08-24T10:00:00+00:00",
                "position_mismatch_details": {
                    "oanda_signed_units": 16_987,
                    "db_signed_units": 0,
                    "kind": "stranded_broker_position",
                },
            }
        )
        client = self._client(long_units=0)  # broker now flat
        db = self._db_with_open_trades([])  # DB also flat

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert any("position_mismatch_resolved" in n for n in result.notes)
        assert "position_mismatch_alerted_at" not in run.parameters


class TestPositionMismatchWilsonAug31:
    """Wilson 2026-08-31: three specific bugs found in the mismatch
    check + reconciler flow, all covered here."""

    def _run(self, parameters=None, last_txn_id="900"):
        return SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            last_transaction_id=last_txn_id,
            parameters=parameters or {},
            user_id=42,
        )

    def _client_with_short(self, short_units=-16741):
        """OANDA reports short units as NEGATIVE per convention."""
        c = MagicMock()
        c.get_position.return_value = {
            "long_units": 0,
            "short_units": short_units,
            "long": {"units": "0"},
            "short": {
                "units": str(short_units),
                "averagePrice": "1.16158",
                "tradeIDs": ["552"],
            },
        }
        c.get_transactions_since.return_value = {
            "transactions": [],
            "lastTransactionID": "900",
        }
        return c

    def test_short_position_mismatch_uses_correct_signed_math(self):
        """Bug 2 fix: `oanda_signed = long + short` (short is
        already negative), not `long - short`. Under the old code, a
        real SHORT position at broker (short=-16741) produced
        oanda_signed=+16741 and could look like a size-drift mismatch
        rather than a stranded position."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run()
        client = self._client_with_short(-16_741)
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.one_or_none.return_value = None

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            result = reconcile_broker_transactions(db=db, run=run, client=client)

        assert "position_mismatch_alerted_at" in run.parameters
        details = run.parameters["position_mismatch_details"]
        # Correct signed value = 0 + (-16741) = -16741
        assert details["oanda_signed_units"] == -16_741
        assert details["kind"] == "stranded_broker_position"

    def test_mismatch_check_runs_when_last_transaction_id_is_None(self):
        """Bug 1 fix: previously the reconciler early-returned when
        last_transaction_id was None, skipping the mismatch check
        entirely. Aug 28-31 event had this state throughout, so
        alarm never fired."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        # last_transaction_id=None simulates a fresh restart cycle.
        run = self._run(last_txn_id=None)
        client = self._client_with_short(-16_741)
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.one_or_none.return_value = None

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            result = reconcile_broker_transactions(db=db, run=run, client=client)

        # Even with no transactions to process, the mismatch check
        # runs and detects the stranded broker position.
        assert any("position_mismatch_alerted" in n for n in result.notes), result.notes
        assert "position_mismatch_alerted_at" in run.parameters
        # And the client's get_transactions_since was NOT called (still
        # early-returned) but get_position WAS called.
        client.get_transactions_since.assert_not_called()
        client.get_position.assert_called()

    def test_stranded_position_gets_adopted_as_new_trade_row(self):
        """Bug 4 fix: on stranded_broker_position mismatch, adopt the
        OANDA position into a new DB Trade row so the operator sees it
        in the UI + the runner has an open_trade reference."""
        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run(last_txn_id=None)
        client = self._client_with_short(-16_741)
        db = MagicMock()
        # first .all() call = mismatch check (0 open trades); rest are
        # simple MagicMock defaults.
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.one_or_none.return_value = None
        added_rows: list = []
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            reconcile_broker_transactions(db=db, run=run, client=client)

        # Exactly one Trade row got added — the adopted position.
        # (Decision-log entry from mismatch alarm also goes through
        # db.add so filter for the Trade rows.)
        from agentfx.api.models import Trade

        trade_rows = [r for r in added_rows if isinstance(r, Trade)]
        assert len(trade_rows) == 1
        adopted = trade_rows[0]
        assert adopted.side == "sell"
        assert adopted.quantity == 16_741
        assert adopted.entry_price == 1.16158
        assert adopted.broker_trade_id == "552"
        assert adopted.origin == "adopted_from_broker"
        assert adopted.exit_timestamp is None

    def test_adoption_reopens_phantom_closed_trade(self):
        """If the reconciler previously marked a trade closed
        (broker_other/pnl=0) but the broker still holds it, adoption
        REOPENS the DB row rather than creating a duplicate."""
        from datetime import UTC, datetime

        from agentfx.services.paper_reconciler import (
            reconcile_broker_transactions,
        )

        run = self._run(last_txn_id=None)
        client = self._client_with_short(-16_741)

        # Existing DB row for broker_trade_id 552, phantom-closed.
        phantom = SimpleNamespace(
            id="tr_phantom",
            broker_trade_id="552",
            exit_timestamp=datetime(2026, 8, 28, 23, 54, tzinfo=UTC),
            exit_price=1.16158,
            pnl=0.0,
            pnl_pct=0.0,
            exit_reason="broker_other",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.one_or_none.return_value = phantom

        with patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            reconcile_broker_transactions(db=db, run=run, client=client)

        # Phantom row got REOPENED, no new Trade added.
        assert phantom.exit_timestamp is None
        assert phantom.exit_price is None
        assert phantom.pnl is None
        assert phantom.exit_reason is None
