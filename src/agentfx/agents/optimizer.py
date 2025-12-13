"""
Parameter Optimizer Agent

Autonomously optimizes strategy parameters using grid search or Bayesian optimization.

See: planning/requirements/US-001-optimization-runs.md
"""

from datetime import datetime
from itertools import product

from agentfx.agents.base import BaseAgent, AgentResult
from agentfx.services.backtest import run_backtest


class OptimizerAgent(BaseAgent):
    """
    Optimizer agent that searches for optimal strategy parameters.

    Given a strategy template and parameter ranges, runs backtests across
    the parameter space and identifies top-performing configurations.

    Usage:
        agent = OptimizerAgent()
        result = agent.run(
            strategy="ma",
            pair="EURUSD",
            param_ranges={
                "fast": {"min": 5, "max": 50, "step": 5},
                "slow": {"min": 20, "max": 200, "step": 10},
            },
            data_root="/path/to/data",
            data_days=180,
        )
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
            combinations.append(dict(zip(param_names, combo)))

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
        Run parameter optimization.

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
                results.append(
                    {
                        "params": params,
                        "metrics": metrics,
                        "sharpe": metrics.get("sharpe", float("-inf")),
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
