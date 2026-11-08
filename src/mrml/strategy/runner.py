"""Configured PhaseAware execution adapter; deliberately has no state resolver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from mrml.contracts.bundle import RecommendationBundle
from mrml.data.bars import D1Bar


@dataclass(frozen=True)
class SignalIntent:
    direction: str
    stop_distance: float
    rationale: str = ""
    # Optional strategy provenance. These are recorded, never applied: MRML's
    # risk model still sizes from ``stop_distance`` and its own limits.
    size_multiplier: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    signal_bar_timestamp: datetime | None = None
    decision_cutoff: datetime | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"long", "short", "flat"}:
            raise ValueError("direction must be long, short, or flat")
        if self.direction != "flat" and self.stop_distance <= 0:
            raise ValueError("trade intents require a positive stop_distance")


class PhaseAwareStrategy(Protocol):
    """Injected strategy seam; it is not a binding to MPML's runtime API."""

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
    size_multiplier: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    signal_bar_timestamp: datetime | None = None
    decision_cutoff: datetime | None = None


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
        raw_intent = strategy.generate_signal(pair, bars)
        intent = SignalIntent(
            direction=raw_intent.direction,
            stop_distance=raw_intent.stop_distance,
            rationale=raw_intent.rationale,
            size_multiplier=raw_intent.size_multiplier,
            stop_loss_pct=raw_intent.stop_loss_pct,
            take_profit_pct=raw_intent.take_profit_pct,
            signal_bar_timestamp=raw_intent.signal_bar_timestamp,
            decision_cutoff=raw_intent.decision_cutoff,
        )
        return StrategyDecision(
            pair=pair,
            composition=composition,
            direction=intent.direction,
            stop_distance=intent.stop_distance,
            evaluation_id=evaluation.evaluation_id,
            candidate_rank=candidate_rank,
            rationale=intent.rationale,
            size_multiplier=intent.size_multiplier,
            stop_loss_pct=intent.stop_loss_pct,
            take_profit_pct=intent.take_profit_pct,
            signal_bar_timestamp=intent.signal_bar_timestamp,
            decision_cutoff=intent.decision_cutoff,
        )
