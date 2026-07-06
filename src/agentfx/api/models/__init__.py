# Import all models here to make them available via the models package
from .evolution import (
    AgentType,
    DecisionLog,
    EvolutionRun,
    EvolutionRunStatus,
    Generation,
    GenomeOrigin,
    StrategyGenome,
    UserAction,
    ValidationFold,
)
from .instrument import Instrument
from .market_data import Candle, MarketDataUpdate
from .optimization import (
    JobStatus,
    OptimizationJob,
    OptimizationSignal,
    OptimizationTrade,
    OptimizationTrial,
    StrategyType,
)
from .run import Order, Run, RunEnvironment, RunStatus, Trade
from .strategy import Strategy
from .user import User, UserRole
from .user_credentials import (
    OandaEnvironment,
    UserOandaAccount,
    UserOandaCredentials,
)
from .verified_gap import VerifiedEmptyGap

# Make models available at the package level
__all__ = [
    "User",
    "UserRole",
    "Run",
    "Trade",
    "Order",
    "RunStatus",
    "RunEnvironment",
    "Strategy",
    "Candle",
    "MarketDataUpdate",
    "Instrument",
    # Optimization (v0.2.0)
    "OptimizationJob",
    "OptimizationTrial",
    "OptimizationSignal",
    "OptimizationTrade",
    "JobStatus",
    "StrategyType",
    "VerifiedEmptyGap",
    # Evolution (v0.3.0)
    "EvolutionRun",
    "EvolutionRunStatus",
    "Generation",
    "StrategyGenome",
    "GenomeOrigin",
    "ValidationFold",
    "DecisionLog",
    "AgentType",
    "UserAction",
    # User credentials (US-019 / v0.6.0)
    "UserOandaCredentials",
    "UserOandaAccount",
    "OandaEnvironment",
]
