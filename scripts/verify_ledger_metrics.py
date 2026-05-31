"""Manual verification of trade-ledger metric alignment.

Runs the same `run_backtest_from_db` the validator + endpoint use,
then re-derives the same Sharpe/CAGR/MaxDD numbers a person would
compute by hand from the trade ledger. Asserts they agree to
floating-point tolerance.

Throwaway — kept in scripts/ so the user can re-run after any future
metric-pipeline change to confirm alignment didn't drift.

Usage (inside backend-uat container):
    python scripts/verify_ledger_metrics.py
"""

from __future__ import annotations

from datetime import datetime, timezone, UTC

import numpy as np
import pandas as pd

from agentfx.core.dsl.seeds import SEED_SPECS
from agentfx.services.backtest import run_backtest_from_db


UTC = UTC


def banner(s: str) -> None:
    print()
    print("=" * 72)
    print(s)
    print("=" * 72)


def run_one_check(
    spec,
    label: str,
    start: datetime,
    end: datetime,
    instrument: str = "EUR_USD",
    granularity: str = "M1",
    account_balance: float = 10_000.0,
    spread_pips: float = 1.0,
) -> None:
    banner(f"{label}  ({instrument}:{granularity}  {start.date()} → {end.date()})")

    result = run_backtest_from_db(
        instrument=instrument,
        granularity=granularity,
        start=start,
        end=end,
        strategy_name="dsl",
        strategy_params={"spec": spec.model_dump(mode="json")},
        account_balance=account_balance,
        leverage=50,
        spread_pips=spread_pips,
        capture_signals=True,
    )

    metrics = result.get("metrics", {})
    sizing = result.get("sizing", {})
    trades = result.get("trades", [])

    print(
        f"n_trades={len(trades)}  sharpe={metrics.get('sharpe'):.4f}  "
        f"max_dd={metrics.get('max_drawdown'):.4f}  "
        f"cagr={metrics.get('cagr'):.4f}  hit_rate={metrics.get('hit_rate'):.4f}  "
        f"total_return={metrics.get('total_return'):.4f}"
    )
    if sizing:
        print(
            f"sizing: start=${sizing.get('starting_balance'):.2f}  "
            f"end=${sizing.get('ending_balance'):.2f}  "
            f"peak=${sizing.get('peak_balance'):.2f}"
        )

    if not trades:
        print("  → no trades produced, skipping ledger reconciliation")
        return

    # ── Independently reconstruct the ledger metrics from trades[] ──
    daily_pnl: dict[pd.Timestamp, float] = {}
    for t in trades:
        ts = pd.Timestamp(t["exit_timestamp"]).normalize()
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        daily_pnl[ts] = daily_pnl.get(ts, 0.0) + float(t["net_pnl_dollars"])

    s_norm = pd.Timestamp(start).normalize()
    e_norm = pd.Timestamp(end).normalize()
    if s_norm.tzinfo is not None:
        s_norm = s_norm.tz_convert("UTC").tz_localize(None)
    if e_norm.tzinfo is not None:
        e_norm = e_norm.tz_convert("UTC").tz_localize(None)
    days = pd.date_range(s_norm, e_norm, freq="D")
    bal = account_balance
    eq = []
    for d in days:
        bal += daily_pnl.get(d, 0.0)
        eq.append(bal)
    eq_series = pd.Series(eq, index=days)
    ending = float(eq_series.iloc[-1])

    indep_total_return = (ending - account_balance) / account_balance
    elapsed = (end - start).total_seconds() / 86400.0
    years = max(elapsed / 365.25, 1 / 365.25)
    indep_cagr = (ending / account_balance) ** (1 / years) - 1 if ending > 0 else -1
    peak = eq_series.cummax()
    indep_mdd = float(((eq_series - peak) / peak).min())
    dr = eq_series.pct_change().dropna()
    indep_sharpe = (
        float(dr.mean() * 252 / (dr.std(ddof=0) * np.sqrt(252)))
        if dr.std(ddof=0) > 0
        else 0.0
    )
    indep_hit = sum(1 for t in trades if float(t.get("net_pnl_dollars", 0)) > 0) / len(
        trades
    )

    print(
        f"INDEPENDENT recon:  sharpe={indep_sharpe:.4f}  max_dd={indep_mdd:.4f}  "
        f"cagr={indep_cagr:.4f}  hit_rate={indep_hit:.4f}  "
        f"total_return={indep_total_return:.4f}"
    )

    deltas = {
        "sharpe": abs(metrics["sharpe"] - indep_sharpe),
        "max_drawdown": abs(metrics["max_drawdown"] - indep_mdd),
        "cagr": abs(metrics["cagr"] - indep_cagr),
        "hit_rate": abs(metrics["hit_rate"] - indep_hit),
        "total_return": abs(metrics["total_return"] - indep_total_return),
    }
    max_delta = max(deltas.values())
    status = "OK" if max_delta < 1e-9 else "MISMATCH"
    print(f"  → max delta vs independent recon: {max_delta:.2e}  [{status}]")
    if max_delta >= 1e-9:
        for k, v in deltas.items():
            if v > 1e-9:
                print(f"     ! {k}: {v:.4e}")


def main() -> None:
    banner("Trade-ledger metric verification")
    print("Verifies that run_backtest_from_db's Sharpe/MaxDD/CAGR/hit_rate")
    print("for each fold equal what you'd derive by hand from trades[].")

    seed = SEED_SPECS[0]
    print(f"\nUsing seed[0]: {seed.model_dump_json()[:140]}…")

    # ── Test 1: 1y window, the 4-fold split the GA + validator use ──
    win_start = datetime(2024, 7, 1, tzinfo=UTC)
    win_end = datetime(2025, 7, 1, tzinfo=UTC)
    fold_starts = pd.date_range(win_start, win_end, periods=5).to_list()
    fold_labels = ["Fold 0 (train)", "Fold 1 (train)", "Fold 2 (train)", "Fold 3 (OOS)"]
    for i, label in enumerate(fold_labels):
        run_one_check(
            seed,
            label,
            start=fold_starts[i].to_pydatetime(),
            end=fold_starts[i + 1].to_pydatetime(),
        )

    # ── Test 2: Training window (folds 0..K-2 concatenated) ──
    run_one_check(
        seed,
        "Concatenated training window (folds 0..K-2)",
        start=fold_starts[0].to_pydatetime(),
        end=fold_starts[3].to_pydatetime(),
    )

    # ── Test 3: Full window ──
    run_one_check(
        seed,
        "Full window (folds 0..K-1)",
        start=win_start,
        end=win_end,
    )

    banner("Done. All [OK] above = ledger reconciliation passes end-to-end.")


if __name__ == "__main__":
    main()
