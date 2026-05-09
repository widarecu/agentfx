"""Fitness scoring tests with a mocked backtest function."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.agents.evolution.fitness import (
    DIVERSITY_PENALTY_FACTOR,
    GATE_B_FAILURE_DILUTION,
    compute_fitness,
    is_near_elite,
    spec_edit_distance,
)
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    ma_crossover_seed,
)


def _gate_passing_metrics(sharpe: float = 1.5) -> dict:
    """Metrics that pass Gate B (Sharpe>=1.0, MDD<=10%, hit_rate>=48%, n_trades>=300)."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 500,
    }


def _gate_failing_metrics(sharpe: float = 1.5) -> dict:
    """Metrics that pass on Sharpe but fail on trade count (insufficient trades)."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 100,
    }


def _make_backtest_mock(metrics: dict) -> MagicMock:
    mock = MagicMock(return_value={"metrics": metrics})
    return mock


# ─────────────────────────────────────────────────────────────────────────
# Edit distance
# ─────────────────────────────────────────────────────────────────────────


class TestSpecEditDistance:
    def test_identical_specs_zero_distance(self) -> None:
        assert spec_edit_distance(MA_CROSSOVER_SEED, MA_CROSSOVER_SEED) == 0

    def test_different_seeds_have_positive_distance(self) -> None:
        # MA vs RSI seeds use different indicators — must register a diff
        d = spec_edit_distance(MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED)
        assert d > 0

    def test_distance_grows_with_dissimilarity(self) -> None:
        # MA(20,50) vs MA(5,10) differ only in arg values; should be smaller
        # than MA vs Donchian which differ in indicator names.
        ma_alt = ma_crossover_seed(fast=5, slow=10)
        small_diff = spec_edit_distance(MA_CROSSOVER_SEED, ma_alt)
        big_diff = spec_edit_distance(MA_CROSSOVER_SEED, DONCHIAN_BREAKOUT_SEED)
        assert small_diff > 0
        assert big_diff > small_diff


class TestIsNearElite:
    def test_no_elites_returns_false(self) -> None:
        assert is_near_elite(MA_CROSSOVER_SEED, [], threshold=2) is False

    def test_identical_to_elite_is_near(self) -> None:
        assert is_near_elite(MA_CROSSOVER_SEED, [MA_CROSSOVER_SEED]) is True

    def test_distant_from_all_elites_is_not_near(self) -> None:
        # MA vs Donchian/RSI: enough leaves differ to exceed default threshold
        assert (
            is_near_elite(
                MA_CROSSOVER_SEED,
                [DONCHIAN_BREAKOUT_SEED, RSI_MEAN_REVERSION_SEED],
                threshold=2,
            )
            is False
        )


# ─────────────────────────────────────────────────────────────────────────
# Fitness scoring
# ─────────────────────────────────────────────────────────────────────────


class TestComputeFitness:
    def test_passes_through_sharpe_when_gate_b_passes_and_no_elites(self) -> None:
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.5))
        fitness, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert fitness == pytest.approx(2.5)
        assert metrics["sharpe"] == 2.5

    def test_calls_backtest_with_dsl_dispatch(self) -> None:
        backtest = _make_backtest_mock(_gate_passing_metrics())
        compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            granularity="M5",
            backtest_fn=backtest,
        )
        # Verify backtest was called with strategy_name="dsl" and a spec dict
        kwargs = backtest.call_args.kwargs
        assert kwargs["instrument"] == "EUR_USD"
        assert kwargs["granularity"] == "M5"
        assert kwargs["strategy_name"] == "dsl"
        assert "spec" in kwargs["strategy_params"]
        assert kwargs["strategy_params"]["spec"]["version"] == 1

    def test_extra_backtest_kwargs_forwarded(self) -> None:
        backtest = _make_backtest_mock(_gate_passing_metrics())
        compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            account_balance=20000.0,
            position_size=5000,
            spread_pips=0.3,
        )
        kwargs = backtest.call_args.kwargs
        assert kwargs["account_balance"] == 20000.0
        assert kwargs["position_size"] == 5000
        assert kwargs["spread_pips"] == 0.3

    def test_gate_b_failure_dilutes_positive_fitness(self) -> None:
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=1.8))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert fitness == pytest.approx(1.8 * GATE_B_FAILURE_DILUTION)

    def test_gate_b_failure_does_not_alter_negative_fitness(self) -> None:
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=-0.5))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # Negative fitness shouldn't be "diluted upward" by a multiplier
        assert fitness == pytest.approx(-0.5)

    def test_diversity_penalty_applied_when_near_elite(self) -> None:
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[MA_CROSSOVER_SEED],  # same spec → near-elite
        )
        assert fitness == pytest.approx(2.0 * DIVERSITY_PENALTY_FACTOR)

    def test_no_diversity_penalty_when_distant_from_elites(self) -> None:
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[DONCHIAN_BREAKOUT_SEED, RSI_MEAN_REVERSION_SEED],
        )
        assert fitness == pytest.approx(2.0)

    def test_combined_gate_b_and_diversity_penalties(self) -> None:
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[MA_CROSSOVER_SEED],
        )
        # Both penalties apply: 2.0 * 0.5 (gate B) * 0.5 (diversity) = 0.5
        expected = 2.0 * GATE_B_FAILURE_DILUTION * DIVERSITY_PENALTY_FACTOR
        assert fitness == pytest.approx(expected)

    def test_missing_sharpe_returns_neg_inf(self) -> None:
        backtest = MagicMock(return_value={"metrics": {}})
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert fitness == float("-inf")

    def test_metrics_returned_verbatim(self) -> None:
        # Caller persists these on the genome row — they must round-trip
        # without modification.
        rich_metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
            "total_return": 0.32,
            "profit_factor": 2.1,
            "extra_field": "preserved",
        }
        backtest = MagicMock(return_value={"metrics": rich_metrics})
        _, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert metrics == rich_metrics
