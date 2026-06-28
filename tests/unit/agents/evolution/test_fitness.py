"""Fitness scoring tests with a mocked backtest function."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.agents.evolution.fitness import (
    COMPILER_ERROR_PENALTY,
    DIVERSITY_PENALTY_FACTOR,
    FOLD_CONSISTENCY_LAMBDA_DEFAULT,
    _aggregate_fold_metrics,
    _gate_b_proximity_score,
    compute_fitness,
    is_near_elite,
    spec_edit_distance,
)
from agentfx.core.dsl.compiler import CompilerError
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
    def test_gate_b_proximity_used_as_raw_fitness(self) -> None:
        """Single-window mode uses Gate-B-proximity score, not raw Sharpe.

        With sharpe=2.5, cagr=0.20, hit=0.55, mdd=0.05, n_trades=500:
          sharpe_term = clamp(1.5, -2, 2) = 1.5
          cagr_term   = clamp(0.333, -2, 2) ≈ 0.333
          hit_term    = clamp(0.1458, -1, 1) ≈ 0.1458
          mdd_term    = clamp(0.5, -2, 1) = 0.5
          trades_term = clamp(0.667, -1, 1) ≈ 0.667
          raw ≈ 0.35×0.333 + 0.25×0.5 + 0.20×1.5 + 0.10×0.1458 + 0.10×0.667
              ≈ 0.623
        """
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.5))
        fitness, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # Gate-B-proximity score ≈ 0.623 — NOT 2.5
        assert fitness == pytest.approx(0.623, abs=0.005)
        # sharpe key still holds the actual Sharpe (not overwritten by fitness)
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

    def test_low_trade_count_reduces_proximity_score(self) -> None:
        """n_trades=100 vs threshold=300 → trades_term ≈ -0.667, weight 0.10.
        The trade-shortfall additive penalty (-6.667) no longer applies
        (v0.6.0 removal); the score reflects the reduced trades_term only.

        With sharpe=1.8, cagr=0.20, hit=0.55, mdd=0.05, n_trades=100:
          sharpe_term ≈ 0.8, cagr_term ≈ 0.333, hit_term ≈ 0.1458
          mdd_term = 0.5, trades_term ≈ -0.667
          raw ≈ 0.35×0.333 + 0.25×0.5 + 0.20×0.8 + 0.10×0.1458 + 0.10×(-0.667)
              ≈ 0.350
        """
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=1.8))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert fitness == pytest.approx(0.350, abs=0.005)

    def test_negative_sharpe_with_low_trades_gives_low_proximity_score(self) -> None:
        """Sharpe=-0.5, n_trades=100 → both sharpe_term and trades_term are
        negative, depressing the score significantly.

        With sharpe=-0.5, cagr=0.20, hit=0.55, mdd=0.05, n_trades=100:
          sharpe_term = clamp(-1.5, -2, 2) = -1.5
          cagr_term ≈ 0.333, hit_term ≈ 0.1458, mdd_term = 0.5
          trades_term ≈ -0.667
          raw ≈ 0.35×0.333 + 0.25×0.5 + 0.20×(-1.5) + 0.10×0.1458 + 0.10×(-0.667)
              ≈ -0.110
        """
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=-0.5))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        assert fitness == pytest.approx(-0.110, abs=0.005)

    def test_zero_trades_degenerate_genome_strongly_penalized(self) -> None:
        """Regression test for v0.3.0 acceptance runs #5 + #6:

        A DSL genome where long and short conditions are identical never
        triggers any trades. Gate-B-proximity's trades_term contributes only
        −0.10 for zero trades (weight 0.10 × clamp −1), which is nowhere near
        enough to prevent degenerate genomes from winning tournaments over active
        losers. The hard floor of -100 still fires and dominates.

        With sharpe=0, cagr=0, hit=0, mdd=0, n_trades=0:
          score ≈ -0.50 (proximity terms); then -100 hard penalty → ≈ -100.50
        """
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
        # Proximity score ≈ -0.50, then -100 hard floor → total ≈ -100.50
        assert fitness == pytest.approx(-100.50, abs=0.05)

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
        """Diversity penalty halves the proximity score (not the raw Sharpe).

        With sharpe=2.0, cagr=0.20, hit=0.55, mdd=0.05, n_trades=500:
          sharpe_term=1.0, cagr_term≈0.333, hit_term≈0.146, mdd_term=0.5,
          trades_term≈0.667 → proximity ≈ 0.523
        After diversity ×0.5 → ≈ 0.261
        """
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[MA_CROSSOVER_SEED],  # same spec → near-elite
        )
        # Proximity score ≈ 0.523 × 0.5 diversity ≈ 0.261
        assert fitness == pytest.approx(0.523 * DIVERSITY_PENALTY_FACTOR, abs=0.005)

    def test_no_diversity_penalty_when_distant_from_elites(self) -> None:
        """When far from all elites, diversity factor is 1.0 — fitness = raw proximity."""
        backtest = _make_backtest_mock(_gate_passing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[DONCHIAN_BREAKOUT_SEED, RSI_MEAN_REVERSION_SEED],
        )
        # Proximity score ≈ 0.523 (sharpe=2.0, all gate-passing metrics)
        assert fitness == pytest.approx(0.523, abs=0.005)

    def test_combined_low_trades_and_diversity_penalties(self) -> None:
        """Low trades (n_trades=100) depresses proximity; diversity halves it.

        With sharpe=2.0, cagr=0.20, hit=0.55, mdd=0.05, n_trades=100:
          sharpe_term=1.0, cagr_term≈0.333, hit_term≈0.146, mdd_term=0.5,
          trades_term≈-0.667 → proximity ≈ 0.390
        After diversity ×0.5 → ≈ 0.195
        """
        backtest = _make_backtest_mock(_gate_failing_metrics(sharpe=2.0))
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
            elite_specs=[MA_CROSSOVER_SEED],
        )
        # proximity ≈ 0.390, diversity × 0.5 → ≈ 0.195
        assert fitness == pytest.approx(0.195, abs=0.005)

    def test_missing_sharpe_scores_as_zero_trade_degenerate(self) -> None:
        """Empty metrics (no sharpe key) defaults all values to 0/None.
        n_trades defaults to 0 → zero-trade hard penalty fires (-100).
        Result: large negative value (proximity ≈ -0.5, then -100 hard floor).
        """
        backtest = MagicMock(return_value={"metrics": {}})
        fitness, _ = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=backtest,
        )
        # Zero-trade penalty dominates; fitness is far below zero
        assert fitness < -50.0

    def test_metrics_returned_with_extra_scoring_fields(self) -> None:
        """compute_fitness enriches the backtest metrics dict with proximity
        scoring fields; user-supplied fields are preserved."""
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
        # All original fields preserved
        assert metrics["sharpe"] == 1.5
        assert metrics["extra_field"] == "preserved"
        # Added by compute_fitness for transparency
        assert "gate_b_proximity_terms" in metrics
        assert "raw_fitness" in metrics
        assert "penalty_breakdown" in metrics


# ─────────────────────────────────────────────────────────────────────────
# Fold-consistency mode (v0.5.0)
# ─────────────────────────────────────────────────────────────────────────
# The GA's fitness was previously the Sharpe of ONE backtest over the
# concatenated training window, which rewarded "found a regime and rode
# it" strategies that look great on average but collapse OOS. The new
# mode runs a backtest per training fold and aggregates per-fold Sharpe
# via `mean − λ·std`, penalising variance. These tests lock the
# aggregator math and the orchestrator-to-aggregator wiring.


class TestAggregatorMath:
    """Pure math on _aggregate_fold_metrics — no backtest, no DB."""

    def test_consistent_folds_proximity_score(self):
        """Three identical folds → std=0 → no fold-variance deduction.

        With sharpe=1.2, cagr=0.10, hit=0.5, mdd=0.05, n_trades=600 (summed):
          sharpe_term  = clamp(0.2, -2, 2) = 0.2
          cagr_term    = clamp(0.10/0.15 - 1, -2, 2) = clamp(-0.333, -2, 2) = -0.333
          hit_term     = clamp(0.5/0.48 - 1, -1, 1) ≈ clamp(0.0417, -1, 1) = 0.0417
          mdd_term     = clamp(1 - 0.05/0.10, -2, 1) = 0.5
          trades_term  = clamp(600/300 - 1, -1, 1) = clamp(1.0, -1, 1) = 1.0
          raw ≈ 0.35×(-0.333) + 0.25×0.5 + 0.20×0.2 + 0.10×0.0417 + 0.10×1.0
              ≈ -0.1167 + 0.125 + 0.040 + 0.00417 + 0.10 = 0.1524

        agg["sharpe"] must be the actual mean Sharpe (1.2), not the score.
        """
        per_fold = [
            {
                "sharpe": 1.2,
                "max_drawdown": -0.05,
                "hit_rate": 0.5,
                "n_trades": 200,
                "cagr": 0.10,
            },
            {
                "sharpe": 1.2,
                "max_drawdown": -0.05,
                "hit_rate": 0.5,
                "n_trades": 200,
                "cagr": 0.10,
            },
            {
                "sharpe": 1.2,
                "max_drawdown": -0.05,
                "hit_rate": 0.5,
                "n_trades": 200,
                "cagr": 0.10,
            },
        ]
        raw, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.5)
        # agg["sharpe"] is the mean Sharpe (not the proximity score — v0.6.0 fix)
        assert agg["sharpe"] == pytest.approx(1.2)
        assert agg["fold_consistency_std"] == pytest.approx(0.0)
        assert agg["mean_fold_sharpe"] == pytest.approx(1.2)
        # raw is the Gate-B-proximity score ≈ 0.152
        assert raw == pytest.approx(0.152, abs=0.005)
        # Transparency fields present
        assert "gate_b_proximity_terms" in agg
        assert "raw_fitness" in agg

    def test_one_great_fold_two_bad_folds_gets_penalised(self):
        """The pathological case the v0.5.0 change is built to catch:
        one fold has Sharpe 3.0, the other two have 0.1 each.
        std(Sharpe) ≈ 1.37 → strong fold-variance penalty → fitness << mean.

        Gate-B-proximity on aggregated (mean_sharpe≈1.067, cagr≈0.133,
        hit≈0.45, mdd_worst=-0.05, trades_sum=400):
          raw_proximity ≈ 0.127
        After fold-variance penalty (−0.5×1.37): score ≈ −0.557

        A genuinely consistent Sharpe-1.0 strategy with good CAGR/hit
        would score higher, which is the whole point.
        """
        per_fold = [
            {
                "sharpe": 0.1,
                "n_trades": 50,
                "max_drawdown": -0.03,
                "hit_rate": 0.4,
                "cagr": 0.0,
            },
            {
                "sharpe": 3.0,
                "n_trades": 300,
                "max_drawdown": -0.05,
                "hit_rate": 0.55,
                "cagr": 0.4,
            },
            {
                "sharpe": 0.1,
                "n_trades": 50,
                "max_drawdown": -0.03,
                "hit_rate": 0.4,
                "cagr": 0.0,
            },
        ]
        raw, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.5)
        # Core invariant: fold-inconsistency penalty drives fitness below mean Sharpe
        assert raw < agg["mean_fold_sharpe"], (
            "Inconsistent strategy must get a fitness BELOW its average — "
            "that's the whole point of fold-consistency penalty."
        )
        # The fold-variance penalty is now embedded in the proximity scorer
        assert raw == pytest.approx(-0.557, abs=0.05)
        # Inspection fields surface through to the caller
        assert agg["per_fold_sharpe"] == [0.1, 3.0, 0.1]
        assert agg["fold_consistency_std"] > 1.0

    def test_max_drawdown_aggregated_as_worst_fold(self):
        """MaxDD must be the WORST per-fold value, not the mean. If one
        fold drops 30% and the others are fine, the strategy is still
        risky overall and the penalty function should see that."""
        per_fold = [
            {
                "sharpe": 1.0,
                "max_drawdown": -0.30,
                "hit_rate": 0.5,
                "n_trades": 100,
                "cagr": 0.0,
            },
            {
                "sharpe": 1.0,
                "max_drawdown": -0.02,
                "hit_rate": 0.5,
                "n_trades": 100,
                "cagr": 0.0,
            },
            {
                "sharpe": 1.0,
                "max_drawdown": -0.03,
                "hit_rate": 0.5,
                "n_trades": 100,
                "cagr": 0.0,
            },
        ]
        _, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.5)
        assert agg["max_drawdown"] == pytest.approx(-0.30), (
            "Worst-fold DD must propagate so the Gate-B drawdown penalty "
            "still fires when ANY fold breaches the 10% cap."
        )

    def test_n_trades_aggregated_as_sum(self):
        """Total trades = sum across folds. The Gate-B 300-trade-minimum
        threshold operates on this total, the same way it would on a
        concatenated training run."""
        per_fold = [
            {
                "sharpe": 1.0,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
            {
                "sharpe": 1.0,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
            {
                "sharpe": 1.0,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
        ]
        _, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.5)
        assert agg["n_trades"] == 300

    def test_empty_per_fold_returns_minus_infinity(self):
        """No folds → no signal → -inf fitness so the GA discards it."""
        raw, agg = _aggregate_fold_metrics([], fold_consistency_lambda=0.5)
        assert raw == float("-inf")
        assert agg == {}

    def test_all_folds_have_minus_infinity_sharpe(self):
        """All-degenerate (no trades on any fold) → -inf fitness."""
        per_fold = [
            {"sharpe": float("-inf"), "n_trades": 0},
            {"sharpe": float("-inf"), "n_trades": 0},
        ]
        raw, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.5)
        assert raw == float("-inf")

    def test_fold_consistency_penalty_embedded_in_proximity_scorer(self):
        """As of v0.6.0, the fold-variance penalty coefficient (0.5) is
        embedded in ``_gate_b_proximity_score`` rather than being controlled
        by ``fold_consistency_lambda``.  ``fold_consistency_lambda`` is now a
        no-op on the coefficient but still controls which std is passed in.

        With sharpe folds [0.5, 1.5, 2.5], std ≈ 0.816:
          proximity(aggregated, std=0.816) ≈ 0.004 − 0.5×0.816 ≈ −0.404
        The variance penalty fires regardless of fold_consistency_lambda.
        """
        per_fold = [
            {
                "sharpe": 0.5,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
            {
                "sharpe": 1.5,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
            {
                "sharpe": 2.5,
                "n_trades": 100,
                "max_drawdown": 0,
                "hit_rate": 0.5,
                "cagr": 0,
            },
        ]
        # fold_consistency_lambda is a no-op on the 0.5 coefficient now
        raw, agg = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.0)
        assert agg["mean_fold_sharpe"] == pytest.approx(1.5)
        # fold-variance still penalised because std is embedded at 0.5 in scorer
        assert raw == pytest.approx(-0.404, abs=0.01)


class TestComputeFitnessFoldMode:
    """End-to-end via compute_fitness with the new ``training_folds`` arg."""

    def test_fold_mode_runs_backtest_per_fold(self):
        """Three folds → three backtest invocations, in order. Each call
        receives the fold's (start, end) and the backtest's metrics flow
        into the aggregator."""
        from datetime import UTC, datetime, timedelta

        d0 = datetime(2024, 1, 1, tzinfo=UTC)
        folds = [
            (d0, d0 + timedelta(days=90)),
            (d0 + timedelta(days=90), d0 + timedelta(days=180)),
            (d0 + timedelta(days=180), d0 + timedelta(days=270)),
        ]
        # Each fold gives the same metrics — pure consistency case.
        per_fold_result = {
            "metrics": {
                "sharpe": 1.5,
                "max_drawdown": -0.05,
                "hit_rate": 0.5,
                "n_trades": 200,
                "cagr": 0.20,
            }
        }
        backtest = MagicMock(return_value=per_fold_result)
        fitness, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            training_folds=folds,
            backtest_fn=backtest,
        )

        assert backtest.call_count == 3, (
            "One backtest per training fold; if this is 1, the function "
            "fell through to single-window mode silently."
        )
        # Per-fold start/end pairs were passed through verbatim
        call_windows = [
            (c.kwargs["start"], c.kwargs["end"]) for c in backtest.call_args_list
        ]
        assert call_windows == folds
        # Aggregated metrics carry the consistency-inspection fields
        assert metrics["mean_fold_sharpe"] == pytest.approx(1.5)
        assert metrics["fold_consistency_std"] == pytest.approx(0.0)
        assert metrics["fold_consistency_lambda"] == pytest.approx(
            FOLD_CONSISTENCY_LAMBDA_DEFAULT
        )
        # v0.6.0: sharpe key holds actual mean Sharpe, not fitness score
        assert metrics["sharpe"] == pytest.approx(1.5)
        # Gate-B-proximity transparency fields present
        assert "gate_b_proximity_terms" in metrics
        assert "raw_fitness" in metrics

    def test_fold_mode_overrides_start_end_window(self):
        """When ``training_folds`` is given, the legacy ``start`` and
        ``end`` args are ignored — no single-window backtest happens."""
        from datetime import UTC, datetime, timedelta

        d0 = datetime(2024, 1, 1, tzinfo=UTC)
        folds = [(d0, d0 + timedelta(days=30))]
        backtest = MagicMock(return_value={"metrics": {"sharpe": 1.0, "n_trades": 100}})
        compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=d0,  # different from the fold window
            end=d0 + timedelta(days=999),
            training_folds=folds,
            backtest_fn=backtest,
        )
        # Exactly one call, with the fold's window — NOT the start/end.
        assert backtest.call_count == 1
        assert backtest.call_args.kwargs["start"] == folds[0][0]
        assert backtest.call_args.kwargs["end"] == folds[0][1]

    def test_empty_training_folds_falls_back_to_single_window(self):
        """An empty list ≠ "no folds requested" — fall back to single
        window mode so existing callers (and the orchestrator on a
        misconfigured run) don't crash."""
        from datetime import UTC, datetime

        d0 = datetime(2024, 1, 1, tzinfo=UTC)
        backtest = MagicMock(return_value={"metrics": {"sharpe": 1.0, "n_trades": 100}})
        compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            start=d0,
            end=d0,
            training_folds=[],
            backtest_fn=backtest,
        )
        assert backtest.call_count == 1  # single-window path


# ─────────────────────────────────────────────────────────────────────────
# CompilerError defense-in-depth (2026-06-16 staging crash)
# ─────────────────────────────────────────────────────────────────────────


class TestCompileErrorPenalty:
    """Defense-in-depth: a genome whose DSL passes Pydantic grammar
    validation but fails the compiler's semantic checks (e.g. arg arity
    mismatch from a mutation that left donchian_high with 0 args) must
    NOT crash the orchestrator subprocess. The fitness function catches
    CompilerError and assigns the hard penalty; the GA continues with
    the next genome.

    Pre-fix bug: `evorun_20260615_f4fc6e63` on staging died with
    `process_crashed` and stack ending in
    `CompilerError: indicator 'donchian_high' takes 1 args, got 0`.
    """

    def test_compiler_error_returns_hard_penalty_in_single_window(self):
        def raising_backtest(**kwargs):
            raise CompilerError("indicator 'donchian_high' takes 1 args, got 0")

        fitness, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            backtest_fn=raising_backtest,
        )
        assert fitness == COMPILER_ERROR_PENALTY
        assert metrics["compiler_error"]  # truthy string set
        assert metrics["n_trades"] == 0
        assert metrics["penalty_breakdown"]["compiler_rejected"] is True

    def test_compiler_error_returns_hard_penalty_in_fold_mode(self):
        from datetime import UTC, datetime

        call_count = [0]

        def raising_on_first_fold(**kwargs):
            call_count[0] += 1
            raise CompilerError("indicator 'donchian_high' takes 1 args, got 0")

        d0 = datetime(2024, 1, 1, tzinfo=UTC)
        folds = [
            (d0, datetime(2024, 4, 1, tzinfo=UTC)),
            (datetime(2024, 4, 1, tzinfo=UTC), datetime(2024, 7, 1, tzinfo=UTC)),
        ]
        fitness, metrics = compute_fitness(
            MA_CROSSOVER_SEED,
            instrument="EUR_USD",
            training_folds=folds,
            backtest_fn=raising_on_first_fold,
        )
        # First call raised; subsequent folds skipped. Fitness pinned to
        # the hard penalty floor.
        assert fitness == COMPILER_ERROR_PENALTY
        assert metrics["penalty_breakdown"]["compiler_rejected"] is True
        assert call_count[0] == 1  # bailed after the first raising backtest

    def test_non_compiler_value_errors_still_propagate(self):
        """We want CompilerError caught, but other ValueErrors (real bugs
        in the backtest pipeline) should still surface to the caller so
        they don't get swallowed by the new defensive catch."""

        def raising_other(**kwargs):
            raise ValueError("genuinely broken — not a CompilerError")

        with pytest.raises(ValueError, match="genuinely broken"):
            compute_fitness(
                MA_CROSSOVER_SEED,
                instrument="EUR_USD",
                backtest_fn=raising_other,
            )


# ─────────────────────────────────────────────────────────────────────────
# Gate-B-proximity scorer unit tests (v0.6.0)
# ─────────────────────────────────────────────────────────────────────────


class TestGateBProximityScore:
    """Direct unit tests for _gate_b_proximity_score.

    Verifies the core promise of the v0.6.0 redesign: a balanced strategy
    that clears all Gate B thresholds scores higher than a Sharpe-maximiser
    that blows through MDD, CAGR, and hit rate.
    """

    def test_at_thresholds_scores_near_zero(self):
        """Every metric exactly at its Gate B threshold → each term = 0 → score = 0.

        This is the "break-even" point: a strategy that just barely passes
        Gate B should score ≈ 0 on the proximity function.
        """
        metrics = {
            "sharpe": 1.0,
            "cagr": 0.15,
            "hit_rate": 0.48,
            "max_drawdown": -0.10,
            "n_trades": 300,
        }
        score, terms = _gate_b_proximity_score(metrics, fold_std_sharpe=0.0)
        assert abs(score) < 1e-9, f"Expected ≈ 0 at thresholds, got {score}"
        assert terms["sharpe_term"] == pytest.approx(0.0)
        assert terms["cagr_term"] == pytest.approx(0.0)
        assert terms["hit_term"] == pytest.approx(0.0)
        assert terms["mdd_term"] == pytest.approx(0.0)
        assert terms["trades_term"] == pytest.approx(0.0)

    def test_above_thresholds_scores_positive(self):
        """Metrics at 2× thresholds → each term ≈ +1 (or capped) → positive score."""
        metrics = {
            "sharpe": 2.0,
            "cagr": 0.30,
            "hit_rate": 0.96,
            "max_drawdown": -0.05,  # mdd_term = 1 - 0.05/0.10 = 0.5, not 2×
            "n_trades": 600,
        }
        score, _ = _gate_b_proximity_score(metrics, fold_std_sharpe=0.0)
        assert score > 0.5, f"Expected positive score above thresholds, got {score}"

    def test_failing_metrics_scores_negative(self):
        """Metrics far below all thresholds → clamped at negatives → negative score."""
        metrics = {
            "sharpe": 0.0,
            "cagr": 0.0,
            "hit_rate": 0.0,
            "max_drawdown": -0.50,
            "n_trades": 0,
        }
        score, terms = _gate_b_proximity_score(metrics, fold_std_sharpe=0.0)
        assert score < -0.5, f"Expected strongly negative score, got {score}"
        assert terms["sharpe_term"] == -1.0
        assert terms["cagr_term"] == -1.0
        assert terms["hit_term"] == -1.0
        assert terms["mdd_term"] == -2.0  # clamped at lower bound

    def test_balanced_strategy_outscores_sharpe_maximiser(self):
        """Core promise of the v0.6.0 redesign.

        Strategy A (balanced):  Sharpe 0.6, hit 50%, CAGR 18%, MDD 8%, trades 500
        Strategy B (Sharpe-max): Sharpe 1.5, hit 30%, CAGR 5%, MDD 15%, trades 300

        Strategy A clears more Gate B thresholds weighted by priority
        (CAGR + MDD together are 60% of the score), so it should score higher
        even though Strategy B has a much higher raw Sharpe.
        """
        strategy_a = {
            "sharpe": 0.6,
            "cagr": 0.18,
            "hit_rate": 0.50,
            "max_drawdown": -0.08,
            "n_trades": 500,
        }
        strategy_b = {
            "sharpe": 1.5,
            "cagr": 0.05,
            "hit_rate": 0.30,
            "max_drawdown": -0.15,
            "n_trades": 300,
        }
        score_a, _ = _gate_b_proximity_score(strategy_a, fold_std_sharpe=0.0)
        score_b, _ = _gate_b_proximity_score(strategy_b, fold_std_sharpe=0.0)
        assert score_a > score_b, (
            f"Balanced strategy A (score={score_a:.4f}) should outscore "
            f"Sharpe-max B (score={score_b:.4f}) — the v0.6.0 promise"
        )

    def test_cagr_weight_dominates_sharpe_weight(self):
        """CAGR has weight 0.35 vs Sharpe 0.20. Improving CAGR from threshold
        to 2× threshold should move fitness more than the same relative
        improvement to Sharpe.

        At-threshold base: every metric at threshold → score = 0.
        Doubling CAGR: cagr_term goes 0→1, Δscore = 0.35×1 = +0.35
        Doubling Sharpe: sharpe_term goes 0→1, Δscore = 0.20×1 = +0.20
        """
        base = {
            "sharpe": 1.0,
            "cagr": 0.15,
            "hit_rate": 0.48,
            "max_drawdown": -0.10,
            "n_trades": 300,
        }
        high_cagr = {**base, "cagr": 0.30}
        high_sharpe = {**base, "sharpe": 2.0}

        score_base, _ = _gate_b_proximity_score(base, 0.0)
        score_cagr, _ = _gate_b_proximity_score(high_cagr, 0.0)
        score_sharpe, _ = _gate_b_proximity_score(high_sharpe, 0.0)

        cagr_delta = score_cagr - score_base
        sharpe_delta = score_sharpe - score_base

        assert cagr_delta > sharpe_delta, (
            f"CAGR delta ({cagr_delta:.4f}) should exceed Sharpe delta "
            f"({sharpe_delta:.4f}) — CAGR weight 0.35 > Sharpe weight 0.20"
        )
        assert cagr_delta == pytest.approx(0.35, abs=1e-9)
        assert sharpe_delta == pytest.approx(0.20, abs=1e-9)

    def test_zero_trade_proximity_term_is_small(self):
        """Gate-B-proximity trades_term only contributes −0.10 for zero trades.

        This verifies WHY the hard zero-trade penalty is still needed:
        proximity alone is insufficient to prevent degenerate genomes winning.
        With all other metrics at 2× threshold (excellent strategy) + 0 trades:
          trades_term = clamp(-1, -1, 1) = -1, weight 0.10 → contribution -0.10
        The score is only 0.10 lower than the same strategy with 300 trades.
        """
        excellent_with_trades = {
            "sharpe": 2.0,
            "cagr": 0.30,
            "hit_rate": 0.60,
            "max_drawdown": -0.05,
            "n_trades": 300,
        }
        excellent_zero_trades = {**excellent_with_trades, "n_trades": 0}

        score_300, _ = _gate_b_proximity_score(excellent_with_trades, 0.0)
        score_zero, _ = _gate_b_proximity_score(excellent_zero_trades, 0.0)

        # Trades contribution alone: weight 0.10 × (0 − (−1)) = exactly 0.10
        assert abs((score_300 - score_zero) - 0.10) < 1e-9

    def test_fold_variance_penalty_fires(self):
        """fold_std_sharpe > 0 reduces fitness (regime-fragility penalty)."""
        metrics = {
            "sharpe": 1.5,
            "cagr": 0.20,
            "hit_rate": 0.55,
            "max_drawdown": -0.05,
            "n_trades": 500,
        }
        score_stable, _ = _gate_b_proximity_score(metrics, fold_std_sharpe=0.0)
        score_volatile, _ = _gate_b_proximity_score(metrics, fold_std_sharpe=1.0)
        # penalty = 0.5 × 1.0 = 0.5
        assert score_stable - score_volatile == pytest.approx(0.5, abs=1e-9)
