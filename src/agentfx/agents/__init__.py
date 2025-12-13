"""
AgentFX Autonomous Trading Agents

This module contains the autonomous agents that power the AgentFX strategy factory:

- optimizer: Parameter optimization using grid/Bayesian search
- validator: Walk-forward validation with Gate B threshold enforcement
- discovery: Strategy hypothesis generation and exploration
- evolution: Performance drift detection and re-optimization triggers
- orchestrator: Pipeline coordination (discover -> optimize -> validate)

Each agent operates autonomously within the defined constraints (MVP_CONSTRAINTS.md)
and surfaces strategies for human approval before live trading.
"""

from agentfx.agents.base import BaseAgent

__all__ = ["BaseAgent"]
