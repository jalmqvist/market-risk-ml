"""MRML strategy adapters and the pinned-runtime contract boundary."""

from mrml.strategy.runner import (
    PhaseAwareStrategy,
    SignalIntent,
    StrategyDecision,
    StrategyRunner,
)
from mrml.strategy.runtime import (
    PHASEAWARE_V0_COMPOSITION,
    PINNED_MPML_REVISION,
    CanonicalPhaseAwareRuntime,
    InsufficientStrategyHistory,
    InvalidStrategyHistory,
    InvalidStrategyOutput,
    PhaseAwareDecision,
    PhaseAwareDecisionAdapter,
    PreparedPhaseAwareHistory,
)

__all__ = [
    "PINNED_MPML_REVISION",
    "PHASEAWARE_V0_COMPOSITION",
    "CanonicalPhaseAwareRuntime",
    "InsufficientStrategyHistory",
    "InvalidStrategyHistory",
    "InvalidStrategyOutput",
    "PhaseAwareDecision",
    "PhaseAwareDecisionAdapter",
    "PhaseAwareStrategy",
    "PreparedPhaseAwareHistory",
    "SignalIntent",
    "StrategyDecision",
    "StrategyRunner",
]
