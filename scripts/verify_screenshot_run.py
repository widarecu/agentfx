"""Replay the user's screenshot run (evorun_20260529_43b2a92a, genome
genome_gen_evorun_20260529_43b2a92a_006_001) through the new
ledger-metric pipeline and print what the three surfaces SHOULD show
after my changes:

  1. Hero "ending_balance" + "total_return"
  2. CAGR / Sharpe / MaxDD tiles (post-ledger-fix)
  3. WF table on a fresh re-validation (which would persist these values)

Then explicitly confirm Hero ↔ Tile alignment by computing the
annualised CAGR from the hero's realised balance change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, UTC

from agentfx.api.core.database import SessionLocal
from agentfx.api.models.evolution import StrategyGenome
from agentfx.core.dsl.grammar import StrategySpec
from agentfx.services.backtest import run_backtest_from_db


UTC = UTC


def banner(s: str) -> None:
    print("=" * 72)
    print(s)
    print("=" * 72)


def fmt_pct(v):
    if v is None:
        return "    n/a"
    return f"{v * 100:+7.2f}%"


def main() -> None:
    genome_id = "genome_gen_evorun_20260529_43b2a92a_006_001"
    instrument = "EUR_USD"
    granularity = "M1"
    account_balance = 10_000.0
    spread_pips = 1.0  # pinned from config_json.effective_spread_pips_per_pair
    leverage = 50

    db = SessionLocal()
    try:
        genome = db.query(StrategyGenome).filter_by(id=genome_id).first()
        if genome is None:
            print(f"genome {genome_id} not found")
            return
        spec_dict = genome.dsl_json
    finally:
        db.close()

    spec = StrategySpec.model_validate(spec_dict)
    print()
    banner(f"Replay {genome_id} through the new ledger pipeline")
    print(f"DSL: {json.dumps(spec_dict)[:140]}…")
    print("Inputs: balance=$10k  leverage=50  spread=1.0 pips")

    # Slice definitions match the validator's 4-fold split on the run's
    # effective_data_window 2024-07-01 → 2025-07-01.
    folds = [
        (
            "Fold 0 (train)",
            datetime(2024, 7, 1, tzinfo=UTC),
            datetime(2024, 9, 30, tzinfo=UTC),
        ),
        (
            "Fold 1 (train)",
            datetime(2024, 9, 30, tzinfo=UTC),
            datetime(2024, 12, 30, tzinfo=UTC),
        ),
        (
            "Fold 2 (train)",
            datetime(2024, 12, 30, tzinfo=UTC),
            datetime(2025, 3, 31, tzinfo=UTC),
        ),
        (
            "Fold 3 (OOS)  ",
            datetime(2025, 3, 31, tzinfo=UTC),
            datetime(2025, 7, 1, tzinfo=UTC),
        ),
    ]

    print()
    print(
        f"{'FOLD':<16} {'TRADES':>7} {'SHARPE':>8} {'MAX_DD':>8} "
        f"{'CAGR':>8} {'HIT':>6} {'NET_RET':>8} {'END_BAL':>10}"
    )
    print("-" * 80)
    for label, start, end in folds:
        result = run_backtest_from_db(
            instrument=instrument,
            granularity=granularity,
            start=start,
            end=end,
            strategy_name="dsl",
            strategy_params={"spec": spec_dict},
            account_balance=account_balance,
            leverage=leverage,
            spread_pips=spread_pips,
            capture_signals=True,
        )
        m = result["metrics"]
        sz = result["sizing"]
        end_bal = sz.get("ending_balance", account_balance)
        print(
            f"{label:<16} {m['n_trades']:>7} {m['sharpe']:>8.2f} "
            f"{fmt_pct(m['max_drawdown'])} {fmt_pct(m['cagr'])} "
            f"{fmt_pct(m['hit_rate'])} {fmt_pct(m['total_return'])} ${end_bal:>9.2f}"
        )

    # ── Explicit cross-check on the OOS fold (the one in the screenshot) ──
    print()
    banner("OOS-fold internal consistency cross-check")
    print("Re-running OOS fold only to confirm Hero ↔ Tile alignment.")

    result = run_backtest_from_db(
        instrument=instrument,
        granularity=granularity,
        start=folds[3][1],
        end=folds[3][2],
        strategy_name="dsl",
        strategy_params={"spec": spec_dict},
        account_balance=account_balance,
        leverage=leverage,
        spread_pips=spread_pips,
        capture_signals=True,
    )
    m = result["metrics"]
    sz = result["sizing"]

    elapsed_days = (folds[3][2] - folds[3][1]).total_seconds() / 86400
    years = elapsed_days / 365.25
    start_bal = sz["starting_balance"]
    end_bal = sz["ending_balance"]
    realised_return = (end_bal - start_bal) / start_bal
    if end_bal > 0:
        annualised_from_balance = (end_bal / start_bal) ** (1 / years) - 1
    else:
        annualised_from_balance = -1.0

    print()
    print(
        f"  Hero balance:    ${start_bal:.2f} → ${end_bal:.2f} ({realised_return * 100:+.2f}%)"
    )
    print(f"  Tile total_return:                  {fmt_pct(m['total_return'])}")
    print(f"  Tile CAGR:                          {fmt_pct(m['cagr'])}")
    print(f"  Independent annualised from hero:   {fmt_pct(annualised_from_balance)}")
    print()

    delta = abs(m["cagr"] - annualised_from_balance)
    delta_tr = abs(m["total_return"] - realised_return)
    print(f"  Δ(tile_cagr - hero_annualised)    = {delta:.2e}")
    print(f"  Δ(tile_total_return - hero pct)   = {delta_tr:.2e}")
    if delta < 1e-9 and delta_tr < 1e-9:
        print()
        print("  ✓ HERO ↔ TILES are mathematically aligned.")
    else:
        print()
        print("  ✗ MISMATCH — investigate.")


if __name__ == "__main__":
    main()
