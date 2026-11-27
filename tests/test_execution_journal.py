from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, event, func, insert, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_market_data_storage import MAPPING, history, pg_db, record  # noqa: F401, F811

from mrml.broker.evidence import Scope
from mrml.broker.journal import (
    ApprovedRequest,
    DispatchHeld,
    EvidenceEvent,
    ExecutionJournal,
    JournalConflict,
    JournalStorageError,
)
from mrml.broker.journal_models import AccountRow, ClaimRow, EvidenceRow, IntentRow
from mrml.data.storage import MarketDataRepository
from mrml.journal.models import ConfigSnapshot, Run
from mrml.journal.repository import JournalRepository

ACCOUNT = Scope("IG", "demo", "synthetic-account")
OTHER = replace(ACCOUNT, account_id="other-account")
NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
QUANTITY = Decimal("12345678901234567890.123456789012345678")
PRICE = Decimal("101.000000000000000001")
REQUEST = ApprovedRequest(
    "OPEN",
    "SYNTHETIC.EPIC",
    "BUY",
    QUANTITY,
    "LIMIT",
    "-",
    currency="USD",
    force_open=True,
    guaranteed_stop=False,
    level=PRICE,
    stop_distance=Decimal("1.25"),
    limit_distance=Decimal("2.50"),
    time_in_force="FILL_OR_KILL",
)
OLD_HEAD = "0004_ig_gap_revisit"


@pytest.fixture
def journal_pg(pg_db):  # noqa: F811
    engine, config = pg_db
    command.upgrade(config, "head")
    return ExecutionJournal(engine), engine, config


def create(journal, key="operation-1", scope=ACCOUNT, request=REQUEST, **metadata):
    return journal.create_intent(
        scope,
        idempotency_key=key,
        request=request,
        authorization_id="synthetic-approval",
        **metadata,
    )


def evidence(kind="EXPOSURE", **changes):
    return replace(
        EvidenceEvent(uuid4(), f"source:{uuid4()}", kind, "OBSERVED", NOW), **changes
    )


def count(engine, model):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model))


def test_postgres_intent_replay_conflict_and_independent_keys(journal_pg):
    journal, engine, _ = journal_pg
    first = create(journal)
    assert first == create(
        journal, request=replace(REQUEST, stop_distance=Decimal("1.2500"))
    )
    assert first.quantity == QUANTITY
    assert json.loads(first.canonical_request)["version"] == 1
    assert json.loads(first.canonical_request)["level"] == format(PRICE, "f")
    for term in (
        {"side": "SELL"},
        {"force_open": False},
        {"time_in_force": "EXECUTE_AND_ELIMINATE"},
        {"stop_distance": Decimal("1.26")},
        {"request_reference": "new-reference"},
    ):
        for _ in range(2):
            with pytest.raises(JournalConflict, match="different approved"):
                create(journal, request=replace(REQUEST, **term))
    again, events = journal.read_intent(ACCOUNT, first.id)
    assert again == first
    assert len(events) == 5
    assert {record.event.reason for record in events} == {
        "REQUEST_FINGERPRINT_CONFLICT"
    }
    separate = create(journal, key="operation-2")
    assert separate.id != first.id and separate.fingerprint == first.fingerprint
    assert create(journal, scope=OTHER).id != first.id
    assert create(journal, scope=replace(ACCOUNT, provider="other")).id != first.id
    assert count(engine, IntentRow) == 4


def test_postgres_reference_uniqueness_and_close_target(journal_pg):
    journal, _, _ = journal_pg
    opening = replace(REQUEST, request_reference="Ref-Ab")
    create(journal, request=opening)
    with pytest.raises(JournalConflict, match="deal reference"):
        create(journal, key="different", request=opening)
    assert create(journal, scope=OTHER, request=opening).request_reference == "Ref-Ab"
    closing = ApprovedRequest(
        "CLOSE",
        REQUEST.instrument,
        "SELL",
        QUANTITY,
        "MARKET",
        "-",
        target_deal_id="target-One",
    )
    close = create(journal, key="close", request=closing)
    assert json.loads(close.canonical_request)["target_deal_id"] == "target-One"
    with pytest.raises(JournalConflict):
        create(
            journal, key="close", request=replace(closing, target_deal_id="target-Two")
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "MODIFY"},
        {"quantity": 1.5},
        {"quantity": Decimal("1.0000000000000000001")},
        {"quantity": Decimal("1e20")},
        {"quantity": Decimal("NaN")},
        {"currency": "USD-invalid"},
        {"order_type": "QUOTE"},
        {"force_open": None},
        {"target_deal_id": "not-valid-for-open"},
        {"level": None},
        {"stop_level": Decimal("1")},
    ],
)
def test_request_refuses_unrepresentable_or_unsupported_terms(changes):
    # This validator runs before any transaction.
    from mrml.broker.journal import _canonical

    with pytest.raises(ValueError):
        _canonical(replace(REQUEST, **changes))


def test_postgres_insertion_rolls_back_scope_and_intent(journal_pg):
    journal, engine, _ = journal_pg
    with pytest.raises(JournalStorageError) as failure:
        create(journal, run_id="missing-run")
    assert ACCOUNT.account_id not in str(failure.value)
    assert count(engine, AccountRow) == count(engine, IntentRow) == 0
    assert create(journal).id


def fail_insert(engine, table):
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION reject_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected insert failure'; END;
            $$
            """)
        connection.exec_driver_sql(
            f"CREATE TRIGGER reject_insert AFTER INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_test_insert()"
        )


def test_postgres_claim_and_hold_rollback_together(journal_pg):
    journal, engine, _ = journal_pg
    intent = create(journal)
    fail_insert(engine, "execution_claims")
    with pytest.raises(JournalStorageError):
        journal.claim_dispatch(ACCOUNT, intent.id)
    assert journal.account_hold(ACCOUNT) is None
    assert journal.read_intent(ACCOUNT, intent.id)[1] == ()
    assert count(engine, ClaimRow) == count(engine, EvidenceRow) == 0


def test_postgres_failed_evidence_insert_preserves_existing_hold(journal_pg):
    journal, engine, _ = journal_pg
    intent = create(journal)
    claim = journal.claim_dispatch(ACCOUNT, intent.id)
    _, original = journal.read_intent(ACCOUNT, intent.id)
    fail_insert(engine, "execution_evidence")
    with pytest.raises(JournalStorageError):
        journal.append_evidence(ACCOUNT, intent.id, evidence())
    assert journal.read_intent(ACCOUNT, intent.id)[1] == original
    assert journal.recover_unresolved(ACCOUNT) == (claim,)


@pytest.mark.parametrize(
    "competition", ["same-intent", "same-account", "separate-accounts"]
)
def test_postgres_concurrent_claims(journal_pg, competition):
    journal, engine, _ = journal_pg
    first = create(journal)
    second = (
        first
        if competition == "same-intent"
        else create(
            journal,
            key="second",
            scope=OTHER if competition == "separate-accounts" else ACCOUNT,
        )
    )
    ready = Barrier(2, timeout=5)
    inserted = Barrier(2, timeout=5)

    if competition == "separate-accounts":

        @event.listens_for(engine, "after_cursor_execute")
        def require_overlapping_inserts(_conn, _cursor, statement, *_rest):
            if statement.startswith("INSERT INTO execution_claims"):
                inserted.wait()

    @event.listens_for(engine, "begin")
    def bound_locks(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")

    def claim(intent):
        ready.wait()
        try:
            return ExecutionJournal(engine).claim_dispatch(intent.scope, intent.id)
        except DispatchHeld:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, intent) for intent in (first, second)]
        results = [future.result(timeout=10) for future in futures]
    claims = [item for item in results if item is not None]
    assert len(claims) == (2 if competition == "separate-accounts" else 1)
    assert count(engine, ClaimRow) == count(engine, EvidenceRow) == len(claims)
    for claim_row in claims:
        assert journal.account_hold(claim_row.scope) == claim_row


def test_postgres_restart_and_evidence_do_not_clear_hold(journal_pg):
    journal, engine, _ = journal_pg
    intent = create(journal)
    baseline = journal.append_evidence(ACCOUNT, intent.id, evidence())
    claim = journal.claim_dispatch(ACCOUNT, intent.id, baseline_id=baseline.event.id)
    reconnected = create_engine(engine.url)
    try:
        reopened = ExecutionJournal(reconnected)
        assert reopened.recover_unresolved(ACCOUNT) == (claim,)
        assert claim.baseline_id == baseline.event.id
        for kind, reason in (
            ("CONFIRMATION", "NOT_FOUND"),
            ("CONFIRMATION", "ACCEPTED"),
            ("CONFIRMATION", "CONFLICTING_CONFIRMATION"),
            ("EXPOSURE", "EMPTY"),
            ("ADMIN_REVIEW", "ADMINISTRATIVELY_UNRESOLVED"),
        ):
            reopened.append_evidence(
                ACCOUNT,
                intent.id,
                evidence(
                    kind,
                    reason=reason,
                    actor_id="operator-approval" if kind == "ADMIN_REVIEW" else None,
                ),
            )
        assert reopened.account_hold(ACCOUNT) == claim
        assert all(
            row.event.kind != "ACKNOWLEDGEMENT"
            for row in reopened.read_intent(ACCOUNT, intent.id)[1]
        )
        with pytest.raises(DispatchHeld):
            reopened.claim_dispatch(ACCOUNT, intent.id)
    finally:
        reconnected.dispose()


def test_postgres_evidence_replay_conflict_and_exact_decimal(journal_pg):
    journal, engine, _ = journal_pg
    intent = create(journal)
    item = evidence(
        broker_id="Abc", broker_role="POSITION", quantity=QUANTITY, price=PRICE
    )
    stored = journal.append_evidence(ACCOUNT, intent.id, item)
    assert journal.append_evidence(ACCOUNT, intent.id, item) == stored
    for changed in (
        replace(item, id=uuid4()),
        replace(item, deduplication_key="new-key"),
        replace(item, price=Decimal("101.000000000000000002")),
    ):
        with pytest.raises(JournalConflict, match="evidence identity"):
            journal.append_evidence(ACCOUNT, intent.id, changed)
    with engine.connect() as connection:
        assert tuple(
            connection.execute(
                text(
                    "SELECT quantity, price, pg_typeof(price)::text "
                    "FROM execution_evidence"
                )
            ).one()
        ) == (QUANTITY, PRICE, "numeric")
        assert connection.scalar(select(IntentRow.level)) == PRICE
    assert journal.read_intent(ACCOUNT, intent.id)[1] == (stored,)


def db_event(scope_id, intent_id, *, event_id=None, kind="DISPATCH_CLAIM"):
    return insert(EvidenceRow).values(
        id=event_id or uuid4(),
        scope_id=scope_id,
        intent_id=intent_id,
        deduplication_key=f"sql:{uuid4()}",
        kind=kind,
        reason="TEST",
        observed_at=NOW,
        recorded_at=NOW,
    )


def test_postgres_database_scope_and_claim_constraints(journal_pg):
    journal, engine, _ = journal_pg
    first, other = create(journal), create(journal, scope=OTHER)
    with engine.connect() as connection:
        first_scope = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == first.id)
        )
        other_scope = connection.scalar(
            select(IntentRow.scope_id).where(IntentRow.id == other.id)
        )
    with pytest.raises(LookupError):
        journal.claim_dispatch(OTHER, first.id)
    with pytest.raises(LookupError):
        journal.append_evidence(OTHER, first.id, evidence())
    with pytest.raises(ValueError, match="demo"):
        journal.account_hold(replace(ACCOUNT, environment="live"))

    with pytest.raises(IntegrityError) as foreign, engine.begin() as connection:
        connection.execute(db_event(other_scope, first.id))
    assert foreign.value.orig.sqlstate == "23503"

    with pytest.raises(IntegrityError) as wrong_kind, engine.begin() as connection:
        event_id = uuid4()
        connection.execute(
            db_event(first_scope, first.id, event_id=event_id, kind="EXPOSURE")
        )
        connection.execute(
            insert(ClaimRow).values(
                id=event_id, scope_id=first_scope, intent_id=first.id, claimed_at=NOW
            )
        )
    assert wrong_kind.value.orig.sqlstate == "23503"

    first_claim = journal.claim_dispatch(ACCOUNT, first.id)
    second = create(journal, key="second")
    with pytest.raises(IntegrityError) as duplicate, engine.begin() as connection:
        event_id = uuid4()
        connection.execute(db_event(first_scope, second.id, event_id=event_id))
        connection.execute(
            insert(ClaimRow).values(
                id=event_id, scope_id=first_scope, intent_id=second.id, claimed_at=NOW
            )
        )
    assert duplicate.value.orig.sqlstate == "23505"
    assert count(engine, EvidenceRow) == 1
    assert journal.account_hold(ACCOUNT) == first_claim


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE execution_intents SET idempotency_key = 'changed'",
        "DELETE FROM execution_claims",
        "UPDATE execution_evidence SET reason = 'changed'",
        "DELETE FROM execution_evidence",
        "TRUNCATE execution_accounts CASCADE",
    ],
)
def test_postgres_database_guards_immutable_rows(journal_pg, statement):
    journal, engine, _ = journal_pg
    intent = create(journal)
    claim = journal.claim_dispatch(ACCOUNT, intent.id)
    with pytest.raises(DBAPIError) as denied, engine.begin() as connection:
        connection.exec_driver_sql(statement)
    assert denied.value.orig.sqlstate == "55000"
    assert journal.account_hold(ACCOUNT) == claim


def test_postgres_migration_preserves_prior_records_and_refuses_nonempty_downgrade(
    pg_db,  # noqa: F811
):
    engine, config = pg_db
    command.upgrade(config, OLD_HEAD)
    operational = JournalRepository(engine)
    run = operational.create_run(
        run_date=NOW.date(),
        mode="fixture",
        config_hash="config",
        bundle_hash="bundle",
        code_version="version",
        config_snapshot={"preserved": True},
    )
    market = MarketDataRepository(engine)
    market.register_mapping(MAPPING, provenance="fixture")
    record(market, "before-upgrade")
    old_tables = set(inspect(engine).get_table_names())
    assert not any(table.startswith("execution_") for table in old_tables)
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, OLD_HEAD)
    assert set(inspect(engine).get_table_names()) == old_tables
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(select(Run.id)) == run.run_id
        assert connection.scalar(select(ConfigSnapshot.snapshot)) == {"preserved": True}
    assert history(market).revisions[0].source.retrieval_id == "before-upgrade"
    journal = ExecutionJournal(engine)
    intent = create(journal, run_id=run.run_id)
    claim = journal.claim_dispatch(ACCOUNT, intent.id)
    with pytest.raises(RuntimeError, match="empty execution"):
        command.downgrade(config, OLD_HEAD)
    assert journal.recover_unresolved(ACCOUNT) == (claim,)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0005_execution_journal"
        )


def test_postgres_unique_intent_and_evidence_keys_even_without_repository(journal_pg):
    journal, engine, _ = journal_pg
    intent = create(journal, request=replace(REQUEST, request_reference="Ref-DB"))
    item = journal.append_evidence(ACCOUNT, intent.id, evidence()).event
    with engine.connect() as connection:
        data = dict(connection.execute(select(IntentRow.__table__)).mappings().one())
        event_row = dict(
            connection.execute(select(EvidenceRow.__table__)).mappings().one()
        )
    for patch in ({"request_reference": None}, {"idempotency_key": "other"}):
        with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
            connection.execute(
                insert(IntentRow).values({**data, **patch, "id": uuid4()})
            )
        assert denied.value.orig.sqlstate == "23505"
    for patch in ({"id": uuid4()}, {"deduplication_key": "db-different-source"}):
        with pytest.raises(IntegrityError) as denied, engine.begin() as connection:
            connection.execute(insert(EvidenceRow).values({**event_row, **patch}))
        assert denied.value.orig.sqlstate == "23505"
    other = create(journal, scope=OTHER)
    with pytest.raises(JournalConflict, match="uniqueness"):
        journal.append_evidence(OTHER, other.id, item)
