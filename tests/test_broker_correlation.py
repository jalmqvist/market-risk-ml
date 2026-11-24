from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from mrml.broker.evidence import (
    Acknowledgement,
    ActivityOpening,
    AffectedDeal,
    Confirmation,
    OpeningEvidence,
    OpeningIntent,
    Position,
    PositionRead,
    Provenance,
    Scope,
    WorkingOrder,
    WorkingOrderRead,
    correlate_opening,
)

NOW = datetime(2026, 11, 24, tzinfo=UTC)
AGE = timedelta(seconds=30)
SCOPE = Scope("IG", "demo", "demo-account")
INTENT = OpeningIntent(
    "intent", SCOPE, "Ref-1", "EPIC", "BUY", Decimal("1.000000000000000001")
)


def provenance(key: str) -> Provenance:
    return Provenance(key, SCOPE, NOW - timedelta(seconds=1), key)


CONFIRMATION = Confirmation(
    provenance("confirmation"),
    "Ref-1",
    "ACCEPTED",
    transaction_deal_id="transaction",
    affected_deals=(AffectedDeal("opened-deal", "OPENED"),),
    instrument="EPIC",
    side="BUY",
    quantity=INTENT.quantity,
)
REJECTED = replace(
    CONFIRMATION,
    outcome="REJECTED",
    affected_deals=None,
    reason="MARKET_CLOSED",
    definitive_rejection=True,
)
POSITION = Position("opened-deal", "EPIC", "BUY", INTENT.quantity)
POSITIONS = PositionRead(
    replace(provenance("positions"), observed_at=NOW), True, (POSITION,)
)
ORDERS = WorkingOrderRead(provenance("orders"), True, ())
ACK = Acknowledgement(provenance("ack"), "Ref-1")
ACTIVITY = ActivityOpening(provenance("activity"), "Ref-1", "opened-deal")
BASE = OpeningEvidence(POSITIONS, ORDERS, (CONFIRMATION,), ACK)


def assess(evidence: OpeningEvidence):
    return correlate_opening(INTENT, evidence, now=NOW, max_age=AGE)


@pytest.mark.parametrize(
    ("evidence", "status", "reason", "outcome"),
    [
        (BASE, "MATCHED", "OPEN_POSITION_MATCHED", "ACCEPTED"),
        (
            replace(
                BASE,
                confirmations=(REJECTED,),
                positions=replace(POSITIONS, positions=()),
            ),
            "MATCHED",
            "REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE",
            "REJECTED",
        ),
        (
            replace(
                BASE, confirmations=(), confirmation_not_found=provenance("missing")
            ),
            "UNRESOLVED",
            "CONFIRMATION_NOT_FOUND",
            None,
        ),
        (replace(BASE, confirmations=()), "UNRESOLVED", "CONFIRMATION_PENDING", None),
        (
            replace(
                BASE,
                confirmations=(),
                acknowledgement=None,
                positions=replace(POSITIONS, positions=()),
            ),
            "UNRESOLVED",
            "SUBMISSION_UNCERTAIN",
            None,
        ),
        (
            replace(
                BASE,
                positions=replace(
                    POSITIONS, positions=(), target_deal_id="opened-deal"
                ),
            ),
            "MISMATCH",
            "POSITION_ABSENT",
            "ACCEPTED",
        ),
        (
            replace(
                BASE, confirmations=(replace(CONFIRMATION, returned_reference="ref-1"),)
            ),
            "MISMATCH",
            "CONFIRMATION_REFERENCE_CONFLICT",
            None,
        ),
        (
            replace(BASE, acknowledgement=replace(ACK, returned_reference="wrong")),
            "MISMATCH",
            "ACKNOWLEDGEMENT_REFERENCE_CONFLICT",
            None,
        ),
        (
            replace(
                BASE,
                confirmations=(
                    replace(
                        CONFIRMATION,
                        affected_deals=(
                            AffectedDeal("one", "OPENED"),
                            AffectedDeal("two", "OPENED"),
                        ),
                    ),
                ),
            ),
            "UNRESOLVED",
            "UNSUPPORTED_AFFECTED_DEALS",
            "ACCEPTED",
        ),
        (
            replace(
                BASE,
                positions=replace(
                    POSITIONS,
                    provenance=replace(
                        POSITIONS.provenance,
                        scope=replace(SCOPE, account_id="different"),
                    ),
                ),
            ),
            "UNRESOLVED",
            "WRONG_SCOPE",
            None,
        ),
        (
            replace(
                BASE,
                positions=replace(
                    POSITIONS,
                    provenance=replace(
                        POSITIONS.provenance,
                        observed_at=NOW - AGE - timedelta(microseconds=1),
                    ),
                ),
            ),
            "UNRESOLVED",
            "STALE_OR_INVALID_EXPOSURE_READ",
            "ACCEPTED",
        ),
        (
            replace(BASE, positions=replace(POSITIONS, complete=False)),
            "UNRESOLVED",
            "INCOMPLETE_EXPOSURE_READ",
            "ACCEPTED",
        ),
        (
            replace(BASE, working_orders=replace(ORDERS, complete=False)),
            "UNRESOLVED",
            "INCOMPLETE_EXPOSURE_READ",
            "ACCEPTED",
        ),
        (
            replace(BASE, confirmations=(CONFIRMATION, REJECTED)),
            "MISMATCH",
            "CONFLICTING_CONFIRMATIONS",
            None,
        ),
        (
            replace(
                BASE,
                confirmations=(
                    CONFIRMATION,
                    replace(CONFIRMATION, provenance=provenance("duplicate")),
                ),
            ),
            "MATCHED",
            "OPEN_POSITION_MATCHED",
            "ACCEPTED",
        ),
        (
            replace(
                BASE,
                positions=replace(POSITIONS, positions=()),
                working_orders=replace(
                    ORDERS, orders=(WorkingOrder("working-id", "EPIC", "Ref-1"),)
                ),
            ),
            "MISMATCH",
            "CONTINGENT_EXPOSURE",
            "ACCEPTED",
        ),
        (
            replace(BASE, activity_openings=(ACTIVITY,)),
            "MATCHED",
            "OPEN_POSITION_MATCHED",
            "ACCEPTED",
        ),
        (
            replace(
                BASE,
                activity_openings=(replace(ACTIVITY, affected_deal_id="different"),),
            ),
            "MISMATCH",
            "ACTIVITY_CONFLICT",
            "ACCEPTED",
        ),
        (
            replace(BASE, confirmations=(), activity_openings=(ACTIVITY,)),
            "UNRESOLVED",
            "CONFIRMATION_PENDING",
            None,
        ),
        (
            replace(BASE, confirmations=(REJECTED,), activity_openings=(ACTIVITY,)),
            "MISMATCH",
            "ACTIVITY_CONFLICT",
            "REJECTED",
        ),
        (
            replace(
                BASE,
                confirmations=(REJECTED,),
                positions=replace(
                    POSITIONS, target_deal_id="opened-deal", positions=()
                ),
            ),
            "UNRESOLVED",
            "REJECTION_REQUIRES_POSITION_LIST",
            "REJECTED",
        ),
    ],
)
def test_opening_correlation(evidence, status, reason, outcome):
    result = assess(evidence)
    assert (result.status, result.reason, result.confirmation_outcome) == (
        status,
        reason,
        outcome,
    )
    assert result.intent_id == INTENT.intent_id
    assert "positions" in result.evidence_ids


@pytest.mark.parametrize(
    "position",
    [
        replace(POSITION, instrument="OTHER"),
        replace(POSITION, side="SELL"),
        replace(POSITION, quantity=Decimal("1.000000000000000002")),
        replace(POSITION, reference="other-reference"),
    ],
)
def test_exact_position_terms(position):
    result = assess(replace(BASE, positions=replace(POSITIONS, positions=(position,))))
    assert result.reason == "POSITION_TERMS_CONFLICT"


def test_replay_and_input_order_preserve_result_and_evidence_ids():
    duplicate = replace(CONFIRMATION, provenance=provenance("duplicate"))
    first = assess(replace(BASE, confirmations=(duplicate, CONFIRMATION)))
    replay = assess(replace(BASE, confirmations=(CONFIRMATION, duplicate)))
    assert first == replay
    assert first.evidence_ids == (
        "ack",
        "confirmation",
        "duplicate",
        "orders",
        "positions",
    )


def test_rejection_does_not_make_an_account_level_eligibility_decision():
    other = replace(POSITION, deal_id="external-position", reference="another-ref")
    result = assess(
        replace(
            BASE,
            confirmations=(REJECTED,),
            positions=replace(POSITIONS, positions=(other,)),
        )
    )
    assert result.reason == "REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE"
    assert not hasattr(result, "can_execute")


def test_conflicting_affected_deal_multiplicity_cannot_depend_on_input_order():
    duplicated = replace(CONFIRMATION, affected_deals=CONFIRMATION.affected_deals * 2)
    left = assess(replace(BASE, confirmations=(duplicated, CONFIRMATION)))
    right = assess(replace(BASE, confirmations=(CONFIRMATION, duplicated)))
    assert left == right
    assert left.reason == "CONFLICTING_CONFIRMATIONS"


@pytest.mark.parametrize(
    "confirmation_observed_at",
    (NOW + timedelta(microseconds=1), NOW, NOW.replace(tzinfo=None)),
    ids=(
        "position-predates-confirmation",
        "equal-observation-time",
        "naive-confirmation",
    ),
)
def test_matching_position_requires_ordered_confirmation_observation(
    confirmation_observed_at,
):
    confirmation = replace(
        CONFIRMATION,
        provenance=replace(
            CONFIRMATION.provenance, observed_at=confirmation_observed_at
        ),
        raw_date="2026-11-23T00:00:00Z",
    )
    # Keep both observations within the evaluation interval.
    result = correlate_opening(
        INTENT,
        replace(BASE, confirmations=(confirmation,)),
        now=NOW + timedelta(seconds=1),
        max_age=AGE,
    )
    assert result.status == "UNRESOLVED"
    assert result.reason == "POSITION_OBSERVATION_ORDER_UNVERIFIED"
    assert result.confirmation_outcome == "ACCEPTED"


def test_later_equivalent_confirmation_does_not_invalidate_earlier_observation():
    duplicate = replace(
        CONFIRMATION, provenance=replace(provenance("duplicate"), observed_at=NOW)
    )
    assert (
        assess(replace(BASE, confirmations=(duplicate, CONFIRMATION))).status
        == "MATCHED"
    )
