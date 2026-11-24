"""Broker-independent evidence and pure opening-intent assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True, repr=False)
class Scope:
    provider: str
    environment: str
    account_id: str


@dataclass(frozen=True, repr=False)
class Provenance:
    evidence_id: str
    scope: Scope
    observed_at: datetime
    source: str


@dataclass(frozen=True, repr=False)
class OpeningIntent:
    intent_id: str
    scope: Scope
    request_reference: str | None
    instrument: str
    side: str
    quantity: Decimal


@dataclass(frozen=True, repr=False)
class Acknowledgement:
    provenance: Provenance
    returned_reference: str


@dataclass(frozen=True, repr=False)
class AffectedDeal:
    deal_id: str
    effect: str


@dataclass(frozen=True, repr=False)
class Confirmation:
    # Adapters explicitly classify which rejection reasons permit exposure assessment.
    provenance: Provenance
    returned_reference: str
    outcome: str
    transaction_deal_id: str | None = None
    affected_deals: tuple[AffectedDeal, ...] | None = None
    instrument: str | None = None
    side: str | None = None
    quantity: Decimal | None = None
    reason: str | None = None
    object_status: str | None = None
    definitive_rejection: bool = False
    level: Decimal | None = None
    stop_level: Decimal | None = None
    limit_level: Decimal | None = None
    raw_date: str | None = None

    def facts(self) -> tuple[object, ...]:
        return (
            self.outcome,
            self.transaction_deal_id,
            (
                tuple(sorted((row.deal_id, row.effect) for row in self.affected_deals))
                if self.affected_deals is not None
                else None
            ),
            self.instrument,
            self.side,
            self.quantity,
            self.reason,
            self.object_status,
            self.definitive_rejection,
            self.level,
            self.stop_level,
            self.limit_level,
            self.raw_date,
        )


@dataclass(frozen=True, repr=False)
class Position:
    deal_id: str
    instrument: str
    side: str
    quantity: Decimal
    reference: str | None = None


@dataclass(frozen=True, repr=False)
class WorkingOrder:
    deal_id: str
    instrument: str
    reference: str | None = None


@dataclass(frozen=True, repr=False)
class PositionRead:
    provenance: Provenance
    complete: bool
    positions: tuple[Position, ...]
    # An individual lookup covers only its target, even when it returns no row.
    target_deal_id: str | None = None


@dataclass(frozen=True, repr=False)
class WorkingOrderRead:
    provenance: Provenance
    complete: bool
    orders: tuple[WorkingOrder, ...]


@dataclass(frozen=True, repr=False)
class ActivityOpening:
    """An activity action that explicitly claims an opening for a reference."""

    provenance: Provenance
    reference: str
    affected_deal_id: str


class CorrelationStatus(StrEnum):
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, repr=False)
class Correlation:
    status: CorrelationStatus
    reason: str
    intent_id: str
    evidence_ids: tuple[str, ...]
    confirmation_outcome: str | None = None


@dataclass(frozen=True, repr=False)
class OpeningEvidence:
    positions: PositionRead
    working_orders: WorkingOrderRead
    confirmations: tuple[Confirmation, ...] = ()
    acknowledgement: Acknowledgement | None = None
    activity_openings: tuple[ActivityOpening, ...] = ()
    confirmation_not_found: Provenance | None = None


def _terms(instrument: str, side: str, quantity: Decimal) -> tuple[str, str, Decimal]:
    return instrument, side, quantity


def correlate_opening(
    intent: OpeningIntent,
    evidence: OpeningEvidence,
    *,
    now: datetime,
    max_age: timedelta,
) -> Correlation:
    """Assess one intent using validated evidence and a caller-supplied clock."""
    positions, orders = evidence.positions, evidence.working_orders
    acknowledgement = evidence.acknowledgement
    sources = [
        positions.provenance,
        orders.provenance,
        *(row.provenance for row in evidence.confirmations),
        *(row.provenance for row in evidence.activity_openings),
    ]
    if acknowledgement is not None:
        sources.append(acknowledgement.provenance)
    if evidence.confirmation_not_found is not None:
        sources.append(evidence.confirmation_not_found)
    ids = tuple(sorted({row.evidence_id for row in sources}))
    outcome: str | None = None

    def result(status: CorrelationStatus, reason: str) -> Correlation:
        return Correlation(status, reason, intent.intent_id, ids, outcome)

    unresolved = CorrelationStatus.UNRESOLVED
    mismatch = CorrelationStatus.MISMATCH
    matched = CorrelationStatus.MATCHED
    if any(row.scope != intent.scope for row in sources):
        return result(unresolved, "WRONG_SCOPE")
    if not intent.request_reference:
        return result(unresolved, "REFERENCE_UNAVAILABLE")
    if acknowledgement is not None and (
        acknowledgement.returned_reference != intent.request_reference
    ):
        return result(mismatch, "ACKNOWLEDGEMENT_REFERENCE_CONFLICT")
    if any(
        row.returned_reference != intent.request_reference
        for row in evidence.confirmations
    ):
        return result(mismatch, "CONFIRMATION_REFERENCE_CONFLICT")
    if not evidence.confirmations:
        if evidence.confirmation_not_found is not None:
            return result(unresolved, "CONFIRMATION_NOT_FOUND")
        if acknowledgement is not None:
            return result(unresolved, "CONFIRMATION_PENDING")
        return result(unresolved, "SUBMISSION_UNCERTAIN")

    confirmation = evidence.confirmations[0]
    if any(row.facts() != confirmation.facts() for row in evidence.confirmations[1:]):
        return result(mismatch, "CONFLICTING_CONFIRMATIONS")
    if confirmation.outcome not in {"ACCEPTED", "REJECTED"}:
        return result(unresolved, "UNKNOWN_CONFIRMATION_OUTCOME")

    expected = _terms(intent.instrument, intent.side, intent.quantity)
    supplied = (confirmation.instrument, confirmation.side, confirmation.quantity)
    if any(
        actual is not None and actual != wanted
        for actual, wanted in zip(supplied, expected, strict=True)
    ):
        return result(mismatch, "CONFIRMATION_TERMS_CONFLICT")
    outcome = confirmation.outcome
    related_activity = {
        row.affected_deal_id
        for row in evidence.activity_openings
        if row.reference == intent.request_reference
    }
    affected = confirmation.affected_deals or ()
    affected_ids = {row.deal_id for row in affected}
    if outcome == "REJECTED":
        if related_activity:
            return result(mismatch, "ACTIVITY_CONFLICT")
        if not confirmation.definitive_rejection:
            return result(unresolved, "REJECTION_EXPOSURE_UNCERTAIN")
        if any(row.effect == "OPENED" for row in affected):
            return result(mismatch, "CONFIRMATION_EFFECT_CONFLICT")

    if not positions.complete or not orders.complete:
        return result(unresolved, "INCOMPLETE_EXPOSURE_READ")
    if (
        now.utcoffset() is None
        or max_age <= timedelta(0)
        or any(
            read.observed_at.utcoffset() is None
            or not timedelta(0) <= now - read.observed_at <= max_age
            for read in (positions.provenance, orders.provenance)
        )
    ):
        return result(unresolved, "STALE_OR_INVALID_EXPOSURE_READ")

    if outcome == "REJECTED":
        if positions.target_deal_id is not None:
            return result(unresolved, "REJECTION_REQUIRES_POSITION_LIST")
        exposure: tuple[Position | WorkingOrder, ...] = (
            *positions.positions,
            *orders.orders,
        )
        if any(
            row.reference == intent.request_reference or row.deal_id in affected_ids
            for row in exposure
        ):
            return result(mismatch, "EXPOSURE_WITH_REJECTION")
        return result(matched, "REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE")

    if len(affected) != 1 or affected[0].effect != "OPENED":
        return result(unresolved, "UNSUPPORTED_AFFECTED_DEALS")
    deal_id = affected[0].deal_id
    if related_activity - {deal_id}:
        return result(mismatch, "ACTIVITY_CONFLICT")
    if positions.target_deal_id is not None and positions.target_deal_id != deal_id:
        return result(unresolved, "POSITION_READ_TARGET_CONFLICT")
    if any(
        row.deal_id == deal_id or row.reference == intent.request_reference
        for row in orders.orders
    ):
        return result(mismatch, "CONTINGENT_EXPOSURE")
    matches = [row for row in positions.positions if row.deal_id == deal_id]
    if len(matches) != 1:
        return result(
            mismatch, "POSITION_ABSENT" if not matches else "DUPLICATE_POSITION"
        )
    [position] = matches
    if (
        position.reference is not None
        and position.reference != intent.request_reference
    ) or _terms(position.instrument, position.side, position.quantity) != expected:
        return result(mismatch, "POSITION_TERMS_CONFLICT")
    if any(
        row.deal_id != deal_id and row.reference == intent.request_reference
        for row in positions.positions
    ):
        return result(mismatch, "CONFLICTING_POSITION_LINK")
    return result(matched, "OPEN_POSITION_MATCHED")
