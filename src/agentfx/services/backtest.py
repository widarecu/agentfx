from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from agentfx.core.strategy import (
    Strategy,
    MACrossover,
    RSIMeanReversion,
    DonchianBreakout,
)
from agentfx.core.metrics import compute_returns, summarize
from agentfx.core.policy import PolicyConfig, apply_policies
from agentfx.adapters.storage.sqlite import Registry


def _data_dir(root: str, pair: str, granularity: str = "M1") -> str:
    return os.path.join(root, pair, granularity)


def load_price_df(
    data_root: str,
    pair: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    base = _data_dir(data_root, pair, granularity)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"No data for {pair} at {base}")
    files = sorted(glob.glob(os.path.join(base, "*", "*.csv")))
    if not files:
        raise FileNotFoundError(f"No CSV data files found for {pair} at {base}")
    parts = []
    for f in files:
        df = pd.read_csv(f)
        if df.empty:
            continue
        df["time"] = pd.to_datetime(df["time"], utc=True)
        parts.append(df)
    if not parts:
        raise FileNotFoundError(f"All CSV files empty for {pair}")
    df = pd.concat(parts, ignore_index=True).sort_values("time")

    def _to_utc(ts: datetime) -> pd.Timestamp:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            return t.tz_localize("UTC")
        return t.tz_convert("UTC")

    original_len = len(df)
    if start is not None:
        df = df[df["time"] >= _to_utc(start)]
    if end is not None:
        df = df[df["time"] <= _to_utc(end)]
    # Fallback: if windowing produced empty result but there was data, use full dataset
    if original_len > 0 and len(df) == 0:
        df = pd.concat(parts, ignore_index=True).sort_values("time")
    df = df.set_index("time").loc[:, ["open", "high", "low", "close", "volume"]]
    return df


def make_strategy(name: str, **kwargs) -> Strategy:
    n = name.lower()
    if n in ("ma", "mac", "ma_crossover", "macrossover"):
        return MACrossover(
            fast=int(kwargs.get("fast", 20)), slow=int(kwargs.get("slow", 50))
        )
    if n in ("rsi", "rsi_mr", "rsi_mean_reversion"):
        # Map generic FAST/SLOW to RSI window if provided
        window = int(kwargs.get("window", kwargs.get("fast", 14)))
        lower = float(kwargs.get("lower", 30.0))
        upper = float(kwargs.get("upper", 70.0))
        return RSIMeanReversion(window=window, lower=lower, upper=upper)
    if n in ("donchian", "breakout", "donchian_breakout"):
        window = int(kwargs.get("window", kwargs.get("fast", 20)))
        return DonchianBreakout(window=window)
    raise ValueError(f"Unknown strategy: {name}")


def run_backtest(
    data_root: str,
    pair: str,
    start_days: int | None = None,
    strategy_name: str = "ma",
    strategy_params: dict | None = None,
    spread_pips: float = 0.5,
    slippage_pips_per_side: float = 0.5,
    max_abs_position: int = 1,
    max_trades_per_hour: int = 60,
    daily_loss_threshold: float = 0.02,
    persist: bool = False,
    db_path: str | None = None,
) -> dict:
    strategy_params = strategy_params or {}
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=start_days) if start_days else None
    df = load_price_df(data_root, pair, start=start, end=end)
    strat = make_strategy(strategy_name, **strategy_params)
    raw_signals = strat.generate_signals(df)
    # Apply policies
    pconf = PolicyConfig(
        max_abs_position=max_abs_position,
        max_trades_per_hour=max_trades_per_hour,
        daily_loss_threshold=daily_loss_threshold,
    )
    presult = apply_policies(raw_signals, df["close"], pconf)
    signals = presult.adjusted_signals
    rets = compute_returns(df["close"]) * signals.shift(0).fillna(0)

    # Apply transaction costs on signal changes (approximate):
    # total cost per execution in pips = spread + 2*slippage (enter or exit)
    # convert pips to fractional return by pip_size / price
    def pip_size_for_pair(p: str) -> float:
        return 0.01 if p.upper().endswith("JPY") else 0.0001

    total_pips = spread_pips + 2.0 * slippage_pips_per_side
    if total_pips > 0:
        trade_points = signals.diff().fillna(0).ne(0)
        pip_size = pip_size_for_pair(pair)
        # Cost return at trade points
        cost_ret = (-total_pips * pip_size) / df["close"]
        cost_ret = cost_ret.where(trade_points, other=0.0)
        rets = rets + cost_ret
    metrics = summarize(rets, signals)
    result = {
        "pair": pair,
        "start": df.index.min().isoformat() if len(df) else None,
        "end": df.index.max().isoformat() if len(df) else None,
        "n": int(len(df)),
        "metrics": metrics,
        "policy": {
            "trades_blocked": presult.trades_blocked_rate,
            "halted_days": presult.halted_days,
            "config": {
                "max_abs_position": max_abs_position,
                "max_trades_per_hour": max_trades_per_hour,
                "daily_loss_threshold": daily_loss_threshold,
            },
        },
        "params": {
            "strategy_name": strategy_name,
            "strategy_params": strategy_params,
            "costs": {
                "spread_pips": spread_pips,
                "slippage_pips_per_side": slippage_pips_per_side,
            },
        },
    }
    if persist and db_path:
        reg = Registry(db_path=db_path)
        reg.init()
        reg.record_backtest(
            pair=pair,
            start_ts=result["start"],
            end_ts=result["end"],
            n=result["n"],
            metrics=result["metrics"],
            policy=result["policy"],
            params=result["params"],
        )
    return result


def backtest_pairs(
    data_root: str,
    pairs: list[str],
    start_days: int | None = None,
    strategy_name: str = "ma",
    strategy_params: dict | None = None,
    spread_pips: float = 0.5,
    slippage_pips_per_side: float = 0.5,
    max_abs_position: int = 1,
    max_trades_per_hour: int = 60,
    daily_loss_threshold: float = 0.02,
    persist: bool = False,
    db_path: str | None = None,
) -> list[dict]:
    out = []
    for p in pairs:
        try:
            out.append(
                run_backtest(
                    data_root,
                    p,
                    start_days,
                    strategy_name,
                    strategy_params,
                    spread_pips=spread_pips,
                    slippage_pips_per_side=slippage_pips_per_side,
                    max_abs_position=max_abs_position,
                    max_trades_per_hour=max_trades_per_hour,
                    daily_loss_threshold=daily_loss_threshold,
                    persist=persist,
                    db_path=db_path,
                )
            )
        except Exception as e:
            out.append({"pair": p, "error": str(e)})
    return out
