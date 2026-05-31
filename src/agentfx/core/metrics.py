from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ClosedTrade:
    """A single round-trip trade extracted from a (close, signals) pair.

    The fields here are the *minimum* needed to compute trade-level
    metrics consistently across every code path (backtest engine,
    validator, fitness, UI). Downstream code can layer additional
    information on top (pips, spread/swap costs, dollar P&L, timestamps)
    — see `services.backtest.run_backtest_from_db` for an example —
    without forking the trade-walking logic.

    Trade timing follows the LIVE-TRADING convention: a trade enters at
    the close of the bar where the signal first becomes non-zero (you
    can't execute at a close that already happened, so the earliest
    realistic fill is the next bar's close) and exits at the close of
    the bar where the signal first flips (to opposite direction or to
    flat). Bars where the position is still open at the end of the
    series are NOT emitted — they haven't realised P&L yet.
    """

    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    direction: Literal["long", "short"]

    @property
    def return_pct(self) -> float:
        """Per-trade return as a fraction (e.g. 0.01 = +1%). Direction-
        signed: positive means the trade made money regardless of side."""
        if self.entry_price == 0:
            return 0.0
        raw = (self.exit_price - self.entry_price) / self.entry_price
        return raw if self.direction == "long" else -raw

    @property
    def is_win(self) -> bool:
        return self.return_pct > 0


def extract_closed_trades(close: pd.Series, signals: pd.Series) -> list[ClosedTrade]:
    """Walk a (close, signals) pair and emit one `ClosedTrade` per closed
    round-trip.

    This is the **single source of truth** for trade walking across the
    codebase. The validator's per-fold backtest, the GA fitness path, the
    on-demand backtest detail endpoint, and the trades-table all derive
    their per-trade information from this iteration so they can never
    disagree on what "a trade" was.

    Convention (matches live execution semantics):
        - Entry bar: the first bar where `signals` becomes non-zero
          coming from zero or from the opposite sign. The entry price
          is `close[entry_bar]`.
        - Exit bar: the first bar where `signals` flips to zero or to
          the opposite sign. The exit price is `close[exit_bar]`.
        - A flip from +1 to -1 (or -1 to +1) emits BOTH a close on the
          previous position AND an open on the new one at the same bar.
        - Positions still open at the end of the series are NOT emitted.

    Args:
        close: Per-bar close prices, indexed the same as `signals`.
        signals: Per-bar position series {-1, 0, +1}.
    """
    if len(close) != len(signals):
        raise ValueError("close and signals must have the same length")
    trades: list[ClosedTrade] = []
    if len(signals) == 0:
        return trades

    s = signals.fillna(0).astype(int).to_numpy()
    c = close.astype(float).to_numpy()

    prev_signal = 0
    entry_idx: int | None = None
    entry_price: float | None = None
    entry_signal = 0

    for i in range(len(s)):
        sig = int(s[i])
        if sig == prev_signal:
            continue

        # Close the existing position if any
        if prev_signal != 0 and entry_idx is not None and entry_price is not None:
            direction: Literal["long", "short"] = (
                "long" if entry_signal > 0 else "short"
            )
            trades.append(
                ClosedTrade(
                    entry_idx=entry_idx,
                    exit_idx=i,
                    entry_price=entry_price,
                    exit_price=float(c[i]),
                    direction=direction,
                )
            )
            entry_idx = None
            entry_price = None

        # Open a new position if the new signal is non-zero (covers both
        # flat→position and position→opposite flips).
        if sig != 0:
            entry_idx = i
            entry_price = float(c[i])
            entry_signal = sig

        prev_signal = sig

    return trades


def compute_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1.0 + returns).cumprod()


def sharpe(returns: pd.Series, trading_days_per_year: int = 252) -> float:
    # Resample to daily returns before annualizing to avoid inflating
    # the ratio with high-frequency autocorrelated bar returns.
    eq = (1.0 + returns).cumprod()
    if not isinstance(eq.index, pd.DatetimeIndex):
        # Fallback for non-datetime indices (e.g., unit tests with RangeIndex)
        mu = returns.mean() * trading_days_per_year
        sigma = returns.std(ddof=0) * np.sqrt(trading_days_per_year)
        return float(mu / sigma) if sigma > 0 else 0.0
    daily_returns = eq.resample("1D").last().pct_change().dropna()
    if daily_returns.empty or daily_returns.std(ddof=0) == 0:
        return 0.0
    mu = daily_returns.mean() * trading_days_per_year
    sigma = daily_returns.std(ddof=0) * np.sqrt(trading_days_per_year)
    return float(mu / sigma)


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def hit_rate(trade_returns: pd.Series) -> float:
    if trade_returns.empty:
        return 0.0
    wins = (trade_returns > 0).sum()
    return float(wins / len(trade_returns))


def per_trade_returns(returns: pd.Series, signals: pd.Series) -> pd.Series:
    """Collapse per-bar returns into one cumulative return per closed trade.

    A "trade" is a contiguous run of bars with the same non-zero signal
    (e.g. +1, +1, +1 → one long trade; -1, -1 → one short trade). Bars
    with signal=0 are flat and don't belong to any trade. Returns are
    compounded multiplicatively within each trade so the result matches
    (exit_price / entry_price - 1) modulo sign.

    This is the trade-level definition the rest of the system (Gate B's
    hit-rate threshold, the backtest endpoint's `capture_signals` block,
    trader UX intuition) assumes. The earlier `summarize()` was computing
    hit_rate from bar-level returns sampled at signal-flip points, which
    is a near-meaningless statistic for any opposite-signal-exit
    strategy. See plan addendum "Hit rate inconsistency between validator
    folds and re-backtest" for the root cause analysis.
    """
    if returns.empty or signals.empty:
        return pd.Series(dtype=float)
    s = signals.fillna(0)
    # New "group" starts each time the signal value changes. Consecutive
    # bars with the same signal share a group id.
    group_ids = s.ne(s.shift()).cumsum()
    in_trade = s != 0
    if not in_trade.any():
        return pd.Series(dtype=float)
    trade_rets = (1.0 + returns[in_trade]).groupby(group_ids[in_trade]).prod() - 1.0
    # If the final bar of the series is still in a non-zero position,
    # that group hasn't been closed yet — drop it. Hit rate and trade
    # count should reflect *realised* P&L only; an open position is not
    # a closed trade.
    if s.iloc[-1] != 0:
        last_group = int(group_ids.iloc[-1])
        if last_group in trade_rets.index:
            trade_rets = trade_rets.drop(last_group)
    return trade_rets


def turnover(signals: pd.Series) -> float:
    # Sum of absolute changes in position (per period)
    return float(signals.diff().abs().fillna(0).sum())


def _cagr(eq: pd.Series) -> float:
    if len(eq) <= 1 or eq.iloc[-1] <= 0:
        return 0.0
    if isinstance(eq.index, pd.DatetimeIndex):
        total_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400
        years = total_days / 365.25
    else:
        # Fallback: assume 252 trading days per year
        years = len(eq) / 252
    if years <= 0:
        return 0.0
    return float(eq.iloc[-1] ** (1.0 / years) - 1.0)


def summarize_from_trade_ledger(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, float]:
    """Trade-ledger metrics: how the configured strategy actually moved
    the account.

    Replaces the older bar-level Sharpe/MaxDD/CAGR/HitRate definitions
    that computed against a fictional unit-position return series. Those
    were useful for direction-only signal evaluation but disagreed
    wildly with what a real account would have done — the hero balance
    chip and the headline tiles were derived from different concepts of
    "performance" and routinely told contradictory stories.

    Inputs are the per-trade dicts that ``run_backtest_from_db``
    produces inside its ``capture_signals`` block (each carrying
    ``exit_timestamp``, ``net_pnl_dollars``, and the
    ``current_balance``-aware sizing applied at trade time). The
    function builds a daily equity series from realised net P&L, then
    derives Sharpe/MDD/CAGR/HitRate the way a brokerage statement
    would.

    Args:
        trades: List of trade dicts. Required keys: ``exit_timestamp``
            (ISO string or ``datetime``) and ``net_pnl_dollars`` (float).
        starting_balance: Account balance at the start of the window
            (before any trade).
        window_start: Start of the data window (used for CAGR
            annualisation — uses elapsed calendar time, not bar count).
        window_end: End of the data window.
    """
    if not trades:
        return {
            "sharpe": 0.0,
            "mdd": 0.0,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "cagr": 0.0,
            "n_trades": 0,
            "total_return": 0.0,
            "starting_balance": float(starting_balance),
            "ending_balance": float(starting_balance),
            "peak_balance": float(starting_balance),
        }

    def _parse_ts(v: Any) -> pd.Timestamp:
        if isinstance(v, pd.Timestamp):
            return v
        if isinstance(v, datetime):
            return pd.Timestamp(v)
        return pd.Timestamp(v)

    # Aggregate realised P&L by calendar day. Daily granularity matches
    # standard Sharpe-annualisation convention (sqrt(252)) and is the
    # right resolution for FX strategies that close many trades per day.
    daily_pnl: dict[pd.Timestamp, float] = {}
    for t in trades:
        ts = _parse_ts(t["exit_timestamp"]).normalize()
        daily_pnl[ts] = daily_pnl.get(ts, 0.0) + float(t["net_pnl_dollars"])

    start_ts = pd.Timestamp(window_start).normalize()
    end_ts = pd.Timestamp(window_end).normalize()
    # Strip tz so the index is comparable to the keys (which we already
    # normalised). Trade timestamps are UTC in production; for tests
    # with naive timestamps this is a no-op.
    if start_ts.tzinfo is not None:
        start_ts = start_ts.tz_convert("UTC").tz_localize(None)
    if end_ts.tzinfo is not None:
        end_ts = end_ts.tz_convert("UTC").tz_localize(None)
    daily_pnl = {
        (k.tz_convert("UTC").tz_localize(None) if k.tzinfo is not None else k): v
        for k, v in daily_pnl.items()
    }
    all_days = pd.date_range(start_ts, end_ts, freq="D")
    balance = float(starting_balance)
    equity_vals: list[float] = []
    for d in all_days:
        balance += daily_pnl.get(d, 0.0)
        equity_vals.append(balance)
    if not equity_vals:
        equity_vals = [float(starting_balance)]
        all_days = pd.DatetimeIndex([start_ts])
    eq = pd.Series(equity_vals, index=all_days, dtype=float)

    # Sharpe from daily equity returns, annualised √252.
    daily_returns = eq.pct_change().dropna()
    sharpe_val = 0.0
    if not daily_returns.empty:
        sigma = float(daily_returns.std(ddof=0))
        if sigma > 0:
            mu = float(daily_returns.mean())
            sharpe_val = (mu * 252.0) / (sigma * np.sqrt(252.0))

    # MaxDD as a negative fraction of peak equity, same convention the
    # existing `max_drawdown()` returns.
    peak = eq.cummax()
    # Guard against divide-by-zero if equity ever hits 0 (catastrophic
    # blowup — peak is still positive at that point so safe).
    dd_series = (eq - peak) / peak
    max_dd = float(dd_series.min())

    ending = float(eq.iloc[-1])
    peak_balance = float(peak.iloc[-1])
    total_return = (ending - starting_balance) / starting_balance

    # CAGR: annualise the total realised return over elapsed calendar
    # time. Clamp years to >= 1 day to avoid blowing up on very short
    # windows (a 1-trade single-day fold becomes a huge CAGR otherwise).
    elapsed_days = (window_end - window_start).total_seconds() / 86400.0
    years = max(elapsed_days / 365.25, 1.0 / 365.25)
    cagr_val = 0.0
    if ending > 0:
        cagr_val = (ending / float(starting_balance)) ** (1.0 / years) - 1.0
    else:
        cagr_val = -1.0  # account was wiped out

    # Hit rate: trades that paid AFTER costs (net), not gross. A +2 pip
    # gross trade that paid 1.5 pip in spread is NOT a winning trade in
    # the trader's mental model.
    n_wins = sum(1 for t in trades if float(t.get("net_pnl_dollars", 0.0)) > 0)
    hit = n_wins / len(trades)

    return {
        "sharpe": float(sharpe_val),
        "mdd": max_dd,
        "max_drawdown": max_dd,
        "hit_rate": float(hit),
        "cagr": float(cagr_val),
        "n_trades": len(trades),
        "total_return": float(total_return),
        "starting_balance": float(starting_balance),
        "ending_balance": ending,
        "peak_balance": peak_balance,
    }


def summarize(
    returns: pd.Series,
    signals: pd.Series,
    *,
    close: pd.Series | None = None,
) -> dict[str, float]:
    """Aggregate metrics for a backtest run.

    Trade-level fields (`hit_rate`, `n_trades`) are computed via
    `extract_closed_trades(close, signals)` when `close` is supplied —
    the same trade walker the `capture_signals=True` block uses, so the
    two code paths can never disagree on what a "trade" is.

    When `close` is omitted (legacy callers / unit tests that don't
    have price data handy), trade-level fields fall back to
    `per_trade_returns(returns, signals)`. That fallback uses the
    no-lookahead convention (entry one bar before the signal first
    appears) and produces slightly different numbers — fine for tests,
    but production paths should always pass `close`.
    """
    eq = equity_curve(returns)
    mdd_value = max_drawdown(eq)

    if close is not None:
        trades = extract_closed_trades(close, signals)
        n = len(trades)
        if n == 0:
            hr = 0.0
        else:
            wins = sum(1 for t in trades if t.is_win)
            hr = wins / n
    else:
        trade_rets = per_trade_returns(returns, signals)
        n = len(trade_rets)
        hr = hit_rate(trade_rets)

    return {
        "sharpe": sharpe(returns),
        "mdd": mdd_value,
        "max_drawdown": mdd_value,  # Alias for Gate B compatibility
        "hit_rate": hr,
        "turnover": turnover(signals),
        "cagr": _cagr(eq),
        "n_trades": int(n),
    }
