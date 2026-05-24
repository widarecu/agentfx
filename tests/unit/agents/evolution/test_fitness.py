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
    """Metrics that pass Gate B (Sharpe>=1.0, MDD<=10%, hit_rate>=48%, n_trades>=300, cagr>=15%)."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 500,
        "cagr": 0.20,
    }


def _gate_failing_metrics(sharpe: float = 1.5) -> dict:
    """Metrics that pass on Sharpe but fail on trade count (insufficient trades)."""
    return {
        "sharpe": sharpe,
        "max_drawdown": 0.05,
        "hit_rate": 0.55,
        "n_trades": 100,
        "cagr": 0.20,
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

    def test_gate_b_failure_applies_trade_shortfall_penalty(self) -> None:
        # n_trades=100 vs min=300 → shortfall_ratio = 2/3 → -6.667 additive
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=1.8))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # 1.8 raw - (2/3 * 10.0 shortfall) = -4.867. Negative now, so the
        # Gate B *dilution* doesn't apply (only halves positive fitness).
        expected = 1.8 - (200 / 300) * 10.0
        assert fitness == pytest.approx(expected)

    def test_gate_b_failure_compounds_with_negative_fitness(self) -> None:
        # Negative sharpe AND insufficient trades — both penalties stack
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=-0.5))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # -0.5 raw + (-6.667 shortfall) = -7.167
        expected = -0.5 - (200 / 300) * 10.0
        assert fitness == pytest.approx(expected)

    def test_zero_trades_degenerate_genome_strongly_penalized(self) -> None:
        """Regression test for v0.3.0 acceptance runs #5 + #6:

        A DSL genome where long and short conditions are identical never
        triggers any trades. The trade-shortfall penalty alone proved too
        weak (a zero-trade genome at -10 still beat a 100-trade losing
        strategy at -21). The fix is a hard penalty floor of -100 for
        any genome with literally zero trades — they should ALWAYS lose
        tournaments to active strategies."""
        zero_trade_metrics = {
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "n_trades": 0,
        }
        backtest = _make_backtest_mock(zero_trade_metrics)
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # Hard floor: 0 sharpe - 100 zero-trade penalty = -100
        assert fitness == pytest.approx(-100.0)

    def test_one_trade_strategy_beats_zero_trade_degenerate(self) -> None:
        """Regression test: even a single-trade strategy with a bad sharpe
        should rank above the zero-trade degenerate. This is what
        validates the *hard* floor on zero-trade genomes — without it the
        GA's elitism perpetuates do-nothing genomes."""
        # 1-trade strategy losing badly
        one_trade_loser = {
            "sharpe": -20.0,
            "max_drawdown": 0.5,
            "hit_rate": 0.0,
            "n_trades": 1,
        }
        zero_trade = {
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "n_trades": 0,
        }
        backtest_one = _make_backtest_mock(one_trade_loser)
        backtest_zero = _make_backtest_mock(zero_trade)
        fit_one, _ = compute_fitness(
            MA_CROSSOVER_SEED, instrument="EUR_USD", backtest_fn=backtest_one
        )
        fit_zero, _ = compute_fitness(
            MA_CROSSOVER_SEED, instrument="EUR_USD", backtest_fn=backtest_zero
        )
        assert fit_one > fit_zero, (
            f"1-trade loser ({fit_one}) must outrank 0-trade degenerate "
            f"({fit_zero}) — otherwise GA picks do-nothing genomes"
        )

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
        # Penalties: 2.0 raw - 6.667 trade-shortfall = -4.667 (negative,
        # Gate B dilution skipped) → -4.667 * 0.5 (diversity) = -2.333
        expected = (2.0 - (200 / 300) * 10.0) * DIVERSITY_PENALTY_FACTOR
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
            "cagr": 0.20,
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
