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
from mrml.broker.journal import (
    ApprovedRequest,
    DispatchHeld,
    JournalConflict,
    JournalStorageError,
)
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


def closing(gate, key="close", *, deal=DEAL, quantity=POSITION.quantity, auth="close"):
    return gate.create_intent(
        ACCOUNT,
        idempotency_key=key,
        request=ApprovedRequest(
            "CLOSE",
            POSITION.instrument,
            "SELL",
            quantity,
            "MARKET",
            "-",
            target_deal_id=deal,
        ),
        authorization_id=auth,
    )


def test_postgres_closing_preparation_requires_fresh_reads_after_restart(
    gate_pg,
):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    transition = resolve(gate, claim, assessment, at)
    later = at + timedelta(minutes=1)
    successor = closing(gate)
    with pytest.raises(GateEvidenceError, match="EXPOSURE_STALE"):
        gate.prepare_closing(ACCOUNT, successor.id, at=later)
    position_id, orders_id = make_reads(gate, later, "refreshed")
    decision = later + 8 * SECOND
    engine.dispose()
    reopened = GateJournal(engine)
    state = reopened.recover_gate(ACCOUNT, at=decision)
    assert state.claims == reopened.claim_history(ACCOUNT) == (claim,)
    assert state.latest_claim == claim
    assert state.outstanding_claims == ()
    assert state.history == (transition,)
    assert state.attributable_positions == (POSITION,)
    assert state.unmatched_positions == ()
    binding = reopened.prepare_closing(ACCOUNT, successor.id, at=decision)
    assert binding.opening_claim == claim
    assert binding.closing_intent_id == successor.id
    assert binding.authorization_id == successor.authorization_id
    assert (binding.target_deal_id, binding.quantity) == (DEAL, POSITION.quantity)
    assert (binding.positions_id, binding.orders_id) == (position_id, orders_id)
    assert binding.gate_revision == transition.revision
    for _ in range(2):
        with pytest.raises(DispatchHeld):
            reopened.claim_dispatch(ACCOUNT, successor.id)
    assert reopened.read_intent(ACCOUNT, successor.id)[1] == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"deal": "unrelated"},
        {"quantity": POSITION.quantity * 2},
        {"quantity": POSITION.quantity - 1},
        {"auth": "operator-approval"},
    ],
)
def test_postgres_closing_cannot_change_opening_binding(gate_pg, changes):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    resolve(gate, claim, assessment, at)
    opening_intent, _ = gate.read_intent(ACCOUNT, claim.intent_id)
    if "auth" in changes:
        changes = {"auth": opening_intent.authorization_id}
    with pytest.raises(JournalConflict, match="full-close terms"):
        gate.prepare_closing(ACCOUNT, closing(gate, **changes).id, at=at)


@pytest.mark.parametrize("case", ["external", "incomplete", "restriction", "late"])
def test_postgres_closing_preparation_preserves_account_blockers(gate_pg, case):
    gate, _, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(
        gate, claim, "external" if case == "external" else "accepted"
    )
    resolve(gate, claim, assessment, at)
    if case == "restriction":
        restrict(gate, at, revision=1)
    if case == "incomplete":
        make_reads(gate, at, "incomplete-refresh", status="INCOMPLETE")
    if case == "late":
        gate.record_transaction(
            uuid4(),
            claim.intent_id,
            accepted(provenance=_origin("contradiction", at, 2), outcome="REJECTED"),
        )
    with pytest.raises(GateEvidenceError):
        gate.prepare_closing(ACCOUNT, closing(gate).id, at=at + 8 * SECOND)


@pytest.mark.parametrize("competing_action", ["successor", "restriction"])
def test_postgres_direct_successor_races_are_disabled(gate_pg, competing_action):
    gate, engine, _ = gate_pg
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    resolve(gate, claim, assessment, at)
    successors = [closing(gate, key=f"successor-{n}") for n in range(2)]
    with engine.connect() as connection:
        account = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == claim.intent_id)
        )
    start = Barrier(2, timeout=5)

    def insert_successor(intent):
        start.wait()
        with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            identity = uuid4()
            connection.execute(db_event(account, intent.id, event_id=identity))
            connection.execute(
                insert(ClaimRow).values(
                    id=identity,
                    scope_id=account,
                    intent_id=intent.id,
                    claim_number=2,
                    claimed_at=at,
                )
            )
        assert denied.value.orig.sqlstate == "23514"

    def add_restriction():
        start.wait()
        return restrict(GateJournal(engine), at, revision=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(insert_successor, successors[0])
        second = (
            pool.submit(insert_successor, successors[1])
            if competing_action == "successor"
            else pool.submit(add_restriction)
        )
        first.result(timeout=10)
        restriction = second.result(timeout=10)
    state = gate.recover_gate(ACCOUNT, at=at)
    assert state.claims == (claim,)
    assert (Blocker.OPERATIONAL_RESTRICTION in state.blockers) == bool(restriction)
    assert all(gate.read_intent(ACCOUNT, intent.id)[1] == () for intent in successors)


def test_postgres_populated_0007_upgrade_and_unrepresentable_downgrade(pg_db):  # noqa: F811
    engine, config = pg_db
    # Populate via the compatible single-claim writer and existing transition schema.
    command.upgrade(config, "0007_account_gate")
    gate = GateJournal(engine)
    claim = new_claim(gate)
    assessment, at = make_assessment(gate, claim)
    # Version 0007's writer and reader have no claim-number column.
    with engine.begin() as connection:
        scope = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == claim.intent_id)
        )
        transition_id = uuid4()
        connection.execute(
            insert(GateTransitionRow).values(
                id=transition_id,
                scope_id=scope,
                revision=1,
                previous_revision=0,
                reason="OPENING_RESOLVED",
                before_blockers=["CLAIM_UNRESOLVED"],
                after_blockers=["KNOWN_POSITION"],
                claim_id=claim.id,
                intent_id=claim.intent_id,
                assessment_id=assessment.id,
                positions_id=assessment.inputs.positions_id,
                orders_id=assessment.inputs.orders_id,
                actor=ACTOR,
                policy="account-gate-v1",
                decided_at=at,
                recorded_at=at,
            )
        )
    expected = gate.read_intent(ACCOUNT, claim.intent_id)
    command.upgrade(config, "head")
    command.check(config)
    state = gate.recover_gate(ACCOUNT, at=at)
    assert state.claims == (claim,)
    assert state.outstanding_claims == ()
    assert state.history[0].id == transition_id
    assert gate.read_intent(ACCOUNT, claim.intent_id) == expected
    command.downgrade(config, "0007_account_gate")
    command.upgrade(config, "head")
    assert gate.recover_gate(ACCOUNT, at=at) == state

    # Model future data using fixture-owner DDL. Normal writers cannot insert it.
    successor = closing(gate)
    identity = uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE execution_claims DISABLE TRIGGER "
            "broker_claim_successive_admission"
        )
        connection.execute(db_event(scope, successor.id, event_id=identity))
        connection.execute(
            insert(ClaimRow).values(
                id=identity,
                scope_id=scope,
                intent_id=successor.id,
                claim_number=2,
                claimed_at=at,
            )
        )
        connection.exec_driver_sql(
            "ALTER TABLE execution_claims ENABLE TRIGGER "
            "broker_claim_successive_admission"
        )
    recovered = GateJournal(engine).recover_gate(ACCOUNT, at=at)
    assert recovered.claims[0] == claim and recovered.latest_claim.id == identity
    assert recovered.outstanding_claims == (recovered.latest_claim,)
    assert recovered.history == state.history
    assert gate.account_hold(ACCOUNT) == claim
    with pytest.raises(DispatchHeld):
        gate.claim_dispatch(ACCOUNT, successor.id)
    with pytest.raises(RuntimeError, match="cannot represent successive claim history"):
        command.downgrade(config, "0007_account_gate")


def test_postgres_0008_refuses_ambiguous_version_0007_accounts(pg_db):  # noqa: F811
    engine, config = pg_db
    command.upgrade(config, "0007_account_gate")
    gate = GateJournal(engine)
    first = new_claim(gate)
    other = opening(gate, "legacy-ambiguous")
    with engine.begin() as connection:
        account = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == first.intent_id)
        )
        connection.exec_driver_sql(
            "ALTER TABLE execution_claims DROP CONSTRAINT execution_claims_scope_id_key"
        )
        identity = uuid4()
        connection.execute(db_event(account, other.id, event_id=identity))
        connection.exec_driver_sql(
            "INSERT INTO execution_claims (id, scope_id, intent_id, claimed_at) "
            "VALUES (%s, %s, %s, %s)",
            (identity, account, other.id, first.claimed_at),
        )
    with pytest.raises(DBAPIError, match="Ambiguous version 0007 claim history"):
        command.upgrade(config, "head")
    assert "claim_number" not in {
        column["name"] for column in inspect(engine).get_columns("execution_claims")
    }


def test_postgres_first_close_and_numbered_opening_cannot_bypass_admission(gate_pg):
    gate, engine, _ = gate_pg
    proposed_close = closing(gate)
    with pytest.raises(DispatchHeld):
        gate.claim_dispatch(ACCOUNT, proposed_close.id)
    for intent, number in ((proposed_close, 1), (opening(gate), 2)):
        with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
            account = connection.scalar(
                select(IntentRow.scope_id).where(IntentRow.id == intent.id)
            )
            identity = uuid4()
            connection.execute(db_event(account, intent.id, event_id=identity))
            connection.execute(
                insert(ClaimRow).values(
                    id=identity,
                    scope_id=account,
                    intent_id=intent.id,
                    claim_number=number,
                    claimed_at=datetime.now(UTC),
                )
            )
        assert denied.value.orig.sqlstate == "23514"
        assert gate.read_intent(ACCOUNT, intent.id)[1] == ()
    assert gate.claim_history(ACCOUNT) == ()
