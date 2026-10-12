"""Frozen dataclasses representing the supported MPML artifact schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Mapping


def immutable_metadata(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only copy of metadata; callers cannot mutate schema instances."""
    return MappingProxyType(dict(values))


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

    @property
    def pair(self) -> str | None:
        """Return the metadata pair when present; its contract semantics remain ADR-001."""
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


ManifestValue = str | int | float | bool | None
ManifestTimestamp = date | datetime | str | None
