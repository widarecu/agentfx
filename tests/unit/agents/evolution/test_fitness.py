"""Fitness scoring tests with a mocked backtest function."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.agents.evolution.fitness import (
    COMPILER_ERROR_PENALTY,
    DIVERSITY_PENALTY_FACTOR,
    FOLD_CONSISTENCY_LAMBDA_DEFAULT,
    GATE_B_FAILURE_DILUTION,
    _aggregate_fold_metrics,
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

    def test_consistent_folds_keep_full_mean(self):
        """Three folds with the same Sharpe → std=0 → fitness = mean.
        This is the "perfectly consistent" baseline; nothing should be
        deducted because there's no inconsistency to penalise."""
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
        assert raw == pytest.approx(1.2)
        assert agg["sharpe"] == pytest.approx(1.2)
        assert agg["fold_consistency_std"] == pytest.approx(0.0)
        assert agg["mean_fold_sharpe"] == pytest.approx(1.2)

    def test_one_great_fold_two_bad_folds_gets_penalised(self):
        """The pathological case the v0.5.0 change is built to catch:
        one fold has Sharpe 3.0, the other two have 0.1 each.
        Mean = 1.07, but std is large → fitness < mean.
        Compare against the OLD (mean-only) signal of 1.07."""
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
        # Old behaviour: fitness ≈ mean = 1.07
        # New: mean − 0.5·std ≈ 1.07 − 0.5·1.37 ≈ 0.39
        # A genuinely consistent Sharpe-1.0 strategy would now beat this.
        assert raw < agg["mean_fold_sharpe"], (
            "Inconsistent strategy must get a fitness BELOW its average — "
            "that's the whole point of fold-consistency penalty."
        )
        assert raw == pytest.approx(0.39, abs=0.05)
        # Inspection fields surface through to the caller so the portal
        # can show the per-fold spread if it wants.
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

    def test_lambda_zero_reverts_to_pure_mean(self):
        """λ=0 disables the consistency penalty; result = mean(per-fold)."""
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
        raw, _ = _aggregate_fold_metrics(per_fold, fold_consistency_lambda=0.0)
        assert raw == pytest.approx(1.5)  # plain mean


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
