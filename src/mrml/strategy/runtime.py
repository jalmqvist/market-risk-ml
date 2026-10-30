"""Fail-closed contract for a pinned, MPML-owned PhaseAware runtime."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from numbers import Real
from types import MappingProxyType
from typing import Protocol

from mrml.data.bars import D1Bar

PINNED_MPML_REVISION = "90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d"
PHASEAWARE_V0_COMPOSITION = "PhaseAware_TF4_MR42"
_FORWARD_LABELS = {
    "next_return",
    "next_direction",
    "next_direction_binary",
}
_PAIR_PATTERN = re.compile(r"[A-Z]{3}_[A-Z]{3}")


class InvalidStrategyHistory(ValueError):
    """Raised when history cannot safely be passed to a strategy runtime."""


class InsufficientStrategyHistory(InvalidStrategyHistory):
    """Raised when no completed bars are available for a decision."""


class InvalidStrategyOutput(ValueError):
    """Raised when a strategy runtime returns an invalid or mismatched result."""


def _utc_datetime(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidStrategyHistory(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _finite_number(value: object) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class PreparedPhaseAwareHistory:
    """Completed price bars and caller-supplied canonical MPML input fields.

    The ``phase`` field is required because the audited PhaseAware input
    includes it. Other MPML-prepared fields are preserved without MRML
    calculating or interpreting them. Completion is an explicit upstream
    attestation because ``D1Bar`` itself has no completion flag.
    """

    pair: str
    decision_cutoff: datetime
    bars: tuple[D1Bar, ...]
    bar_completed: Sequence[bool]
    canonical_fields: Mapping[str, Sequence[object]]

    def __post_init__(self) -> None:
        if not isinstance(self.pair, str) or _PAIR_PATTERN.fullmatch(self.pair) is None:
            raise InvalidStrategyHistory("pair must be an FX pair like EUR_USD")
        cutoff = _utc_datetime(self.decision_cutoff, "decision_cutoff")
        if isinstance(self.bars, (str, bytes)) or not isinstance(self.bars, Sequence):
            raise InvalidStrategyHistory("bars must be a sequence of D1Bar values")
        bars = tuple(self.bars)
        if not bars:
            raise InsufficientStrategyHistory(
                "at least one completed D1 bar is required"
            )
        if isinstance(self.bar_completed, (str, bytes)) or not isinstance(
            self.bar_completed, Sequence
        ):
            raise InvalidStrategyHistory("bar_completed must be a boolean sequence")
        completion = tuple(self.bar_completed)
        if len(completion) != len(bars) or any(
            value is not True for value in completion
        ):
            raise InvalidStrategyHistory(
                "every history bar must be explicitly marked complete"
            )

        normalized_bars: list[D1Bar] = []
        prior_timestamp: datetime | None = None
        for bar in bars:
            if not isinstance(bar, D1Bar):
                raise InvalidStrategyHistory("history entries must be D1Bar values")
            timestamp = _utc_datetime(bar.timestamp, "bar timestamp")
            if prior_timestamp is not None and timestamp <= prior_timestamp:
                reason = (
                    "duplicate bar timestamps"
                    if timestamp == prior_timestamp
                    else "bar timestamps must be strictly increasing"
                )
                raise InvalidStrategyHistory(reason)
            if timestamp >= cutoff:
                raise InvalidStrategyHistory(
                    "history contains a bar at or after decision_cutoff"
                )
            prices = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if (
                not all(_finite_number(value) for value in prices)
                or min(bar.open, bar.high, bar.low, bar.close) <= 0
                or bar.volume < 0
                or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close)
                or bar.low > bar.high
            ):
                raise InvalidStrategyHistory("D1 bar values are invalid")
            normalized_bars.append(
                D1Bar(
                    timestamp,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
            )
            prior_timestamp = timestamp

        if not isinstance(self.canonical_fields, Mapping):
            raise InvalidStrategyHistory("canonical_fields must be a mapping")
        fields: dict[str, tuple[object, ...]] = {}
        for name, values in self.canonical_fields.items():
            if not isinstance(name, str) or not name.strip():
                raise InvalidStrategyHistory("canonical field names must be non-empty")
            if name.strip().casefold() in _FORWARD_LABELS:
                raise InvalidStrategyHistory(
                    f"forward-looking field {name!r} is not a runtime input"
                )
            if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
                raise InvalidStrategyHistory(
                    f"canonical field {name!r} must contain one value per bar"
                )
            column = tuple(values)
            if len(column) != len(normalized_bars):
                raise InvalidStrategyHistory(
                    f"canonical field {name!r} does not align with history"
                )
            if not all(
                (isinstance(value, str) and bool(value.strip()))
                or _finite_number(value)
                for value in column
            ):
                raise InvalidStrategyHistory(
                    f"canonical field {name!r} contains an invalid value"
                )
            fields[name] = column

        phases = fields.get("phase")
        if phases is None or not all(isinstance(value, str) for value in phases):
            raise InvalidStrategyHistory(
                "canonical_fields must include a string phase for every bar"
            )

        object.__setattr__(self, "decision_cutoff", cutoff)
        object.__setattr__(self, "bars", tuple(normalized_bars))
        object.__setattr__(self, "bar_completed", completion)
        object.__setattr__(self, "canonical_fields", MappingProxyType(fields))


@dataclass(frozen=True)
class PhaseAwareDecision:
    """Canonical numeric signal and provenance; not an order instruction.

    ``canonical_signal`` is passed through unchanged and is not mapped to an
    MRML direction. The result is available no earlier than ``decision_cutoff``;
    order actionability remains outside this strategy boundary.
    """

    pair: str
    signal_bar_timestamp: datetime
    decision_cutoff: datetime
    actionable_after: datetime
    canonical_signal: float
    phase: str | None
    stop_loss_pct: float | None
    take_profit_pct: float | None
    composition: str
    mpml_revision: str
    runtime_provenance: str

    def __post_init__(self) -> None:
        if not isinstance(self.pair, str) or _PAIR_PATTERN.fullmatch(self.pair) is None:
            raise InvalidStrategyOutput("result pair is invalid")
        timestamps: dict[str, datetime] = {}
        for field in ("signal_bar_timestamp", "decision_cutoff", "actionable_after"):
            value = getattr(self, field)
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise InvalidStrategyOutput(f"{field} must be timezone-aware")
            timestamps[field] = value.astimezone(UTC)
        if timestamps["actionable_after"] != timestamps["decision_cutoff"]:
            raise InvalidStrategyOutput("actionable_after must equal decision_cutoff")
        if not _finite_number(self.canonical_signal):
            raise InvalidStrategyOutput("canonical_signal must be finite")
        if self.phase is not None and (
            not isinstance(self.phase, str) or not self.phase.strip()
        ):
            raise InvalidStrategyOutput("phase must be a non-empty string or None")
        for field in ("stop_loss_pct", "take_profit_pct"):
            value = getattr(self, field)
            if value is not None and (not _finite_number(value) or value < 0):
                raise InvalidStrategyOutput(f"{field} must be finite and non-negative")
        if self.composition != PHASEAWARE_V0_COMPOSITION:
            raise InvalidStrategyOutput("unexpected PhaseAware composition")
        if self.mpml_revision != PINNED_MPML_REVISION:
            raise InvalidStrategyOutput("unexpected MPML revision")
        if not isinstance(self.runtime_provenance, str) or not (
            self.runtime_provenance.strip()
        ):
            raise InvalidStrategyOutput("runtime_provenance must be non-empty")

        for field, value in timestamps.items():
            object.__setattr__(self, field, value)


class CanonicalPhaseAwareRuntime(Protocol):
    """Expected contract for an MPML-owned runtime implementation."""

    def latest_decision(
        self, history: PreparedPhaseAwareHistory
    ) -> PhaseAwareDecision: ...


class PhaseAwareDecisionAdapter:
    """Validate bounded inputs and outputs around an injected canonical runtime."""

    def __init__(self, runtime: CanonicalPhaseAwareRuntime) -> None:
        if not callable(getattr(runtime, "latest_decision", None)):
            raise ValueError("runtime must provide latest_decision")
        self._runtime = runtime

    def decide(
        self,
        *,
        pair: str,
        bars: Sequence[D1Bar],
        bar_completed: Sequence[bool],
        decision_cutoff: datetime,
        canonical_fields: Mapping[str, Sequence[object]],
    ) -> PhaseAwareDecision:
        if isinstance(bars, (str, bytes)) or not isinstance(bars, Sequence):
            raise InvalidStrategyHistory("bars must be a sequence of D1Bar values")
        history = PreparedPhaseAwareHistory(
            pair=pair,
            decision_cutoff=decision_cutoff,
            bars=tuple(bars),
            bar_completed=bar_completed,
            canonical_fields=canonical_fields,
        )
        result = self._runtime.latest_decision(history)
        if not isinstance(result, PhaseAwareDecision):
            raise InvalidStrategyOutput(
                "canonical runtime must return PhaseAwareDecision"
            )
        if result.pair != history.pair:
            raise InvalidStrategyOutput("runtime result pair does not match input")
        if result.signal_bar_timestamp != history.bars[-1].timestamp:
            raise InvalidStrategyOutput(
                "runtime result timestamp is not the latest completed bar"
            )
        if result.decision_cutoff != history.decision_cutoff:
            raise InvalidStrategyOutput("runtime result cutoff does not match input")
        return result
