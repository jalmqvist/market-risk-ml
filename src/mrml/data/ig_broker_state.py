"""Typed IG demo broker state returned by read-only REST requests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from mrml.broker.evidence import (
    AffectedDeal,
    Confirmation,
    Position,
    Provenance,
    WorkingOrder,
)


@dataclass(frozen=True, repr=False)
class IGAccountBalance:
    balance: Decimal | None
    available: Decimal | None
    deposit: Decimal | None
    profit_loss: Decimal | None


@dataclass(frozen=True, repr=False)
class IGPosition:
    deal_id: str
    epic: str
    direction: str
    size: Decimal
    deal_reference: str | None
    status: str | None
    open_level: Decimal | None
    created_date: str | None


@dataclass(frozen=True, repr=False)
class IGWorkingOrder:
    deal_id: str
    epic: str
    direction: str
    size: Decimal
    deal_reference: str | None
    status: str | None
    order_type: str | None
    time_in_force: str | None
    order_level: Decimal | None
    created_date: str | None
    created_date_utc: str | None
    good_till_date: str | None


@dataclass(frozen=True, repr=False)
class IGActivityAction:
    affected_deal_id: str
    action_type: str


@dataclass(frozen=True, repr=False)
class IGActivity:
    raw_date: str
    deal_id: str | None
    epic: str | None
    activity_id: str | None
    deal_reference: str | None
    direction: str | None
    size: Decimal | None
    status: str | None
    activity_type: str | None
    channel: str | None
    actions: tuple[IGActivityAction, ...] | None


@dataclass(frozen=True, repr=False)
class IGAffectedDeal:
    deal_id: str
    status: str


@dataclass(frozen=True, repr=False)
class IGConfirmation:
    deal_reference: str
    deal_status: str
    affected_deals: tuple[IGAffectedDeal, ...] | None
    deal_id: str | None
    status: str | None
    reason: str | None
    epic: str | None
    direction: str | None
    size: Decimal | None
    level: Decimal | None
    stop_level: Decimal | None
    limit_level: Decimal | None
    raw_date: str | None


@dataclass(frozen=True, repr=False)
class IGPositionObservation:
    position: IGPosition
    observed_at: datetime
    created_date_utc: str | None
    stop_level: Decimal | None
    limit_level: Decimal | None


def confirmation_evidence(row: IGConfirmation, provenance: Provenance) -> Confirmation:
    """Map a scoped lookup to provider-neutral confirmation facts."""
    return Confirmation(
        provenance=provenance,
        returned_reference=row.deal_reference,
        outcome=row.deal_status,
        transaction_deal_id=row.deal_id,
        affected_deals=(
            tuple(
                AffectedDeal(deal.deal_id, deal.status) for deal in row.affected_deals
            )
            if row.affected_deals is not None
            else None
        ),
        instrument=row.epic,
        side=row.direction,
        quantity=row.size,
        reason=row.reason,
        object_status=row.status,
        definitive_rejection=row.deal_status == "REJECTED"
        and row.reason in {"MARKET_CLOSED", "MARKET_OFFLINE", "INSTRUMENT_NOT_FOUND"},
        level=row.level,
        stop_level=row.stop_level,
        limit_level=row.limit_level,
        raw_date=row.raw_date,
    )


def position_evidence(row: IGPosition) -> Position:
    """Preserve the object identity and terms independently of read coverage."""
    return Position(
        row.deal_id,
        row.epic,
        row.direction,
        row.size,
        row.deal_reference,
        status=row.status,
        open_level=row.open_level,
        created_date=row.created_date,
    )


def individual_position_evidence(row: IGPositionObservation) -> Position:
    return replace(
        position_evidence(row.position),
        created_date_utc=row.created_date_utc,
        stop_level=row.stop_level,
        limit_level=row.limit_level,
    )


def working_order_evidence(row: IGWorkingOrder) -> WorkingOrder:
    return WorkingOrder(
        row.deal_id,
        row.epic,
        row.deal_reference,
        side=row.direction,
        quantity=row.size,
        status=row.status,
        order_type=row.order_type,
        time_in_force=row.time_in_force,
        order_level=row.order_level,
        created_date=row.created_date,
        created_date_utc=row.created_date_utc,
        good_till_date=row.good_till_date,
    )
