from decimal import Decimal, localcontext

import pytest

from mrml.data.ig_eurusd_arithmetic import (
    QuoteAmount,
    no_stop_margin_usd,
    ordinary_stop_margin_usd,
    pip_value_usd,
)

D = Decimal
QUOTE = QuoteAmount(D("1.1000"), "USD/EUR")
NO_STOP = {"contracts": D("0.10"), "quote": QUOTE, "margin_rate": D("0.0333")}
ORDINARY = {
    **NO_STOP,
    "slippage_factor": D("0.50"),
    "stop_distance": QuoteAmount(D("0.0010"), "USD/EUR"),
    "tier": 1,
}


@pytest.mark.parametrize(
    ("contracts", "expected"),
    [("1", "10"), ("0.10", "1"), ("0.25", "2.5"), ("2.5", "25"), ("0", "0")],
)
def test_linear_standard_contract_pip_economics(contracts, expected):
    assert pip_value_usd(D(contracts)) == D(expected)


def test_no_stop_uses_explicit_conventional_quote_quantity_and_rate():
    assert no_stop_margin_usd(**NO_STOP) == D("366.30")
    assert no_stop_margin_usd(
        **{**NO_STOP, "quote": QuoteAmount(D("1.20"), "USD/EUR")}
    ) == D("399.60")


@pytest.mark.parametrize(
    ("distance", "stop_formula", "lower"),
    [("0.0010", "193.15", "193.15"), ("0.0200", "383.15", "366.30")],
)
def test_ordinary_stop_compares_both_formulae(distance, stop_formula, lower):
    result = ordinary_stop_margin_usd(
        **{**ORDINARY, "stop_distance": QuoteAmount(D(distance), "USD/EUR")}
    )
    assert result.no_stop_usd == D("366.30")
    assert result.stop_formula_usd == D(stop_formula)
    assert result.lower_usd == D(lower)


def test_decimal_calculation_does_not_round_to_ambient_precision():
    with localcontext() as context:
        context.prec = 3
        assert pip_value_usd(D("0.123456789")) == D("1.234567890")
        assert ordinary_stop_margin_usd(**ORDINARY).lower_usd == D("193.15")
        assert no_stop_margin_usd(
            contracts=D("1"),
            quote=QuoteAmount(D("1"), "USD/EUR"),
            margin_rate=D("0.033300000000000000710542735760100185871124267578125"),
        ) == D("3330.0000000000000710542735760100185871124267578125")


@pytest.mark.parametrize("invalid", [None, 1, 0.1, D("-1"), D("NaN"), D("Infinity")])
def test_refuse_missing_nondecimal_and_invalid_contract_counts(invalid):
    with pytest.raises(ValueError):
        pip_value_usd(invalid)
    with pytest.raises(ValueError):
        no_stop_margin_usd(**{**NO_STOP, "contracts": invalid})


@pytest.mark.parametrize(
    "changes",
    [
        {"quote": None},
        {"quote": QuoteAmount(D("10990"), "POINTS")},
        {"quote": QuoteAmount(D("0"), "USD/EUR")},
        {"margin_rate": D("3.33")},
        {"margin_rate": None},
        {"margin_rate": D("-0.0333")},
    ],
)
def test_refuse_incompatible_or_missing_no_stop_inputs(changes):
    with pytest.raises(ValueError):
        no_stop_margin_usd(**{**NO_STOP, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"tier": 2},
        {"tier": True},
        {"tier": None},
        {"slippage_factor": D("50")},
        {"slippage_factor": D("NaN")},
        {"slippage_factor": None},
        {"stop_distance": QuoteAmount(D("1"), "POINTS")},
        {"stop_distance": QuoteAmount(D("10.0"), "PERCENTAGE")},
        {"stop_distance": QuoteAmount(D("-1"), "USD/EUR")},
        {"stop_distance": None},
    ],
)
def test_refuse_unverified_tier_units_and_stop_inputs(changes):
    with pytest.raises(ValueError):
        ordinary_stop_margin_usd(**{**ORDINARY, **changes})
