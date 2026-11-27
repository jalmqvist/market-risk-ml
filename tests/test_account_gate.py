from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, event, func, insert, inspect, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_broker_observations import DEAL, ORDER, POSITION, REF, accepted, opening
from test_execution_journal import ACCOUNT, OTHER, REQUEST, db_event
from test_market_data_storage import MAPPING, history, pg_db, record  # noqa: F401, F811

from mrml.broker.evidence import (
    AffectedDeal,
    PositionRead,
    Provenance,
    WorkingOrderRead,
)
from mrml.broker.gate import (
    Blocker,
    GateEvidenceError,
    GateJournal,
    Prerequisite,
    TransitionReason,
)
from mrml.broker.gate_models import GateTransitionRow
from mrml.broker.journal import DispatchHeld, JournalConflict, JournalStorageError
from mrml.broker.journal_models import ClaimRow, EvidenceRow, IntentRow
from mrml.broker.observation_models import AssessmentInputRow, AssessmentRow
from mrml.broker.observations import AssessmentInputs, Collection, ConfirmationNotFound
from mrml.data.storage import MarketDataRepository
from mrml.journal.models import Run
from mrml.journal.repository import JournalRepository

SECOND = timedelta(seconds=1)
ACTOR = "offline-reviewer"


@pytest.fixture
def gate_pg(pg_db):  # noqa: F811
    engine, config = pg_db
    command.upgrade(config, "head")
    return GateJournal(engine), engine, config


def _origin(key, origin, offset):
    return Provenance(key, ACCOUNT, origin + offset * SECOND, "synthetic-gate")


def make_reads(gate, origin, key, rows=(POSITION,), orders=(), *, status="COMPLETE"):
    position = gate.record_collection(
        uuid4(),
        PositionRead(_origin(f"position-{key}", origin, 5), status == "COMPLETE", rows),
        Collection(status, origin + 3 * SECOND, origin + 4 * SECOND),
    )
    order = gate.record_collection(
        uuid4(),
        WorkingOrderRead(_origin(f"orders-{key}", origin, 6), True, orders),
        Collection("COMPLETE", origin + 4 * SECOND, origin + 5 * SECOND),
    )
    return position.id, order.id


def make_assessment(gate, claim, case="accepted"):
    origin = claim.claimed_at
    confirmation = accepted(provenance=_origin("confirmation", origin, 2))
    transactions = (confirmation,)
    rows, orders, status = (POSITION,), (), "COMPLETE"
    if case == "rejected":
        transactions = (
            replace(
                confirmation,
                outcome="REJECTED",
                reason="MARKET_CLOSED",
                affected_deals=(),
                definitive_rejection=True,
            ),
        )
        rows = ()
    if case == "missing":
        transactions = (ConfirmationNotFound(_origin("not-found", origin, 2), REF),)
        rows = ()
    if case == "multi":
        transactions = (
            replace(
                confirmation,
                affected_deals=(
                    AffectedDeal(DEAL, "OPENED"),
                    AffectedDeal("other-leg", "OPENED"),
                ),
            ),
        )
    if case == "conflicting":
        transactions += (
            replace(
                confirmation,
                provenance=_origin("conflict", origin, 2),
                outcome="REJECTED",
            ),
        )
    if case == "incomplete":
        status = "INCOMPLETE"
    if case == "external":
        rows += (replace(POSITION, deal_id="external", reference=None),)
        orders = (ORDER,)
    ids = tuple(
        gate.record_transaction(uuid4(), claim.intent_id, item).id
        for item in transactions
    )
    positions, working = make_reads(
        gate, origin, "opening", rows, orders, status=status
    )
    decision = origin + 8 * SECOND
    return gate.assess_opening(
        uuid4(),
        ACCOUNT,
        claim.intent_id,
        AssessmentInputs(positions, working, ids),
        evaluated_at=decision - SECOND,
        max_age=20 * SECOND,
    ), decision


def new_claim(gate):
    return gate.claim_dispatch(ACCOUNT, opening(gate).id)


def resolve(
    gate, claim, assessment, decision, *, identity=None, revision=0, actor=ACTOR
):
    return gate.resolve_claim(
        identity or uuid4(),
        ACCOUNT,
        claim.id,
        assessment.id,
        expected_revision=revision,
        actor=actor,
        decided_at=decision,
    )


def restrict(gate, at, *, identity=None, revision=0, actor=ACTOR):
    return gate.restrict(
        identity or uuid4(),
        ACCOUNT,
        expected_revision=revision,
        actor=actor,
        decided_at=at,
    )


def test_postgres_initial_gate_and_restart_without_acknowledgement(gate_pg):
    gate, engine, _ = gate_pg
    now = datetime.now(UTC)
    initial = gate.recover_gate(ACCOUNT, at=now)
    assert initial.revision == 0 and initial.claim is None
    assert set(initial.blockers) == {
        Blocker.IDENTITY_UNVERIFIED,
        Blocker.EXPOSURE_UNVERIFIED,
    }
    claim = new_claim(gate)
    reconnected = create_engine(engine.url)
    try:
        reopened = GateJournal(reconnected)
        state = reopened.recover_gate(ACCOUNT, at=claim.claimed_at)
        assert state.claim == state.outstanding_claim == claim
        assert Blocker.CONFIRMATION_MISSING in state.blockers
        result = reopened.consider_action(ACCOUNT, REQUEST, at=claim.claimed_at)
        assert not result.can_consider
        assert result.missing_prerequisites == tuple(Prerequisite)
    finally:
        reconnected.dispose()


@pytest.mark.parametrize(
    ("case", "reason", "exposure"),
    [
        ("accepted", TransitionReason.OPENING_RESOLVED, {Blocker.KNOWN_POSITION}),
        ("rejected", TransitionReason.REJECTION_RESOLVED, set()),
        (
            "external",
            TransitionReason.OPENING_RESOLVED,
            {
                Blocker.KNOWN_POSITION,
                Blocker.UNATTRIBUTED_POSITION,
                Blocker.CONTINGENT_ORDER,
            },
        ),
    ],
)
def test_postgres_claim_specific_resolution_and_reconnect(
    gate_pg, case, reason, exposure
):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim, case)
    before = gate.recover_gate(ACCOUNT, at=at)
    assert before.outstanding_claim == claim
    transition = resolve(gate, claim, assessment, at)
    assert transition.reason == reason
    assert transition.assessment_id == assessment.id
    assert (transition.positions_id, transition.orders_id) == (
        assessment.inputs.positions_id,
        assessment.inputs.orders_id,
    )
    reopened = create_engine(engine.url)
    try:
        fresh = GateJournal(reopened)
        state = fresh.recover_gate(ACCOUNT, at=at)
        assert state.history == (transition,)
        assert state.latest_assessment == assessment
        assert state.claim == claim and state.outstanding_claim is None
        assert set(state.blockers) == {Blocker.IDENTITY_UNVERIFIED, *exposure}
        assert (
            transition.before == before.blockers and transition.after == state.blockers
        )
        assert not fresh.consider_action(ACCOUNT, REQUEST, at=at).can_consider
        assert (
            Blocker.EXPOSURE_STALE
            in fresh.recover_gate(
                ACCOUNT,
                at=at + timedelta(minutes=3),
            ).blockers
        )
        with pytest.raises(DispatchHeld):
            fresh.claim_dispatch(ACCOUNT, opening(fresh, "second-action").id)
    finally:
        reopened.dispose()


@pytest.mark.parametrize(
    ("case", "blocker"),
    [
        ("missing", Blocker.CONFIRMATION_MISSING),
        ("conflicting", Blocker.EVIDENCE_CONFLICT),
        ("multi", Blocker.UNSUPPORTED_OUTCOME),
        ("incomplete", Blocker.EXPOSURE_UNVERIFIED),
        ("stale", Blocker.EXPOSURE_STALE),
    ],
)
def test_postgres_evidence_refuses_resolution(gate_pg, case, blocker):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(
        gate, claim, "accepted" if case == "stale" else case
    )
    if case == "stale":
        at += timedelta(minutes=2)
    with pytest.raises(GateEvidenceError):
        resolve(gate, claim, assessment, at)
    state = gate.recover_gate(ACCOUNT, at=at)
    assert state.outstanding_claim == claim and not state.history
    assert blocker in state.blockers


def test_postgres_wrong_scope_is_rejected(gate_pg):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    with pytest.raises(GateEvidenceError):
        gate.resolve_claim(
            uuid4(),
            OTHER,
            claim.id,
            assessment.id,
            expected_revision=0,
            actor=ACTOR,
            decided_at=at,
        )
    assert gate.recover_gate(OTHER, at=at).claim is None
    assert gate.recover_gate(ACCOUNT, at=at).outstanding_claim == claim


@pytest.mark.parametrize("extra", ["incomplete", "contradictory", "late-transaction"])
def test_postgres_later_evidence_supersedes_resolution(gate_pg, extra):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    transition = resolve(gate, claim, assessment, at)
    later = at + 10 * SECOND
    if extra == "late-transaction":
        gate.record_transaction(
            uuid4(),
            claim.intent_id,
            accepted(
                provenance=_origin("late-confirmation", at, 3),
                outcome="REJECTED",
            ),
        )
    else:
        make_reads(
            gate,
            at,
            "later",
            (),
            status=("INCOMPLETE" if extra == "incomplete" else "COMPLETE"),
        )
    state = gate.recover_gate(ACCOUNT, at=later)
    assert state.history == (transition,)
    assert state.claim == state.outstanding_claim == claim
    assert Blocker.EVIDENCE_CONFLICT in state.blockers
    assert not gate.consider_action(ACCOUNT, REQUEST, at=later).can_consider


def clone_assessment(engine, assessment, **patch):
    identity = uuid4()
    with engine.begin() as connection:
        original = dict(
            connection.execute(
                select(AssessmentRow.__table__).where(AssessmentRow.id == assessment.id)
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(AssessmentRow).values({**original, **patch, "id": identity})
        )
        connection.execute(
            insert(AssessmentInputRow).values(
                assessment_id=identity,
                ordinal=0,
                scope_id=original["scope_id"],
                intent_id=original["intent_id"],
                observation_id=assessment.inputs.transaction_ids[0],
            )
        )
    return replace(assessment, id=identity)


@pytest.mark.parametrize(
    "patch",
    [
        {"algorithm": "unsupported-v9"},
        {"evidence_ids": ["invented-id"]},
    ],
)
def test_postgres_unsupported_or_corrupt_assessment_is_blocking(gate_pg, patch):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    altered = clone_assessment(engine, assessment, **patch)
    with pytest.raises(GateEvidenceError, match="EVIDENCE_UNREPLAYABLE"):
        resolve(gate, claim, altered, at)
    # A valid assessment cannot override other contradictory or unreplayable evidence.
    with pytest.raises(GateEvidenceError, match="EVIDENCE_UNREPLAYABLE"):
        resolve(gate, claim, assessment, at)
    assert gate.recover_gate(ACCOUNT, at=at).outstanding_claim == claim


def test_postgres_review_never_clears_uncertainty_and_replay_is_exact(gate_pg):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    decision = claim.claimed_at
    transition = restrict(gate, decision)
    assert restrict(gate, decision, identity=transition.id) == transition
    with pytest.raises(JournalConflict, match="identity"):
        restrict(gate, decision, identity=transition.id, actor="other-actor")
    state = gate.recover_gate(ACCOUNT, at=decision)
    assert state.outstanding_claim == claim
    assert Blocker.OPERATIONAL_RESTRICTION in state.blockers
    assessment, at = make_assessment(gate, claim)
    resolved = resolve(gate, claim, assessment, at, revision=1)
    assert gate.recover_gate(ACCOUNT, at=at).outstanding_claim is None
    assert Blocker.OPERATIONAL_RESTRICTION in resolved.after


@pytest.mark.parametrize("race", ("restrict-restrict", "restrict-claim"))
def test_postgres_concurrent_gate_mutations(gate_pg, race):
    gate, engine, _ = gate_pg
    intent = opening(gate)
    start = Barrier(2, timeout=5)
    at = datetime.now(UTC)

    @event.listens_for(engine, "begin")
    def limit_lock(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")

    def restriction():
        start.wait()
        try:
            return restrict(GateJournal(engine), at)
        except JournalConflict:
            return None

    def claim():
        start.wait()
        try:
            return GateJournal(engine).claim_dispatch(ACCOUNT, intent.id)
        except JournalStorageError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(restriction)
        second = pool.submit(restriction if race == "restrict-restrict" else claim)
        results = (first.result(timeout=10), second.result(timeout=10))
    state = gate.recover_gate(ACCOUNT, at=at + SECOND)
    assert state.revision == 1 and Blocker.OPERATIONAL_RESTRICTION in state.blockers
    if race == "restrict-restrict":
        assert sum(item is not None for item in results) == 1
    else:
        assert state.claim == state.outstanding_claim == results[1]
        with engine.connect() as connection:
            assert connection.scalar(
                select(func.count()).select_from(EvidenceRow)
            ) == int(results[1] is not None)


def test_postgres_interrupted_transition_rolls_back(gate_pg):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION reject_gate_test() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Test interrupted transition'; END;
            $$
            """)
        connection.exec_driver_sql("""
            CREATE CONSTRAINT TRIGGER reject_gate_test
            AFTER INSERT ON broker_gate_transitions
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION reject_gate_test()
            """)
    with pytest.raises(JournalStorageError):
        resolve(gate, claim, assessment, at)
    state = GateJournal(engine).recover_gate(ACCOUNT, at=at)
    assert state.outstanding_claim == claim and not state.history
    assert state.latest_assessment == assessment


def test_postgres_database_guards_bypass(gate_pg):
    gate, engine, _ = gate_pg
    intent = opening(gate)
    at = datetime.now(UTC)
    restrict(gate, at)
    with engine.connect() as connection:
        account = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == intent.id)
        )
        current = dict(
            connection.execute(select(GateTransitionRow.__table__)).mappings().one()
        )
    with pytest.raises(IntegrityError) as blocked, engine.begin() as connection:
        identity = uuid4()
        connection.execute(db_event(account, intent.id, event_id=identity))
        connection.execute(
            insert(ClaimRow).values(
                id=identity,
                scope_id=account,
                intent_id=intent.id,
                claimed_at=at,
            )
        )
    assert blocked.value.orig.sqlstate == "23514"
    for patch in (
        {},
        {"revision": 4, "previous_revision": 3},
        {"revision": 2, "previous_revision": 1, "after_blockers": ["BYPASS"]},
        {"revision": 2, "previous_revision": 1, "policy": "UNKNOWN"},
    ):
        with pytest.raises(IntegrityError) as invalid, engine.begin() as connection:
            connection.execute(
                insert(GateTransitionRow).values({**current, **patch, "id": uuid4()})
            )
        assert invalid.value.orig.sqlstate == "23514"
    for sql in (
        "UPDATE broker_gate_transitions SET actor = 'replacement'",
        "DELETE FROM broker_gate_transitions",
        "TRUNCATE broker_gate_transitions",
    ):
        with pytest.raises(DBAPIError) as denied, engine.begin() as connection:
            connection.exec_driver_sql(sql)
        assert denied.value.orig.sqlstate == "55000"
    assert gate.recover_gate(ACCOUNT, at=at).claim is None


def test_postgres_gate_migration_preserves_prior_records_and_refuses_loss(pg_db):  # noqa: F811
    engine, config = pg_db
    old = "0006_broker_observations"
    command.upgrade(config, old)
    gate = GateJournal(engine)
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    run = JournalRepository(engine).create_run(
        run_date=at.date(),
        mode="fixture",
        config_hash="config",
        bundle_hash="bundle",
        code_version="version",
        config_snapshot={"legacy": True},
    )
    market = MarketDataRepository(engine)
    market.register_mapping(MAPPING, provenance="fixture")
    record(market, "before-gate")
    baseline = set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, old)
    assert set(inspect(engine).get_table_names()) == baseline
    command.upgrade(config, "head")
    assert gate.recover_gate(ACCOUNT, at=at).outstanding_claim == claim
    assert gate.replay(ACCOUNT, assessment.id).matches
    assert history(market).revisions[0].source.retrieval_id == "before-gate"
    with engine.connect() as connection:
        assert connection.scalar(select(Run.id)) == run.run_id
    transition = resolve(gate, claim, assessment, at)
    with pytest.raises(RuntimeError, match="empty gate history"):
        command.downgrade(config, old)
    assert gate.recover_gate(ACCOUNT, at=at).history == (transition,)


def test_postgres_concurrent_resolution_records_one_transition(gate_pg):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    start = Barrier(2, timeout=5)

    @event.listens_for(engine, "begin")
    def limit_lock(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")

    def attempt():
        start.wait()
        try:
            return resolve(GateJournal(engine), claim, assessment, at)
        except JournalConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    [winner] = [result for result in results if result is not None]
    assert gate.recover_gate(ACCOUNT, at=at).history == (winner,)


def test_postgres_direct_resolution_cannot_use_wrong_collection_or_forged_match(
    gate_pg,
):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    forged = clone_assessment(engine, assessment, evidence_ids=["fabricated"])
    with engine.connect() as connection:
        scope_id = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == claim.intent_id)
        )
    values = {
        "scope_id": scope_id,
        "revision": 1,
        "previous_revision": 0,
        "reason": "OPENING_RESOLVED",
        "before_blockers": [],
        "after_blockers": [],
        "claim_id": claim.id,
        "intent_id": claim.intent_id,
        "assessment_id": forged.id,
        "positions_id": assessment.inputs.positions_id,
        "orders_id": assessment.inputs.orders_id,
        "actor": ACTOR,
        "policy": "account-gate-v1",
        "decided_at": at,
        "recorded_at": at,
    }
    with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
        connection.execute(
            insert(GateTransitionRow).values(
                {
                    **values,
                    "positions_id": uuid4(),
                    "id": uuid4(),
                }
            )
        )
    assert denied.value.orig.sqlstate == "23514"
    with engine.begin() as connection:
        connection.execute(insert(GateTransitionRow).values({**values, "id": uuid4()}))
    state = gate.recover_gate(ACCOUNT, at=at)
    assert state.outstanding_claim == claim
    assert Blocker.EVIDENCE_UNREPLAYABLE in state.blockers
    assert not gate.consider_action(ACCOUNT, REQUEST, at=at).can_consider
