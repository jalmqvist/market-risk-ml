"""Executable examples for the B1-S3 design, with no runtime integration."""

from __future__ import annotations

from decimal import Decimal

import pytest

REFERENCE = "mrml-open-example"
EPIC = "CS.D.SYNTHETIC.IP"
AMOUNT = "1.000000000000000001"
ACCEPTED = {
    "dealReference": REFERENCE,
    "dealStatus": "ACCEPTED",
    "dealId": "submission-deal",
    "affectedDeals": [{"dealId": "open-deal", "status": "OPENED"}],
}
POSITION = {
    "position": {"dealId": "open-deal", "direction": "BUY", "dealSize": AMOUNT},
    "market": {"epic": EPIC},
}
ACTIVITY = {
    "dealId": "submission-deal",
    "epic": EPIC,
    "details": {
        "dealReference": REFERENCE,
        "actions": [{"affectedDealId": "open-deal", "actionType": "POSITION_OPENED"}],
    },
}


def opening_example(
    confirmation=None,
    positions=None,
    *,
    working_orders=(),
    activity=(),
    verified_fresh_scope=True,
):
    """Evaluate an already claimed opening using synthetic evidence."""
    if positions is None or working_orders is None or not verified_fresh_scope:
        return "unresolved", True
    if confirmation is None or confirmation.get("dealReference") != REFERENCE:
        # Activity is supplemental even when broker identifiers correlate.
        return "unresolved", True
    status = confirmation.get("dealStatus")
    if status == "REJECTED":
        if (
            confirmation.get("reason") in {"DUPLICATE_ORDER_ERROR", "CONFLICTING_ORDER"}
            or positions
            or working_orders
            or activity
        ):
            return "unresolved", True
        return "rejected", False
    if status != "ACCEPTED":
        return "unresolved", True
    opened_ids = {
        item["dealId"]
        for item in confirmation["affectedDeals"]
        if item["status"] == "OPENED"
    }
    if len(opened_ids) != 1 or len(positions) != 1 or working_orders:
        return "mismatch", True
    [row] = positions
    position = row["position"]
    if (
        position["dealId"] not in opened_ids
        or row["market"]["epic"] != EPIC
        or position["direction"] != "BUY"
        or Decimal(position["dealSize"]) != Decimal(AMOUNT)
    ):
        return "mismatch", True
    return "matched", False


@pytest.mark.parametrize(
    ("confirmation", "positions", "expected"),
    [
        (ACCEPTED, [POSITION], ("matched", False)),
        (ACCEPTED, [], ("mismatch", True)),
        (ACCEPTED, None, ("unresolved", True)),
        ({**ACCEPTED, "dealReference": "other"}, [POSITION], ("unresolved", True)),
        ({**ACCEPTED, "dealStatus": "UNKNOWN"}, [POSITION], ("unresolved", True)),
        (None, [], ("unresolved", True)),
        (None, [POSITION], ("unresolved", True)),
        (
            {
                "dealReference": REFERENCE,
                "dealStatus": "REJECTED",
                "reason": "MARKET_CLOSED",
            },
            [],
            ("rejected", False),
        ),
        (
            {**ACCEPTED, "dealStatus": "REJECTED", "reason": "DUPLICATE_ORDER_ERROR"},
            [],
            ("unresolved", True),
        ),
    ],
)
def test_opening_evidence_cases(confirmation, positions, expected):
    assert opening_example(confirmation, positions) == expected


def test_affected_deal_id_and_its_exact_size_must_match():
    for patch in ({"dealId": "submission-deal"}, {"dealSize": "1.000000000000000002"}):
        current = {**POSITION, "position": {**POSITION["position"], **patch}}
        assert opening_example(ACCEPTED, [current]) == ("mismatch", True)


def test_activity_alone_cannot_release_a_dispatch_hold():
    assert opening_example(None, [POSITION], activity=[ACTIVITY]) == (
        "unresolved",
        True,
    )


def test_wrong_account_or_stale_snapshot_cannot_release_hold():
    assert opening_example(ACCEPTED, [POSITION], verified_fresh_scope=False) == (
        "unresolved",
        True,
    )


def test_working_order_is_contingent_exposure_and_does_not_match_opening():
    order = {"workingOrderData": {"dealId": "open-deal", "epic": EPIC}}
    assert opening_example(ACCEPTED, [], working_orders=[order]) == ("mismatch", True)
