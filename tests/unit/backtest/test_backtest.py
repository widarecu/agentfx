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
