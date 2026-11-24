"""Typed IG demo broker state returned by read-only REST requests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


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
