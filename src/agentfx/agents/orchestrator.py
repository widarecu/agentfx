"""
Orchestrator Agent

Coordinates the full autonomous pipeline:
Discovery -> Optimize -> Validate -> [Human Approval] -> Paper

See: planning/requirements/US-007 (to be created)
"""

from datetime import datetime
from uuid import uuid4

from agentfx.agents.base import AgentResult, BaseAgent
from agentfx.agents.discovery import DiscoveryAgent
from agentfx.agents.optimizer import OptimizerAgent


class OrchestratorAgent(BaseAgent):
    """
    Orchestrator agent that coordinates the full autonomous pipeline.

    Pipeline stages:
    1. Discovery - Generate strategy hypotheses
    2. Optimization - Find best parameters for each hypothesis
    3. Validation - Validate top performers against Gate B
    4. Surface - Present validated strategies for human approval

    Usage:
        agent = OrchestratorAgent()
        result = agent.run(
            pairs=["EURUSD", "GBPUSD"],
            data_root="/path/to/data",
        )
    """

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """Validate orchestration inputs."""
        errors = []

        if "pairs" not in kwargs or not kwargs["pairs"]:
            errors.append("At least one pair is required")
        if "data_root" not in kwargs:
            errors.append("data_root is required")

        return len(errors) == 0, errors

    def run(
        self,
        pairs: list[str],
        data_root: str,
        max_hypotheses: int = 20,
        top_n_optimize: int = 3,
        **kwargs,
    ) -> AgentResult:
        """
        Run the full autonomous pipeline.

        Args:
            pairs: Currency pairs to explore
            data_root: Path to market data
            max_hypotheses: Max hypotheses from discovery
            top_n_optimize: Top N results from each optimization

        Returns:
            AgentResult with pipeline outcomes
        """
        self.started_at = datetime.utcnow()

        # Validate inputs
        is_valid, errors = self.validate_inputs(pairs=pairs, data_root=data_root)
        if not is_valid:
            return self._create_result("failed", {}, errors)

        pipeline_errors = []
        pipeline_stages = []

        # Stage 1: Discovery
        discovery_agent = DiscoveryAgent()
        discovery_result = discovery_agent.run(
            pairs=pairs,
            max_hypotheses=max_hypotheses,
        )
        pipeline_stages.append(
            {
                "stage": "discovery",
                "status": discovery_result.status,
                "hypotheses_generated": discovery_result.outputs.get(
                    "total_hypotheses", 0
                ),
            }
        )

        if discovery_result.status == "failed":
            pipeline_errors.extend(discovery_result.errors or [])
            return self._create_result(
                "failed",
                {"stages": pipeline_stages},
                pipeline_errors,
            )

        # Stage 2: Optimization (for each unique strategy+pair)
        hypotheses = discovery_result.outputs.get("hypotheses", [])
        optimization_results = []

        # Group hypotheses by strategy+pair
        grouped = {}
        for h in hypotheses:
            key = f"{h['strategy']}_{h['pair']}"
            if key not in grouped:
                grouped[key] = h

        for key, hypothesis in grouped.items():
            optimizer = OptimizerAgent()

            # Build param ranges from hypothesis
            # For now, use default ranges - in production, derive from hypothesis
            param_ranges = self._get_default_param_ranges(hypothesis["strategy"])

            opt_result = optimizer.run(
                strategy=hypothesis["strategy"],
                pair=hypothesis["pair"],
                param_ranges=param_ranges,
                data_root=data_root,
                top_n=top_n_optimize,
            )

            optimization_results.append(
                {
                    "strategy": hypothesis["strategy"],
                    "pair": hypothesis["pair"],
                    "status": opt_result.status,
                    "top_results": opt_result.outputs.get("top_results", []),
                }
            )

            if opt_result.errors:
                pipeline_errors.extend(opt_result.errors)

        pipeline_stages.append(
            {
                "stage": "optimization",
                "strategies_optimized": len(optimization_results),
                "results": optimization_results,
            }
        )

        # Stage 3: Validation (for top performers)
        # TODO: Implement validation stage
        validation_results = []
        pipeline_stages.append(
            {
                "stage": "validation",
                "status": "not_implemented",
                "message": "Validation stage pending implementation",
            }
        )

        # Stage 4: Surface for approval
        strategies_for_approval = []
        for opt in optimization_results:
            if opt["top_results"]:
                top = opt["top_results"][0]
                strategies_for_approval.append(
                    {
                        "strategy": opt["strategy"],
                        "pair": opt["pair"],
                        "params": top["params"],
                        "metrics": top["metrics"],
                        "validation_status": "pending",
                    }
                )

        pipeline_stages.append(
            {
                "stage": "approval_queue",
                "strategies_queued": len(strategies_for_approval),
                "strategies": strategies_for_approval,
            }
        )

        outputs = {
            "pipeline_id": str(uuid4()),
            "pairs": pairs,
            "stages": pipeline_stages,
            "strategies_for_approval": strategies_for_approval,
            "total_errors": len(pipeline_errors),
        }

        status = "success" if not pipeline_errors else "partial"
        return self._create_result(
            status, outputs, pipeline_errors if pipeline_errors else None
        )

    def _get_default_param_ranges(
        self, strategy: str
    ) -> dict[str, dict[str, int | float]]:
        """Get default parameter ranges for a strategy."""
        ranges = {
            "ma_crossover": {
                "fast": {"min": 5, "max": 30, "step": 5},
                "slow": {"min": 30, "max": 100, "step": 10},
            },
            "ma": {
                "fast": {"min": 5, "max": 30, "step": 5},
                "slow": {"min": 30, "max": 100, "step": 10},
            },
            "rsi_mean_reversion": {
                "window": {"min": 7, "max": 28, "step": 7},
            },
            "rsi": {
                "window": {"min": 7, "max": 28, "step": 7},
            },
            "donchian_breakout": {
                "window": {"min": 10, "max": 40, "step": 10},
            },
            "donchian": {
                "window": {"min": 10, "max": 40, "step": 10},
            },
        }
        return ranges.get(strategy, {"window": {"min": 10, "max": 50, "step": 10}})
