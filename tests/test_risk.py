"""Risk-controller limits and fail-closed edge cases."""

from __future__ import annotations

import math

import pytest

from mrml.risk.controller import RiskContext, RiskLimits, evaluate_trade


def test_default_risk_budget_is_half_percent_and_sized_from_stop() -> None:
    decision = evaluate_trade(
        RiskLimits(),
        RiskContext(equity=10_000),
        pair="EUR_USD",
        stop_distance=2,
    )

    assert decision.allowed
    assert decision.reasons == ()
    assert decision.risk_amount == 50
    assert decision.units == 25


def test_position_exposure_drawdown_daily_and_stale_limits_accumulate() -> None:
    decision = evaluate_trade(
        RiskLimits(max_positions_global=2, max_positions_per_pair=1),
        RiskContext(
            equity=10_000,
            open_positions_by_pair={"EUR_USD": 1, "GBP_USD": 1},
            currency_exposure={"EUR": 3},
            drawdown_pct=0.20,
            daily_loss_pct=0.03,
            bundle_stale=True,
        ),
        pair="EUR_USD",
        stop_distance=1,
        currency_exposure_delta={"EUR": 0.1},
    )

    assert not decision.allowed
    assert set(decision.reasons) == {
        "STALE_BUNDLE",
        "DRAWDOWN_KILL_SWITCH",
        "DAILY_LOSS_LIMIT",
        "MAX_POSITIONS_GLOBAL",
        "MAX_POSITIONS_PER_PAIR",
        "CURRENCY_EXPOSURE_CAP",
    }


@pytest.mark.parametrize(
    ("equity", "stop_distance", "reason"),
    [
        (0, 1, "INVALID_EQUITY"),
        (100, 0, "INVALID_STOP_DISTANCE"),
        (math.nan, 1, "INVALID_CONTEXT"),
        (100, math.inf, "INVALID_STOP_DISTANCE"),
    ],
)
def test_invalid_numerics_fail_closed(
    equity: float, stop_distance: float, reason: str
) -> None:
    decision = evaluate_trade(
        RiskLimits(),
        RiskContext(equity=equity),
        pair="EUR_USD",
        stop_distance=stop_distance,
    )
    assert not decision.allowed
    assert reason in decision.reasons


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="risk_per_trade"):
        RiskLimits(risk_per_trade=0)
    with pytest.raises(ValueError, match="finite"):
        RiskLimits(max_currency_exposure=math.nan)


def test_non_account_quote_currency_requires_conversion_for_correct_sizing() -> None:
    missing = evaluate_trade(
        RiskLimits(),
        RiskContext(equity=10_000),
        pair="EUR_GBP",
        stop_distance=2,
    )
    converted = evaluate_trade(
        RiskLimits(),
        RiskContext(
            equity=10_000,
            currency_to_account={"GBP": 1.25},
        ),
        pair="EUR_GBP",
        stop_distance=2,
    )

    assert not missing.allowed
    assert "MISSING_CURRENCY_CONVERSION" in missing.reasons
    assert converted.allowed
    assert converted.units == 20
