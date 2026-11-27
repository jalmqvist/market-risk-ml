"""Typed, diagnostic-only views of native IG market detail fields."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")
Availability = Literal["available", "missing", "malformed", "unsupported", "ambiguous"]


@dataclass(frozen=True)
class NativeField(Generic[T]):
    status: Availability
    value: T | None = None


@dataclass(frozen=True)
class NativeRule:
    value: NativeField[Decimal]
    unit: NativeField[str]


@dataclass(frozen=True)
class MarginBand:
    currency: NativeField[str]
    margin_percent: NativeField[Decimal]
    minimum: NativeField[Decimal]
    maximum: NativeField[Decimal]


@dataclass(frozen=True)
class OrderCurrency:
    code: NativeField[str]
    default: NativeField[bool]


@dataclass(frozen=True)
class IGExecutionMarket:
    epic: str
    instrument_type: str
    expiry: NativeField[str]
    size_unit: NativeField[str]
    size_increment: NativeField[Decimal]
    size_precision: NativeField[int]
    contract_size: NativeField[Decimal]
    lot_size: NativeField[Decimal]
    currencies: NativeField[tuple[OrderCurrency, ...]]
    market_status: NativeField[str]
    market_order_preference: NativeField[str]
    force_open_allowed: NativeField[bool]
    stops_limits_allowed: NativeField[bool]
    controlled_risk_allowed: NativeField[bool]
    minimum_deal_size: NativeField[NativeRule]
    minimum_normal_stop_or_limit: NativeField[NativeRule]
    minimum_controlled_risk_stop: NativeField[NativeRule]
    maximum_stop_or_limit: NativeField[NativeRule]
    minimum_step_distance: NativeField[NativeRule]
    limited_risk_premium: NativeField[NativeRule]
    slippage_factor: NativeField[NativeRule]
    margin_factor: NativeField[Decimal]
    margin_factor_unit: NativeField[str]
    margin_bands: NativeField[tuple[MarginBand, ...]]
    quote_decimal_places_factor: NativeField[Decimal]
    scaling_factor: NativeField[Decimal]
    one_pip_means: NativeField[Decimal]
    value_of_one_pip: NativeField[Decimal]


def parse_market_fields(
    epic: str, payload: dict[str, Any], decimal: Callable[[Any], Decimal]
) -> IGExecutionMarket:
    """Map documented fields after the client has verified market identity."""
    instrument = payload["instrument"]
    snapshot = payload["snapshot"]
    rules = payload.get("dealingRules")

    def number(item: Any) -> Decimal:
        value = decimal(item)
        # Keep exact scientific notation compact without forwarding arbitrary text.
        if (
            value < 0
            or abs(value.adjusted()) > 100
            or len(value.as_tuple().digits) > 100
        ):
            raise ValueError("Invalid native numeric field")
        return value

    def rule(item: dict[str, Any]) -> NativeRule:
        return NativeRule(
            _field(item, "value", number), _enum(item, "unit", _RULE_UNITS)
        )

    def band(item: dict[str, Any]) -> MarginBand:
        return MarginBand(
            _field(item, "currency", _currency),
            _field(item, "margin", number),
            _field(item, "min", number),
            _field(item, "max", number),
        )

    def currency(item: dict[str, Any]) -> OrderCurrency:
        return OrderCurrency(
            _field(item, "code", _currency), _field(item, "isDefault", _boolean)
        )

    currencies = _rows(instrument, "currencies", currency)
    if currencies.value:
        codes = [item.code.value for item in currencies.value]
        defaults = sum(item.default.value is True for item in currencies.value)
        if len(codes) != len(set(codes)) or defaults > 1:
            currencies = NativeField("ambiguous", currencies.value)

    bands = _rows(instrument, "marginDepositBands", band)
    if bands.value and any(
        item.minimum.value is not None
        and item.maximum.value is not None
        and item.minimum.value > item.maximum.value
        for item in bands.value
    ):
        bands = NativeField("ambiguous", bands.value)

    return IGExecutionMarket(
        epic=epic,
        instrument_type="CURRENCIES",
        expiry=_field(instrument, "expiry", _expiry),
        size_unit=_enum(instrument, "unit", {"AMOUNT", "CONTRACTS", "SHARES"}),
        size_increment=NativeField("unsupported"),
        size_precision=NativeField("unsupported"),
        contract_size=_field(instrument, "contractSize", number),
        lot_size=_field(instrument, "lotSize", number),
        currencies=currencies,
        market_status=_enum(
            snapshot,
            "marketStatus",
            {
                "CLOSED",
                "EDITS_ONLY",
                "OFFLINE",
                "ON_AUCTION",
                "ON_AUCTION_NO_EDITS",
                "SUSPENDED",
                "TRADEABLE",
            },
        ),
        market_order_preference=_enum(
            rules,
            "marketOrderPreference",
            {
                "AVAILABLE_DEFAULT_OFF",
                "AVAILABLE_DEFAULT_ON",
                "NOT_AVAILABLE",
            },
        ),
        force_open_allowed=_field(instrument, "forceOpenAllowed", _boolean),
        stops_limits_allowed=_field(instrument, "stopsLimitsAllowed", _boolean),
        controlled_risk_allowed=_field(instrument, "controlledRiskAllowed", _boolean),
        minimum_deal_size=_field(rules, "minDealSize", rule, mapping=True),
        minimum_normal_stop_or_limit=_field(
            rules, "minNormalStopOrLimitDistance", rule, mapping=True
        ),
        minimum_controlled_risk_stop=_field(
            rules, "minControlledRiskStopDistance", rule, mapping=True
        ),
        maximum_stop_or_limit=_field(
            rules, "maxStopOrLimitDistance", rule, mapping=True
        ),
        minimum_step_distance=_field(rules, "minStepDistance", rule, mapping=True),
        limited_risk_premium=_field(
            instrument, "limitedRiskPremium", rule, mapping=True
        ),
        slippage_factor=_field(instrument, "slippageFactor", rule, mapping=True),
        margin_factor=_field(instrument, "marginFactor", number),
        margin_factor_unit=_enum(instrument, "marginFactorUnit", _RULE_UNITS),
        margin_bands=bands,
        quote_decimal_places_factor=_field(snapshot, "decimalPlacesFactor", number),
        scaling_factor=_field(snapshot, "scalingFactor", number),
        one_pip_means=_field(instrument, "onePipMeans", number),
        value_of_one_pip=_field(instrument, "valueOfOnePip", number),
    )


_RULE_UNITS = {"PERCENTAGE", "POINTS"}


def _field(
    parent: Any, key: str, parse: Callable[[Any], T], *, mapping: bool = False
) -> NativeField[T]:
    if parent is None:
        return NativeField("missing")
    if not isinstance(parent, dict):
        return NativeField("malformed")
    item = parent.get(key)
    if item is None:
        return NativeField("missing")
    if mapping and not isinstance(item, dict):
        return NativeField("malformed")
    try:
        return NativeField("available", parse(item))
    except ValueError:
        return NativeField("malformed")


def _enum(parent: Any, key: str, allowed: set[str]) -> NativeField[str]:
    parsed = _field(parent, key, _token)
    if parsed.value is not None and parsed.value not in allowed:
        return NativeField("unsupported")
    return parsed


def _rows(
    parent: dict[str, Any], key: str, parse: Callable[[dict[str, Any]], T]
) -> NativeField[tuple[T, ...]]:
    def collection(items: Any) -> tuple[T, ...]:
        if (
            not isinstance(items, list)
            or len(items) > 100
            or not all(isinstance(row, dict) for row in items)
        ):
            raise ValueError("Invalid native collection")
        return tuple(parse(row) for row in items)

    return _field(parent, key, collection)


def _token(item: Any) -> str:
    if not isinstance(item, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item) is None:
        raise ValueError("Invalid enum")
    return item


def _currency(item: Any) -> str:
    if not isinstance(item, str) or re.fullmatch(r"[A-Z]{3}", item) is None:
        raise ValueError("Invalid currency code")
    return item


def _boolean(item: Any) -> bool:
    if type(item) is not bool:
        raise ValueError("Invalid Boolean")
    return item


def _expiry(item: Any) -> str:
    if (
        not isinstance(item, str)
        or re.fullmatch(r"-|DFB|[A-Z]{3}-[0-9]{2}|[0-9]{4}-[0-9]{2}-[0-9]{2}", item)
        is None
    ):
        raise ValueError("Unrecognized expiry format")
    return item
