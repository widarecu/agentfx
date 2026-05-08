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
}


def evaluate_gate_b(metrics: dict) -> tuple[bool, list[str]]:
    """Evaluate whether the given metrics pass all Gate B thresholds.

    Args:
        metrics: Performance metrics from a backtest. Recognised keys:
            - "sharpe": Sharpe ratio (default 0 if missing)
            - "max_drawdown": Max drawdown as a fraction (default 1.0 if missing)
            - "hit_rate": Win rate as a fraction (default 0 if missing)
            - "n_trades": Trade count (default 0 if missing)

    Returns:
        Tuple of (passes: bool, failures: list[str] of human-readable reasons).
        `passes` is True iff all thresholds are met. `failures` is empty when
        passes is True, otherwise contains one message per breached threshold.
    """
    failures: list[str] = []

    sharpe = metrics.get("sharpe", 0)
    if sharpe < GATE_B_THRESHOLDS["sharpe_min"]:
        failures.append(f"Sharpe {sharpe:.2f} < {GATE_B_THRESHOLDS['sharpe_min']}")

    max_dd = metrics.get("max_drawdown", 1.0)
    if max_dd > GATE_B_THRESHOLDS["max_drawdown"]:
        failures.append(
            f"Max DD {max_dd * 100:.1f}% > {GATE_B_THRESHOLDS['max_drawdown'] * 100}%"
        )

    hit_rate = metrics.get("hit_rate", 0)
    if hit_rate < GATE_B_THRESHOLDS["hit_rate_min"]:
        failures.append(
            f"Hit Rate {hit_rate * 100:.1f}% < {GATE_B_THRESHOLDS['hit_rate_min'] * 100}%"
        )

    trade_count = metrics.get("n_trades", 0)
    if trade_count < GATE_B_THRESHOLDS["min_trades"]:
        failures.append(
            f"Trade Count {trade_count} < {GATE_B_THRESHOLDS['min_trades']}"
        )

    return len(failures) == 0, failures


__all__ = ["GATE_B_THRESHOLDS", "evaluate_gate_b"]
