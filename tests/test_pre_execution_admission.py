from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
import requests
from alembic import command
from sqlalchemy import func, select
from test_account_gate import (
    SECOND,
    closing,
    make_assessment,
    make_reads,
    new_claim,
    resolve,
    restrict,
)
from test_broker_observations import DEAL, ORDER, POSITION
from test_execution_journal import ACCOUNT, OTHER, REQUEST
from test_market_data_storage import pg_db  # noqa: F401

from mrml.broker.admission import (
    AdmissionJournal,
    Finding,
    OfflineAccount,
    OfflineAuthorization,
    OfflineDealingRules,
    OfflineEvidence,
    OfflineExposure,
    OfflineRisk,
    OfflineSession,
    Outcome,
)
from mrml.broker.evidence import PositionRead, Provenance, WorkingOrderRead
from mrml.broker.gate import Blocker
from mrml.broker.journal import DispatchHeld
from mrml.broker.journal_models import ClaimRow, EvidenceRow
from mrml.broker.observations import Collection

D = Decimal
OPEN = replace(
    REQUEST,
    quantity=D("1.25"),
    order_type="MARKET",
    level=None,
    limit_distance=None,
)


@pytest.fixture
def journal_pg(pg_db, monkeypatch):  # noqa: F811
    def unexpected_http(*args, **kwargs):
        pytest.fail("Offline evaluation attempted HTTP")

    monkeypatch.setattr(requests.Session, "request", unexpected_http)
    engine, config = pg_db
    command.upgrade(config, "head")
    command.check(config)
    return AdmissionJournal(engine), engine


def fixtures(
    journal,
    *,
    operation="OPEN",
    deal=DEAL,
    quantity=POSITION.quantity,
    request=OPEN,
):
    if operation == "CLOSE":
        opening_claim = new_claim(journal)
        assessment, at = make_assessment(journal, opening_claim)
        resolve(journal, opening_claim, assessment, at)
        intent = closing(journal, deal=deal, quantity=quantity)
        request = replace(
            request,
            operation="CLOSE",
            side="SELL",
            target_deal_id=deal,
            quantity=quantity,
            currency=None,
            force_open=None,
            guaranteed_stop=None,
            stop_distance=None,
            time_in_force=None,
        )
    else:
        intent = journal.create_intent(
            ACCOUNT, idempotency_key="first", request=request, authorization_id="open"
        )
        origin = datetime.now(UTC)
        at = origin + 8 * SECOND
        make_reads(journal, origin, "flat", ())
    state = journal.recover_gate(ACCOUNT, at=at)
    generation = uuid4()
    evidence = OfflineEvidence(
        session=OfflineSession(
            generation,
            ACCOUNT,
            (OfflineAccount(ACCOUNT, "ENABLED", "USD"),),
            at - 8 * SECOND,
            at + 30 * SECOND,
        ),
        exposure=OfflineExposure(
            generation,
            state.revision,
            tuple(claim.id for claim in state.claims),
            state.positions.id,
            state.orders.id,
        ),
        dealing=OfflineDealingRules(
            evidence_id=uuid4(),
            session_generation=generation,
            observed_at=at - SECOND,
            instrument=request.instrument,
            currency="USD",
            size_unit="CONTRACTS",
            contract_size=D(10),
            minimum_size=D("0.01")
            if operation == "OPEN"
            else D("0.000000000000000001"),
            size_increment=D("0.01")
            if operation == "OPEN"
            else D("0.000000000000000001"),
            size_precision=2 if operation == "OPEN" else 18,
            operations=("OPEN", "CLOSE"),
            order_types=("MARKET",),
            dealing_available=True,
            executable_level=D(100),
            minimum_stop_distance=D(1),
            maximum_stop_distance=D(5),
        ),
        risk=OfflineRisk(
            evidence_id=uuid4(),
            session_generation=generation,
            observed_at=at - SECOND,
            intent_id=intent.id,
            fingerprint=intent.fingerprint,
            account_currency="USD",
            instrument_to_account=D(1),
            funds=D(1000),
            estimated_margin=D(50),
            stop_budget=D(20),
            max_quantity=D(2),
            max_notional=D(1500),
            max_positions=1,
            daily_loss=D(0),
            daily_loss_limit=D(30),
            drawdown=D(0),
            drawdown_limit=D(50),
        ),
        authorization=OfflineAuthorization(
            intent.authorization_id,
            "synthetic-operator",
            ACCOUNT,
            intent.id,
            intent.fingerprint,
            request,
            at - SECOND,
            at + 7 * SECOND,
        ),
    )
    return intent, request, evidence, at


@pytest.mark.parametrize("operation", ["OPEN", "CLOSE"])
def test_consistent_fixtures_never_establish_operational_authority(
    journal_pg, operation
):
    journal, engine = journal_pg
    intent, request, evidence, at = fixtures(journal, operation=operation)
    with engine.connect() as connection:
        count = connection.scalar(select(func.count()).select_from(EvidenceRow))
    result = journal.evaluate_offline(ACCOUNT, intent.id, request, evidence, at=at)
    assert result.outcome is Outcome.INCOMPLETE
    assert not result.refusals
    assert result.missing == (
        Finding.BROKER_VERIFIER_UNAVAILABLE,
        Finding.AUTHORIZATION_VERIFIER_UNAVAILABLE,
    )
    assert result.intent == intent and result.request == request
    assert result.gate.revision == evidence.exposure.gate_revision
    assert result.evaluated_at == at
    assert result.expires_at == evidence.authorization.expires_at
    assert not journal.consider_action(ACCOUNT, request, at=at).can_consider
    if operation == "CLOSE":
        with pytest.raises(DispatchHeld):
            journal.claim_dispatch(ACCOUNT, intent.id)
    engine.dispose()
    assert (
        AdmissionJournal(engine).evaluate_offline(
            ACCOUNT,
            intent.id,
            request,
            evidence,
            at=at,
        )
        == result
    )
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EvidenceRow)) == count
        assert connection.scalar(select(func.count()).select_from(ClaimRow)) == (
            1 if operation == "CLOSE" else 0
        )


@pytest.mark.parametrize(
    ("field", "changes", "reason", "collection"),
    [
        ("session", None, Finding.SESSION_MISSING, "missing"),
        ("session", {"active_scope": OTHER}, Finding.SESSION_INVALID, "refusals"),
        ("session", {"accounts": ()}, Finding.SESSION_INVALID, "refusals"),
        (
            "session",
            {"expires_at": datetime(2025, 1, 1, tzinfo=UTC)},
            Finding.SESSION_INVALID,
            "refusals",
        ),
        (
            "exposure",
            {"session_generation": uuid4()},
            Finding.EXPOSURE_CHANGED,
            "refusals",
        ),
        ("dealing", None, Finding.RULES_MISSING, "missing"),
        ("dealing", {"instrument": "unrelated"}, Finding.RULES_INVALID, "refusals"),
        ("dealing", {"size_unit": "UNSPECIFIED"}, Finding.RULES_INVALID, "refusals"),
        ("dealing", {"minimum_size": D(2)}, Finding.SIZE_INVALID, "refusals"),
        ("dealing", {"size_increment": D("0.03")}, Finding.SIZE_INVALID, "refusals"),
        ("dealing", {"size_precision": 1}, Finding.SIZE_INVALID, "refusals"),
        ("dealing", {"minimum_stop_distance": D(2)}, Finding.STOP_INVALID, "refusals"),
        ("risk", None, Finding.RISK_MISSING, "missing"),
        ("risk", {"estimated_margin": D(0)}, Finding.RISK_INVALID, "refusals"),
        (
            "risk",
            {"estimated_margin": D(1001)},
            Finding.MARGIN_INSUFFICIENT,
            "refusals",
        ),
        (
            "risk",
            {"stop_budget": D("15.624999999999999999")},
            Finding.RISK_LIMIT,
            "refusals",
        ),
        ("risk", {"daily_loss": D(30)}, Finding.RISK_LIMIT, "refusals"),
        ("risk", {"drawdown": D(50)}, Finding.RISK_LIMIT, "refusals"),
        ("authorization", None, Finding.AUTHORIZATION_MISSING, "missing"),
        (
            "authorization",
            {"fingerprint": "bad"},
            Finding.AUTHORIZATION_INVALID,
            "refusals",
        ),
        (
            "authorization",
            {"expires_at": datetime(2025, 1, 1, tzinfo=UTC)},
            Finding.AUTHORIZATION_INVALID,
            "refusals",
        ),
    ],
)
def test_offline_prerequisite_findings(journal_pg, field, changes, reason, collection):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    changed = None if changes is None else replace(getattr(evidence, field), **changes)
    result = journal.evaluate_offline(
        ACCOUNT, intent.id, request, replace(evidence, **{field: changed}), at=at
    )
    assert reason in getattr(result, collection)
    assert result.outcome is (
        Outcome.INCOMPLETE if collection == "missing" else Outcome.REFUSED
    )


def test_wrong_scope_and_modified_immutable_request_are_refused(journal_pg):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    live = replace(ACCOUNT, environment="live")
    assert (
        Finding.WRONG_SCOPE
        in journal.evaluate_offline(
            live,
            intent.id,
            request,
            evidence,
            at=at,
        ).refusals
    )
    assert (
        Finding.INTENT_UNAVAILABLE
        in journal.evaluate_offline(
            OTHER,
            intent.id,
            request,
            evidence,
            at=at,
        ).refusals
    )
    assert (
        Finding.INTENT_MISMATCH
        in journal.evaluate_offline(
            ACCOUNT,
            intent.id,
            replace(request, quantity=D(2)),
            evidence,
            at=at,
        ).refusals
    )


@pytest.mark.parametrize(
    "proposed",
    [
        replace(OPEN, stop_distance=None),
        replace(OPEN, guaranteed_stop=True),
        replace(OPEN, stop_distance=None, stop_level=D(101)),
    ],
)
def test_opening_requires_supported_protection(journal_pg, proposed):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal, request=proposed)
    assert (
        Finding.STOP_INVALID
        in journal.evaluate_offline(
            ACCOUNT,
            intent.id,
            request,
            evidence,
            at=at,
        ).refusals
    )


@pytest.mark.parametrize("case", ["wrong-deal", "different-size"])
def test_closing_binding_is_exact(journal_pg, case):
    journal, _ = journal_pg
    overrides = {"deal": "unrelated"} if case == "wrong-deal" else {"quantity": D(1)}
    intent, request, evidence, at = fixtures(journal, operation="CLOSE", **overrides)
    result = journal.evaluate_offline(ACCOUNT, intent.id, request, evidence, at=at)
    assert result.outcome is Outcome.REFUSED
    assert Finding.CLOSE_MISMATCH in result.refusals


def test_claimed_authorization_reference_cannot_support_another_opening(journal_pg):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    journal.claim_dispatch(ACCOUNT, intent.id)
    result = journal.evaluate_offline(ACCOUNT, intent.id, request, evidence, at=at)
    assert Finding.AUTHORIZATION_REUSED in result.refusals
    assert Finding.GATE_CHANGED in result.refusals
    assert Finding.LIFECYCLE_UNSUPPORTED in result.refusals


@pytest.mark.parametrize(
    ("status", "rows", "orders", "blocker"),
    [
        ("INCOMPLETE", (), (), Blocker.EXPOSURE_UNVERIFIED),
        ("COMPLETE", (POSITION,), (), Blocker.UNATTRIBUTED_POSITION),
        ("COMPLETE", (), (ORDER,), Blocker.CONTINGENT_ORDER),
    ],
)
def test_newer_observation_invalidates_selected_exposure(
    journal_pg, status, rows, orders, blocker
):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    make_reads(journal, at, "later", rows, orders, status=status)
    decision = journal.evaluate_offline(
        ACCOUNT,
        intent.id,
        request,
        evidence,
        at=at + 8 * SECOND,
    )
    assert blocker in decision.refusals
    assert Finding.EXPOSURE_CHANGED in decision.refusals


def test_stale_exposure_and_new_restriction_invalidate_projection(journal_pg):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    later = at + 40 * SECOND
    restrict(journal, later)
    result = journal.evaluate_offline(ACCOUNT, intent.id, request, evidence, at=later)
    assert {
        Blocker.EXPOSURE_STALE,
        Blocker.OPERATIONAL_RESTRICTION,
        Finding.GATE_CHANGED,
    } <= set(result.refusals)


def test_restriction_race_never_admits_close(journal_pg):
    journal, engine = journal_pg
    intent, request, evidence, at = fixtures(journal, operation="CLOSE")
    start = Barrier(2, timeout=5)

    def evaluate():
        start.wait()
        return AdmissionJournal(engine).evaluate_offline(
            ACCOUNT, intent.id, request, evidence, at=at
        )

    def restrict_account():
        start.wait()
        return restrict(AdmissionJournal(engine), at, revision=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(evaluate)
        restricted = pool.submit(restrict_account)
        assert result.result(timeout=10).outcome is not Outcome.ADMISSIBLE
        restricted.result(timeout=10)
    after = journal.evaluate_offline(ACCOUNT, intent.id, request, evidence, at=at)
    assert Blocker.OPERATIONAL_RESTRICTION in after.refusals
    assert Finding.GATE_CHANGED in after.refusals
    with pytest.raises(DispatchHeld):
        journal.claim_dispatch(ACCOUNT, intent.id)


@pytest.mark.parametrize("case", ["incomplete", "stale", "wide-span"])
def test_working_order_collection_has_independent_coverage_and_clock(journal_pg, case):
    journal, _ = journal_pg
    intent, request, evidence, at = fixtures(journal)
    delay = {"incomplete": SECOND, "stale": 28 * SECOND, "wide-span": 6 * SECOND}[case]
    status = "INCOMPLETE" if case == "incomplete" else "COMPLETE"
    read = journal.record_collection(
        uuid4(),
        WorkingOrderRead(
            Provenance("new-orders", ACCOUNT, at + delay, "synthetic"),
            status == "COMPLETE",
            (),
        ),
        Collection(status, at, at + delay),
    )
    decision_time = at + delay
    if case == "stale":
        # A fresh positions read leaves working orders independently stale.
        decision_time += 31 * SECOND
        journal.record_collection(
            uuid4(),
            PositionRead(
                Provenance("fresh-positions", ACCOUNT, decision_time, "synthetic"),
                True,
                (),
            ),
            Collection("COMPLETE", decision_time - SECOND, decision_time),
        )
    basis = journal.recover_gate(ACCOUNT, at=decision_time)
    current = replace(
        evidence,
        exposure=replace(
            evidence.exposure,
            positions_id=basis.positions.id,
            orders_id=read.id,
        ),
    )
    result = journal.evaluate_offline(
        ACCOUNT, intent.id, request, current, at=decision_time
    )
    expected = {
        "incomplete": Blocker.EXPOSURE_UNVERIFIED,
        "stale": Blocker.EXPOSURE_STALE,
        "wide-span": Finding.EXPOSURE_TIME_INVALID,
    }
    assert result.outcome is Outcome.REFUSED
    assert expected[case] in result.refusals
