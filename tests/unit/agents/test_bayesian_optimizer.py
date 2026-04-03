"""
Unit tests for BayesianOptimizerAgent.

Tests the Bayesian optimization logic including:
- Input validation
- Parameter normalization/denormalization
- Expected Improvement calculation
- Random sample generation
- Gate B threshold evaluation
- Iteration estimation
"""

from unittest.mock import patch

import numpy as np

from agentfx.agents.optimizer import (
    BayesianOptimizerAgent,
    OptimizerAgent,
    evaluate_gate_b,
)


class TestGateBEvaluation:
    """Tests for Gate B threshold evaluation."""

    def test_passes_gate_b_all_thresholds_met(self):
        """Test that metrics passing all thresholds return True."""
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is True
        assert failures == []

    def test_fails_gate_b_sharpe_below_threshold(self):
        """Test that low Sharpe fails Gate B."""
        metrics = {
            "sharpe": 0.8,  # Below 1.0 threshold
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 1
        assert "Sharpe" in failures[0]

    def test_fails_gate_b_max_drawdown_exceeded(self):
        """Test that high max drawdown fails Gate B."""
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.15,  # Above 10% threshold
            "hit_rate": 0.55,
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 1
        assert "Max DD" in failures[0]

    def test_fails_gate_b_hit_rate_below_threshold(self):
        """Test that low hit rate fails Gate B."""
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.40,  # Below 48% threshold
            "n_trades": 500,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 1
        assert "Hit Rate" in failures[0]

    def test_fails_gate_b_insufficient_trades(self):
        """Test that insufficient trade count fails Gate B."""
        metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 100,  # Below 300 threshold
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 1
        assert "Trade Count" in failures[0]

    def test_fails_gate_b_multiple_failures(self):
        """Test that multiple failures are all reported."""
        metrics = {
            "sharpe": 0.5,
            "max_drawdown": 0.20,
            "hit_rate": 0.30,
            "n_trades": 50,
        }
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) == 4

    def test_handles_missing_metrics_gracefully(self):
        """Test that missing metrics default to failing values."""
        metrics = {}  # All missing
        passes, failures = evaluate_gate_b(metrics)
        assert passes is False
        assert len(failures) >= 1


class TestBayesianOptimizerAgentValidation:
    """Tests for BayesianOptimizerAgent input validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()

    def test_agent_type(self):
        """Test that agent type is correctly set."""
        assert self.agent.agent_type == "bayesian_optimizer"

    def test_validate_inputs_success(self):
        """Test that valid inputs pass validation."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 50},
                "slow": {"min": 20, "max": 200},
            },
        )
        assert is_valid is True
        assert errors == []

    def test_validate_inputs_missing_strategy(self):
        """Test that missing strategy fails validation."""
        is_valid, errors = self.agent.validate_inputs(
            pair="EUR_USD",
            param_ranges={"fast": {"min": 5, "max": 50}},
        )
        assert is_valid is False
        assert "strategy is required" in errors

    def test_validate_inputs_missing_pair(self):
        """Test that missing pair fails validation."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            param_ranges={"fast": {"min": 5, "max": 50}},
        )
        assert is_valid is False
        assert "pair is required" in errors

    def test_validate_inputs_missing_param_ranges(self):
        """Test that missing param_ranges fails validation."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
        )
        assert is_valid is False
        assert "param_ranges is required" in errors

    def test_validate_inputs_param_range_missing_min(self):
        """Test that param range without min fails validation."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": {"max": 50}},
        )
        assert is_valid is False
        assert any("min and max" in e for e in errors)

    def test_validate_inputs_param_range_min_greater_than_max(self):
        """Test that min >= max fails validation."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": {"min": 100, "max": 50}},
        )
        assert is_valid is False
        assert any("min must be < max" in e for e in errors)


class TestBayesianOptimizerAgentIterationEstimation:
    """Tests for iteration count estimation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()

    def test_estimate_iterations_single_param(self):
        """Test iteration estimation for single parameter."""
        iterations = self.agent._estimate_iterations(param_dims=1)
        # 10 random + 12 per dimension = 22
        assert iterations == 22

    def test_estimate_iterations_two_params(self):
        """Test iteration estimation for two parameters."""
        iterations = self.agent._estimate_iterations(param_dims=2)
        # 10 random + 24 = 34
        assert iterations == 34

    def test_estimate_iterations_five_params(self):
        """Test iteration estimation for five parameters."""
        iterations = self.agent._estimate_iterations(param_dims=5)
        # 10 random + 60 = 70
        assert iterations == 70


class TestBayesianOptimizerAgentParameterNormalization:
    """Tests for parameter normalization and denormalization."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()
        self.param_ranges = {
            "fast": {"min": 5, "max": 55},
            "slow": {"min": 20, "max": 220},
        }

    def test_normalize_params_min_values(self):
        """Test normalization of minimum values."""
        params = {"fast": 5, "slow": 20}
        normalized = self.agent._normalize_params(params, self.param_ranges)
        assert len(normalized) == 2
        np.testing.assert_array_almost_equal(normalized, [0.0, 0.0])

    def test_normalize_params_max_values(self):
        """Test normalization of maximum values."""
        params = {"fast": 55, "slow": 220}
        normalized = self.agent._normalize_params(params, self.param_ranges)
        np.testing.assert_array_almost_equal(normalized, [1.0, 1.0])

    def test_normalize_params_mid_values(self):
        """Test normalization of middle values."""
        params = {"fast": 30, "slow": 120}
        normalized = self.agent._normalize_params(params, self.param_ranges)
        np.testing.assert_array_almost_equal(normalized, [0.5, 0.5])

    def test_denormalize_params_min_values(self):
        """Test denormalization to minimum values."""
        normalized = np.array([0.0, 0.0])
        params = self.agent._denormalize_params(normalized, self.param_ranges)
        assert params["fast"] == 5
        assert params["slow"] == 20

    def test_denormalize_params_max_values(self):
        """Test denormalization to maximum values."""
        normalized = np.array([1.0, 1.0])
        params = self.agent._denormalize_params(normalized, self.param_ranges)
        assert params["fast"] == 55
        assert params["slow"] == 220

    def test_denormalize_params_with_step(self):
        """Test denormalization with step rounding."""
        param_ranges = {
            "fast": {"min": 5, "max": 55, "step": 5},
        }
        # 0.33 would give 21.5, but step=5 should round to 20
        normalized = np.array([0.3])
        params = self.agent._denormalize_params(normalized, param_ranges)
        assert params["fast"] % 5 == 0  # Must be divisible by step

    def test_normalize_denormalize_roundtrip(self):
        """Test that normalize -> denormalize gives back original values."""
        original = {"fast": 30, "slow": 120}
        normalized = self.agent._normalize_params(original, self.param_ranges)
        denormalized = self.agent._denormalize_params(normalized, self.param_ranges)
        assert abs(denormalized["fast"] - original["fast"]) < 0.01
        assert abs(denormalized["slow"] - original["slow"]) < 0.01


class TestBayesianOptimizerAgentRandomSampling:
    """Tests for random sample generation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()
        self.param_ranges = {
            "fast": {"min": 5, "max": 50},
            "slow": {"min": 20, "max": 200},
        }

    def test_generate_random_samples_count(self):
        """Test that correct number of samples are generated."""
        samples = self.agent._generate_random_samples(self.param_ranges, n_samples=10)
        assert len(samples) == 10

    def test_generate_random_samples_has_all_params(self):
        """Test that each sample has all required parameters."""
        samples = self.agent._generate_random_samples(self.param_ranges, n_samples=5)
        for sample in samples:
            assert "fast" in sample
            assert "slow" in sample

    def test_generate_random_samples_within_bounds(self):
        """Test that all samples are within bounds."""
        samples = self.agent._generate_random_samples(self.param_ranges, n_samples=100)
        for sample in samples:
            assert 5 <= sample["fast"] <= 50
            assert 20 <= sample["slow"] <= 200

    def test_generate_random_samples_with_step(self):
        """Test that discrete parameters respect step size."""
        param_ranges = {
            "fast": {"min": 5, "max": 50, "step": 5},
        }
        samples = self.agent._generate_random_samples(param_ranges, n_samples=100)
        for sample in samples:
            # Must be divisible by step (accounting for floating point)
            assert (
                abs(sample["fast"] % 5) < 0.001 or abs(sample["fast"] % 5 - 5) < 0.001
            )


class TestBayesianOptimizerAgentExpectedImprovement:
    """Tests for Expected Improvement acquisition function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()

    def test_expected_improvement_basic(self):
        """Test basic EI calculation with a real GP model."""
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern

        # Create and train a simple GP
        X_train = np.array([[0.1, 0.2], [0.3, 0.4], [0.6, 0.7]])
        y_train = np.array([0.2, 0.5, 0.3])

        gp = GaussianProcessRegressor(kernel=Matern(nu=2.5), alpha=1e-6)
        gp.fit(X_train, y_train)

        # Evaluate EI at new points
        X_eval = np.array([[0.2, 0.3], [0.5, 0.6]])
        y_best = 0.5

        ei = self.agent._expected_improvement(X_eval, gp, y_best, xi=0.01)

        assert len(ei) == 2
        assert all(np.isfinite(ei))
        assert all(ei >= 0)  # EI should be non-negative

    def test_expected_improvement_zero_variance_handling(self):
        """Test that EI handles zero variance (already evaluated points)."""
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern

        # Create a simple GP and fit it
        X_train = np.array([[0.2, 0.3], [0.5, 0.6], [0.8, 0.9]])
        y_train = np.array([0.1, 0.5, 0.3])

        gp = GaussianProcessRegressor(kernel=Matern(nu=2.5), alpha=1e-6)
        gp.fit(X_train, y_train)

        # Evaluate at training points (should have near-zero variance)
        X_eval = X_train.copy()
        y_best = 0.5

        ei = self.agent._expected_improvement(X_eval, gp, y_best, xi=0.01)

        # EI should be small/zero at training points
        assert len(ei) == 3
        assert all(np.isfinite(ei))  # No NaN or Inf


class TestBayesianOptimizerAgentRun:
    """Tests for the main run() method with mocked backtests."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = BayesianOptimizerAgent()
        self.param_ranges = {
            "fast": {"min": 5, "max": 50},
            "slow": {"min": 20, "max": 200},
        }

    def test_run_fails_with_invalid_inputs(self):
        """Test that run fails with invalid inputs."""
        result = self.agent.run(
            strategy="ma",
            pair="EUR_USD",  # Valid pair
            param_ranges={},  # Invalid - empty
        )
        assert result.status == "failed"
        assert len(result.errors) > 0

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_calls_backtest_for_each_trial(self, mock_backtest):
        """Test that run calls backtest for each trial."""
        mock_backtest.return_value = {
            "metrics": {
                "sharpe": 1.2,
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 500,
                "total_return": 0.15,
                "profit_factor": 1.5,
            },
            "signals": [],
            "trades": [],
        }

        # Patch where the function is used (in the optimizer module)
        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            result = self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=5,
                n_random_starts=3,
            )

            assert result.status == "success"
            assert mock_backtest.call_count == 5  # 3 random + 2 bayesian

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_returns_top_n_results(self, mock_backtest):
        """Test that run returns top N results sorted by Sharpe."""
        # Return different Sharpe values for different calls
        sharpe_values = [0.5, 1.5, 0.8, 1.2, 0.3]
        call_count = [0]

        def side_effect(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return {
                "metrics": {
                    "sharpe": sharpe_values[idx % len(sharpe_values)],
                    "max_drawdown": 0.05,
                    "hit_rate": 0.55,
                    "n_trades": 500,
                    "total_return": 0.15,
                    "profit_factor": 1.5,
                },
                "signals": [],
                "trades": [],
            }

        mock_backtest.side_effect = side_effect

        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            result = self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=5,
                n_random_starts=3,
                top_n=3,
            )

            assert result.status == "success"
            top_results = result.outputs["top_results"]
            assert len(top_results) == 3

            # Top results should be sorted by Sharpe descending
            assert top_results[0]["sharpe"] >= top_results[1]["sharpe"]
            assert top_results[1]["sharpe"] >= top_results[2]["sharpe"]

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_tracks_gate_b_passes(self, mock_backtest):
        """Test that run correctly tracks Gate B passes."""
        mock_backtest.return_value = {
            "metrics": {
                "sharpe": 1.5,  # Passes all thresholds
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 500,
            },
            "signals": [],
            "trades": [],
        }

        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            result = self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=5,
                n_random_starts=3,
            )

            stats = result.outputs["optimization_stats"]
            assert stats["gate_b_passes"] == 5  # All should pass

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_handles_backtest_errors(self, mock_backtest):
        """Test that run handles backtest errors gracefully."""
        mock_backtest.side_effect = Exception("Backtest failed")

        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            result = self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=3,
                n_random_starts=2,
            )

            assert result.status == "partial"
            assert len(result.errors) > 0
            stats = result.outputs["optimization_stats"]
            assert stats["failed_trials"] > 0

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_calls_progress_callback(self, mock_backtest):
        """Test that progress callback is called for each trial."""
        mock_backtest.return_value = {
            "metrics": {
                "sharpe": 1.2,
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 500,
            },
            "signals": [],
            "trades": [],
        }

        progress_calls = []

        def progress_callback(current, total, trial_info):
            progress_calls.append((current, total))

        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=5,
                n_random_starts=3,
                progress_callback=progress_callback,
            )

            assert len(progress_calls) == 5
            # First call should be (1, 34) since 2 params = 34 estimated
            assert progress_calls[0][0] == 1

    @patch("agentfx.services.backtest.run_backtest_from_db")
    def test_run_captures_signals_when_enabled(self, mock_backtest):
        """Test that signals are captured when capture_signals=True."""
        mock_signals = [{"timestamp": "2024-01-01", "signal": 1, "price": 1.1000}]
        mock_backtest.return_value = {
            "metrics": {
                "sharpe": 1.2,
                "max_drawdown": 0.05,
                "hit_rate": 0.55,
                "n_trades": 500,
            },
            "signals": mock_signals,
            "trades": [],
        }

        with patch("agentfx.agents.optimizer.run_backtest_from_db", mock_backtest):
            result = self.agent.run(
                strategy="ma",
                pair="EUR_USD",
                param_ranges=self.param_ranges,
                max_iterations=3,
                n_random_starts=2,
                capture_signals=True,
            )

            trials = result.outputs["all_trials"]
            assert len(trials) > 0
            assert trials[0]["signals"] == mock_signals


class TestLegacyOptimizerAgent:
    """Tests for legacy OptimizerAgent (grid search)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = OptimizerAgent()

    def test_agent_type(self):
        """Test that agent type is correctly set."""
        assert self.agent.agent_type == "optimizer"

    def test_validate_inputs_requires_data_root(self):
        """Test that data_root is required for legacy optimizer."""
        is_valid, errors = self.agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": {"min": 5, "max": 50, "step": 5}},
            # Missing data_root
        )
        assert is_valid is False
        assert "data_root is required" in errors

    def test_generate_param_grid_basic(self):
        """Test grid generation with simple ranges."""
        param_ranges = {
            "fast": {"min": 5, "max": 15, "step": 5},
        }
        grid = self.agent._generate_param_grid(param_ranges)
        assert len(grid) == 3  # 5, 10, 15
        assert grid[0]["fast"] == 5
        assert grid[1]["fast"] == 10
        assert grid[2]["fast"] == 15

    def test_generate_param_grid_multiple_params(self):
        """Test grid generation with multiple parameters."""
        param_ranges = {
            "fast": {"min": 5, "max": 10, "step": 5},
            "slow": {"min": 20, "max": 30, "step": 10},
        }
        grid = self.agent._generate_param_grid(param_ranges)
        # 2 values for fast * 2 values for slow = 4 combinations
        assert len(grid) == 4
