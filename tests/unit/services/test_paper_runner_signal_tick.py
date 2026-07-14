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


def _make_strategy(
    signals: list[int],
    entry_long_ind: str = "sma",
    *,
    stop_pips: int = 20,
    target_pips: int = 50,
    max_hold_bars: int = 600,
    opposite_signal: bool = True,
) -> SimpleNamespace:
    """A stand-in for `CompiledDSLStrategy`.

    - `generate_signals` and `raw_entry_signals` both return the
      pre-baked series (the runner uses `raw_entry_signals` after the
      v0.6.1 refactor; the older method is kept for compatibility with
      tests that pre-date the rewrite and for backtest callers).
    - `spec.entry.long.lhs.ind` shape matches `_signal_reason`.
    - `spec.exit` mirrors the DSL exit block so the runner can consult
      max_hold_bars, opposite_signal, and DSL-native SL/TP fallbacks.
    """
    series = pd.Series(signals, dtype=int)

    def _generate(_df):
        return series

    lhs = SimpleNamespace(ind=entry_long_ind, args=[20])
    rhs = SimpleNamespace(ind="sma", args=[50])
    long_cond = SimpleNamespace(lhs=lhs, cmp=">", rhs=rhs)
    entry = SimpleNamespace(long=long_cond, short=None)
    exit_rules = SimpleNamespace(
        stop_pips=stop_pips,
        target_pips=target_pips,
        max_hold_bars=max_hold_bars,
        opposite_signal=opposite_signal,
    )
    spec = SimpleNamespace(entry=entry, exit=exit_rules)
    return SimpleNamespace(
        generate_signals=_generate,
        raw_entry_signals=_generate,
        spec=spec,
    )


def _set_open_trade(db: MagicMock, trade: object | None) -> None:
    """Wire the DB mock so `db.query(Trade).filter(...).order_by(...).first()`
    returns `trade` (or None to signal no open trade). The runner's
    max_hold check runs this chain on every tick when there's an OANDA
    position, so tests with a non-flat position must set it explicitly.
    """
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = trade


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

        # v0.6.1 raw-entry refactor: DSL's exit block seeds SL/TP when
        # `risk_config` doesn't override, so tests use the mock's
        # default stop_pips=20, target_pips=50.
        client.place_market_order.assert_called_once_with(
            "EUR_USD",
            10_000,
            stop_loss_pips=20,
            take_profit_pips=50,
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

    def test_no_entry_event_from_flat_is_silent_noop(self):
        # Under the v0.6.1 raw-entry-signal model, `raw_entries.iloc[-1] == 0`
        # means "no fresh cross at the latest bar." With OANDA flat and
        # no entry event, the runner does nothing (silent, no decision
        # log). This replaces the old USER_ENTRY_MISSED path — the raw-
        # entry model has no projected held state to "miss."
        strategy = _make_strategy(signals=[0, 0, 0])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        db = MagicMock()
        _set_open_trade(db, None)
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
        assert not any(type(r).__name__ == "Trade" for r in added)
        assert not any(type(r).__name__ == "DecisionLog" for r in added)

    def test_hold_signal_matches_position_is_noop(self):
        # Fresh long-entry event AND we're already long on OANDA → hold
        # silently (same-direction). Would happen if a prior cross opened
        # us and another same-direction cross fires before an exit.
        strategy = _make_strategy(signals=[0, 0, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        db = MagicMock()
        # No open Trade row → max_hold check is a no-op.
        _set_open_trade(db, None)
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
        assert not any(type(r).__name__ == "Trade" for r in added)
        assert not any(type(r).__name__ == "DecisionLog" for r in added)

    def test_max_hold_forces_close_of_open_trade(self):
        # OANDA has no concept of a bar-count-based exit — the runner
        # enforces `spec.exit.max_hold_bars` itself. When the open trade
        # has been held longer than max_hold * granularity, force-close.
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        # max_hold_bars=5, granularity=60s → 300s limit. Entry was 400s
        # ago → over the limit.
        entry_ts = now - timedelta(seconds=400)
        strategy = _make_strategy(signals=[0, 0, 0], max_hold_bars=5)
        candles_df = _mk_candles_df(3, now)
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 3.30,
            "short_realized_pnl": 0.0,
        }
        open_trade = SimpleNamespace(
            id="tr_open",
            run_id="run_test",
            entry_price=1.10,
            quantity=10_000,
            entry_timestamp=entry_ts,
            exit_timestamp=None,
            pnl=None,
            pnl_pct=None,
            exit_price=None,
        )
        db = MagicMock()
        _set_open_trade(db, open_trade)
        # Second query (inside _close_position_and_persist) fetches all
        # open trades to update — reuse the same row via .all().
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
            now_utc=now,
        )

        client.close_all_positions.assert_called_once_with("EUR_USD")
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        closed = [d for d in decisions if d.action == "USER_POSITION_CLOSED"]
        assert closed
        assert "max_hold_bars=5" in closed[0].reasoning_text

    def test_opposite_signal_false_holds_through_opposite_cross(self):
        # DSL with `opposite_signal: false` (Wilson's genome 019_028
        # config) — an opposite cross while holding is ignored, position
        # keeps riding until stop/target/max_hold fires. Verifies the
        # runner mirrors backtest's opposite_signal behavior.
        strategy = _make_strategy(signals=[0, 0, -1], opposite_signal=False)
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        db = MagicMock()
        _set_open_trade(db, None)
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

        # Opposite cross ignored — no order, no close.
        client.place_market_order.assert_not_called()
        client.close_all_positions.assert_not_called()
        added = [c.args[0] for c in db.add.call_args_list]
        assert not any(type(r).__name__ == "DecisionLog" for r in added)

    def test_stale_candles_skip_signal_and_log(self):
        # Latest bar 5 minutes old (300s) — beyond the current threshold
        # granularity + INGEST_LAG_TOLERANCE = 60 + 150 = 210s. This is
        # the "ingester actually stopped" case we want the guard to
        # catch; normal 3-minute lag is now under the threshold.
        strategy = _make_strategy(signals=[0, 0, 1])
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        stale_df = _mk_candles_df(3, now - timedelta(minutes=5))
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

    def test_normal_ingest_lag_does_not_trip_stale_guard(self):
        # Real UAT pattern (Wilson 2026-07-14): the ingester UPDATEs
        # bar T to `complete=true` only on the poll AFTER bar close.
        # With an ~80s poll cadence, the newest `complete=true` bar at
        # wall-clock T is typically `T - 2*g` and can be `T - 3*g` in
        # the tail. This test locks in that a 180s-old bar (worst-case
        # tail of normal operation) does NOT trip the current g + 150s
        # = 210s threshold. The old 2*g = 120s threshold trip-flapped
        # on this pattern; g + 90s = 150s tripped on the tail.
        strategy = _make_strategy(signals=[0, 0, 0])
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        # _mk_candles_df places `latest_ts` on df.index[-1].
        fresh_df = _mk_candles_df(3, now - timedelta(seconds=180))
        fetch = MagicMock(return_value=fresh_df)

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
            now_utc=now,
        )

        # Stale guard MUST NOT fire — 100s < 150s threshold.
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert not any(d.action == "USER_SIGNAL_SKIPPED_STALE" for d in decisions)

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
        # Existing long +10k, fresh short cross fires with
        # `opposite_signal=True` (mock default) → close first, don't
        # place yet. Next tick will open the short.
        strategy = _make_strategy(signals=[0, 0, -1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 5.0,
            "short_realized_pnl": 0.0,
        }
        db = MagicMock()
        # No expired-max-hold trade, so the max_hold check is a no-op
        # and the flip path fires.
        _set_open_trade(db, None)
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
