"""
Validation Agent

Validates strategies against Gate B thresholds using walk-forward testing.

See: planning/requirements/US-003 (to be created)

Gate B Thresholds (from MVP_CONSTRAINTS.md):
- Sharpe ratio >= 1.0
- Max drawdown <= 10%
- Hit rate >= 48%
- Minimum 300 trades in out-of-sample period
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentfx.agents.base import AgentResult, BaseAgent


@dataclass
class GateBThresholds:
    """Gate B validation thresholds."""

    min_sharpe: float = 1.0
    max_drawdown: float = 0.10  # 10%
    min_hit_rate: float = 0.48  # 48%
    min_trades: int = 300
    min_oos_days: int = 180  # 6 months


class ValidatorAgent(BaseAgent):
    """
    Validator agent that runs walk-forward validation with Gate B enforcement.

    Validates a strategy configuration against thresholds before
    allowing promotion to paper trading.

    Usage:
        agent = ValidatorAgent()
        result = agent.run(
            strategy="ma",
            pair="EURUSD",
            params={"fast": 10, "slow": 50},
            data_root="/path/to/data",
        )
    """

    def __init__(self, thresholds: GateBThresholds | None = None, **kwargs):
        super().__init__(**kwargs)
        self.thresholds = thresholds or GateBThresholds()

    @property
    def agent_type(self) -> str:
        return "validator"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate validation inputs."""
        errors = []

        if "strategy" not in kwargs:
            errors.append("strategy is required")
        if "pair" not in kwargs:
            errors.append("pair is required")
        if "params" not in kwargs:
            errors.append("params is required")
        if "data_root" not in kwargs:
            errors.append("data_root is required")

        return len(errors) == 0, errors

    def _check_thresholds(self, metrics: dict[str, Any]) -> tuple[bool, list[str]]:
        """Check if metrics pass Gate B thresholds."""
        failures = []

        sharpe = metrics.get("sharpe", 0)
        if sharpe < self.thresholds.min_sharpe:
            failures.append(
                f"Sharpe {sharpe:.2f} < {self.thresholds.min_sharpe} (required)"
            )

        max_dd = abs(metrics.get("max_drawdown", 1.0))
        if max_dd > self.thresholds.max_drawdown:
            failures.append(
                f"Max drawdown {max_dd:.1%} > {self.thresholds.max_drawdown:.1%} (limit)"
            )

        hit_rate = metrics.get("hit_rate", 0)
        if hit_rate < self.thresholds.min_hit_rate:
            failures.append(
                f"Hit rate {hit_rate:.1%} < {self.thresholds.min_hit_rate:.1%} (required)"
            )

        trade_count = metrics.get("trade_count", 0)
        if trade_count < self.thresholds.min_trades:
            failures.append(
                f"Trade count {trade_count} < {self.thresholds.min_trades} (required)"
            )

        return len(failures) == 0, failures

    def run(
        self,
        strategy: str,
        pair: str,
        params: dict[str, Any],
        data_root: str,
        **kwargs,
    ) -> AgentResult:
        """
        Run walk-forward validation with Gate B checks.

        Args:
            strategy: Strategy name
            pair: Currency pair
            params: Strategy parameters
            data_root: Path to market data

        Returns:
            AgentResult with pass/fail status and threshold checks
        """
        self.started_at = datetime.utcnow()

        # Validate inputs
        is_valid, errors = self.validate_inputs(
            strategy=strategy,
            pair=pair,
            params=params,
            data_root=data_root,
        )
        if not is_valid:
            return self._create_result("failed", {}, errors)

        # TODO: Implement walk-forward validation
        # For now, return placeholder result
        outputs = {
            "strategy": strategy,
            "pair": pair,
            "params": params,
            "validation_type": "walk_forward",
            "gate_b_pass": False,
            "threshold_checks": [],
            "message": "Validator agent not yet implemented",
        }

        return self._create_result("failed", outputs, ["Not yet implemented"])
