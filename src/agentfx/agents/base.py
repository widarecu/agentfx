"""
Base Agent Interface

All autonomous agents inherit from BaseAgent and implement the run() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4


@dataclass
class AgentResult:
    """Result of an agent execution."""

    agent_type: str
    job_id: UUID
    status: str  # "success" | "failed" | "partial"
    started_at: datetime
    completed_at: datetime
    outputs: dict[str, Any]
    errors: list[str] | None = None


class BaseAgent(ABC):
    """
    Base class for all autonomous trading agents.

    Agents operate within the following constraints:
    - Risk per trade: <= 0.5% of capital
    - Daily loss limit: 2% (auto-halt)
    - Max drawdown: 10% (system halt)
    - Human approval required before live trading

    Subclasses must implement:
    - run(): Execute the agent's primary function
    - validate_inputs(): Verify inputs before execution
    """

    def __init__(self, job_id: UUID | None = None):
        self.job_id = job_id or uuid4()
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Return the agent type identifier (e.g., 'optimizer', 'validator')."""
        pass

    @abstractmethod
    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        """
        Validate inputs before execution.

        Returns:
            Tuple of (is_valid, error_messages)
        """
        pass

    @abstractmethod
    def run(self, **kwargs) -> AgentResult:
        """
        Execute the agent's primary function.

        Returns:
            AgentResult with outputs and status
        """
        pass

    def _create_result(
        self,
        status: str,
        outputs: dict[str, Any],
        errors: list[str] | None = None,
    ) -> AgentResult:
        """Helper to create a standardized AgentResult."""
        return AgentResult(
            agent_type=self.agent_type,
            job_id=self.job_id,
            status=status,
            started_at=self.started_at or datetime.utcnow(),
            completed_at=datetime.utcnow(),
            outputs=outputs,
            errors=errors,
        )
