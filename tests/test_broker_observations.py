from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, event, func, insert, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_execution_journal import ACCOUNT, OTHER, REQUEST, create
from test_market_data_storage import MAPPING, history, pg_db, record  # noqa: F401, F811

from mrml.broker.evidence import (
    Acknowledgement,
    ActivityOpening,
    AffectedDeal,
    Confirmation,
    Position,
    PositionRead,
    Provenance,
    WorkingOrder,
    WorkingOrderRead,
)
from mrml.broker.journal import DispatchHeld, JournalConflict, JournalStorageError
from mrml.broker.journal_models import ClaimRow
from mrml.broker.observation_models import (
    AssessmentInputRow,
    AssessmentRow,
    ObservationRow,
)
from mrml.broker.observations import (
    AssessmentInputs,
    Collection,
    ConfirmationNotFound,
    ObservationJournal,
    ReplayUnavailable,
)
from mrml.data.storage import MarketDataRepository
from mrml.journal.models import Run
from mrml.journal.repository import JournalRepository

NOW = datetime(2026, 11, 25, tzinfo=UTC)
AGE = timedelta(seconds=15)
REF = "Example-Reference"
DEAL = "example-deal"
QUANTITY = Decimal("1.000000000000000001")
PRICE = Decimal("12345678901234567890.123456789012345678")
POSITION = Position(
    DEAL, REQUEST.instrument, REQUEST.side, QUANTITY, REF, open_level=PRICE
)
ORDER = WorkingOrder(
    "working-deal",
    REQUEST.instrument,
    side="BUY",
    quantity=QUANTITY,
    order_type="LIMIT",
    order_level=PRICE,
    time_in_force="GOOD_TILL_DATE",
)
COMPLETE = Collection(
    "COMPLETE", NOW - timedelta(seconds=5), NOW - timedelta(seconds=4)
)


def provenance(key, offset=3, scope=ACCOUNT):
    return Provenance(key, scope, NOW - timedelta(seconds=offset), "synthetic-v1")


def accepted(**changes):
    return replace(
        Confirmation(
            provenance("confirmation", 6),
            REF,
            "ACCEPTED",
            "transaction-deal",
            (AffectedDeal(DEAL, "OPENED"),),
            instrument=REQUEST.instrument,
            side=REQUEST.side,
            quantity=QUANTITY,
            raw_date="unverified source label",
            level=PRICE,
        ),
        **changes,
    )


@pytest.fixture
def observation_pg(pg_db):  # noqa: F811
    engine, config = pg_db
    command.upgrade(config, "head")
    return ObservationJournal(engine), engine, config


def opening(journal, key="opening"):
    return create(
        journal,
        key=key,
        request=replace(
            REQUEST,
            request_reference=REF if key == "opening" else key,
            quantity=QUANTITY,
        ),
    )


def saved_set(
    journal,
    intent_id,
    *,
    rows=(POSITION,),
    order_rows=(),
    position_offset=3,
    confirmations=None,
    missing=False,
    position_status="COMPLETE",
    target=None,
    order_status="COMPLETE",
):
    positions = journal.record_collection(
        uuid4(),
        PositionRead(
            provenance("positions", position_offset),
            position_status in ("COMPLETE", "NOT_FOUND"),
            rows,
            target,
        ),
        Collection(position_status),
    )
    orders = journal.record_collection(
        uuid4(),
        WorkingOrderRead(
            provenance("orders"),
            order_status == "COMPLETE",
            order_rows,
        ),
        Collection(order_status),
    )
    if confirmations is None:
        confirmations = () if missing else (accepted(),)
    transaction_ids = tuple(
        journal.record_transaction(uuid4(), intent_id, item).id
        for item in confirmations
    )
    if missing:
        transaction_ids += (
            journal.record_transaction(
                uuid4(),
                intent_id,
                ConfirmationNotFound(provenance("missing"), REF),
            ).id,
        )
    return AssessmentInputs(positions.id, orders.id, transaction_ids)


def assess(journal, intent_id, inputs, *, identity=None, **clock):
    return journal.assess_opening(
        identity or uuid4(),
        ACCOUNT,
        intent_id,
        inputs,
        **({"evaluated_at": NOW, "max_age": AGE} | clock),
    )


@pytest.mark.parametrize(
    ("kind", "status", "rows", "target"),
    [
        ("positions", "COMPLETE", (), None),
        ("orders", "COMPLETE", (), None),
        ("positions", "INCOMPLETE", (POSITION,), None),
        ("orders", "FAILED", (), None),
        ("positions", "NOT_FOUND", (), DEAL),
        ("positions", "COMPLETE", (POSITION,), DEAL),
        ("positions", "COMPLETE", (POSITION,), None),
        ("orders", "COMPLETE", (ORDER,), None),
    ],
)
def test_postgres_collection_round_trip_and_coverage(
    observation_pg, kind, status, rows, target
):
    journal, engine, _ = observation_pg
    source = provenance("collection")
    if kind == "positions":
        value = PositionRead(source, status in ("COMPLETE", "NOT_FOUND"), rows, target)
    else:
        value = WorkingOrderRead(source, status == "COMPLETE", rows)
    info = replace(COMPLETE, status=status)
    result = journal.record_collection(uuid4(), value, info)
    assert (
        result.value == value and result.collection == info and result.intent_id is None
    )
    assert journal.read_observation(ACCOUNT, result.id) == result
    if rows:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT jsonb_typeof(payload->0->'quantity') "
                        "FROM broker_observations"
                    )
                )
                == "number"
            )
    with pytest.raises(ReplayUnavailable, match="unavailable"):
        journal.read_observation(ACCOUNT, uuid4())


def test_postgres_observation_replay_conflict_and_repeat_deal(observation_pg):
    journal, _, _ = observation_pg
    row = PositionRead(provenance("first"), True, (POSITION,))
    stored = journal.record_collection(uuid4(), row, COMPLETE)
    assert journal.record_collection(stored.id, row, COMPLETE) == stored
    with pytest.raises(JournalConflict):
        journal.record_collection(stored.id, replace(row, positions=()), COMPLETE)
    with pytest.raises(JournalConflict):
        journal.record_collection(uuid4(), row, COMPLETE)
    later = replace(row, provenance=provenance("later", 1))
    assert journal.record_collection(uuid4(), later, COMPLETE).value == later
    with pytest.raises(ValueError, match="Completeness"):
        journal.record_collection(uuid4(), row, Collection("FAILED"))
    with pytest.raises(ValueError, match="target"):
        journal.record_collection(
            uuid4(), replace(row, positions=()), Collection("NOT_FOUND")
        )


def test_postgres_concurrent_identity_replay(observation_pg):
    journal, engine, _ = observation_pg
    identity = uuid4()
    read = PositionRead(provenance("same"), True, (POSITION,))
    ready = Barrier(2, timeout=5)

    @event.listens_for(engine, "begin")
    def bounded_lock(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")

    def save():
        ready.wait()
        return ObservationJournal(engine).record_collection(identity, read, COMPLETE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        first, second = [future.result(timeout=10) for future in futures]
    assert first == second == journal.read_observation(ACCOUNT, identity)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ObservationRow)) == 1


@pytest.mark.parametrize(
    ("options", "status", "reason"),
    [
        ({}, "MATCHED", "OPEN_POSITION_MATCHED"),
        ({"rows": ()}, "MISMATCH", "POSITION_ABSENT"),
        (
            {"rows": (), "target": DEAL, "position_status": "NOT_FOUND"},
            "MISMATCH",
            "POSITION_ABSENT",
        ),
        ({"position_offset": 9}, "UNRESOLVED", "POSITION_OBSERVATION_ORDER_UNVERIFIED"),
        ({"position_offset": 30}, "UNRESOLVED", "STALE_OR_INVALID_EXPOSURE_READ"),
        ({"position_status": "INCOMPLETE"}, "UNRESOLVED", "INCOMPLETE_EXPOSURE_READ"),
        ({"order_status": "FAILED"}, "UNRESOLVED", "INCOMPLETE_EXPOSURE_READ"),
        ({"missing": True}, "UNRESOLVED", "CONFIRMATION_NOT_FOUND"),
        (
            {"confirmations": (accepted(affected_deals=None),)},
            "UNRESOLVED",
            "UNSUPPORTED_AFFECTED_DEALS",
        ),
        (
            {
                "confirmations": (
                    accepted(
                        affected_deals=(
                            AffectedDeal(DEAL, "OPENED"),
                            AffectedDeal("other-leg", "PARTIALLY_CLOSED"),
                        )
                    ),
                )
            },
            "UNRESOLVED",
            "UNSUPPORTED_AFFECTED_DEALS",
        ),
        (
            {
                "confirmations": (
                    accepted(),
                    accepted(
                        provenance=provenance("different-confirm"), level=Decimal("2")
                    ),
                )
            },
            "MISMATCH",
            "CONFLICTING_CONFIRMATIONS",
        ),
    ],
)
def test_postgres_assessment_cases_keep_claim_hold(
    observation_pg, options, status, reason
):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    claim = journal.claim_dispatch(ACCOUNT, intent.id)
    inputs = saved_set(journal, intent.id, **options)
    assessment = assess(journal, intent.id, inputs)
    assert (assessment.result.status, assessment.result.reason) == (status, reason)
    assert journal.replay(ACCOUNT, assessment.id).matches
    assert journal.account_hold(ACCOUNT) == claim
    with pytest.raises(DispatchHeld):
        journal.claim_dispatch(ACCOUNT, intent.id)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ClaimRow)) == 1


def test_postgres_reconnect_replays_exact_evidence_in_original_order(observation_pg):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    confirmation = accepted()
    activity = ActivityOpening(provenance("activity"), REF, DEAL)
    ack = Acknowledgement(provenance("ack"), REF)
    inputs = saved_set(journal, intent.id, confirmations=(activity, confirmation, ack))
    original = assess(journal, intent.id, inputs)
    assert assess(journal, intent.id, inputs, identity=original.id) == original
    with pytest.raises(JournalConflict):
        assess(journal, intent.id, inputs, identity=original.id, max_age=AGE * 2)
    journal.record_collection(
        uuid4(),
        PositionRead(provenance("later", 1), True, ()),
        Collection("COMPLETE"),
    )
    reopened = create_engine(engine.url)
    try:
        fresh = ObservationJournal(reopened)
        replay = fresh.replay(ACCOUNT, original.id)
        assert replay.matches and replay.stored == original
        assert replay.stored.inputs == inputs
        assert (
            fresh.read_observation(ACCOUNT, inputs.transaction_ids[1]).value
            == confirmation
        )
        assert (
            fresh.read_observation(ACCOUNT, inputs.transaction_ids[0]).value == activity
        )
        assert fresh.read_observation(ACCOUNT, inputs.transaction_ids[2]).value == ack
    finally:
        reopened.dispose()


def test_postgres_scope_and_invalid_explicit_inputs(observation_pg):
    journal, _, _ = observation_pg
    intent = opening(journal)
    other_intent = opening(journal, "different-intent")
    foreign = journal.record_collection(
        uuid4(),
        PositionRead(provenance("other-positions", scope=OTHER), True, ()),
        COMPLETE,
    )
    inputs = saved_set(journal, intent.id)
    with pytest.raises(ReplayUnavailable, match="scope"):
        assess(journal, intent.id, replace(inputs, positions_id=foreign.id))
    with pytest.raises(ReplayUnavailable, match="scope"):
        assess(journal, intent.id, replace(inputs, orders_id=uuid4()))
    with pytest.raises(ReplayUnavailable, match="types"):
        assess(journal, intent.id, replace(inputs, positions_id=inputs.orders_id))
    unrelated = journal.record_transaction(
        uuid4(),
        other_intent.id,
        accepted(
            provenance=provenance("other-confirmation"),
        ),
    )
    with pytest.raises(ReplayUnavailable, match="intended action"):
        assess(journal, intent.id, replace(inputs, transaction_ids=(unrelated.id,)))
    with pytest.raises(LookupError, match="scope"):
        journal.record_transaction(
            uuid4(),
            intent.id,
            accepted(
                provenance=provenance("wrong-scope", scope=OTHER),
            ),
        )
    with pytest.raises(ValueError, match="timezone"):
        assess(journal, intent.id, inputs, evaluated_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="positive"):
        assess(journal, intent.id, inputs, max_age=timedelta(0))
    stored = assess(journal, intent.id, inputs)
    with pytest.raises(ReplayUnavailable, match="scope"):
        journal.replay(OTHER, stored.id)


def reject_insert(engine, table):
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION fail_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Test rollback'; END;
            $$
            """)
        connection.exec_driver_sql(
            f"CREATE TRIGGER fail_test_insert AFTER INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fail_test_insert()"
        )


def test_postgres_batch_rollback_has_no_complete_row(observation_pg):
    journal, engine, _ = observation_pg
    reject_insert(engine, "broker_observations")
    identity = uuid4()
    with pytest.raises(JournalStorageError):
        journal.record_collection(
            identity, PositionRead(provenance("rows"), True, (POSITION,)), COMPLETE
        )
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ObservationRow)) == 0
    with pytest.raises(ReplayUnavailable):
        journal.read_observation(ACCOUNT, identity)


def test_postgres_assessment_rollback_has_no_partial_manifest(observation_pg):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    inputs = saved_set(journal, intent.id)
    reject_insert(engine, "broker_assessment_inputs")
    identity = uuid4()
    with pytest.raises(JournalStorageError):
        assess(journal, intent.id, inputs, identity=identity)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(AssessmentRow)) == 0
        assert (
            connection.scalar(select(func.count()).select_from(AssessmentInputRow)) == 0
        )
        assert connection.scalar(select(func.count()).select_from(ObservationRow)) == 3


def assessment_values(engine, identity):
    with engine.connect() as connection:
        return dict(
            connection.execute(
                select(AssessmentRow.__table__).where(AssessmentRow.id == identity)
            )
            .mappings()
            .one()
        )


def test_postgres_replay_flags_unsupported_algorithm_and_mismatched_result(
    observation_pg,
):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    inputs = saved_set(journal, intent.id, missing=True)
    saved = assess(journal, intent.id, inputs)
    values = assessment_values(engine, saved.id)
    future_id, wrong_id = uuid4(), uuid4()
    with engine.begin() as connection:
        for identity, patch in [
            (future_id, {"algorithm": "opening-v2"}),
            (wrong_id, {"status": "MATCHED", "reason": "OPEN_POSITION_MATCHED"}),
        ]:
            connection.execute(
                insert(AssessmentRow).values({**values, **patch, "id": identity})
            )
            connection.execute(
                insert(AssessmentInputRow).values(
                    assessment_id=identity,
                    ordinal=0,
                    scope_id=values["scope_id"],
                    intent_id=intent.id,
                    observation_id=inputs.transaction_ids[0],
                )
            )
    with pytest.raises(ReplayUnavailable, match="algorithm"):
        journal.replay(ACCOUNT, future_id)
    comparison = journal.replay(ACCOUNT, wrong_id)
    assert not comparison.matches
    assert comparison.recomputed.reason == "CONFIRMATION_NOT_FOUND"


def test_postgres_constraints_seal_assessment_and_refuse_wrong_scope_fk(observation_pg):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    inputs = saved_set(journal, intent.id)
    assessment = assess(journal, intent.id, inputs)
    values = assessment_values(engine, assessment.id)
    foreign = journal.record_collection(
        uuid4(),
        PositionRead(provenance("foreign", scope=OTHER), True, ()),
        COMPLETE,
    )
    for patch, code in (
        ({"positions_id": foreign.id}, "23503"),
        ({"positions_id": uuid4()}, "23503"),
        ({}, "23514"),  # Declares one transaction link but omits it.
    ):
        with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
            connection.execute(
                insert(AssessmentRow).values({**values, **patch, "id": uuid4()})
            )
        assert denied.value.orig.sqlstate == code
    extra = journal.record_transaction(
        uuid4(),
        intent.id,
        Acknowledgement(provenance("extra"), REF),
    )
    with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
        connection.execute(
            insert(AssessmentInputRow).values(
                assessment_id=assessment.id,
                ordinal=1,
                observation_id=extra.id,
                scope_id=values["scope_id"],
                intent_id=intent.id,
            )
        )
    assert denied.value.orig.sqlstate == "23514"
    assert journal.replay(ACCOUNT, assessment.id).matches
    with engine.connect() as connection:
        payload = dict(
            connection.execute(
                select(ObservationRow.__table__).where(
                    ObservationRow.id == inputs.positions_id
                )
            )
            .mappings()
            .one()
        )
    with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
        connection.execute(
            insert(ObservationRow).values(
                {
                    **payload,
                    "id": uuid4(),
                    "original_id": "invalid-members",
                    "payload": [],
                }
            )
        )
    assert denied.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE broker_observations SET member_count = 0",
        "DELETE FROM broker_assessment_inputs",
        "TRUNCATE broker_assessments CASCADE",
    ],
)
def test_postgres_immutable_observations_and_assessments(observation_pg, statement):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    result = assess(journal, intent.id, saved_set(journal, intent.id))
    with pytest.raises(DBAPIError) as denied, engine.begin() as connection:
        connection.exec_driver_sql(statement)
    assert denied.value.orig.sqlstate == "55000"
    assert journal.replay(ACCOUNT, result.id).matches


def test_postgres_observation_migration_preserves_existing_records(pg_db):  # noqa: F811
    engine, config = pg_db
    command.upgrade(config, "0005_execution_journal")
    journal = ObservationJournal(engine)
    legacy = JournalRepository(engine).create_run(
        run_date=NOW.date(),
        mode="fixture",
        config_hash="config",
        bundle_hash="bundle",
        code_version="version",
        config_snapshot={"legacy": True},
    )
    market = MarketDataRepository(engine)
    market.register_mapping(MAPPING, provenance="fixture")
    record(market, "before-observations")
    intent = opening(journal)
    claim = journal.claim_dispatch(ACCOUNT, intent.id)
    baseline = set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "0005_execution_journal")
    assert set(inspect(engine).get_table_names()) == baseline
    command.upgrade(config, "head")
    assert journal.account_hold(ACCOUNT) == claim
    assert history(market).revisions[0].source.retrieval_id == "before-observations"
    with engine.connect() as connection:
        assert connection.scalar(select(Run.id)) == legacy.run_id
    result = assess(journal, intent.id, saved_set(journal, intent.id))
    with pytest.raises(RuntimeError, match="empty"):
        command.downgrade(config, "0005_execution_journal")
    assert journal.replay(ACCOUNT, result.id).matches


def test_postgres_observation_integrity_failure_is_reported_in_replay(observation_pg):
    journal, engine, _ = observation_pg
    intent = opening(journal)
    inputs = saved_set(journal, intent.id)
    saved = assess(journal, intent.id, inputs)
    corrupt_id = uuid4()
    with engine.begin() as connection:
        original = dict(
            connection.execute(
                select(ObservationRow.__table__).where(
                    ObservationRow.id == inputs.positions_id
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(ObservationRow).values(
                {
                    **original,
                    "id": corrupt_id,
                    "original_id": "invalid-digest",
                }
            )
        )
        assessment = assessment_values(engine, saved.id)
        fabricated_id = uuid4()
        connection.execute(
            insert(AssessmentRow).values(
                {
                    **assessment,
                    "id": fabricated_id,
                    "positions_id": corrupt_id,
                    "transaction_count": 0,
                }
            )
        )
    with pytest.raises(ReplayUnavailable, match="integrity"):
        journal.replay(ACCOUNT, fabricated_id)
