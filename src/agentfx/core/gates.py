"""Gate B threshold evaluation for strategy promotion.

Gate B is the quality bar a strategy must clear before it can move toward
paper or live trading. The thresholds and evaluation function live here so
they can be reused by:

- the v0.2.0 BayesianOptimizerAgent (informational display per trial)
- the v0.3.0 ValidatorAgent (enforcement on the held-out OOS fold)
- any future agent that judges strategy fitness against a fixed bar

Thresholds match `planning/MVP_CONSTRAINTS.md`. Changing them is a
roadmap-level decision and should go through `planning/DECISIONS.md`.
"""

from __future__ import annotations

GATE_B_THRESHOLDS: dict[str, float] = {
    "sharpe_min": 1.0,
    "max_drawdown": 0.10,  # 10%
    "hit_rate_min": 0.48,  # 48%
    "min_trades": 300,
    "min_cagr": 0.15,  # 15% net annualised return after spread + swap costs
}


def evaluate_gate_b(
    metrics: dict,
    *,
    overrides: dict[str, float] | None = None,
) -> tuple[bool, list[str]]:
    """Evaluate whether the given metrics pass all Gate B thresholds.

    Args:
        metrics: Performance metrics from a backtest. Recognised keys:
            - "sharpe": Sharpe ratio (default 0 if missing)
            - "max_drawdown": Max drawdown as a fraction (default 1.0 if missing)
            - "hit_rate": Win rate as a fraction (default 0 if missing)
            - "n_trades": Trade count (default 0 if missing)
            - "cagr": Net annualised return as a fraction (default -1.0
              if missing — treats absence as definitely-below-floor)
        overrides: Optional per-run threshold overrides. Keys not present
            fall back to the module-level GATE_B_THRESHOLDS. Used by the
            orchestrator/validator to honour per-run config knobs such as
            min_cagr_pct without mutating the global default.

    Returns:
        Tuple of (passes: bool, failures: list[str] of human-readable reasons).
        `passes` is True iff all thresholds are met. `failures` is empty when
        passes is True, otherwise contains one message per breached threshold.
    """
    thresholds = {**GATE_B_THRESHOLDS, **overrides} if overrides else GATE_B_THRESHOLDS
    failures: list[str] = []

    sharpe = metrics.get("sharpe", 0)
    if sharpe < thresholds["sharpe_min"]:
        failures.append(f"Sharpe {sharpe:.2f} < {thresholds['sharpe_min']}")

    max_dd = metrics.get("max_drawdown", 1.0)
    if max_dd > thresholds["max_drawdown"]:
        failures.append(
            f"Max DD {abs(max_dd) * 100:.1f}% > cap {thresholds['max_drawdown'] * 100:.0f}%"
        )

    hit_rate = metrics.get("hit_rate", 0)
    if hit_rate < thresholds["hit_rate_min"]:
        failures.append(
            f"Hit Rate {hit_rate * 100:.1f}% < {thresholds['hit_rate_min'] * 100}%"
        )

    trade_count = metrics.get("n_trades", 0)
    if trade_count < thresholds["min_trades"]:
        failures.append(f"Trade Count {trade_count} < {thresholds['min_trades']}")

    # CAGR is the net annualised return of the rets-based equity curve
    # (already includes spread + slippage + swap costs). The default
    # value of -1.0 makes a missing metric fail by construction.
    cagr = metrics.get("cagr", -1.0)
    if cagr < thresholds["min_cagr"]:
        failures.append(
            f"CAGR {cagr * 100:+.1f}% < {thresholds['min_cagr'] * 100:.0f}%"
        )

    return len(failures) == 0, failures


__all__ = ["GATE_B_THRESHOLDS", "evaluate_gate_b"]
