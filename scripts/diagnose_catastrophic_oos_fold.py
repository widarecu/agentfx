"""Diagnose what changed between adjacent OOS folds — read-only.

For the genome the user flagged on 2026-06-10, OOS-2 produced Sharpe +4.67
and OOS-3 produced Sharpe -7.15 across two adjacent 22-day windows. The
regime tagger labelled both as "low-vol-choppy" — clearly that labelling
is too coarse to catch the structural change. This script pulls per-fold
candle data and computes a panel of regime features beyond the two we
already store, so we can see what actually broke.

Run via the UAT stack:

    docker compose --profile uat exec backend-uat python \\
        scripts/diagnose_catastrophic_oos_fold.py

Outputs a table of per-fold features. Read-only: no DB writes, no model
changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

GENOME_ID = "genome_gen_evorun_20260610_3bc80a6a_001_010"


def _annualised_vol(returns, granularity: str = "M10") -> float:
    bars_per_year = 252 * {"M1": 1440, "M10": 144, "M30": 48, "H1": 24}.get(
        granularity, 144
    )
    if len(returns) < 2:
        return 0.0
    return float(returns.std() * (bars_per_year**0.5))


def _trend_strength(closes) -> float:
    if len(closes) < 2:
        return 0.0
    net = abs(float(closes.iloc[-1]) - float(closes.iloc[0]))
    path = float(closes.diff().abs().sum())
    return net / path if path > 0 else 0.0


def _avg_true_range(df, n: int = 14) -> float:
    """Simple ATR over the slice — proxy for typical bar size."""
    if len(df) < n + 1:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = (
        (high - low)
        .combine(abs(high - prev_close), max)
        .combine(abs(low - prev_close), max)
    )
    return float(tr.rolling(n).mean().iloc[-1])


def _max_run_directional(closes) -> tuple[int, int]:
    """Longest run of consecutive up bars and down bars.

    A choppy regime alternates frequently → short runs. A reversal regime
    has one long run in one direction then a long run in the other — easy
    to see if you look at run lengths.
    """
    if len(closes) < 2:
        return 0, 0
    diffs = closes.diff().dropna()
    up_runs = []
    down_runs = []
    cur_dir = 0
    cur_len = 0
    for d in diffs.values:
        new_dir = 1 if d > 0 else (-1 if d < 0 else 0)
        if new_dir == 0:
            continue
        if new_dir == cur_dir:
            cur_len += 1
        else:
            if cur_dir == 1:
                up_runs.append(cur_len)
            elif cur_dir == -1:
                down_runs.append(cur_len)
            cur_dir = new_dir
            cur_len = 1
    if cur_dir == 1:
        up_runs.append(cur_len)
    elif cur_dir == -1:
        down_runs.append(cur_len)
    return (
        max(up_runs) if up_runs else 0,
        max(down_runs) if down_runs else 0,
    )


def _path_curvature(closes) -> float:
    """A simple measure of how 'wiggly' the close path is. Compare the
    distance walked to the distance covered. Higher = more reversals.

    Ratio of std(close) to abs(end - start). Pure linear = 0. Pure
    oscillation = unbounded.
    """
    if len(closes) < 2:
        return 0.0
    net = abs(float(closes.iloc[-1]) - float(closes.iloc[0]))
    sd = float(closes.std())
    if net == 0:
        return float("inf") if sd > 0 else 0.0
    return sd / net


def _fold_features(df) -> dict[str, Any]:
    if len(df) < 2:
        return {}
    returns = df["close"].pct_change().dropna()
    closes = df["close"]
    up_run, down_run = _max_run_directional(closes)
    return {
        "bars": len(df),
        "ann_vol": _annualised_vol(returns) * 100,  # %
        "trend": _trend_strength(closes),
        "atr_pips_14": _avg_true_range(df) * 10000,  # convert to pips
        "max_up_run": up_run,
        "max_down_run": down_run,
        "curvature": _path_curvature(closes),
        "net_move_pips": (float(closes.iloc[-1]) - float(closes.iloc[0])) * 10000,
        "max_range_pips": (float(df["high"].max()) - float(df["low"].min())) * 10000,
    }


def main() -> None:
    # Lazy imports so the script can be discovered without the SQLAlchemy
    # session being live in a fresh shell.
    from sqlalchemy import text

    from agentfx.api.core.database import SessionLocal
    from agentfx.services.backtest import load_price_df_from_db

    db = SessionLocal()
    try:
        rows = (
            db.execute(
                text(
                    "SELECT fold_index, is_oos, fold_start, fold_end, "
                    "sharpe, hit_rate, n_trades, regime_volatility, regime_trend "
                    "FROM validation_folds WHERE genome_id = :g "
                    "ORDER BY fold_index"
                ),
                {"g": GENOME_ID},
            )
            .mappings()
            .all()
        )
    finally:
        db.close()

    if not rows:
        print(f"No folds for {GENOME_ID}")
        return

    header = (
        f"{'Fold':<5} {'Type':<7} {'Sharpe':>7} {'Hit':>6} {'Trades':>7} "
        f"{'StoredRegime':<18}"
        f"{'Vol%':>8} {'Trend':>7} {'ATRpips':>9} {'UpRun':>6} {'DnRun':>6} "
        f"{'Curve':>7} {'NetMove':>9} {'MaxRange':>9}"
    )
    print(header)
    print("-" * len(header))

    deltas: list[dict[str, Any]] = []
    prev_features: dict[str, Any] | None = None
    prev_label: str | None = None

    for r in rows:
        df = load_price_df_from_db(
            instrument="EUR_USD",
            granularity="M10",
            start=r["fold_start"],
            end=r["fold_end"],
        )
        feats = _fold_features(df)
        kind = "OOS" if r["is_oos"] else "TRAIN"
        label = (
            f"{r['regime_volatility']}-vol-{r['regime_trend']}"
            if r["regime_volatility"]
            else "—"
        )
        print(
            f"{r['fold_index']:<5} {kind:<7} {r['sharpe']:>7.2f} "
            f"{r['hit_rate']:>6.3f} {r['n_trades']:>7} {label:<18}"
            f"{feats.get('ann_vol', 0):>8.2f} {feats.get('trend', 0):>7.3f} "
            f"{feats.get('atr_pips_14', 0):>9.2f} {feats.get('max_up_run', 0):>6} "
            f"{feats.get('max_down_run', 0):>6} {feats.get('curvature', 0):>7.2f} "
            f"{feats.get('net_move_pips', 0):>+9.1f} {feats.get('max_range_pips', 0):>9.1f}"
        )
        deltas.append(
            {
                "fold": r["fold_index"],
                "kind": kind,
                "sharpe": float(r["sharpe"] or 0),
                **feats,
            }
        )
        prev_features = feats
        prev_label = label

    # Side-by-side OOS-2 (Sharpe +4.67) vs OOS-3 (Sharpe -7.15)
    print()
    print("=" * 78)
    print("Catastrophic transition: OOS-2 vs OOS-3 (Sharpe +4.67 → -7.15)")
    print("=" * 78)
    oos2 = next(d for d in deltas if d["fold"] == 4)
    oos3 = next(d for d in deltas if d["fold"] == 5)
    keys = (
        "ann_vol",
        "trend",
        "atr_pips_14",
        "max_up_run",
        "max_down_run",
        "curvature",
        "net_move_pips",
        "max_range_pips",
    )
    print(f"{'Feature':<20}{'OOS-2 (+4.67)':>18}{'OOS-3 (-7.15)':>18}{'Δ':>15}")
    print("-" * 71)
    for k in keys:
        v2 = oos2.get(k, 0.0)
        v3 = oos3.get(k, 0.0)
        delta = v3 - v2
        delta_pct = (delta / v2 * 100) if v2 != 0 else 0
        print(f"{k:<20}{v2:>18.3f}{v3:>18.3f}{delta:>+10.3f} ({delta_pct:+.0f}%)")

    # OOS group summary: what's consistent in good OOS folds vs bad?
    print()
    print("=" * 78)
    print("Good OOS folds (Sharpe > 0) vs Bad OOS folds (Sharpe < 0) — averages")
    print("=" * 78)
    oos_only = [d for d in deltas if d["kind"] == "OOS"]
    good = [d for d in oos_only if d["sharpe"] > 0]
    bad = [d for d in oos_only if d["sharpe"] < 0]
    if good and bad:
        print(
            f"  Good folds: {[d['fold'] for d in good]} (mean Sharpe {np.mean([d['sharpe'] for d in good]):+.2f})"
        )
        print(
            f"  Bad folds:  {[d['fold'] for d in bad]} (mean Sharpe {np.mean([d['sharpe'] for d in bad]):+.2f})"
        )
        print()
        print(f"{'Feature':<20}{'Good avg':>15}{'Bad avg':>15}{'Δ':>15}")
        print("-" * 65)
        for k in keys:
            g_mean = np.mean([d.get(k, 0) for d in good])
            b_mean = np.mean([d.get(k, 0) for d in bad])
            print(f"{k:<20}{g_mean:>15.3f}{b_mean:>15.3f}{b_mean - g_mean:>+10.3f}")


if __name__ == "__main__":
    main()
