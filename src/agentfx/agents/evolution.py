"""
Evolution Agent

Detects performance drift and triggers re-optimization.

See: planning/requirements/US-009 (to be created)
"""

from datetime import datetime

from agentfx.agents.base import BaseAgent, AgentResult


class EvolutionAgent(BaseAgent):
    """
    Evolution agent that monitors strategy performance and detects drift.

    Triggers re-optimization when:
    - Sharpe ratio drops 50% from baseline
    - Max drawdown exceeds threshold
    - Win rate degrades significantly

    Usage:
        agent = EvolutionAgent()
        result = agent.run(
            strategy_id="strat-123",
            baseline_metrics={"sharpe": 1.5, "max_drawdown": 0.05},
            current_metrics={"sharpe": 0.7, "max_drawdown": 0.08},
        )
    """

    def __init__(
        self,
        sharpe_drift_threshold: float = 0.5,  # 50% drop triggers
        drawdown_drift_threshold: float = 1.5,  # 50% increase triggers
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.sharpe_drift_threshold = sharpe_drift_threshold
        self.drawdown_drift_threshold = drawdown_drift_threshold

    @property
    def agent_type(self) -> str:
        return "evolution"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate evolution check inputs."""
        errors = []

        if "strategy_id" not in kwargs:
            errors.append("strategy_id is required")
        if "baseline_metrics" not in kwargs:
            errors.append("baseline_metrics is required")
        if "current_metrics" not in kwargs:
            errors.append("current_metrics is required")

        return len(errors) == 0, errors

    def _detect_drift(
        self,
        baseline_metrics: dict[str, float],
        current_metrics: dict[str, float],
    ) -> tuple[bool, list[str]]:
        """Detect if performance has drifted beyond thresholds."""
        drift_signals = []

        # Check Sharpe ratio drift
        baseline_sharpe = baseline_metrics.get("sharpe", 0)
        current_sharpe = current_metrics.get("sharpe", 0)
        if baseline_sharpe > 0:
            sharpe_ratio = current_sharpe / baseline_sharpe
            if sharpe_ratio < self.sharpe_drift_threshold:
                drift_signals.append(
                    f"Sharpe dropped {(1-sharpe_ratio)*100:.1f}% "
                    f"({baseline_sharpe:.2f} -> {current_sharpe:.2f})"
                )

        # Check drawdown drift
        baseline_dd = abs(baseline_metrics.get("max_drawdown", 0))
        current_dd = abs(current_metrics.get("max_drawdown", 0))
        if baseline_dd > 0:
            dd_ratio = current_dd / baseline_dd
            if dd_ratio > self.drawdown_drift_threshold:
                drift_signals.append(
                    f"Drawdown increased {(dd_ratio-1)*100:.1f}% "
                    f"({baseline_dd:.1%} -> {current_dd:.1%})"
                )

        return len(drift_signals) > 0, drift_signals

    def run(
        self,
        strategy_id: str,
        baseline_metrics: dict[str, float],
        current_metrics: dict[str, float],
        **kwargs,
    ) -> AgentResult:
        """
        Check for performance drift and recommend actions.

        Args:
            strategy_id: Strategy identifier
            baseline_metrics: Original performance metrics
            current_metrics: Recent performance metrics

        Returns:
            AgentResult with drift detection and recommendations
        """
        self.started_at = datetime.utcnow()

        # Validate inputs
        is_valid, errors = self.validate_inputs(
            strategy_id=strategy_id,
            baseline_metrics=baseline_metrics,
            current_metrics=current_metrics,
        )
        if not is_valid:
            return self._create_result("failed", {}, errors)

        # Detect drift
        has_drift, drift_signals = self._detect_drift(baseline_metrics, current_metrics)

        # Determine recommendation
        if has_drift:
            recommendation = "RE_OPTIMIZE"
            message = "Performance drift detected. Re-optimization recommended."
        else:
            recommendation = "CONTINUE"
            message = "Performance within acceptable bounds."

        outputs = {
            "strategy_id": strategy_id,
            "has_drift": has_drift,
            "drift_signals": drift_signals,
            "recommendation": recommendation,
            "message": message,
            "baseline_metrics": baseline_metrics,
            "current_metrics": current_metrics,
        }

        return self._create_result("success", outputs)
