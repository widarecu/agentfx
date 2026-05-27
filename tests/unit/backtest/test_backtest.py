from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pandas as pd

from agentfx.services.backtest import run_backtest


def _write_csv_partition(
    root: str, pair: str, granularity: str, df: pd.DataFrame
) -> None:
    # Partition path: data/raw/<pair>/<granularity>/YYYY-MM/YYYY-MM.csv
    ts = df["time"].iloc[0]
    year = ts.strftime("%Y")
    month = ts.strftime("%m")
    part_dir = os.path.join(root, pair, granularity, f"{year}-{month}")
    os.makedirs(part_dir, exist_ok=True)
    df.to_csv(os.path.join(part_dir, f"{year}-{month}.csv"), index=False)


def _make_sample_df(start: datetime, minutes: int = 120) -> pd.DataFrame:
    times = pd.date_range(start=start, periods=minutes, freq="T", tz="UTC")
    # simple random-walk-ish close series
    close = pd.Series(1.0, index=times).cumprod() * 1.0
    # deterministic prices: slight trend
    close = 1.0 + (pd.RangeIndex(len(times)) * 0.00001)
    df = pd.DataFrame(
        {
            "time": times,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": close.values,
            "volume": 1,
        }
    )
    return df


def test_window_fallback_uses_full_dataset_when_empty_filter():
    with tempfile.TemporaryDirectory() as tmp:
        data_root = tmp
        pair = "TESTUSD"
        gran = "M1"
        # Create data for last 2 hours
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        df = _make_sample_df(now - timedelta(hours=2), minutes=120)
        _write_csv_partition(data_root, pair, gran, df)

        # Request a future-only window which should yield empty, triggering fallback
        start_days = 0  # will generate start=end (no-op), but we pass explicit start/end via run_backtest
        res = run_backtest(
            data_root=data_root,
            pair=pair,
            start_days=1,  # normal flow
            strategy_name="ma",
            strategy_params={"fast": 5, "slow": 10},
            spread_pips=0.0,
            slippage_pips_per_side=0.0,
        )
        assert res["n"] > 0, (
            "Fallback should have used full dataset when filter emptied the slice"
        )


def test_costs_reduce_cagr_vs_no_costs():
    with tempfile.TemporaryDirectory() as tmp:
        data_root = tmp
        pair = "TESTUSD"
        gran = "M1"
        # Create 6 hours of synthetic data
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        df = _make_sample_df(now - timedelta(hours=6), minutes=360)
        _write_csv_partition(data_root, pair, gran, df)

        # No costs
        no_cost = run_backtest(
            data_root=data_root,
            pair=pair,
            start_days=1,
            strategy_name="ma",
            strategy_params={"fast": 5, "slow": 10},
            spread_pips=0.0,
            slippage_pips_per_side=0.0,
        )
        # With significant costs
        with_cost = run_backtest(
            data_root=data_root,
            pair=pair,
            start_days=1,
            strategy_name="ma",
            strategy_params={"fast": 5, "slow": 10},
            spread_pips=2.0,
            slippage_pips_per_side=2.0,
        )

        assert with_cost["n"] == no_cost["n"]
        assert with_cost["metrics"]["cagr"] <= no_cost["metrics"]["cagr"], (
            "Costs should not improve CAGR"
        )


# ─────────────────────────────────────────────────────────────────────────
# Compounded sizing: a DSL strategy's position size on each new trade is
# computed against the running balance (starting + realised P&L), not the
# static starting capital. These tests verify that contract directly.
# ─────────────────────────────────────────────────────────────────────────


def _staircase_close_series(
    bars_per_leg: int,
    n_legs: int,
    leg_pct: float,
    start_price: float = 1.0000,
    freq: str = "T",
) -> pd.DataFrame:
    """Build an EUR_USD-shaped DataFrame whose close climbs `leg_pct` per
    leg over `bars_per_leg` bars, then flips direction. Designed so a
    long-on-up / short-on-down strategy wins every trade by a known
    amount, making compounding observable trade-over-trade."""
    times = pd.date_range(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        periods=bars_per_leg * n_legs + 1,
        freq=freq,
        tz="UTC",
    )
    closes = [start_price]
    direction = 1
    for _ in range(n_legs):
        for _ in range(bars_per_leg):
            closes.append(closes[-1] * (1.0 + direction * leg_pct / bars_per_leg))
        direction *= -1
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1,
        },
        index=times,
    )


def test_dsl_compounding_grows_position_size_after_wins(monkeypatch):
    """After a sequence of winning trades, the position size on trade N
    must exceed the position size on trade 1 (compounding has occurred)."""
    from agentfx.core.dsl.grammar import (
        Comparison,
        EntryConditions,
        ExitRules,
        IndicatorCall,
        SizeRules,
        StrategySpec,
    )
    from agentfx.services import backtest as bt_module

    # 4 alternating legs of ±0.5% → 4 closed trades, each winning
    # ~50 pips before costs. Plenty of room above the 400-pip stop for
    # the trade not to be killed by the exit rules.
    df = _staircase_close_series(bars_per_leg=120, n_legs=4, leg_pct=0.005)

    # Spec: long if SMA(2) > SMA(10), short if SMA(2) < SMA(10).
    # Permissive exits (large stop/target/max_hold) so trades only close
    # via opposite_signal flips → one closed trade per leg flip.
    spec = StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=IndicatorCall(ind="sma", args=[2]),
                cmp=">",
                rhs=IndicatorCall(ind="sma", args=[10]),
            ),
            short=Comparison(
                lhs=IndicatorCall(ind="sma", args=[2]),
                cmp="<",
                rhs=IndicatorCall(ind="sma", args=[10]),
            ),
        ),
        exit=ExitRules(
            stop_pips=400,
            target_pips=800,
            max_hold_bars=2880,
            opposite_signal=True,
        ),
        size=SizeRules(risk_pct=0.005),
    )

    monkeypatch.setattr(bt_module, "load_price_df_from_db", lambda **_: df)
    monkeypatch.setattr(bt_module, "_lookup_typical_spread", lambda _i: 0.0)
    monkeypatch.setattr(bt_module, "_lookup_financing_rates", lambda _i: (0.0, 0.0))

    result = bt_module.run_backtest_from_db(
        instrument="EUR_USD",
        granularity="M1",
        start=None,
        end=None,
        strategy_name="dsl",
        strategy_params={"spec": spec.model_dump(mode="json")},
        slippage_pips_per_side=0.0,
        capture_signals=True,
        account_balance=10_000.0,
        leverage=30,
    )

    trades = result["trades"]
    sizing = result["sizing"]
    winners = [t for t in trades if t["net_pnl_dollars"] > 0]
    assert len(winners) >= 2, (
        f"Test requires at least 2 winning trades for compounding to be "
        f"observable; got {len(winners)} winners out of {len(trades)} trades"
    )

    # The defining check: a later winning trade must size larger than an
    # earlier winning trade, because the running balance grew between them.
    assert winners[-1]["position_size"] > winners[0]["position_size"], (
        f"Compounding broken: first winner sized {winners[0]['position_size']}, "
        f"last winner sized {winners[-1]['position_size']} — should be strictly "
        f"larger after intervening wins."
    )

    # And the sizing block must reflect the realised growth.
    assert sizing["starting_balance"] == 10_000.0
    assert sizing["ending_balance"] > sizing["starting_balance"], (
        "Ending balance must exceed starting balance after net-positive run"
    )
    assert sizing["peak_balance"] >= sizing["ending_balance"], (
        "Peak balance must be >= ending balance by definition"
    )
    assert sizing["compounded"] is True


def test_dsl_compounding_shrinks_position_size_after_losses(monkeypatch):
    """The symmetric case: after net losses the running balance shrinks,
    so subsequent position sizes must shrink too."""
    from agentfx.core.dsl.grammar import (
        Comparison,
        EntryConditions,
        ExitRules,
        IndicatorCall,
        SizeRules,
        StrategySpec,
    )
    from agentfx.services import backtest as bt_module

    df = _staircase_close_series(bars_per_leg=120, n_legs=4, leg_pct=0.005)

    # Inverted spec: long when fast SMA is BELOW slow (mean-reversion sign),
    # short when above. On a staircase trend, this loses every trade.
    spec = StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=IndicatorCall(ind="sma", args=[2]),
                cmp="<",
                rhs=IndicatorCall(ind="sma", args=[10]),
            ),
            short=Comparison(
                lhs=IndicatorCall(ind="sma", args=[2]),
                cmp=">",
                rhs=IndicatorCall(ind="sma", args=[10]),
            ),
        ),
        exit=ExitRules(
            stop_pips=400,
            target_pips=800,
            max_hold_bars=2880,
            opposite_signal=True,
        ),
        size=SizeRules(risk_pct=0.005),
    )

    monkeypatch.setattr(bt_module, "load_price_df_from_db", lambda **_: df)
    monkeypatch.setattr(bt_module, "_lookup_typical_spread", lambda _i: 0.0)
    monkeypatch.setattr(bt_module, "_lookup_financing_rates", lambda _i: (0.0, 0.0))

    result = bt_module.run_backtest_from_db(
        instrument="EUR_USD",
        granularity="M1",
        start=None,
        end=None,
        strategy_name="dsl",
        strategy_params={"spec": spec.model_dump(mode="json")},
        slippage_pips_per_side=0.0,
        capture_signals=True,
        account_balance=10_000.0,
        leverage=30,
    )

    trades = result["trades"]
    sizing = result["sizing"]
    losers = [t for t in trades if t["net_pnl_dollars"] < 0]
    assert len(losers) >= 2, "Need at least 2 losing trades to test shrinkage"

    # First losing trade should size larger than the last losing trade —
    # the balance went down between them.
    assert losers[-1]["position_size"] <= losers[0]["position_size"], (
        f"Compounding-down broken: first loser sized {losers[0]['position_size']}, "
        f"last loser sized {losers[-1]['position_size']} — should be <= after losses."
    )
    assert sizing["ending_balance"] < sizing["starting_balance"]
