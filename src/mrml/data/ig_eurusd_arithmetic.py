"""Offline standard-contract EUR/USD economics and supplied-input USD algebra."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from math import prod
from typing import cast

_STANDARD_CONTRACT_EUR = Decimal("100000")
_PIP_USD_PER_EUR = Decimal("0.0001")


@dataclass(frozen=True)
class QuoteAmount:
    """An independently established quote or quote-distance with its unit."""

    value: Decimal
    unit: str


@dataclass(frozen=True)
class OrdinaryStopMarginUSD:
    no_stop_usd: Decimal
    stop_formula_usd: Decimal

    @property
    def lower_usd(self) -> Decimal:
        return min(self.no_stop_usd, self.stop_formula_usd)


def _nonnegative(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative Decimal")
    return value


def _fraction(value: Decimal, name: str) -> Decimal:
    if _nonnegative(value, name) > 1:
        raise ValueError(f"{name} must be supplied as a fraction from zero to one")
    return value


def _quote(amount: QuoteAmount, name: str) -> Decimal:
    if not isinstance(amount, QuoteAmount) or amount.unit != "USD/EUR":
        raise ValueError(f"{name} requires explicit USD/EUR units")
    return _nonnegative(amount.value, name)


def _product(*factors: Decimal) -> Decimal:
    # Multiplication needs at most the sum of the coefficient lengths.
    with localcontext() as context:
        context.prec = sum(len(factor.as_tuple().digits) for factor in factors)
        return prod(factors, start=Decimal(1))


def pip_value_usd(contracts: Decimal) -> Decimal:
    """USD per standard pip for an already established contract count."""
    return _product(
        _nonnegative(contracts, "contracts"), _STANDARD_CONTRACT_EUR, _PIP_USD_PER_EUR
    )


def no_stop_margin_usd(
    *, contracts: Decimal, quote: QuoteAmount, margin_rate: Decimal
) -> Decimal:
    """Evaluate the no-stop formula using the caller's quote and rate fraction."""
    price = _quote(quote, "quote")
    if price == 0:
        raise ValueError("quote must be positive")
    return _product(
        _nonnegative(contracts, "contracts"),
        _STANDARD_CONTRACT_EUR,
        price,
        _fraction(margin_rate, "margin rate"),
    )


def ordinary_stop_margin_usd(
    *,
    contracts: Decimal,
    quote: QuoteAmount,
    margin_rate: Decimal,
    slippage_factor: Decimal,
    stop_distance: QuoteAmount,
    tier: int,
) -> OrdinaryStopMarginUSD:
    """Compare both supplied-input formulae for a caller-established Tier 1."""
    if type(tier) is not int or tier != 1:
        raise ValueError("ordinary-stop reduction requires an established Tier 1")
    no_stop = no_stop_margin_usd(
        contracts=contracts, quote=quote, margin_rate=margin_rate
    )
    slippage_margin = _product(no_stop, _fraction(slippage_factor, "slippage factor"))
    stop_risk = _product(
        contracts, _STANDARD_CONTRACT_EUR, _quote(stop_distance, "stop distance")
    )
    # Allow the entire aligned coefficient plus a possible carry.
    with localcontext() as context:
        lowest_exponent = min(
            cast(int, operand.as_tuple().exponent)
            for operand in (slippage_margin, stop_risk)
        )
        context.prec = (
            max(slippage_margin.adjusted(), stop_risk.adjusted())
            - (lowest_exponent)
            + 2
        )
        stop_formula = slippage_margin + stop_risk
    return OrdinaryStopMarginUSD(no_stop, stop_formula)
