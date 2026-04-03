"""
Discovery Agent

Generates strategy hypothesis variations for testing.

See: planning/requirements/US-005 (to be created)
"""

from datetime import datetime
from typing import Any

from agentfx.agents.base import AgentResult, BaseAgent

# Strategy templates for discovery
STRATEGY_TEMPLATES = {
    "ma_crossover": {
        "base_params": {"fast": 20, "slow": 50},
        "param_ranges": {
            "fast": [5, 10, 15, 20, 30, 40],
            "slow": [30, 50, 75, 100, 150, 200],
        },
    },
    "rsi_mean_reversion": {
        "base_params": {"window": 14, "lower": 30, "upper": 70},
        "param_ranges": {
            "window": [7, 14, 21, 28],
            "lower": [20, 25, 30, 35],
            "upper": [65, 70, 75, 80],
        },
    },
    "donchian_breakout": {
        "base_params": {"window": 20},
        "param_ranges": {
            "window": [10, 15, 20, 25, 30, 40, 50],
        },
    },
}


class DiscoveryAgent(BaseAgent):
    """
    Discovery agent that generates strategy variations for exploration.

    Explores the strategy space by generating combinations of:
    - Different strategy types
    - Parameter variations within each type
    - Potentially new indicator combinations (future)

    Usage:
        agent = DiscoveryAgent()
        result = agent.run(pairs=["EURUSD", "GBPUSD"])
    """

    @property
    def agent_type(self) -> str:
        return "discovery"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate discovery inputs."""
        errors = []

        pairs = kwargs.get("pairs", [])
        if not pairs:
            errors.append("At least one pair is required")

        return len(errors) == 0, errors

    def _generate_hypotheses(
        self,
        pairs: list[str],
        max_per_strategy: int = 10,
    ) -> list[dict[str, Any]]:
        """Generate strategy hypotheses to test."""
        hypotheses = []

        for pair in pairs:
            for strategy_name, template in STRATEGY_TEMPLATES.items():
                param_ranges = template["param_ranges"]

                # Generate parameter combinations
                param_names = list(param_ranges.keys())
                param_values = [param_ranges[name] for name in param_names]

                # Create combinations (limited)
                from itertools import product

                all_combos = list(product(*param_values))
                # Sample if too many
                if len(all_combos) > max_per_strategy:
                    import random

                    all_combos = random.sample(all_combos, max_per_strategy)

                for combo in all_combos:
                    params = dict(zip(param_names, combo, strict=False))
                    hypotheses.append(
                        {
                            "strategy": strategy_name,
                            "pair": pair,
                            "params": params,
                            "source": "grid_exploration",
                            "reasoning": f"Systematic exploration of {strategy_name} parameter space",
                        }
                    )

        return hypotheses

    def run(
        self,
        pairs: list[str],
        max_hypotheses: int = 50,
        **kwargs,
    ) -> AgentResult:
        """
        Generate strategy hypotheses for exploration.

        Args:
            pairs: Currency pairs to generate hypotheses for
            max_hypotheses: Maximum number of hypotheses to generate

        Returns:
            AgentResult with generated hypotheses
        """
        self.started_at = datetime.utcnow()

        # Validate inputs
        is_valid, errors = self.validate_inputs(pairs=pairs)
        if not is_valid:
            return self._create_result("failed", {}, errors)

        # Generate hypotheses
        hypotheses = self._generate_hypotheses(pairs)

        # Limit to max
        if len(hypotheses) > max_hypotheses:
            import random

            hypotheses = random.sample(hypotheses, max_hypotheses)

        outputs = {
            "total_hypotheses": len(hypotheses),
            "pairs": pairs,
            "strategies_explored": list(STRATEGY_TEMPLATES.keys()),
            "hypotheses": hypotheses,
        }

        return self._create_result("success", outputs)
