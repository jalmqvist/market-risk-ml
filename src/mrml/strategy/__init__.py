"""MRML strategy adapters and the pinned-runtime contract boundary."""

from mrml.strategy.mpml_runtime import (
    MpmlPhaseAwareRuntime,
    MpmlPhaseAwareStrategy,
    MpmlRuntimeUnavailable,
    UnexecutableDecision,
    signal_intent_from_decision,
    verify_mpml_checkout,
)
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
    CompletedD1History,
    InsufficientStrategyHistory,
    InvalidStrategyHistory,
    InvalidStrategyOutput,
    PhaseAwareDecision,
    PhaseAwareDecisionAdapter,
)

__all__ = [
    "MpmlPhaseAwareRuntime",
    "MpmlPhaseAwareStrategy",
    "MpmlRuntimeUnavailable",
    "UnexecutableDecision",
    "signal_intent_from_decision",
    "verify_mpml_checkout",
    "PINNED_MPML_REVISION",
    "PHASEAWARE_V0_COMPOSITION",
    "CanonicalPhaseAwareRuntime",
    "InsufficientStrategyHistory",
    "InvalidStrategyHistory",
    "InvalidStrategyOutput",
    "PhaseAwareDecision",
    "PhaseAwareDecisionAdapter",
    "PhaseAwareStrategy",
    "CompletedD1History",
    "SignalIntent",
    "StrategyDecision",
    "StrategyRunner",
]
