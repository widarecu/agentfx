"""
Parameter Optimizer Agent

Autonomously optimizes strategy parameters using Bayesian optimization.
Uses Gaussian Process regression with Expected Improvement acquisition function
to efficiently search the parameter space.

See: planning/requirements/US-001-optimization-runs.md
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import numpy as np

from agentfx.agents.base import AgentResult, BaseAgent

# Re-export Gate B symbols (PEP 484 explicit re-export via `as`-alias) so v0.2.0
# callers that imported them from agentfx.agents.optimizer continue to work.
# Canonical location is now agentfx.core.gates.
from agentfx.core.gates import GATE_B_THRESHOLDS as GATE_B_THRESHOLDS  # noqa: PLC0414
from agentfx.core.gates import evaluate_gate_b as evaluate_gate_b  # noqa: PLC0414
from agentfx.services.backtest import run_backtest, run_backtest_from_db

logger = logging.getLogger(__name__)


class OptimizerAgent(BaseAgent):
    """
    Legacy optimizer agent using grid search.
    Kept for backward compatibility.

    For new optimizations, use BayesianOptimizerAgent instead.
    """

    @property
    def agent_type(self) -> str:
        return "optimizer"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate optimization inputs."""
        errors = []

        if "strategy" not in kwargs:
            errors.append("strategy is required")
        if "pair" not in kwargs:
            errors.append("pair is required")
        if "param_ranges" not in kwargs:
            errors.append("param_ranges is required")
        if "data_root" not in kwargs:
            errors.append("data_root is required")

        param_ranges = kwargs.get("param_ranges", {})
        for param_name, range_config in param_ranges.items():
            if not all(k in range_config for k in ["min", "max", "step"]):
                errors.append(f"param_ranges.{param_name} must have min, max, and step")

        return len(errors) == 0, errors

    def _generate_param_grid(
        self, param_ranges: dict[str, dict[str, int | float]]
    ) -> list[dict[str, int | float]]:
        """Generate all parameter combinations from ranges."""
        param_names = list(param_ranges.keys())
        param_values = []

        for name in param_names:
            r = param_ranges[name]
            min_val, max_val, step = r["min"], r["max"], r["step"]
            values = []
            current = min_val
            while current <= max_val:
                values.append(current)
                current += step
            param_values.append(values)

        combinations = []
        for combo in product(*param_values):
            combinations.append(dict(zip(param_names, combo, strict=False)))

        return combinations

    def run(
        self,
        strategy: str,
        pair: str,
        param_ranges: dict[str, dict[str, int | float]],
        data_root: str,
        data_days: int = 180,
        top_n: int = 3,
        **kwargs,
    ) -> AgentResult:
        """
        Run parameter optimization using grid search.

        Args:
            strategy: Strategy name (ma, rsi, donchian)
            pair: Currency pair (e.g., EURUSD)
            param_ranges: Dict of param name to {min, max, step}
            data_root: Path to market data
            data_days: Days of data to use
            top_n: Number of top results to return

        Returns:
            AgentResult with top parameter configurations
        """
        self.started_at = datetime.utcnow()

        # Validate inputs
        is_valid, errors = self.validate_inputs(
            strategy=strategy,
            pair=pair,
            param_ranges=param_ranges,
            data_root=data_root,
        )
        if not is_valid:
            return self._create_result("failed", {}, errors)

        # Generate parameter grid
        param_grid = self._generate_param_grid(param_ranges)
        total_combinations = len(param_grid)

        # Run backtests for each combination
        results = []
        run_errors = []

        for i, params in enumerate(param_grid):
            try:
                backtest_result = run_backtest(
                    data_root=data_root,
                    pair=pair,
                    start_days=data_days,
                    strategy_name=strategy,
                    strategy_params=params,
                )

                metrics = backtest_result.get("metrics", {})
                passes_gate_b, gate_b_failures = evaluate_gate_b(metrics)

                results.append(
                    {
                        "params": params,
                        "metrics": metrics,
                        "sharpe": metrics.get("sharpe", float("-inf")),
                        "passes_gate_b": passes_gate_b,
                        "gate_b_failures": gate_b_failures,
                    }
                )
            except Exception as e:
                run_errors.append(f"Backtest failed for {params}: {str(e)}")

        # Sort by Sharpe ratio and get top N
        results.sort(key=lambda x: x["sharpe"], reverse=True)
        top_results = results[:top_n]

        # Add rank to top results
        for i, result in enumerate(top_results):
            result["rank"] = i + 1

        outputs = {
            "strategy": strategy,
            "pair": pair,
            "total_combinations": total_combinations,
            "successful_runs": len(results),
            "failed_runs": len(run_errors),
            "top_results": top_results,
        }

        status = "success" if not run_errors else "partial"
        return self._create_result(status, outputs, run_errors if run_errors else None)


class BayesianOptimizerAgent(BaseAgent):
    """
    Bayesian Optimizer Agent using Gaussian Process regression.

    Uses Expected Improvement (EI) acquisition function to efficiently
    explore the parameter space with fewer backtests than grid search.

    Key features:
    - Adaptive iteration count based on parameter dimensions
    - Warm-up phase with random sampling for initial GP training
    - Expected Improvement for exploitation/exploration balance
    - Gate B threshold evaluation for each trial

    Usage:
        agent = BayesianOptimizerAgent()
        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 50},
                "slow": {"min": 20, "max": 200},
            },
            data_years=5,
        )
    """

    @property
    def agent_type(self) -> str:
        return "bayesian_optimizer"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate optimization inputs for Bayesian optimizer."""
        errors = []

        if "strategy" not in kwargs:
            errors.append("strategy is required")
        if "pair" not in kwargs:
            errors.append("pair is required")
        if "param_ranges" not in kwargs:
            errors.append("param_ranges is required")

        param_ranges = kwargs.get("param_ranges", {})
        if not param_ranges:
            errors.append("param_ranges must not be empty")

        for param_name, range_config in param_ranges.items():
            if "min" not in range_config or "max" not in range_config:
                errors.append(f"param_ranges.{param_name} must have min and max")
            elif range_config["min"] >= range_config["max"]:
                errors.append(f"param_ranges.{param_name}: min must be < max")

        return len(errors) == 0, errors

    def _estimate_iterations(self, param_dims: int) -> int:
        """Estimate optimal number of iterations based on parameter dimensions.

        Uses formula: 10 (random warm-up) + 10-15 per dimension.
        This is more efficient than grid search which grows exponentially.

        Args:
            param_dims: Number of parameters to optimize

        Returns:
            Estimated number of iterations
        """
        random_samples = 10  # Initial random exploration
        per_dimension = 12  # Additional iterations per dimension
        return random_samples + (param_dims * per_dimension)

    def _normalize_params(
        self,
        params: dict[str, float],
        param_ranges: dict[str, dict],
    ) -> np.ndarray:
        """Normalize parameters to [0, 1] range for GP.

        Args:
            params: Dictionary of parameter values
            param_ranges: Parameter range definitions

        Returns:
            Normalized numpy array
        """
        normalized = []
        for name in sorted(param_ranges.keys()):
            p_min = param_ranges[name]["min"]
            p_max = param_ranges[name]["max"]
            val = params[name]
            normalized.append((val - p_min) / (p_max - p_min))
        return np.array(normalized)

    def _denormalize_params(
        self,
        normalized: np.ndarray,
        param_ranges: dict[str, dict],
    ) -> dict[str, float]:
        """Denormalize parameters from [0, 1] range.

        Args:
            normalized: Normalized parameter array
            param_ranges: Parameter range definitions

        Returns:
            Dictionary of denormalized parameter values
        """
        params = {}
        for i, name in enumerate(sorted(param_ranges.keys())):
            p_min = param_ranges[name]["min"]
            p_max = param_ranges[name]["max"]
            # Round to reasonable precision
            val = p_min + normalized[i] * (p_max - p_min)
            # Round to step if specified
            step = param_ranges[name].get("step")
            if step is not None:
                val = round(val / step) * step
            params[name] = val
        return params

    def _expected_improvement(
        self,
        X: np.ndarray,
        gp,
        y_best: float,
        xi: float = 0.01,
    ) -> np.ndarray:
        """Calculate Expected Improvement acquisition function.

        Args:
            X: Points to evaluate (n_samples, n_features)
            gp: Trained Gaussian Process model
            y_best: Best observed objective value
            xi: Exploration-exploitation trade-off parameter

        Returns:
            Expected improvement values for each point
        """
        from scipy.stats import norm

        mu, sigma = gp.predict(X, return_std=True)

        # Handle zero variance (already evaluated points)
        with np.errstate(divide="ignore", invalid="ignore"):
            imp = mu - y_best - xi
            Z = imp / sigma
            ei = imp * norm.cdf(Z) + sigma * norm.pdf(Z)
            ei[sigma == 0.0] = 0.0

        return ei

    def _generate_random_samples(
        self,
        param_ranges: dict[str, dict],
        n_samples: int,
    ) -> list[dict[str, float]]:
        """Generate random parameter samples for warm-up phase.

        Args:
            param_ranges: Parameter range definitions
            n_samples: Number of samples to generate

        Returns:
            List of parameter dictionaries
        """
        samples = []
        for _ in range(n_samples):
            sample = {}
            for name, bounds in param_ranges.items():
                p_min = bounds["min"]
                p_max = bounds["max"]
                step = bounds.get("step")
                if step is not None:
                    # Discrete parameter
                    n_steps = int((p_max - p_min) / step) + 1
                    val = p_min + np.random.randint(0, n_steps) * step
                else:
                    # Continuous parameter
                    val = np.random.uniform(p_min, p_max)
                sample[name] = val
            samples.append(sample)
        return samples

    def run(
        self,
        strategy: str,
        pair: str,
        param_ranges: dict[str, dict],
        data_years: int = 5,
        granularity: str = "M1",
        account_balance: float = 10000.0,
        position_size: int = 10000,
        top_n: int = 3,
        max_iterations: int | None = None,
        n_random_starts: int = 10,
        capture_signals: bool = True,
        progress_callback: Callable[[int, int, dict], None] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Run Bayesian parameter optimization.

        Args:
            strategy: Strategy name (ma, rsi, donchian)
            pair: Currency pair (e.g., EUR_USD)
            param_ranges: Dict of param name to {min, max, step?}
            data_years: Years of historical data to use
            granularity: Candle timeframe (M1, M5, H1)
            account_balance: Starting account balance
            position_size: Position size in units
            top_n: Number of top results to return
            max_iterations: Override auto-calculated iteration count
            n_random_starts: Number of random samples for warm-up
            capture_signals: If True, capture signals for visualization
            progress_callback: Optional callback(current, total, trial_info)

        Returns:
            AgentResult with optimization results including:
            - all_trials: List of all trials with params, metrics, Gate B status
            - top_results: Top N trials sorted by Sharpe ratio
            - best_trial: Single best trial
            - optimization_stats: Stats about the optimization run
        """
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern

        self.started_at = datetime.now(UTC)

        # Validate inputs
        is_valid, errors = self.validate_inputs(
            strategy=strategy,
            pair=pair,
            param_ranges=param_ranges,
        )
        if not is_valid:
            return self._create_result("failed", {}, errors)

        # Calculate date range
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=data_years * 365)

        # Estimate iterations if not specified
        param_dims = len(param_ranges)
        estimated_iterations = max_iterations or self._estimate_iterations(param_dims)

        logger.info(
            f"Starting Bayesian optimization for {strategy} on {pair}: "
            f"{param_dims} params, {estimated_iterations} iterations"
        )

        # Initialize GP with Matern kernel (works well for continuous optimization)
        kernel = Matern(nu=2.5)
        gp = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-6,  # Noise level
            normalize_y=True,
            n_restarts_optimizer=5,
        )

        # Storage for trials
        all_trials: list[dict[str, Any]] = []
        X_observed = []  # Normalized parameter values
        y_observed = []  # Sharpe ratios

        run_errors = []
        trial_index = 0

        # Phase 1: Random warm-up
        random_samples = self._generate_random_samples(param_ranges, n_random_starts)

        for params in random_samples:
            trial_index += 1
            try:
                backtest_result = run_backtest_from_db(
                    instrument=pair,
                    granularity=granularity,
                    start=start_time,
                    end=end_time,
                    strategy_name=strategy,
                    strategy_params=params,
                    account_balance=account_balance,
                    position_size=position_size,
                    capture_signals=capture_signals,
                )

                metrics = backtest_result.get("metrics", {})
                sharpe = metrics.get("sharpe", float("-inf"))
                passes_gate_b, gate_b_failures = evaluate_gate_b(metrics)

                trial = {
                    "trial_index": trial_index,
                    "params": params,
                    "metrics": metrics,
                    "sharpe": sharpe,
                    "passes_gate_b": passes_gate_b,
                    "gate_b_failures": gate_b_failures,
                    "signals": backtest_result.get("signals", []),
                    "trades": backtest_result.get("trades", []),
                    "phase": "random",
                }
                all_trials.append(trial)

                # Add to observation history
                X_observed.append(self._normalize_params(params, param_ranges))
                y_observed.append(sharpe if sharpe > float("-inf") else -10.0)

                if progress_callback:
                    progress_callback(trial_index, estimated_iterations, trial)

                logger.debug(
                    f"Trial {trial_index}: params={params}, sharpe={sharpe:.3f}"
                )

            except Exception as e:
                error_msg = f"Backtest failed for trial {trial_index} ({params}): {e}"
                run_errors.append(error_msg)
                logger.warning(error_msg)

        # Fit initial GP model
        if len(X_observed) >= 2:
            X_array = np.array(X_observed)
            y_array = np.array(y_observed)
            gp.fit(X_array, y_array)

        # Phase 2: Bayesian optimization
        remaining_iterations = estimated_iterations - len(all_trials)

        for _ in range(remaining_iterations):
            trial_index += 1

            try:
                # Generate candidate points
                n_candidates = 1000
                candidates = []
                for _ in range(n_candidates):
                    sample = self._generate_random_samples(param_ranges, 1)[0]
                    candidates.append(self._normalize_params(sample, param_ranges))
                candidates = np.array(candidates)

                # Calculate Expected Improvement for each candidate
                y_best = max(y_observed) if y_observed else 0.0
                ei_values = self._expected_improvement(candidates, gp, y_best)

                # Select best candidate
                best_idx = np.argmax(ei_values)
                best_normalized = candidates[best_idx]
                params = self._denormalize_params(best_normalized, param_ranges)

                # Run backtest
                backtest_result = run_backtest_from_db(
                    instrument=pair,
                    granularity=granularity,
                    start=start_time,
                    end=end_time,
                    strategy_name=strategy,
                    strategy_params=params,
                    account_balance=account_balance,
                    position_size=position_size,
                    capture_signals=capture_signals,
                )

                metrics = backtest_result.get("metrics", {})
                sharpe = metrics.get("sharpe", float("-inf"))
                passes_gate_b, gate_b_failures = evaluate_gate_b(metrics)

                trial = {
                    "trial_index": trial_index,
                    "params": params,
                    "metrics": metrics,
                    "sharpe": sharpe,
                    "passes_gate_b": passes_gate_b,
                    "gate_b_failures": gate_b_failures,
                    "signals": backtest_result.get("signals", []),
                    "trades": backtest_result.get("trades", []),
                    "phase": "bayesian",
                    "expected_improvement": float(ei_values[best_idx]),
                }
                all_trials.append(trial)

                # Update observation history
                X_observed.append(best_normalized)
                y_observed.append(sharpe if sharpe > float("-inf") else -10.0)

                # Refit GP with new observation
                X_array = np.array(X_observed)
                y_array = np.array(y_observed)
                gp.fit(X_array, y_array)

                if progress_callback:
                    progress_callback(trial_index, estimated_iterations, trial)

                logger.debug(
                    f"Trial {trial_index}: params={params}, sharpe={sharpe:.3f}, "
                    f"EI={ei_values[best_idx]:.4f}"
                )

            except Exception as e:
                error_msg = f"Backtest failed for trial {trial_index}: {e}"
                run_errors.append(error_msg)
                logger.warning(error_msg)

        # Sort trials by Sharpe ratio and get top N
        sorted_trials = sorted(all_trials, key=lambda x: x["sharpe"], reverse=True)
        top_results = sorted_trials[:top_n]

        # Add rank to top results
        for i, trial in enumerate(top_results):
            trial["rank"] = i + 1

        # Calculate optimization stats
        duration = (datetime.now(UTC) - self.started_at).total_seconds()
        gate_b_passes = sum(1 for t in all_trials if t["passes_gate_b"])

        optimization_stats = {
            "total_trials": len(all_trials),
            "successful_trials": len(all_trials) - len(run_errors),
            "failed_trials": len(run_errors),
            "gate_b_passes": gate_b_passes,
            "gate_b_pass_rate": gate_b_passes / len(all_trials) if all_trials else 0,
            "random_phase_trials": n_random_starts,
            "bayesian_phase_trials": len(all_trials) - n_random_starts,
            "duration_seconds": duration,
            "iterations_per_second": len(all_trials) / duration if duration > 0 else 0,
        }

        outputs = {
            "strategy": strategy,
            "pair": pair,
            "data_years": data_years,
            "param_ranges": param_ranges,
            "account_balance": account_balance,
            "position_size": position_size,
            "all_trials": all_trials,
            "top_results": top_results,
            "best_trial": top_results[0] if top_results else None,
            "optimization_stats": optimization_stats,
        }

        status = "success" if not run_errors else "partial"
        best_sharpe = top_results[0]["sharpe"] if top_results else 0.0
        logger.info(
            f"Optimization complete: {len(all_trials)} trials, "
            f"best Sharpe={best_sharpe:.3f}, "
            f"Gate B passes={gate_b_passes}"
        )

        return self._create_result(status, outputs, run_errors if run_errors else None)
