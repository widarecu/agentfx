from __future__ import annotations

import glob
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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

logger = logging.getLogger(__name__)

# OANDA charges financing at 17:00 New York time. On Wednesdays the charge
# is tripled to cover the upcoming weekend (when interest still accrues but
# FX markets are closed).
_ET_ZONE = ZoneInfo("America/New_York")
_ROLLOVER_HOUR = 17  # 17:00 ET


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


def _count_rollovers(prev_ts: pd.Timestamp, ts: pd.Timestamp) -> int:
    """Sum the financing-charge multiplier for rollovers in (prev_ts, ts].

    OANDA charges interest each weekday at 17:00 ET, with Wednesday counting
    triple to cover Saturday and Sunday when FX is closed but interest still
    accrues. The weekly schedule is therefore:

        Mon : 1×   Thu : 1×
        Tue : 1×   Fri : 1×
        Wed : 3×   Sat : 0× (market closed)
                   Sun : 0× (market closed)

    which sums to 7 charges per 7 calendar days.

    Both timestamps must be tz-aware (UTC).
    """
    prev_et = prev_ts.tz_convert(_ET_ZONE)
    ts_et = ts.tz_convert(_ET_ZONE)

    candidate = prev_et.normalize() + pd.Timedelta(hours=_ROLLOVER_HOUR)
    if candidate <= prev_et:
        candidate = candidate + pd.Timedelta(days=1)

    total = 0
    while candidate <= ts_et:
        wd = candidate.weekday()  # Mon=0..Sun=6
        if wd == 2:  # Wednesday
            total += 3
        elif wd in (5, 6):  # Sat, Sun — market closed
            pass
        else:  # Mon, Tue, Thu, Fri
            total += 1
        candidate = candidate + pd.Timedelta(days=1)
    return total


def compute_swap_returns(
    df: pd.DataFrame,
    signals: pd.Series,
    long_rate: float | None,
    short_rate: float | None,
) -> pd.Series:
    """Per-bar overnight financing return aligned to df.index.

    For each bar whose interval (prev_close, this_close] crosses one or more
    OANDA rollovers (17:00 ET) and where a position is held during the bar,
    return ``rate / 365 * multiplier`` where rate is ``long_rate`` for longs
    and ``short_rate`` for shorts.

    Sign convention matches OANDA's ``/v3/instruments`` payload: positive
    rate = position receives credit, negative rate = position pays. The
    returned series is added directly to the strategy returns.

    If either rate is ``None`` (financing not refreshed yet for this pair),
    the helper returns a zero series so existing backtests continue to run.
    """
    if long_rate is None or short_rate is None or len(df) < 2:
        return pd.Series(0.0, index=df.index)

    swap_ret = pd.Series(0.0, index=df.index)
    timestamps = df.index
    sig_values = signals.to_numpy()

    for i in range(1, len(timestamps)):
        pos = float(sig_values[i])
        if pos == 0.0:
            continue
        multiplier = _count_rollovers(timestamps[i - 1], timestamps[i])
        if multiplier == 0:
            continue
        rate = long_rate if pos > 0 else short_rate
        swap_ret.iloc[i] = rate / 365.0 * multiplier

    return swap_ret


def _pip_value_usd(instrument: str, close: float) -> float:
    """USD value of 1 pip per 1 unit of position at the given close.

    For USD-quoted majors (EUR/USD, GBP/USD, AUD/USD) the pip is already
    in USD and the close price is irrelevant: it's just `pip_size`.

    For USD-base majors (USD/JPY, USD/CHF, USD/CAD) the pip is in the
    quote currency and converts to USD by dividing by the current close.

    Cross pairs (no USD leg) are not supported in v0.3.0 — they would
    need a triangulation via a base/USD rate that the v0.3.0 catalogue
    doesn't expose.
    """
    parts = instrument.upper().replace("/", "_").split("_")
    if len(parts) != 2:
        raise ValueError(f"unsupported instrument symbol: {instrument}")
    base, quote = parts
    pip_in_quote = 0.01 if quote == "JPY" else 0.0001
    if quote == "USD":
        return pip_in_quote
    if base == "USD":
        if close <= 0:
            raise ValueError(
                f"cannot compute pip value for {instrument} at close={close}"
            )
        return pip_in_quote / close
    raise ValueError(
        f"pip value not supported for cross pair (no USD leg): {instrument}"
    )


def _notional_usd_per_unit(instrument: str, close: float) -> float:
    """USD notional per 1 unit of position at the given close.

    USD-base pairs (USD/JPY, USD/CHF, USD/CAD): 1 unit = $1.
    USD-quote pairs (EUR/USD, GBP/USD, AUD/USD): 1 unit = `close` USD
    (you hold 1 unit of the base ccy worth `close` USD).
    """
    base, _ = instrument.upper().replace("/", "_").split("_")
    return 1.0 if base == "USD" else float(close)


def _dsl_position_size(
    strat: Strategy,
    instrument: str,
    entry_close: float,
    account_balance: float,
    leverage: int,
) -> tuple[int, bool]:
    """Compute the per-trade position size for a DSL strategy.

    Sizing rule (account-currency = USD):
        risk_usd       = account_balance × spec.size.risk_pct
        desired_units  = risk_usd / (stop_pips × pip_value_usd_at_entry)
        notional       = desired_units × notional_usd_per_unit_at_entry
        if notional > account_balance × leverage:
            clamp desired_units so notional == leverage cap (with warning)

    ``account_balance`` is the running balance at the moment the trade is
    opened, not the static starting capital — the caller (the trade loop
    in ``run_backtest_from_db``) compounds it by adding each closed
    trade's net P&L. That keeps the per-trade dollar ledger consistent
    with the rets-based equity curve, which already compounds via
    ``cumprod``.

    Returns (position_units, was_capped). For non-DSL strategies the
    caller should not invoke this — they keep their static
    `position_size` argument.
    """
    # Local import to avoid a cycle: backtest.py is imported during
    # `make_strategy` which is itself called above this helper.
    from agentfx.core.dsl.compiler import CompiledDSLStrategy

    if not isinstance(strat, CompiledDSLStrategy):
        raise TypeError(
            f"_dsl_position_size called for non-DSL strategy {type(strat).__name__}"
        )
    spec = strat.spec
    risk_pct = float(spec.size.risk_pct)
    stop_pips = int(spec.exit.stop_pips)
    pip_value = _pip_value_usd(instrument, entry_close)
    notional_per_unit = _notional_usd_per_unit(instrument, entry_close)

    risk_usd = account_balance * risk_pct
    desired_units = risk_usd / (stop_pips * pip_value)
    notional = desired_units * notional_per_unit
    max_notional = account_balance * leverage

    was_capped = False
    if notional > max_notional:
        desired_units = max_notional / notional_per_unit
        was_capped = True
        logger.warning(
            "Leverage cap hit on %s @ close=%.5f: notional $%.0f clamped "
            "to $%.0f (leverage=%d:1, account=$%.0f)",
            instrument,
            entry_close,
            notional,
            max_notional,
            leverage,
            account_balance,
        )
    units = max(1, int(round(desired_units)))
    return units, was_capped


def _lookup_financing_rates(instrument: str) -> tuple[float | None, float | None]:
    """Read cached OANDA financing rates for an instrument from the DB.

    Returns ``(long_rate, short_rate)`` or ``(None, None)`` if the row is
    missing, the rates have not been seeded, or the database is unreachable
    (e.g. unit tests run without a DB). A None result causes the backtest
    to apply zero swap, preserving prior behaviour rather than crashing.
    """
    try:
        from agentfx.api.core.database import SessionLocal
        from agentfx.api.models.instrument import Instrument
    except Exception as e:  # pragma: no cover - import guard
        logger.debug("Cannot import DB layer for financing lookup: %s", e)
        return None, None

    try:
        db = SessionLocal()
    except Exception as e:
        logger.debug("Could not open DB session for financing lookup: %s", e)
        return None, None

    try:
        row = db.query(Instrument).filter(Instrument.symbol == instrument).first()
        if row is None:
            logger.warning(
                "No instrument row for %s — swap costs will be zero in backtest.",
                instrument,
            )
            return None, None
        if row.financing_long_rate is None or row.financing_short_rate is None:
            logger.warning(
                "Financing rates not refreshed for %s — swap costs will be zero. "
                "Run scripts/seed_instruments.py --refresh-financing to populate.",
                instrument,
            )
            return None, None
        return float(row.financing_long_rate), float(row.financing_short_rate)
    except Exception as e:
        # DB unreachable, table missing, etc. — degrade to zero swap.
        logger.debug("Financing lookup failed for %s: %s", instrument, e)
        return None, None
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _lookup_typical_spreads(instruments: list[str]) -> dict[str, float]:
    """Read the per-pair typical spread (pips) for many instruments in
    one query. Returns ``{symbol: spread_pips}`` for every instrument
    whose row has a populated ``typical_spread_pips``. Missing rows /
    null spreads are simply absent from the returned dict — the caller
    decides how to handle them (default value, warning, raise).

    This is the v0.4.0 entry point for the per-pair cost model. Today
    the orchestrator calls it with a single-element list (one pair per
    run); v0.5.0+ multi-symbol runs will pass the full instrument set
    so each pair gets its own pinned spread. The function shape is the
    upgrade — no migration needed when more pairs start coming in.
    """
    if not instruments:
        return {}
    try:
        from agentfx.api.core.database import SessionLocal
        from agentfx.api.models.instrument import Instrument
    except Exception as e:  # pragma: no cover - import guard
        logger.debug("Cannot import DB layer for spread lookup: %s", e)
        return {}

    try:
        db = SessionLocal()
    except Exception as e:
        logger.debug("Could not open DB session for spread lookup: %s", e)
        return {}

    try:
        rows = (
            db.query(Instrument.symbol, Instrument.typical_spread_pips)
            .filter(Instrument.symbol.in_(instruments))
            .all()
        )
        return {symbol: float(spread) for symbol, spread in rows if spread is not None}
    except Exception as e:
        logger.debug("Per-pair spread lookup failed for %s: %s", instruments, e)
        return {}
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _lookup_typical_spread(instrument: str) -> float | None:
    """Single-pair wrapper around :func:`_lookup_typical_spreads` kept
    for back-compat with the v0.3.0 call sites in this module. New
    callers (and v0.4.0+ orchestrator code) should prefer the bulk
    helper so multi-pair upgrades stay zero-touch.
    """
    found = _lookup_typical_spreads([instrument])
    return found.get(instrument)


def run_backtest_from_db(
    instrument: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
    strategy_name: str = "ma",
    strategy_params: dict | None = None,
    spread_pips: float | None = None,
    slippage_pips_per_side: float = 0.1,
    max_abs_position: int = 1,
    max_trades_per_hour: int = 60,
    daily_loss_threshold: float = 0.02,
    capture_signals: bool = False,
    account_balance: float = 10000.0,
    position_size: int = 10000,
    leverage: int = 30,
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

    # Resolve per-pair typical spread when caller didn't pin a value.
    # OANDA's actual cost varies by pair (EUR_USD ~0.6 pips, USD_CAD ~1.5
    # pips); seeded by scripts/seed_instruments.py from live pricing.
    if spread_pips is None:
        looked_up = _lookup_typical_spread(instrument)
        if looked_up is None:
            logger.warning(
                "No typical_spread_pips on instruments row for %s — "
                "defaulting to 0.5 pips. Run scripts/seed_instruments.py "
                "--refresh-spreads to populate.",
                instrument,
            )
            spread_pips = 0.5
        else:
            spread_pips = looked_up

    # Load data from database
    df = load_price_df_from_db(
        instrument=instrument,
        granularity=granularity,
        start=start,
        end=end,
    )

    # Pip size for the instrument — needed for both cost application and
    # DSL strategies (their stop/target are in pips).
    def pip_size_for_pair(p: str) -> float:
        return 0.01 if p.upper().endswith("JPY") or "_JPY" in p.upper() else 0.0001

    pip_size = pip_size_for_pair(instrument)

    # Create strategy and generate signals — DSL strategies need pip_size
    # for the stop/target/max_hold exit walker; classic ones ignore it.
    strat = make_strategy(strategy_name, pip_size=pip_size, **strategy_params)
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

    total_pips = spread_pips + 2.0 * slippage_pips_per_side
    if total_pips > 0:
        trade_points = signals.diff().fillna(0).ne(0)
        cost_ret = (-total_pips * pip_size) / df["close"]
        cost_ret = cost_ret.where(trade_points, other=0.0)
        rets = rets + cost_ret

    # Apply OANDA overnight financing (swap) costs.
    long_rate, short_rate = _lookup_financing_rates(instrument)
    swap_ret = compute_swap_returns(df, signals, long_rate, short_rate)
    rets = rets + swap_ret

    metrics = summarize(rets, signals, close=df["close"])

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
        from agentfx.core.dsl.compiler import CompiledDSLStrategy

        signal_list = []
        trade_list = []
        trade_id = 0
        leverage_cap_hits = 0
        is_dsl = isinstance(strat, CompiledDSLStrategy)

        # Compounded sizing: position size on each new DSL trade is computed
        # against the running balance (starting balance + sum of realised
        # net P&L from prior closed trades), not the static starting capital.
        # Matches what a real OANDA account does — profits grow the next
        # trade's risk capital, losses shrink it. The rets-based equity
        # curve already compounds; this keeps the per-trade dollar ledger
        # consistent with that assumption.
        current_balance = float(account_balance)
        peak_balance = current_balance

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
                # Get indicator values at the SIGNAL bar (previous bar),
                # not the entry bar. The signal is shifted by 1 bar to
                # prevent lookahead, so the indicator that triggered the
                # trade is from the bar before.
                indicator_values = {}
                if indicator_df is not None:
                    ts_idx = (
                        indicator_df.index.get_loc(timestamp)
                        if timestamp in indicator_df.index
                        else -1
                    )
                    signal_bar_idx = ts_idx - 1 if ts_idx > 0 else ts_idx
                    if signal_bar_idx >= 0:
                        signal_bar_ts = indicator_df.index[signal_bar_idx]
                        for col in indicator_df.columns:
                            val = indicator_df.loc[signal_bar_ts, col]
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

                        # Per-trade position sizing:
                        #   - DSL strategies: risk_pct × account_balance, with
                        #     a leverage cap (clamps notional to leverage × balance).
                        #   - Classic strategies: fixed `position_size` arg.
                        # Pip→USD conversion uses the entry close so a 1-pip
                        # move on USD/JPY at 150 correctly worth less in USD
                        # than on EUR/USD at 1.10.
                        if is_dsl:
                            # Pass the *running* balance, not the starting
                            # one — this is what makes sizing compound with
                            # realised P&L. Floor at 0 to keep the helper's
                            # arithmetic well-defined on a busted account;
                            # _dsl_position_size already enforces units >= 1.
                            sizing_balance = max(current_balance, 0.0)
                            trade_units, was_capped = _dsl_position_size(
                                strat,
                                instrument,
                                entry_price,
                                sizing_balance,
                                leverage,
                            )
                            if was_capped:
                                leverage_cap_hits += 1
                        else:
                            trade_units = int(position_size)
                        pip_value_usd = _pip_value_usd(instrument, entry_price)

                        gross_pnl = pips * pip_value_usd * trade_units

                        # Spread, slippage and swap costs per round trip.
                        # The `rets` series above charges (spread_pips + 2 *
                        # slippage_pips_per_side) at each signal-change bar; to
                        # keep the per-trade ledger consistent with what Sharpe
                        # sees, we attribute the same total to each closed
                        # trade. Slippage is execution friction (entry + exit
                        # = 2 sides), spread is the OANDA fee paid once over
                        # the round trip.
                        # Swap is the sum of per-bar overnight financing
                        # returns inside the trade window (entry, exit],
                        # converted to pips using entry price as reference.
                        # Sign-flipped because swap_ret > 0 means the position
                        # gained, while *_cost_pips uses cost convention
                        # (positive = cost subtracted from net_pips).
                        spread_cost_pips = spread_pips
                        slippage_cost_pips = 2.0 * slippage_pips_per_side
                        trade_swap_ret = float(
                            swap_ret.loc[
                                (swap_ret.index > entry_time)
                                & (swap_ret.index <= timestamp)
                            ].sum()
                        )
                        swap_cost_pips = -trade_swap_ret * entry_price / pip_size
                        net_pips = (
                            pips
                            - spread_cost_pips
                            - slippage_cost_pips
                            - swap_cost_pips
                        )

                        net_pnl_dollars = net_pips * pip_value_usd * trade_units
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
                                "slippage_cost_pips": slippage_cost_pips,
                                "swap_cost_pips": swap_cost_pips,
                                "net_pips": net_pips,
                                "gross_pnl_dollars": gross_pnl,
                                "spread_cost_dollars": spread_cost_pips
                                * pip_value_usd
                                * trade_units,
                                "slippage_cost_dollars": slippage_cost_pips
                                * pip_value_usd
                                * trade_units,
                                "swap_cost_dollars": swap_cost_pips
                                * pip_value_usd
                                * trade_units,
                                "net_pnl_dollars": net_pnl_dollars,
                                "duration_minutes": (
                                    timestamp - entry_time
                                ).total_seconds()
                                / 60,
                                "position_size": trade_units,
                                "balance_after": current_balance + net_pnl_dollars,
                            }
                        )
                        current_balance += net_pnl_dollars
                        peak_balance = max(peak_balance, current_balance)
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
        # Sizing context for the UI — lets the page show "leverage cap
        # hit on N trades" and the leverage setting used.
        result["sizing"] = {
            "account_balance": account_balance,
            "starting_balance": float(account_balance),
            "ending_balance": current_balance,
            "peak_balance": peak_balance,
            "compounded": is_dsl,
            "account_currency": "USD",
            "leverage": leverage,
            "leverage_cap_hits": leverage_cap_hits,
            "is_dsl_risk_sized": is_dsl,
        }

        # ── Trade-ledger metrics (canonical) ──
        # Overwrite Sharpe / MaxDD / CAGR / HitRate / n_trades / total_return
        # with values derived from the *risk-sized, compounded* dollar
        # ledger. The previous bar-level computation assumed a fictional
        # always-100%-invested unit-position strategy and routinely
        # disagreed with the hero's realised account change. Trade-ledger
        # is the version every other surface in the platform (hero chip,
        # ending_balance, peak_balance, cost breakdown) is consistent
        # with — picking it as canonical means the hero, the tiles, and
        # the WF table (on new runs) all tell the same story.
        from agentfx.core.metrics import summarize_from_trade_ledger

        ledger = summarize_from_trade_ledger(
            trade_list,
            starting_balance=float(account_balance),
            window_start=df.index[0].to_pydatetime() if len(df) else start,
            window_end=df.index[-1].to_pydatetime() if len(df) else end,
        )
        # Drop balance fields out of the metrics dict — those already live
        # under result["sizing"] for the UI hero. Keeping them only on the
        # canonical surface avoids two competing copies that can drift.
        for k in (
            "sharpe",
            "mdd",
            "max_drawdown",
            "hit_rate",
            "cagr",
            "n_trades",
            "total_return",
        ):
            metrics[k] = ledger[k]

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

            # Pips-based extended metrics
            total_pips_val = sum(t["pips"] for t in trade_list)
            total_spread = sum(t["spread_cost_pips"] for t in trade_list)
            total_slippage = sum(t["slippage_cost_pips"] for t in trade_list)
            total_swap = sum(t["swap_cost_pips"] for t in trade_list)
            metrics["total_pips"] = total_pips_val
            metrics["avg_pips_per_trade"] = total_pips_val / len(trade_list)
            metrics["total_spread_cost_pips"] = total_spread
            metrics["total_slippage_cost_pips"] = total_slippage
            metrics["total_swap_cost_pips"] = total_swap
            metrics["net_pips"] = (
                total_pips_val - total_spread - total_slippage - total_swap
            )

            # Dollar-based extended metrics
            metrics["gross_pnl_dollars"] = sum(
                t["gross_pnl_dollars"] for t in trade_list
            )
            metrics["total_spread_cost_dollars"] = sum(
                t["spread_cost_dollars"] for t in trade_list
            )
            metrics["total_slippage_cost_dollars"] = sum(
                t["slippage_cost_dollars"] for t in trade_list
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
        window = int(
            strategy_params.get(
                "period", strategy_params.get("window", strategy_params.get("fast", 14))
            )
        )
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
    if n == "dsl":
        # DSL dispatch: compile a v0.3.0 StrategySpec into a Strategy.
        # `spec` may be a StrategySpec instance or its dict form (for callers
        # that round-trip through JSON, e.g. the optimizer storing genomes).
        from agentfx.core.dsl import StrategySpec, compile_spec

        spec_data = kwargs.get("spec")
        if spec_data is None:
            raise ValueError(
                "strategy 'dsl' requires a 'spec' kwarg with a StrategySpec or dict"
            )
        if isinstance(spec_data, StrategySpec):
            spec = spec_data
        elif isinstance(spec_data, dict):
            spec = StrategySpec.model_validate(spec_data)
        else:
            raise ValueError(
                f"'spec' must be StrategySpec or dict, got {type(spec_data).__name__}"
            )
        # pip_size required for stop/target enforcement. Caller (the
        # backtest engine) passes the instrument-correct value;
        # default of 0.0001 covers majors when the caller hasn't
        # threaded it through.
        pip_size = float(kwargs.get("pip_size", 0.0001))
        return compile_spec(spec, pip_size=pip_size)
    if n in ("ma", "mac", "ma_crossover", "macrossover"):
        return MACrossover(
            fast=int(kwargs.get("fast", 20)), slow=int(kwargs.get("slow", 50))
        )
    if n in ("rsi", "rsi_mr", "rsi_mean_reversion"):
        window = int(kwargs.get("period", kwargs.get("window", kwargs.get("fast", 14))))
        lower = float(kwargs.get("oversold", kwargs.get("lower", 30.0)))
        upper = float(kwargs.get("overbought", kwargs.get("upper", 70.0)))
        return RSIMeanReversion(window=window, lower=lower, upper=upper)
    if n in ("donchian", "breakout", "donchian_breakout"):
        window = int(kwargs.get("window", kwargs.get("period", kwargs.get("fast", 20))))
        return DonchianBreakout(window=window)
    raise ValueError(f"Unknown strategy: {name}")


def run_backtest(
    data_root: str,
    pair: str,
    start_days: int | None = None,
    strategy_name: str = "ma",
    strategy_params: dict | None = None,
    spread_pips: float | None = None,
    slippage_pips_per_side: float = 0.1,
    max_abs_position: int = 1,
    max_trades_per_hour: int = 60,
    daily_loss_threshold: float = 0.02,
    persist: bool = False,
    db_path: str | None = None,
) -> dict:
    strategy_params = strategy_params or {}
    if spread_pips is None:
        looked_up = _lookup_typical_spread(pair)
        if looked_up is None:
            logger.warning(
                "No typical_spread_pips for %s — defaulting to 0.5 pips. "
                "Run scripts/seed_instruments.py --refresh-spreads to populate.",
                pair,
            )
            spread_pips = 0.5
        else:
            spread_pips = looked_up
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(days=start_days) if start_days else None
    df = load_price_df(data_root, pair, start=start, end=end)

    def pip_size_for_pair(p: str) -> float:
        return 0.01 if p.upper().endswith("JPY") else 0.0001

    pip_size = pip_size_for_pair(pair)

    strat = make_strategy(strategy_name, pip_size=pip_size, **strategy_params)
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

    total_pips = spread_pips + 2.0 * slippage_pips_per_side
    if total_pips > 0:
        trade_points = signals.diff().fillna(0).ne(0)
        # Cost return at trade points
        cost_ret = (-total_pips * pip_size) / df["close"]
        cost_ret = cost_ret.where(trade_points, other=0.0)
        rets = rets + cost_ret

    # Apply OANDA overnight financing (swap) costs.
    long_rate, short_rate = _lookup_financing_rates(pair)
    swap_ret = compute_swap_returns(df, signals, long_rate, short_rate)
    rets = rets + swap_ret

    metrics = summarize(rets, signals, close=df["close"])
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
    spread_pips: float | None = None,
    slippage_pips_per_side: float = 0.1,
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
