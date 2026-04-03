from __future__ import annotations

import glob
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from agentfx.adapters.storage.sqlite import Registry
from agentfx.core.metrics import compute_returns, summarize
from agentfx.core.policy import PolicyConfig, apply_policies
from agentfx.core.strategy import (
    DonchianBreakout,
    MACrossover,
    RSIMeanReversion,
    Strategy,
)


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


def load_price_df_from_db(
    instrument: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
    exchange: str = "oanda",
) -> pd.DataFrame:
    """Load price data from PostgreSQL candles table.

    Args:
        instrument: Instrument symbol (e.g., EUR_USD, GBP_USD)
        granularity: Candle timeframe (M1, M5, H1, etc.)
        start: Start datetime (UTC). If None, fetch all available data.
        end: End datetime (UTC). If None, fetch up to now.
        exchange: Exchange identifier (default: oanda)

    Returns:
        DataFrame with columns [open, high, low, close, volume] indexed by time

    Raises:
        FileNotFoundError: If no data is found for the given parameters
    """
    from agentfx.api.core.database import SessionLocal
    from agentfx.api.services.market_data import MarketDataService

    db = SessionLocal()
    try:
        # Get candles from database (large limit to get all data)
        candles, total = MarketDataService.get_candles(
            db=db,
            exchange=exchange,
            instrument=instrument.upper().replace("/", "_"),
            timeframe=granularity.upper(),
            from_time=start,
            to_time=end,
            limit=1_000_000,  # Large limit to fetch all available data
        )

        if not candles:
            raise FileNotFoundError(
                f"No data for {instrument}:{granularity} in database. "
                f"Run backfill to populate data."
            )

        # Convert to DataFrame
        data = []
        for candle in candles:
            data.append(
                {
                    "time": candle.timestamp,
                    "open": float(candle.open),
                    "high": float(candle.high),
                    "low": float(candle.low),
                    "close": float(candle.close),
                    "volume": float(candle.volume) if candle.volume else 0.0,
                }
            )

        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time")
        df = df.set_index("time").loc[:, ["open", "high", "low", "close", "volume"]]

        return df
    finally:
        db.close()


def run_backtest_from_db(
    instrument: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
    strategy_name: str = "ma",
    strategy_params: dict | None = None,
    spread_pips: float = 0.5,
    slippage_pips_per_side: float = 0.5,
    max_abs_position: int = 1,
    max_trades_per_hour: int = 60,
    daily_loss_threshold: float = 0.02,
    capture_signals: bool = False,
    account_balance: float = 10000.0,
    position_size: int = 10000,
) -> dict[str, Any]:
    """Run backtest using candle data from PostgreSQL database.

    This function reads candle data from the database (populated by the
    candle ingestion worker) and runs a backtest with the specified strategy.

    Args:
        instrument: Currency pair (e.g., EUR_USD)
        granularity: Candle timeframe (M1, M5, H1, etc.)
        start: Start datetime for backtest
        end: End datetime for backtest
        strategy_name: Strategy to use (ma, rsi, donchian)
        strategy_params: Strategy parameters (e.g., {fast: 10, slow: 50})
        spread_pips: Spread cost in pips
        slippage_pips_per_side: Slippage per side in pips
        max_abs_position: Maximum position size
        max_trades_per_hour: Rate limit for trades
        daily_loss_threshold: Daily loss threshold for halting
        capture_signals: If True, capture trade signals for visualization
        account_balance: Starting account balance in dollars
        position_size: Position size in units (10000 = 0.1 lot)

    Returns:
        dict with backtest results including:
            - pair: Instrument
            - start/end: Data range
            - n: Number of candles
            - metrics: Performance metrics (sharpe, max_drawdown, etc.)
            - policy: Policy application results
            - params: Strategy parameters
            - signals: Trade signals (if capture_signals=True)
            - trades: Individual trades (if capture_signals=True)
    """
    strategy_params = strategy_params or {}

    # Load data from database
    df = load_price_df_from_db(
        instrument=instrument,
        granularity=granularity,
        start=start,
        end=end,
    )

    # Create strategy and generate signals
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

    # Apply transaction costs on signal changes
    def pip_size_for_pair(p: str) -> float:
        return 0.01 if p.upper().endswith("JPY") or "_JPY" in p.upper() else 0.0001

    total_pips = spread_pips + 2.0 * slippage_pips_per_side
    if total_pips > 0:
        trade_points = signals.diff().fillna(0).ne(0)
        pip_size = pip_size_for_pair(instrument)
        cost_ret = (-total_pips * pip_size) / df["close"]
        cost_ret = cost_ret.where(trade_points, other=0.0)
        rets = rets + cost_ret

    metrics = summarize(rets, signals)

    result: dict[str, Any] = {
        "pair": instrument,
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
            "account_balance": account_balance,
            "position_size": position_size,
        },
    }

    # Capture signals and trades for visualization
    if capture_signals:
        pip_size = pip_size_for_pair(instrument)
        signal_list = []
        trade_list = []
        trade_id = 0

        # Get indicator values based on strategy
        indicator_df = _get_indicator_values(strat, df, strategy_name, strategy_params)

        # Iterate through signals to capture entries/exits
        prev_signal = 0
        entry_time = None
        entry_price = None
        entry_signal = 0

        for i, (timestamp, signal_val) in enumerate(signals.items()):
            signal_val = int(signal_val)

            if signal_val != prev_signal:
                # Get indicator values at this point
                indicator_values = {}
                if indicator_df is not None and timestamp in indicator_df.index:
                    for col in indicator_df.columns:
                        val = indicator_df.loc[timestamp, col]
                        if pd.notna(val):
                            indicator_values[col] = float(val)

                # Determine signal type
                if prev_signal == 0 and signal_val != 0:
                    # Entry
                    signal_type = "entry_long" if signal_val > 0 else "entry_short"
                    entry_time = timestamp
                    entry_price = float(df.loc[timestamp, "close"])
                    entry_signal = signal_val
                elif prev_signal != 0 and (
                    signal_val == 0 or signal_val != prev_signal
                ):
                    # Exit (or flip — close current position first)
                    signal_type = "exit_long" if prev_signal > 0 else "exit_short"
                    exit_price = float(df.loc[timestamp, "close"])

                    # Calculate trade P&L
                    if entry_time is not None and entry_price is not None:
                        direction = "long" if entry_signal > 0 else "short"
                        price_diff = exit_price - entry_price
                        if direction == "short":
                            price_diff = -price_diff
                        pips = price_diff / pip_size
                        gross_pnl = pips * pip_size * position_size

                        # Spread and swap costs
                        spread_cost_pips = spread_pips
                        # Simplified swap calculation (0 for now)
                        swap_cost_pips = 0.0
                        net_pips = pips - spread_cost_pips - swap_cost_pips

                        trade_list.append(
                            {
                                "id": f"trade_{trade_id}",
                                "direction": direction,
                                "entry_timestamp": entry_time.isoformat(),
                                "exit_timestamp": timestamp.isoformat(),
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "pips": pips,
                                "spread_cost_pips": spread_cost_pips,
                                "swap_cost_pips": swap_cost_pips,
                                "net_pips": net_pips,
                                "gross_pnl_dollars": gross_pnl,
                                "spread_cost_dollars": spread_cost_pips
                                * pip_size
                                * position_size,
                                "swap_cost_dollars": swap_cost_pips
                                * pip_size
                                * position_size,
                                "net_pnl_dollars": net_pips * pip_size * position_size,
                                "duration_minutes": (
                                    timestamp - entry_time
                                ).total_seconds()
                                / 60,
                                "position_size": position_size,
                            }
                        )
                        trade_id += 1

                    # If flipping to a new position, open the new entry
                    if signal_val != 0:
                        # Emit exit signal for closed position
                        signal_list.append(
                            {
                                "timestamp": timestamp.isoformat(),
                                "signal": 0,
                                "signal_type": signal_type,
                                "price": float(df.loc[timestamp, "close"]),
                                "indicator_values": indicator_values,
                            }
                        )
                        # Set up entry signal for the new position
                        entry_time = timestamp
                        entry_price = exit_price
                        entry_signal = signal_val
                        signal_type = "entry_long" if signal_val > 0 else "entry_short"
                    else:
                        entry_time = None
                        entry_price = None

                signal_list.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "signal": signal_val,
                        "signal_type": signal_type,
                        "price": float(df.loc[timestamp, "close"]),
                        "indicator_values": indicator_values,
                    }
                )

            prev_signal = signal_val

        result["signals"] = signal_list
        result["trades"] = trade_list

        # Compute additional metrics from closed trades
        if trade_list:
            pips_list = [t["pips"] for t in trade_list]
            wins = [p for p in pips_list if p > 0]
            losses = [p for p in pips_list if p < 0]

            # avg_win/avg_loss as decimal price returns (frontend multiplies by 10000 for pips display)
            metrics["avg_win"] = (sum(wins) / len(wins)) * pip_size if wins else 0.0
            metrics["avg_loss"] = (
                (sum(losses) / len(losses)) * pip_size if losses else 0.0
            )
            metrics["profit_factor"] = sum(wins) / abs(sum(losses)) if losses else 0.0
            metrics["n_trades"] = len(trade_list)
            metrics["hit_rate"] = len(wins) / len(trade_list)

            # Max consecutive losses
            max_consec = 0
            current_consec = 0
            for p in pips_list:
                if p < 0:
                    current_consec += 1
                    max_consec = max(max_consec, current_consec)
                else:
                    current_consec = 0
            metrics["max_consecutive_losses"] = max_consec

            # Average trade duration
            durations = [t["duration_minutes"] for t in trade_list]
            metrics["avg_trade_duration"] = (
                sum(durations) / len(durations) / 60.0
            )  # in hours

            # Total return from equity curve
            eq = (1.0 + rets).cumprod()
            metrics["total_return"] = float(eq.iloc[-1] - 1.0) if len(eq) > 0 else 0.0

            # Pips-based extended metrics
            total_pips_val = sum(t["pips"] for t in trade_list)
            total_spread = sum(t["spread_cost_pips"] for t in trade_list)
            total_swap = sum(t["swap_cost_pips"] for t in trade_list)
            metrics["total_pips"] = total_pips_val
            metrics["avg_pips_per_trade"] = total_pips_val / len(trade_list)
            metrics["total_spread_cost_pips"] = total_spread
            metrics["total_swap_cost_pips"] = total_swap
            metrics["net_pips"] = total_pips_val - total_spread - total_swap

            # Dollar-based extended metrics
            metrics["gross_pnl_dollars"] = sum(
                t["gross_pnl_dollars"] for t in trade_list
            )
            metrics["total_spread_cost_dollars"] = sum(
                t["spread_cost_dollars"] for t in trade_list
            )
            metrics["total_swap_cost_dollars"] = sum(
                t["swap_cost_dollars"] for t in trade_list
            )
            metrics["net_pnl_dollars"] = sum(t["net_pnl_dollars"] for t in trade_list)

    return result


def _get_indicator_values(
    strat: Strategy,
    df: pd.DataFrame,
    strategy_name: str,
    strategy_params: dict,
) -> pd.DataFrame | None:
    """Calculate indicator values for visualization.

    Args:
        strat: Strategy instance
        df: Price DataFrame
        strategy_name: Name of strategy
        strategy_params: Strategy parameters

    Returns:
        DataFrame with indicator columns, or None if not applicable
    """
    strategy_name = strategy_name.lower()

    if strategy_name in ("ma", "mac", "ma_crossover", "macrossover"):
        fast = int(strategy_params.get("fast", 20))
        slow = int(strategy_params.get("slow", 50))
        return pd.DataFrame(
            {
                "fast_ma": df["close"].rolling(fast).mean(),
                "slow_ma": df["close"].rolling(slow).mean(),
            },
            index=df.index,
        )

    elif strategy_name in ("rsi", "rsi_mr", "rsi_mean_reversion"):
        window = int(strategy_params.get("window", strategy_params.get("fast", 14)))
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return pd.DataFrame(
            {
                "rsi": rsi,
            },
            index=df.index,
        )

    elif strategy_name in ("donchian", "breakout", "donchian_breakout"):
        window = int(strategy_params.get("window", strategy_params.get("fast", 20)))
        return pd.DataFrame(
            {
                "upper_band": df["high"].rolling(window).max(),
                "lower_band": df["low"].rolling(window).min(),
            },
            index=df.index,
        )

    return None


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
    end = datetime.now(UTC).replace(second=0, microsecond=0)
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
