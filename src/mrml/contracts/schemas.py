"""Frozen dataclasses representing the supported MPML artifact schema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def immutable_metadata(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only copy of metadata; callers cannot mutate schema instances."""
    return MappingProxyType({key: _freeze(value) for key, value in values.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class StrategyEvaluation:
    """A single evaluated strategy; pair is unresolved per ADR-001."""

    evaluation_id: str
    surface_id: str
    surface_version: str
    state_id: str
    strategy_id: str
    expected_return: float
    expected_sharpe: float
    expected_drawdown: float
    win_rate: float | None
    confidence: float | None
    stability: float
    n_folds: int
    n_trades: int
    metadata: Mapping[str, Any]

    # TODO(ADR-001): Confirm pair dimension and strategy_id semantics with MPML.
    @property
    def pair(self) -> str | None:
        """Return metadata pair when present; semantics remain unresolved in ADR-001."""
        pair = self.metadata.get("pair")
        return pair if isinstance(pair, str) else None


@dataclass(frozen=True)
class Recommendation:
    """A general recommendation record from recommendations.parquet."""

    recommendation_id: str
    evaluation_id: str
    rank: int
    recommendation_policy: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class RecommendationPerPair:
    """A pair-specific recommendation with state retained as provenance."""

    recommendation_id: str
    evaluation_id: str
    pair: str
    surface_id: str
    state_id: str
    rank: int
    recommendation_policy: str
    metadata: Mapping[str, Any]
