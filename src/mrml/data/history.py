"""Sequence diagnostics and explicit market-data eligibility policies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from mrml.data.canonical import (
    CompletionState,
    InstrumentMapping,
    Resolution,
    SourceBar,
    SourceProduct,
    require_text,
    require_utc,
)


class GapKind(StrEnum):
    EXPECTED_CLOSURE = "EXPECTED_CLOSURE"
    MISSING_EXPECTED_TRADING = "MISSING_EXPECTED_TRADING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SessionEvidence:
    kind: GapKind
    reference: str

    def __post_init__(self) -> None:
        require_text(self.reference, "session evidence")
        if not isinstance(self.kind, GapKind) or self.kind is GapKind.UNKNOWN:
            raise ValueError("Session evidence must classify the gap")


# Evidence must describe the supplied product and whole missing UTC range.
SessionLookup = Callable[[SourceProduct, datetime, datetime], SessionEvidence | None]


@dataclass(frozen=True)
class CalendarGap:
    start: datetime
    end: datetime
    evidence: SessionEvidence | None

    @property
    def kind(self) -> GapKind:
        return self.evidence.kind if self.evidence else GapKind.UNKNOWN


class SequenceIssue(StrEnum):
    MIXED_SERIES = "MIXED_SERIES"
    UNORDERED = "UNORDERED"
    DUPLICATE = "DUPLICATE"
    OVERLAP = "OVERLAP"


@dataclass(frozen=True)
class SequenceDiagnostics:
    issues: tuple[SequenceIssue, ...]
    gaps: tuple[CalendarGap, ...]


def diagnose_history(
    bars: Sequence[SourceBar], *, session_lookup: SessionLookup | None = None
) -> SequenceDiagnostics:
    """Report defects in input order and interior gaps in chronological order."""
    issues: set[SequenceIssue] = set()
    if len({(bar.mapping, bar.resolution) for bar in bars}) > 1:
        return SequenceDiagnostics((SequenceIssue.MIXED_SERIES,), ())
    if any(
        later.start < earlier.start
        for earlier, later in zip(bars, bars[1:], strict=False)
    ):
        issues.add(SequenceIssue.UNORDERED)
    ordered = sorted(bars, key=lambda bar: bar.start)
    gaps: list[CalendarGap] = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if (earlier.start, earlier.end) == (later.start, later.end):
            issues.add(SequenceIssue.DUPLICATE)
        if later.start < earlier.end:
            issues.add(SequenceIssue.OVERLAP)
        if later.start > earlier.end:
            evidence = (
                session_lookup(earlier.mapping.product, earlier.end, later.start)
                if session_lookup is not None
                else None
            )
            gaps.append(CalendarGap(earlier.end, later.start, evidence))
    return SequenceDiagnostics(tuple(sorted(issues)), tuple(gaps))


@dataclass(frozen=True)
class EligibilityPolicy:
    require_complete: bool
    require_first_availability: bool
    require_session_coverage: bool
    max_age: timedelta | None = None
    minimum_bars: int = 1

    def __post_init__(self) -> None:
        if type(self.minimum_bars) is not int or self.minimum_bars < 1:
            raise ValueError("minimum_bars must be a positive integer")
        if self.max_age is not None and self.max_age < timedelta(0):
            raise ValueError("max_age must be nonnegative")


class Refusal(StrEnum):
    UNSUPPORTED_RESOLUTION = "UNSUPPORTED_RESOLUTION"
    UNVALIDATED_MAPPING = "UNVALIDATED_MAPPING"
    INVALID_HISTORY = "INVALID_HISTORY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNVERIFIED_SESSION_COVERAGE = "UNVERIFIED_SESSION_COVERAGE"
    MISSING_EXPECTED_TRADING = "MISSING_EXPECTED_TRADING"
    UNKNOWN_COMPLETION = "UNKNOWN_COMPLETION"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN_AVAILABILITY = "UNKNOWN_AVAILABILITY"
    NOT_RETRIEVED_AT_EVALUATION = "NOT_RETRIEVED_AT_EVALUATION"
    STALE = "STALE"


@dataclass(frozen=True)
class Eligibility:
    refusals: tuple[Refusal, ...]
    sequence: SequenceDiagnostics
    unknown_completion: bool
    unknown_availability: bool

    @property
    def eligible(self) -> bool:
        return not self.refusals


def evaluate_history(
    bars: Sequence[SourceBar],
    *,
    policy: EligibilityPolicy,
    evaluated_at: datetime,
    validated_mapping: InstrumentMapping | None,
    resolution: str = Resolution.H1,
    session_lookup: SessionLookup | None = None,
) -> Eligibility:
    """Evaluate observed history as known at evaluated_at, with no clock reads."""
    require_utc(evaluated_at, "evaluation time")
    sequence = diagnose_history(bars, session_lookup=session_lookup)
    unknown_completion = any(
        bar.completion.state is CompletionState.UNKNOWN for bar in bars
    )
    unknown_availability = any(
        bar.availability.first_available_at is None for bar in bars
    )
    refused: set[Refusal] = set()
    if resolution != Resolution.H1:
        refused.add(Refusal.UNSUPPORTED_RESOLUTION)
    if validated_mapping is None or any(
        bar.mapping != validated_mapping for bar in bars
    ):
        refused.add(Refusal.UNVALIDATED_MAPPING)
    if sequence.issues:
        refused.add(Refusal.INVALID_HISTORY)
    if len(bars) < policy.minimum_bars:
        refused.add(Refusal.INSUFFICIENT_HISTORY)
    if any(gap.kind is GapKind.MISSING_EXPECTED_TRADING for gap in sequence.gaps):
        refused.add(Refusal.MISSING_EXPECTED_TRADING)
    if policy.require_session_coverage and any(
        gap.kind is GapKind.UNKNOWN for gap in sequence.gaps
    ):
        refused.add(Refusal.UNVERIFIED_SESSION_COVERAGE)
    if policy.require_complete and unknown_completion:
        refused.add(Refusal.UNKNOWN_COMPLETION)
    if any(bar.completion.state is CompletionState.INCOMPLETE for bar in bars):
        refused.add(Refusal.INCOMPLETE)
    if policy.require_first_availability and unknown_availability:
        refused.add(Refusal.UNKNOWN_AVAILABILITY)
    if any(bar.retrieved_at > evaluated_at for bar in bars):
        refused.add(Refusal.NOT_RETRIEVED_AT_EVALUATION)
    if (
        bars
        and policy.max_age is not None
        and (evaluated_at - max(bar.end for bar in bars) > policy.max_age)
    ):
        refused.add(Refusal.STALE)
    return Eligibility(
        tuple(sorted(refused)), sequence, unknown_completion, unknown_availability
    )
