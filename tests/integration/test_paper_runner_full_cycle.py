"""Integration test for the paper runner's live execution loop (US-024).

Drives `_execute_loop` end-to-end against a real Postgres session with:
- pre-seeded EUR_USD M1 candles containing a clear upward trend so an
  SMA-crossover strategy generates deterministic entry signals
- a fake `OandaClient` (MagicMock) mirroring the shape from the unit
  tests — full HTTP wire mocking (respx) is already covered by the
  OANDA adapter's own suite, so we don't re-litigate it here
- a compiled DSL strategy from the actual `compile_spec` helper — the
  same code path the backtest engine uses; this is what makes the
  test a genuine parity check between paper and backtest

Locks the end-to-end invariants v0.6.0 shipped without:
- candles flow into signals
- signals flow into orders
- orders persist Trade rows tagged `origin='live'` with `signal_reason`
- close paths update Trade rows with exit_timestamp + pnl
- USER_ORDER_PLACED / USER_POSITION_CLOSED / USER_SIGNAL_SKIPPED_STALE
  DecisionLog rows land with the paper_run_id in outputs_json
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _mk_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{str(uuid4())[:8]}"


@pytest.fixture
def paper_run_with_candles(db_session: Session):
    """A paper Run in RUNNING state with 200 M1 candles containing a
    clear upward trend + a matching SMA-crossover genome deployed."""
    from src.agentfx.api.models import (
        Candle,
        EvolutionRun,
        EvolutionRunStatus,
        Generation,
        GenomeOrigin,
        Run,
        RunEnvironment,
        RunStatus,
        StrategyGenome,
        User,
    )
    from src.agentfx.api.core.security import get_password_hash

    unique = str(uuid4())[:8]
    user = User(
        email=f"runner_full_cycle_{unique}@example.com",
        hashed_password=get_password_hash("stub"),
        role="trader",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # DSL: SMA(5) > SMA(20) crossover — will fire long once the trend
    # takes off. Small windows so the 200 seeded bars trigger the
    # signal well before we run out of history.
    dsl_json = {
        "version": 1,
        # Use `>` / `<` (not crosses_above/below) so the signal is
        # true throughout the uptrend — a crossover would fire on
        # exactly one bar and the runner's last-bar read might miss
        # it depending on where in the series the cross happens.
        "entry": {
            "long": {
                "lhs": {"ind": "sma", "args": [5]},
                "cmp": ">",
                "rhs": {"ind": "sma", "args": [20]},
            },
            "short": {
                "lhs": {"ind": "sma", "args": [5]},
                "cmp": "<",
                "rhs": {"ind": "sma", "args": [20]},
            },
        },
        "exit": {
            "stop_pips": 100,
            "target_pips": 200,
            "max_hold_bars": 200,
            "min_hold_bars": 0,
            "opposite_signal": True,
        },
        "size": {"risk_pct": 0.005},
    }

    evo_id = _mk_id("evorun")
    gen_id = f"gen_{evo_id}_1"
    genome_id = f"genome_{gen_id}_0"

    evo = EvolutionRun(
        id=evo_id,
        status=EvolutionRunStatus.COMPLETED,
        instrument="EUR_USD",
        granularity="M1",
        config_json={},
        stop_conditions_json={},
        current_generation=1,
        llm_cost_usd=0,
        created_by=user.id,
    )
    gen = Generation(id=gen_id, run_id=evo_id, index=1)
    genome = StrategyGenome(
        id=genome_id,
        generation_id=gen_id,
        dsl_json=dsl_json,
        parent_ids=[],
        origin=GenomeOrigin.SEED,
        validator_passed=True,
        validation_failures=[],
        fitness=1.5,
        metrics_json={"sharpe": 1.5},
    )

    # 200 EUR_USD M1 candles with a strong uptrend in the last 50 bars
    # so the SMA(5) > SMA(20) condition holds at the newest bar. The
    # newest bar's timestamp is what we anchor the test clock against
    # (stale-candle guard is 2× granularity_seconds = 120s).
    now = datetime.now(UTC).replace(microsecond=0)
    candles: list[Candle] = []
    base = 1.0800
    for i in range(200):
        ts = now - timedelta(minutes=200 - i)
        # First 150 bars: flat around 1.0800; last 50: clear uptrend to
        # 1.0900. Ensures SMA(5) crosses SMA(20) up somewhere in the
        # last stretch.
        if i < 150:
            price = base + (i % 5) * 0.00001
        else:
            price = base + (i - 149) * 0.00020
        candles.append(
            Candle(
                exchange="oanda",
                instrument="EUR_USD",
                timeframe="M1",
                timestamp=ts,
                open=price,
                high=price + 0.00005,
                low=price - 0.00005,
                close=price,
                volume=100.0,
                complete=True,
            )
        )

    run_id = _mk_id("run_paper")
    run = Run(
        id=run_id,
        strategy_id=None,
        strategy_genome_id=genome.id,
        pair="EUR_USD",
        environment=RunEnvironment.PAPER,
        status=RunStatus.RUNNING,
        parameters={
            "name": "test full-cycle",
            "instrument": "EUR_USD",
            "granularity": "M1",
            "dsl_json": dsl_json,
        },
        initial_balance=10_000.0,
        snapshot_balance=10_000.0,
        current_balance=10_000.0,
        pnl=0.0,
        risk_config={
            "max_position_size_units": 10_000,
            "daily_loss_pct": 2.0,
            "kill_switch_dd_pct": 5.0,
        },
        user_id=user.id,
    )

    # Commit the genome hierarchy first so the FK on
    # `runs.strategy_genome_id` resolves. Postgres checks the constraint
    # inline on INSERT, not at commit — a single `add_all` + `commit`
    # can fail depending on flush order.
    db_session.add_all([evo, gen, genome])
    db_session.commit()
    db_session.add_all([run, *candles])
    db_session.commit()
    return {
        "user": user,
        "run": run,
        "genome": genome,
        "dsl_json": dsl_json,
        "candles": candles,
        # Timestamp of the newest seeded bar — the test clock anchors
        # against this so the stale-candle guard doesn't trip on
        # wall-clock drift between fixture setup and test call.
        "newest_candle_ts": candles[-1].timestamp,
    }


class TestPaperRunnerFullCycle:
    def test_signal_tick_places_order_and_persists_trade(
        self, db_session, paper_run_with_candles
    ):
        from src.agentfx.api.models import DecisionLog, Trade
        from src.agentfx.core.dsl import StrategySpec, compile_spec
        from src.agentfx.services.paper_runner import (
            _execute_loop,
            _default_candle_fetch,
            granularity_to_seconds,
            pip_size_for_pair,
        )

        run = paper_run_with_candles["run"]
        dsl_json = paper_run_with_candles["dsl_json"]

        spec = StrategySpec.model_validate(dsl_json)
        strategy = compile_spec(spec, pip_size=pip_size_for_pair("EUR_USD"))

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}
        client.get_position.return_value = {"long_units": 0, "short_units": 0}
        client.place_market_order.return_value = {
            "trade_id": "broker_trade_1",
            "fill_price": 1.0900,
            "units": 10_000,
        }

        # Anchor the clock 30 seconds after the newest seeded bar so:
        #   - the stale-candle guard (2× granularity_seconds = 120s)
        #     stays comfortably inside its tolerance, AND
        #   - `base + 90` (the "now" the signal check sees on iter 1)
        #     is 120s after the newest bar, still inside the tolerance.
        # Independent of wall-clock drift between fixture and test.
        newest_ts = paper_run_with_candles["newest_candle_ts"].timestamp()
        base = newest_ts + 30.0
        clock = iter([base, base + 90, base + 90, base + 90, base + 120, base + 120])
        _execute_loop(
            db=db_session,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=_default_candle_fetch(db_session),
            instrument="EUR_USD",
            granularity_seconds=granularity_to_seconds("M1"),
            now_fn=lambda: next(clock),
            sleep_fn=MagicMock(),
            max_iterations=2,
        )

        # A Trade row landed with origin='live' + signal_reason.
        trades = db_session.query(Trade).filter(Trade.run_id == run.id).all()
        assert len(trades) == 1
        trade = trades[0]
        assert trade.side == "buy"
        assert trade.quantity == 10_000
        assert trade.entry_price == pytest.approx(1.0900)
        assert trade.origin == "live"
        assert trade.signal_reason is not None
        assert "sma" in trade.signal_reason.lower()

        # USER_ORDER_PLACED decision log with paper_run_id in outputs.
        placements = (
            db_session.query(DecisionLog)
            .filter(DecisionLog.action == "USER_ORDER_PLACED")
            .all()
        )
        matching = [
            d
            for d in placements
            if (d.outputs_json or {}).get("paper_run_id") == run.id
        ]
        assert len(matching) == 1
        assert matching[0].outputs_json["broker_trade_id"] == "broker_trade_1"
        assert matching[0].outputs_json["instrument"] == "EUR_USD"

    def test_multi_tick_open_hold_close_reconciles_pnl(
        self, db_session, paper_run_with_candles
    ):
        """PR-C: drive the loop across a full open → hold → close cycle
        and reconcile pnl at the end.

        Uses a stateful mock OANDA that tracks position across ticks
        (so `get_position` after a `place_market_order` returns the
        placed side) and a smart candle-fetch adapter that returns a
        long-signal DataFrame for the first N calls, then flips to a
        flat-signal DataFrame so the runner's signal tick sees the
        exit trigger. Asserts:

        - At least one Trade row lands with entry AND exit populated.
        - Trade.pnl matches the fake broker's realized long_pnl.
        - `runs.pnl` (updated by the health tick from balance deltas)
          reconciles with the sum of closed-trade pnls to within a
          rounding tolerance.
        """
        import pandas as pd

        from src.agentfx.api.models import Run, Trade
        from src.agentfx.core.dsl import StrategySpec, compile_spec
        from src.agentfx.services.paper_runner import (
            _execute_loop,
            granularity_to_seconds,
            pip_size_for_pair,
        )

        run = paper_run_with_candles["run"]
        dsl_json = paper_run_with_candles["dsl_json"]

        spec = StrategySpec.model_validate(dsl_json)
        strategy = compile_spec(spec, pip_size=pip_size_for_pair("EUR_USD"))

        # ─── Build two candle DataFrames the fetch adapter swaps between
        # ─── Uptrend: SMA(5) > SMA(20) at last bar → long signal
        # ─── Downtrend: SMA(5) < SMA(20) at last bar → flat/short signal
        # ─── The DSL uses `>` / `<` so the last-row signal reflects the
        # ─── current trend directly.
        newest_ts = paper_run_with_candles["newest_candle_ts"]

        def _mk_df(direction: str, anchor: pd.Timestamp) -> pd.DataFrame:
            """direction='up' → uptrend; direction='down' → downtrend."""
            bars = 200
            index = pd.DatetimeIndex(
                [anchor - pd.Timedelta(minutes=bars - 1 - i) for i in range(bars)],
                tz="UTC",
            )
            base_price = 1.08
            prices: list[float] = []
            for i in range(bars):
                if i < 150:
                    prices.append(base_price + (i % 3) * 0.00001)
                else:
                    step = (i - 149) * 0.00020
                    prices.append(
                        base_price + step if direction == "up" else base_price - step
                    )
            return pd.DataFrame(
                {
                    "open": prices,
                    "high": [p + 0.00005 for p in prices],
                    "low": [p - 0.00005 for p in prices],
                    "close": prices,
                    "volume": [100.0] * bars,
                },
                index=index,
            )

        # Sanity: verify the strategy actually produces the signals we
        # designed the candles for. If either check fails the whole
        # test-cycle logic is wrong.
        anchor0 = pd.Timestamp(newest_ts)
        up_last = int(strategy.generate_signals(_mk_df("up", anchor0)).iloc[-1])
        down_last = int(strategy.generate_signals(_mk_df("down", anchor0)).iloc[-1])
        assert up_last > 0, "uptrend candles should produce a long signal"
        assert down_last < 0, "downtrend candles should produce a short signal"

        # ─── Stateful position mock ────────────────────────────────
        position_state: dict[str, float] = {
            "long_units": 0,
            "short_units": 0,
            "unrealized_pnl": 0.0,
        }
        # Track the broker's total realized pnl so `get_account_balance`
        # can reflect it (the runner's health tick reads this to update
        # `run.pnl = balance - snapshot_balance`).
        realized_pnl_total = [0.0]
        # Fake fill price — used to compute the trade's entry_price so
        # the reconciliation math is deterministic.
        FILL_PRICE = 1.0900
        BROKER_LONG_PNL = 12.30  # what close_all_positions "realizes"

        client = MagicMock()
        client.get_account_balance.side_effect = lambda: {
            "balance": 10_000.0 + realized_pnl_total[0],
        }
        client.get_position.side_effect = lambda instrument: dict(position_state)

        def _place(instrument, units, **kwargs):
            if units > 0:
                position_state["long_units"] = int(units)
                position_state["short_units"] = 0
            else:
                position_state["long_units"] = 0
                position_state["short_units"] = int(units)
            position_state["unrealized_pnl"] = 0.0
            return {
                "trade_id": f"broker_trade_{id(kwargs)}",
                "fill_price": FILL_PRICE,
                "units": int(units),
            }

        def _close(instrument, **kwargs):
            long_pnl = BROKER_LONG_PNL if position_state["long_units"] > 0 else 0.0
            long_units_closed = position_state["long_units"]
            short_units_closed = position_state["short_units"]
            position_state["long_units"] = 0
            position_state["short_units"] = 0
            position_state["unrealized_pnl"] = 0.0
            realized_pnl_total[0] += long_pnl
            return {
                "long_realized_pnl": long_pnl,
                "short_realized_pnl": 0.0,
                "long_units_closed": long_units_closed,
                "short_units_closed": short_units_closed,
            }

        client.place_market_order.side_effect = _place
        client.close_all_positions.side_effect = _close

        # ─── Clock: 5 boundary crossings for 5 signal ticks ────────
        base = newest_ts.timestamp() + 30.0

        # ─── Candle fetch swaps mid-run to trigger the exit ─────────
        # Anchor each returned DF to "just now" (as of the last clock
        # value the fetcher sees) so the stale-candle guard doesn't
        # trip on the runner's advancing clock. Constructed fresh per
        # call because the anchor needs to move with the clock.
        fetch_call_count = [0]
        last_clock_value = [base]  # Updated by now_fn below.

        def stateful_fetch(instrument, granularity, bars):
            fetch_call_count[0] += 1
            anchor = pd.Timestamp(datetime.fromtimestamp(last_clock_value[0], tz=UTC))
            direction = "up" if fetch_call_count[0] <= 2 else "down"
            return _mk_df(direction, anchor)

        # Precompute the clock so every iteration crosses a signal
        # boundary. Two subtleties:
        #   1. `base % 60` varies with wall clock → naive "advance by
        #      90s per iter" would flake between isolated / sequenced
        #      runs. Anchor to `first_boundary` instead.
        #   2. After a signal fires, the runner computes
        #      `next_signal_at = align_to_granularity(now + gs, gs)`.
        #      Since `align_to_granularity` treats a value exactly on
        #      a boundary as "advance to the NEXT boundary" (to avoid
        #      firing twice at the same instant), that effectively
        #      pushes `next_signal_at` 2 granularities ahead when
        #      `now + gs` lands on a boundary. So each test iter
        #      advances by 2×gs to guarantee it crosses.
        from agentfx.services.paper_runner import align_to_granularity

        gs = granularity_to_seconds("M1")
        first_boundary = align_to_granularity(base, gs)
        clock_values = [base]
        for i in range(5):
            tick_now = first_boundary + 2 * gs * i + 1
            clock_values.extend([tick_now, tick_now])  # signal + sleep calc

        clock = iter(clock_values)

        def now_fn() -> float:
            try:
                v = next(clock)
            except StopIteration:
                # Defensive — if the loop runs more iterations than
                # planned, return a very-late timestamp so no signal
                # tick fires again and we exit gracefully.
                v = base + 10_000.0
            last_clock_value[0] = v
            return v

        _execute_loop(
            db=db_session,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=stateful_fetch,
            instrument="EUR_USD",
            granularity_seconds=granularity_to_seconds("M1"),
            now_fn=now_fn,
            sleep_fn=MagicMock(),
            max_iterations=5,
        )

        # ─── Assertions ─────────────────────────────────────────────
        trades = (
            db_session.query(Trade)
            .filter(Trade.run_id == run.id)
            .order_by(Trade.entry_timestamp)
            .all()
        )
        # At least one trade got opened (the long entry).
        assert len(trades) >= 1
        opened_trades = [t for t in trades if t.side == "buy"]
        assert len(opened_trades) >= 1
        first_trade = opened_trades[0]
        assert first_trade.entry_price == pytest.approx(FILL_PRICE)
        assert first_trade.quantity == 10_000
        assert first_trade.origin == "live"

        # At least one trade got closed via the signal-flip path.
        closed_trades = [t for t in trades if t.exit_timestamp is not None]
        assert len(closed_trades) >= 1, (
            "expected at least one Trade row to be closed by the signal flip"
        )
        closed = closed_trades[0]
        assert closed.pnl is not None
        assert closed.pnl == pytest.approx(BROKER_LONG_PNL, abs=0.01)
        assert closed.exit_price is not None
        assert closed.exit_price > 0

        # PnL reconciliation: sum of closed-trade pnls should match
        # the broker's cumulative realized pnl (which the health tick
        # rolls into `run.pnl` via balance delta from snapshot).
        db_session.refresh(run)
        summed_trade_pnl = sum(t.pnl or 0.0 for t in closed_trades)
        assert summed_trade_pnl == pytest.approx(realized_pnl_total[0], abs=0.01)
        # `run.pnl = current_balance - snapshot_balance` and the fake
        # balance = 10_000 + realized_pnl_total. So run.pnl should
        # match realized_pnl_total exactly.
        assert run.pnl == pytest.approx(realized_pnl_total[0], abs=0.01)

    def test_kill_switch_halts_within_one_iteration(
        self, db_session, paper_run_with_candles
    ):
        from src.agentfx.api.models import Run, RunStatus
        from src.agentfx.core.dsl import StrategySpec, compile_spec
        from src.agentfx.services.paper_runner import (
            _execute_loop,
            _default_candle_fetch,
            granularity_to_seconds,
            pip_size_for_pair,
        )

        run = paper_run_with_candles["run"]
        # Pre-arm the kill flag so the first iteration's refresh sees it.
        run.kill_requested_at = datetime.now(UTC)
        db_session.commit()

        dsl_json = paper_run_with_candles["dsl_json"]
        spec = StrategySpec.model_validate(dsl_json)
        strategy = compile_spec(spec, pip_size=pip_size_for_pair("EUR_USD"))

        client = MagicMock()
        client.get_account_balance.return_value = {"balance": 10_000.0}

        _execute_loop(
            db=db_session,
            run=run,
            client=client,
            strategy=strategy,
            candle_fetch_fn=_default_candle_fetch(db_session),
            instrument="EUR_USD",
            granularity_seconds=granularity_to_seconds("M1"),
            sleep_fn=MagicMock(),
            max_iterations=1,
        )

        # Broker got the close call, run flipped to HALTED_MANUAL, no
        # order placement happened.
        client.close_all_positions.assert_called_once_with("EUR_USD")
        client.place_market_order.assert_not_called()

        db_session.expire_all()
        refreshed = db_session.query(Run).filter(Run.id == run.id).one()
        assert refreshed.status == RunStatus.HALTED_MANUAL
        assert refreshed.stop_reason == "USER_KILL"
