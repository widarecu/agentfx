"""Unit tests for the v0.6.1 signal-tick + alignment helpers (US-024).

Locks the pieces that took Wilson's UAT run from monitoring-only to
actually trading:

- `align_to_granularity` — DST-safe rounding to the next M1/M5/M15/H1/D
  boundary.
- `pip_size_for_pair` + `granularity_to_seconds` — pure lookups.
- `_signal_tick` — one boundary tick end-to-end with mocked
  OandaClient + injected candle DataFrame:
   * long signal on flat position places order and persists Trade row.
   * flat signal on open position closes position and updates Trade
     row with exit_timestamp / pnl.
   * hold signal on matching position is a no-op.
   * stale candles skip signal evaluation + write
     USER_SIGNAL_SKIPPED_STALE.
- `_execute_loop` in signal-enabled mode fires the tick once per
  boundary and never on non-boundary iterations.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from agentfx.services.paper_runner import (
    DEFAULT_LOOKBACK_BARS,
    _execute_loop,
    _signal_tick,
    align_to_granularity,
    granularity_to_seconds,
    pip_size_for_pair,
)


pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# align_to_granularity
# ─────────────────────────────────────────────────────────────────────


class TestAlignToGranularity:
    def test_between_boundaries_rounds_up(self):
        # 12:00:15 → next M1 boundary is 12:01:00
        # (60s granularity, remainder 15, add 45)
        now = 43_215.0  # 12:00:15 in epoch modular arithmetic
        assert align_to_granularity(now, 60) == 43_260.0

    def test_on_boundary_advances_to_next(self):
        # Exactly on a boundary must NOT return the same instant —
        # otherwise the signal tick would fire twice.
        now = 60.0
        assert align_to_granularity(now, 60) == 120.0

    def test_h1_boundary(self):
        # 01:23:45 → next hour is 02:00:00
        now = 5_025.0  # ~01:23:45
        aligned = align_to_granularity(now, 3600)
        assert aligned == 7_200.0

    def test_d_boundary(self):
        # Halfway through a day → aligns to next midnight
        now = 43_200.0
        aligned = align_to_granularity(now, 86_400)
        assert aligned == 86_400.0

    def test_zero_granularity_returns_now(self):
        # Defensive — a misconfigured granularity of 0 shouldn't
        # divide-by-zero.
        assert align_to_granularity(100.0, 0) == 100.0


class TestGranularityToSeconds:
    @pytest.mark.parametrize(
        ("granularity", "expected"),
        [
            ("M1", 60),
            ("M5", 300),
            ("M15", 900),
            ("H1", 3600),
            ("H4", 14_400),
            ("D", 86_400),
            ("UNKNOWN", 60),  # Fallback
        ],
    )
    def test_lookup(self, granularity, expected):
        assert granularity_to_seconds(granularity) == expected


class TestPipSize:
    def test_major_returns_0_0001(self):
        assert pip_size_for_pair("EUR_USD") == 0.0001

    def test_jpy_returns_0_01(self):
        assert pip_size_for_pair("USD_JPY") == 0.01

    def test_jpy_prefix_returns_0_01(self):
        assert pip_size_for_pair("EUR_JPY") == 0.01


# ─────────────────────────────────────────────────────────────────────
# _signal_tick
# ─────────────────────────────────────────────────────────────────────


def _make_run(user_id: int = 42) -> SimpleNamespace:
    from agentfx.api.models import RunStatus

    return SimpleNamespace(
        id="run_test_signal",
        pair="EUR_USD",
        user_id=user_id,
        status=RunStatus.RUNNING,
        stop_reason=None,
        completed_at=None,
        snapshot_balance=10_000.0,
        current_balance=10_000.0,
        pnl=0.0,
        kill_requested_at=None,
        risk_config={
            "max_position_size_units": 10_000,
            "daily_loss_pct": 2.0,
            "kill_switch_dd_pct": 5.0,
        },
    )


def _make_strategy(signals: list[int], entry_long_ind: str = "sma") -> SimpleNamespace:
    """A stand-in for `CompiledDSLStrategy`. `generate_signals` returns
    the pre-baked signal series. `spec.entry.long.lhs.ind` shape
    matches what `_signal_reason` walks."""
    series = pd.Series(signals, dtype=int)

    def _generate(_df):
        return series

    lhs = SimpleNamespace(ind=entry_long_ind, args=[20])
    rhs = SimpleNamespace(ind="sma", args=[50])
    long_cond = SimpleNamespace(lhs=lhs, cmp=">", rhs=rhs)
    entry = SimpleNamespace(long=long_cond, short=None)
    spec = SimpleNamespace(entry=entry)
    return SimpleNamespace(generate_signals=_generate, spec=spec)


def _mk_candles_df(bars: int, latest_ts: datetime) -> pd.DataFrame:
    """Return `bars` M1 candles ending at `latest_ts`."""
    index = pd.DatetimeIndex(
        [latest_ts - timedelta(minutes=(bars - 1 - i)) for i in range(bars)], tz="UTC"
    )
    return pd.DataFrame(
        {
            "open": [1.10 + 0.0001 * i for i in range(bars)],
            "high": [1.1005 + 0.0001 * i for i in range(bars)],
            "low": [1.0995 + 0.0001 * i for i in range(bars)],
            "close": [1.10 + 0.0001 * i for i in range(bars)],
            "volume": [100.0] * bars,
        },
        index=index,
    )


class TestSignalTick:
    """The signal tick is the whole point of US-024 — placement, close,
    and skip paths get direct coverage here so `_execute_loop` only
    needs to wire them together."""

    def test_long_signal_from_flat_places_order_and_persists_trade(self):
        # Signal series ends with +1 → long entry from flat.
        strategy = _make_strategy(signals=[0, 0, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.place_market_order.return_value = {
            "trade_id": "trade_broker_123",
            "fill_price": 1.1005,
            "units": 10_000,
        }
        db = MagicMock()
        run = _make_run()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.place_market_order.assert_called_once_with(
            "EUR_USD",
            10_000,
            stop_loss_pips=None,
            take_profit_pips=None,
        )
        # A Trade row got added.
        added = [c.args[0] for c in db.add.call_args_list]
        trade_rows = [r for r in added if type(r).__name__ == "Trade"]
        assert len(trade_rows) == 1
        trade = trade_rows[0]
        assert trade.side == "buy"
        assert trade.quantity == 10_000
        assert trade.entry_price == 1.1005
        assert trade.origin == "live"
        # v0.6.1 UAT enrichment (2026-07-13): signal_reason now includes
        # indicator args + trigger-bar timestamp so Wilson can verify
        # what the runner saw on the chart. Mock is duck-typed so
        # value evaluation is skipped, but labels + timestamp render.
        assert trade.signal_reason.startswith("sma[20] > sma[50]")
        assert " @ " in trade.signal_reason
        # A USER_ORDER_PLACED decision log too.
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_ORDER_PLACED" for d in decisions)

    def test_flat_signal_on_open_position_closes_and_updates_trade(self):
        # Signal series ends with 0 → close current long position.
        strategy = _make_strategy(signals=[1, 1, 0])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 12.30,
            "short_realized_pnl": 0.0,
            "long_units_closed": 10_000,
            "short_units_closed": 0,
        }

        # Simulate one open Trade row for this run so the update path
        # fires. db.query returns a chain terminating in an .all() that
        # returns the open trade.
        open_trade = SimpleNamespace(
            id="tr_existing",
            run_id="run_test_signal",
            entry_price=1.1000,
            quantity=10_000,
            exit_timestamp=None,
            pnl=None,
            pnl_pct=None,
            exit_price=None,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [open_trade]

        run = _make_run()
        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.close_all_positions.assert_called_once_with("EUR_USD")
        # Open trade row got updated with exit_timestamp + pnl.
        assert open_trade.pnl == pytest.approx(12.30)
        assert open_trade.exit_timestamp == datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        # USER_POSITION_CLOSED decision log written.
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_POSITION_CLOSED" for d in decisions)

    def test_hold_signal_matches_position_is_noop(self):
        # Signal +1 with existing +10_000 long position → hold.
        strategy = _make_strategy(signals=[1, 1, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        db = MagicMock()
        run = _make_run()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.place_market_order.assert_not_called()
        client.close_all_positions.assert_not_called()
        # No Trade rows added, no decision log spam.
        added = [c.args[0] for c in db.add.call_args_list]
        assert not any(type(r).__name__ == "Trade" for r in added)
        assert not any(type(r).__name__ == "DecisionLog" for r in added)

    def test_held_state_from_flat_position_logs_entry_missed(self):
        # Backtest state is +1 at bar N AND at bar N-1 — no fresh cross
        # at the latest bar. OANDA position is flat (cold deploy or
        # manual close mid-hold). Runner must NOT chase the entry late;
        # it logs USER_ENTRY_MISSED and waits for the next fresh cross.
        # Wilson's UAT observation 2026-07-13 was that the runner opened
        # a short immediately at deploy time even though no fresh MACD
        # cross was visible at the latest bar.
        strategy = _make_strategy(signals=[1, 1, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        db = MagicMock()
        run = _make_run()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.place_market_order.assert_not_called()
        client.close_all_positions.assert_not_called()
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_ENTRY_MISSED" for d in decisions)
        # No Trade rows — we did NOT enter.
        assert not any(type(r).__name__ == "Trade" for r in added)

    def test_stale_candles_skip_signal_and_log(self):
        # Latest candle is 3 minutes old vs a 60s granularity → skip.
        strategy = _make_strategy(signals=[0, 0, 1])
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        stale_df = _mk_candles_df(3, now - timedelta(minutes=3))
        fetch = MagicMock(return_value=stale_df)

        client = MagicMock()
        db = MagicMock()
        run = _make_run()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=now,
        )

        # No broker traffic — signal skipped entirely.
        client.get_position.assert_not_called()
        client.place_market_order.assert_not_called()
        # Skip decision written.
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_SIGNAL_SKIPPED_STALE" for d in decisions)

    def test_empty_candles_skip(self):
        # Ingestion gap: fetch returns 0 rows.
        strategy = _make_strategy(signals=[1])
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        ).astype(float)
        fetch = MagicMock(return_value=empty_df)

        client = MagicMock()
        db = MagicMock()
        run = _make_run()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.get_position.assert_not_called()
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_SIGNAL_SKIPPED_STALE" for d in decisions)

    def test_two_step_flip_closes_first(self):
        # Existing long +10k, signal says short → close first, don't
        # place yet. Next tick will open the short.
        strategy = _make_strategy(signals=[1, 1, -1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 5.0,
            "short_realized_pnl": 0.0,
        }
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        run = _make_run()
        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.close_all_positions.assert_called_once_with("EUR_USD")
        client.place_market_order.assert_not_called()

    def test_stop_loss_and_take_profit_passed_through(self):
        strategy = _make_strategy(signals=[0, 0, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.place_market_order.return_value = {
            "trade_id": "trade_sl_tp",
            "fill_price": 1.1010,
            "units": 5000,
        }
        db = MagicMock()
        run = _make_run()
        run.risk_config = {
            "max_position_size_units": 5000,
            "daily_loss_pct": 2.0,
            "kill_switch_dd_pct": 5.0,
            "stop_loss_pips": 20.0,
            "take_profit_pips": 40.0,
        }

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        client.place_market_order.assert_called_once_with(
            "EUR_USD",
            5000,
            stop_loss_pips=20.0,
            take_profit_pips=40.0,
        )


# ─────────────────────────────────────────────────────────────────────
# _execute_loop with strategy enabled
# ─────────────────────────────────────────────────────────────────────


class TestExecuteLoopSignalIntegration:
    """One boundary-crossing tick fires the signal path; the second
    iteration is still on the previous boundary so it doesn't."""

    def test_signal_tick_fires_once_at_boundary(self):
        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            user_id=1,
            snapshot_balance=10_000.0,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=None,
            risk_config={
                "max_position_size_units": 10_000,
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.place_market_order.return_value = {
            "trade_id": "trade_1",
            "fill_price": 1.10,
            "units": 10_000,
        }
        db = MagicMock()
        strategy = _make_strategy(signals=[0, 0, 1])

        # Feed a clock that starts BEFORE a boundary and advances past
        # it on the first iteration. `align_to_granularity` will set
        # `next_signal_at` initially to +60s from t=100; the tick fires
        # once we cross it.
        clock = iter(
            [
                100.0,  # initial align_to_granularity call
                180.0,  # first "now" inside the loop (past boundary at 120)
                180.0,  # sleep_seconds calculation
                180.0,  # second iteration "now" (still before next 240 boundary)
                180.0,  # second sleep calculation
            ]
        )

        def now_fn() -> float:
            return next(clock)

        candles_df = _mk_candles_df(3, datetime.now(UTC))
        fetch = MagicMock(return_value=candles_df)

        _execute_loop(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_fn=now_fn,
            sleep_fn=MagicMock(),
            max_iterations=2,
        )

        # Only one broker order placement across the two iterations.
        assert client.place_market_order.call_count == 1
