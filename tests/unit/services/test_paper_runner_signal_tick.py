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
        last_transaction_id=None,
        parameters={},
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
    min_hold_bars: int = 0,
    opposite_signal: bool = True,
    risk_pct: float = 0.005,
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
        # Wilson UAT 2026-08-02 Fix D: return with the DataFrame's
        # DatetimeIndex so the runner's per-bar watermark logic can
        # read `.index[-1]`. Production `raw_entry_signals` always
        # returns a DatetimeIndex-indexed Series.
        if hasattr(_df, "index") and len(_df.index) == len(series):
            return pd.Series(signals, index=_df.index, dtype=int)
        return series

    lhs = SimpleNamespace(ind=entry_long_ind, args=[20])
    rhs = SimpleNamespace(ind="sma", args=[50])
    long_cond = SimpleNamespace(lhs=lhs, cmp=">", rhs=rhs)
    entry = SimpleNamespace(long=long_cond, short=None)
    exit_rules = SimpleNamespace(
        stop_pips=stop_pips,
        target_pips=target_pips,
        max_hold_bars=max_hold_bars,
        min_hold_bars=min_hold_bars,
        opposite_signal=opposite_signal,
    )
    size_rules = SimpleNamespace(risk_pct=risk_pct)
    spec = SimpleNamespace(entry=entry, exit=exit_rules, size=size_rules)
    return SimpleNamespace(
        generate_signals=_generate,
        raw_entry_signals=_generate,
        spec=spec,
    )


def _set_open_trade(db: MagicMock, trade: object | None) -> None:
    """Wire the DB mock so `db.query(Trade).filter(...).order_by(...).first()`
    returns `trade` (or None to signal no open trade). The runner's
    max_hold + broker-reconcile paths run this chain on every tick,
    so tests must set it explicitly. Called automatically by
    `_make_db()` — use directly only to override the None default.
    """
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = trade


def _make_db() -> MagicMock:
    """MagicMock() + default `_set_open_trade(None)` so the runner's
    time-based-exit query doesn't fetch a Mock instead of a Trade row.
    """
    db = MagicMock()
    _set_open_trade(db, None)
    return db


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

    def test_bar_watermark_backfill_catches_missed_cross_event(self):
        """Wilson UAT 2026-08-02 Fix D regression.

        Setup mirrors the exact bug from the 2026-07-29 audit: bar T
        fires a LONG cross event, but its `complete=True` marker lands
        in the DB AFTER the tick corresponding to T fires. The next
        tick, without Fix D, would read `raw_entries.iloc[-1]` which
        by then is bar T+1's value (say 0 or opposite), silently
        skipping the T event.

        Fix D: the runner walks EVERY bar since the last-evaluated
        watermark and acts on the earliest nonzero raw_entry. Cross
        events that lag one tick get caught on the next tick instead
        of being permanently lost.

        Scenario: run has `last_evaluated_bar_ts = 12:00`. Fresh tick
        fires with a candle df ending at 12:03. `raw_entries` at bars
        12:01, 12:02, 12:03 = [1, 0, 0] — LONG cross fired at 12:01
        (the bar that got ingested late). Fix D should enter LONG.
        Without Fix D, `iloc[-1] = 0` would silently no-op.
        """
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        # Signal series: bar 12:01 fires +1 (LONG), 12:02 and 12:03
        # are 0. Under iloc[-1] the runner would see 0 → skip.
        strategy = _make_strategy(signals=[1, 0, 0])
        # Candles [12:01, 12:02, 12:03] — first bar is what we want
        # the watermark walk to catch.
        candles_df = _mk_candles_df(3, base + timedelta(minutes=3))
        # candles_df's index is [12:01, 12:02, 12:03] since
        # _mk_candles_df ends at `latest_ts` and steps back N-1 mins.
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        client.place_market_order.return_value = {
            "trade_id": "trade_broker_watermark",
            "fill_price": 1.1005,
            "units": 25_000,
        }
        db = _make_db()
        run = _make_run()
        # Watermark set to 12:00 — BEFORE the first candle in df.
        # Fix D should treat all three bars as "new" and act on the
        # first nonzero one (12:01 LONG).
        run.parameters["last_evaluated_bar_ts"] = base.isoformat()

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=base + timedelta(minutes=3, seconds=5),
        )

        # LONG order placed despite iloc[-1] being 0.
        client.place_market_order.assert_called_once()
        args, kwargs = client.place_market_order.call_args
        assert args[1] > 0, f"expected LONG (positive units), got {args[1]}"

        # Watermark advanced to the LATEST bar (12:03), not just 12:01.
        # So the next tick won't re-fire on 12:01.
        assert (
            run.parameters["last_evaluated_bar_ts"]
            == (base + timedelta(minutes=3)).isoformat()
        )

    def test_watermark_first_tick_uses_only_latest_bar(self):
        """When the run is fresh (no watermark yet), Fix D takes only
        the latest bar's raw_entry — not the whole history. Prevents
        the runner from "backfilling" a trade against a cross that
        fired 20 bars ago (before we were watching)."""
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        # Series [1, 0, 0, 0, 0] — cross fired 4 bars ago. Latest bar
        # is 0. Without watermark, Fix D should NOT catch the stale
        # cross → no order.
        strategy = _make_strategy(signals=[1, 0, 0, 0, 0])
        candles_df = _mk_candles_df(5, base + timedelta(minutes=5))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        db = _make_db()
        run = _make_run()
        # No watermark on run.parameters — first tick.

        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=base + timedelta(minutes=5, seconds=5),
        )

        client.place_market_order.assert_not_called()
        # But watermark IS set so subsequent ticks scan from here forward.
        assert (
            run.parameters["last_evaluated_bar_ts"]
            == (base + timedelta(minutes=5)).isoformat()
        )

    def test_long_signal_from_flat_places_order_and_persists_trade(self):
        # Signal series ends with +1 → long entry from flat.
        strategy = _make_strategy(signals=[0, 0, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        # v0.6.1 dynamic sizing (Wilson UAT 2026-07-15): balance $10k,
        # risk_pct 0.005, stop 20 pips, EUR/USD pip $0.0001 →
        # desired_units = 10000 * 0.005 / (20 * 0.0001) = 25000
        client.place_market_order.return_value = {
            "trade_id": "trade_broker_123",
            "fill_price": 1.1005,
            "units": 25_000,
        }
        db = _make_db()
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
            25_000,
            stop_loss_pips=20,
            take_profit_pips=50,
        )
        # A Trade row got added.
        added = [c.args[0] for c in db.add.call_args_list]
        trade_rows = [r for r in added if type(r).__name__ == "Trade"]
        assert len(trade_rows) == 1
        trade = trade_rows[0]
        assert trade.side == "buy"
        assert trade.quantity == 25_000
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
        db = _make_db()
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
        db = _make_db()
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
        # enforces `spec.exit.max_hold_bars` itself. When the count of
        # candles since entry (bars_held) reaches max_hold_bars,
        # force-close.
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
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
        db = _make_db()
        _set_open_trade(db, open_trade)
        # bars_held = 6 (>= max_hold=5) — DB says 6 completed candles
        # have landed since entry. Wilson UAT 2026-07-17: replaced the
        # earlier wall-clock check with a DB row count so weekend gaps
        # (Fri entry → Sat force-close in wall-clock) don't fire.
        db.query.return_value.filter.return_value.count.return_value = 6
        # _close_position_and_persist re-queries open trades — reuse
        # the same row via .all().
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

    def test_runner_close_stamps_broker_close_transaction_id(self):
        """Wilson UAT 2026-07-26: `_close_position_and_persist` must
        stamp the OANDA fill txn id onto the Trade row so the
        reconciler's idempotency guard recognises this close as
        already-authoritatively-reconciled and doesn't overwrite
        the runner's pnl with what would be the same value from a
        subsequent /transactions/sinceid poll.
        """
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        entry_ts = now - timedelta(seconds=400)
        strategy = _make_strategy(signals=[0, 0, 0], max_hold_bars=5)
        candles_df = _mk_candles_df(3, now)
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 3.30,
            "short_realized_pnl": 0.0,
            "long_fill_transaction_id": "12345",
            "short_fill_transaction_id": None,
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
            broker_close_transaction_id=None,
        )
        db = _make_db()
        _set_open_trade(db, open_trade)
        db.query.return_value.filter.return_value.count.return_value = 6
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

        assert open_trade.broker_close_transaction_id == "12345"

    def test_max_hold_does_not_fire_across_weekend_gap(self):
        """Wilson UAT 2026-07-17 audit: the old wall-clock max_hold
        check would force-close a Friday-evening entry at Saturday
        11:15 UTC (13.25h wall-clock elapsed) even though no trading
        bars had ticked. Backtest counts bars in the OHLCV series so
        the counter freezes during weekend gaps. The bar-count version
        matches: if only 3 bars have landed in the DB since entry,
        max_hold=795 is far from tripped."""
        now = datetime(2026, 7, 12, 11, 15, tzinfo=UTC)  # Saturday
        entry_ts = datetime(2026, 7, 11, 22, 0, tzinfo=UTC)  # Fri evening
        strategy = _make_strategy(signals=[0, 0, 0], max_hold_bars=795)
        candles_df = _mk_candles_df(3, now)
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        open_trade = SimpleNamespace(
            id="tr_weekend",
            run_id="run_test",
            entry_price=1.10,
            quantity=10_000,
            entry_timestamp=entry_ts,
            exit_timestamp=None,
            pnl=None,
            pnl_pct=None,
            exit_price=None,
        )
        db = _make_db()
        _set_open_trade(db, open_trade)
        # Only 3 M1 bars closed after Fri 22:00 (weekend market close).
        # Wall-clock would have said ~48,300s elapsed (~13.4h × granularity)
        # — old logic would have force-closed. Bar count = 3 → hold.
        db.query.return_value.filter.return_value.count.return_value = 3
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

        # Must NOT force-close — no bars ticked over the weekend.
        client.close_all_positions.assert_not_called()

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
        db = _make_db()
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
        db = _make_db()
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
        # Skip decision written on FIRST stale tick.
        added = [c.args[0] for c in db.add.call_args_list]
        decisions = [r for r in added if type(r).__name__ == "DecisionLog"]
        assert any(d.action == "USER_SIGNAL_SKIPPED_STALE" for d in decisions)
        # `stale_active` flag now set on run.parameters so subsequent
        # stale ticks stay silent.
        assert run.parameters.get("stale_active") is True

    def test_stale_transitions_dont_spam_decision_log(self):
        """Wilson UAT 2026-07-19: over a 60h weekend the decision log
        accumulated ~3600 identical STALE entries (one per M1 tick),
        burying every meaningful event. Now log only on state
        transitions — enter-stale once, exit-stale once."""
        strategy = _make_strategy(signals=[0, 0, 0])
        now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        stale_df = _mk_candles_df(3, now - timedelta(minutes=5))
        # Fresh tick fires 10 min later — fresh_df's latest bar must be
        # recent relative to THAT clock, not `now`.
        fresh_now = now + timedelta(minutes=10)
        fresh_df = _mk_candles_df(3, fresh_now - timedelta(seconds=30))
        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        db = _make_db()
        run = _make_run()

        # Three consecutive stale ticks — only the first should log.
        for i in range(3):
            _signal_tick(
                db=db,
                run=run,
                client=client,
                strategy=strategy,
                candle_fetch_fn=MagicMock(return_value=stale_df),
                instrument="EUR_USD",
                granularity_seconds=60,
                now_utc=now + timedelta(minutes=i),
            )
        added = [c.args[0] for c in db.add.call_args_list]
        stale_logs = [
            r
            for r in added
            if type(r).__name__ == "DecisionLog"
            and r.action == "USER_SIGNAL_SKIPPED_STALE"
        ]
        assert len(stale_logs) == 1, (
            f"expected exactly 1 STALE log across 3 stale ticks, got {len(stale_logs)}"
        )
        assert run.parameters.get("stale_active") is True

        # Fresh candle arrives → RESUMED log fires, flag clears.
        db2 = _make_db()  # fresh db to isolate the resume log
        _signal_tick(
            db=db2,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=MagicMock(return_value=fresh_df),
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=fresh_now,
        )
        resumed = [
            c.args[0]
            for c in db2.add.call_args_list
            if type(c.args[0]).__name__ == "DecisionLog"
            and c.args[0].action == "USER_SIGNAL_RESUMED"
        ]
        assert len(resumed) == 1
        assert run.parameters.get("stale_active") is False

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
        db = _make_db()
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
        db = _make_db()
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

    def test_atomic_flip_closes_and_opens_same_tick(self):
        """Wilson UAT 2026-07-17 audit: backtest's `_apply_exits`
        atomically flips in one bar (close + open opposite in the same
        `raw_pos` step). The earlier paper runner did a two-step
        "close now, reopen next tick" which silently dropped the reopen
        for crossover-style entry rules (`crosses_above` /
        `crosses_below` fire on ONE bar only). Threshold-style
        entries (`obv < 0`) stayed true across bars so those reopened
        on tick T+1; crossover entries didn't.

        Now: existing LONG +10k, short signal fires with
        opposite_signal=True → close AND place SHORT order in the
        same _signal_tick call.
        """
        strategy = _make_strategy(signals=[0, 0, -1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 10_000, "short_units": 0}
        client.close_all_positions.return_value = {
            "long_realized_pnl": 5.0,
            "short_realized_pnl": 0.0,
        }
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        client.place_market_order.return_value = {
            "trade_id": "trade_after_flip",
            "fill_price": 1.1020,
            "units": -25_000,
        }
        db = _make_db()
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
        # Atomic flip: the SHORT order MUST be placed in the same tick.
        # `signed_units < 0` since we're flipping into a short.
        client.place_market_order.assert_called_once()
        signed_units = client.place_market_order.call_args.args[1]
        assert signed_units < 0

    def test_min_hold_bars_blocks_opposite_flip_within_window(self):
        """Regression, Wilson UAT 2026-07-17: paper runner didn't
        enforce `min_hold_bars` at all. Backtest's `_apply_exits` gates
        every exit path (stop / target / max_hold / opposite_flip) on
        `bars_held < min_hold_bars`. Genome
        genome_gen_evorun_20260712_30f6bc87_017_019 was tested with
        min_hold=60; paper ran it without the gate and hit 13
        consecutive losses in 22h (backtest max was 11 over 510
        trades) — most trades closed in 7-25 min on the first noisy
        MACD flip signal.

        SHORT open at 12:00, tick at 12:30 (30 min held) with an
        opposite (LONG) entry event → runner must HOLD (no
        close_all_positions), matching backtest.
        """
        strategy = _make_strategy(
            signals=[0, 0, 1],  # LONG entry event vs open SHORT = opposite
            min_hold_bars=60,
            opposite_signal=True,
        )
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 30, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {
            "long_units": 0,
            "short_units": -10_000,
            "unrealized_pnl": 5.0,
        }
        db = _make_db()
        # Trade opened 30 min ago at 12:00 — still inside min_hold=60m.
        trade = SimpleNamespace(
            id="trade_within_min_hold",
            entry_timestamp=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
            entry_price=1.1000,
            exit_timestamp=None,
            exit_price=None,
            pnl=None,
            exit_reason=None,
        )
        _set_open_trade(db, trade)
        db.query.return_value.filter.return_value.all.return_value = [trade]
        # 30 bars in DB since entry — inside min_hold=60.
        db.query.return_value.filter.return_value.count.return_value = 30

        run = _make_run()
        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
        )

        client.close_all_positions.assert_not_called()
        client.place_market_order.assert_not_called()

    def test_min_hold_bars_allows_flip_after_window(self):
        """Mirror of the block-test: same setup but at 90 bars held
        (past min_hold=60) — flip MUST fire."""
        strategy = _make_strategy(
            signals=[0, 0, 1],
            min_hold_bars=60,
            opposite_signal=True,
        )
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 13, 30, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {
            "long_units": 0,
            "short_units": -10_000,
            "unrealized_pnl": 5.0,
        }
        client.close_all_positions.return_value = {
            "long_realized_pnl": 0.0,
            "short_realized_pnl": 5.0,
        }
        db = _make_db()
        trade = SimpleNamespace(
            id="trade_past_min_hold",
            entry_timestamp=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
            entry_price=1.1000,
            quantity=10_000,
            exit_timestamp=None,
            exit_price=None,
            pnl=None,
            exit_reason=None,
            financing=None,
            commission=None,
        )
        _set_open_trade(db, trade)
        db.query.return_value.filter.return_value.all.return_value = [trade]
        # 90 bars in DB since entry — past min_hold=60.
        db.query.return_value.filter.return_value.count.return_value = 90

        run = _make_run()
        _signal_tick(
            db=db,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="EUR_USD",
            granularity_seconds=60,
            now_utc=datetime(2026, 7, 10, 13, 30, tzinfo=UTC),
        )

        client.close_all_positions.assert_called_once_with("EUR_USD")

    def test_stop_loss_and_take_profit_passed_through(self):
        # SL/TP from risk_config override → passed through to the
        # broker call. Sizing under the v0.6.1 dynamic model:
        # balance $10k × 0.005 risk / (20 pip stop × $0.0001 pip) =
        # 25,000 units. `max_position_size_units` unset (blank) so
        # no safety cap kicks in.
        strategy = _make_strategy(signals=[0, 0, 1])
        candles_df = _mk_candles_df(3, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
        fetch = MagicMock(return_value=candles_df)

        client = MagicMock()
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        client.place_market_order.return_value = {
            "trade_id": "trade_sl_tp",
            "fill_price": 1.1010,
            "units": 25_000,
        }
        db = _make_db()
        run = _make_run()
        run.risk_config = {
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
            25_000,
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
            last_transaction_id=None,
            risk_config={
                "max_position_size_units": 10_000,
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            parameters={},
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
        db = _make_db()
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

    def test_signal_tick_advances_to_next_boundary_not_two_ahead(self):
        """Regression for the 2-minute-cadence bug (Wilson UAT 2026-07-15).

        The scheduler previously computed `next_signal_at =
        align_to_granularity(now + granularity_seconds, granularity_seconds)`
        which, when a tick fired a few ms past the boundary (typical:
        ~230ms of processing latency), overshot by a full bar. Every
        M1 tick would then fire every OTHER minute, skipping half the
        signals. This test locks in that the scheduler advances by
        exactly one granularity even when the tick's actual firing
        time is slightly past the previous boundary.
        """
        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            user_id=1,
            snapshot_balance=10_000.0,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=None,
            last_transaction_id=None,
            risk_config={
                "daily_loss_pct": 2.0,
                "kill_switch_dd_pct": 5.0,
            },
            parameters={},
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.place_market_order.return_value = {
            "trade_id": "t",
            "fill_price": 1.10,
            "units": 5_000,
        }
        db = _make_db()
        strategy = _make_strategy(signals=[0, 0, 0])
        candles_df = _mk_candles_df(3, datetime.now(UTC))
        fetch = MagicMock(return_value=candles_df)

        # Clock updated for Wilson UAT 2026-08-02: signal ticks now
        # fire at (boundary + SIGNAL_TICK_OFFSET_SECONDS=10), so with
        # granularity=60 they land at 130, 190, 250, ...
        # Original test intent preserved: fires at TWO consecutive
        # boundaries with slight jitter. Under the old advance-by-two
        # bug, the second tick at 190 would have been skipped.
        # Initial align(110, 60) = 120; +10 offset = 130.
        clock = iter(
            [
                110.0,  # initial align_to_granularity call → 130
                130.230,  # iter 1 "now" (past 130 with 230ms jitter, fires)
                130.230,  # iter 1 sleep calc
                190.0,  # iter 2 "now" (exactly on 190 = 180+10, should fire)
                190.0,  # iter 2 sleep calc
            ]
        )

        def now_fn() -> float:
            return next(clock)

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

        # Signal tick evaluates on BOTH iterations (nothing placed
        # because signals=[0,0,0] but the tick fires). We verify this
        # via equity-snapshot writes: my code snapshots equity on
        # every silent no-op tick.
        added = [c.args[0] for c in db.add.call_args_list]
        snapshots = [r for r in added if type(r).__name__ == "PaperEquitySnapshot"]
        assert len(snapshots) == 2, (
            f"expected 2 signal ticks (130 + 190), got {len(snapshots)} — "
            "the 2-minute-cadence bug is back"
        )

    def test_signal_tick_fires_at_boundary_plus_configured_offset(self):
        """Wilson UAT 2026-08-02: tick must fire at
        (bar_boundary + SIGNAL_TICK_OFFSET_SECONDS), NOT at the raw
        boundary. Otherwise the runner races the ingester and misses
        the just-closed bar every minute.
        """
        from agentfx.api.models import RunStatus

        run = SimpleNamespace(
            id="run_test",
            pair="EUR_USD",
            user_id=1,
            snapshot_balance=10_000.0,
            current_balance=10_000.0,
            pnl=0.0,
            kill_requested_at=None,
            last_transaction_id=None,
            risk_config={"daily_loss_pct": 2.0, "kill_switch_dd_pct": 5.0},
            parameters={},
            status=RunStatus.RUNNING,
            stop_reason=None,
            completed_at=None,
        )
        client = MagicMock()
        client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        db = _make_db()
        strategy = _make_strategy(signals=[0, 0, 0])
        candles_df = _mk_candles_df(3, datetime.now(UTC))
        fetch = MagicMock(return_value=candles_df)

        # Iter 1 now=120.5 — PAST the raw :00 boundary by 500ms.
        # Under the pre-offset code, tick would have fired here.
        # Under the offset code (default offset=10), next_signal_at=130
        # → 120.5 < 130 → tick does NOT fire. Good.
        clock = iter([100.0, 120.5, 120.5, 120.5, 120.5])

        def now_fn() -> float:
            return next(clock)

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

        # NO tick fired — 120.5 is past raw boundary but not past
        # boundary + offset.
        added = [c.args[0] for c in db.add.call_args_list]
        snapshots = [r for r in added if type(r).__name__ == "PaperEquitySnapshot"]
        assert len(snapshots) == 0, (
            f"expected 0 ticks fired (offset guards :00-:10 window), "
            f"got {len(snapshots)} — runner is racing the ingester"
        )
