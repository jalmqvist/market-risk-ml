"""Configured PhaseAware execution adapter; deliberately has no state resolver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from mrml.contracts.bundle import RecommendationBundle
from mrml.data.bars import D1Bar


@dataclass(frozen=True)
class SignalIntent:
    direction: str
    stop_distance: float
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.direction not in {"long", "short", "flat"}:
            raise ValueError("direction must be long, short, or flat")
        if self.direction != "flat" and self.stop_distance <= 0:
            raise ValueError("trade intents require a positive stop_distance")


class PhaseAwareStrategy(Protocol):
    """Adapter contract implemented by the pinned MPML PhaseAware strategy."""

    def generate_signal(self, pair: str, bars: Sequence[D1Bar]) -> SignalIntent: ...


@dataclass(frozen=True)
class StrategyDecision:
    pair: str
    composition: str
    direction: str
    stop_distance: float
    evaluation_id: str
    candidate_rank: int
    rationale: str


class StrategyRunner:
    """Run a frozen per-pair PhaseAware composition, not a ranked selector."""

    def __init__(
        self,
        *,
        pair_compositions: dict[str, str],
        strategies: dict[str, PhaseAwareStrategy],
    ) -> None:
        if not pair_compositions:
            raise ValueError("at least one frozen pair composition is required")
        if set(pair_compositions.values()) - strategies.keys():
            raise ValueError("a configured composition has no PhaseAware strategy")
        self._pair_compositions = MappingProxyType(dict(pair_compositions))
        self._strategies = dict(strategies)

    @property
    def pair_compositions(self) -> Mapping[str, str]:
        """Return the immutable strategy routing frozen for this runner."""
        return self._pair_compositions

    def decide(
        self, pair: str, bars: Sequence[D1Bar], bundle: RecommendationBundle
    ) -> StrategyDecision:
        """Validate recommendations, then invoke only the configured composition."""
        if pair not in self._pair_compositions:
            raise ValueError(f"pair {pair!r} is not in the frozen pair universe")
        candidates = bundle.candidates(pair)
        ranked_candidates = {
            (candidate.surface_id, candidate.state_id, candidate.rank)
            for candidate in candidates
        }
        if (
            not candidates
            or any(candidate.rank <= 0 for candidate in candidates)
            or len(ranked_candidates) != len(candidates)
        ):
            raise ValueError(f"invalid or missing recommendation candidates for {pair}")
        composition = self._pair_compositions[pair]
        evaluation = bundle.evaluation_for(pair, composition)
        if evaluation is None or not any(
            candidate.evaluation_id == evaluation.evaluation_id
            for candidate in candidates
        ):
            raise ValueError(f"configured composition {composition!r} is not evidenced")
        matching_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.evaluation_id == evaluation.evaluation_id
        )
        candidate_rank = min(item.rank for item in matching_candidates)
        # Candidate rank is retained as provenance. It never determines whether
        # this configured strategy enters; the strategy's own signal does.
        strategy = self._strategies[composition]
        intent = strategy.generate_signal(pair, bars)
        return StrategyDecision(
            pair=pair,
            composition=composition,
            direction=intent.direction,
            stop_distance=intent.stop_distance,
            evaluation_id=evaluation.evaluation_id,
            candidate_rank=candidate_rank,
            rationale=intent.rationale,
        )
