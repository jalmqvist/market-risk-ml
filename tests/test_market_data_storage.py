from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from mrml.data.canonical import (
    HOUR,
    Availability,
    Completion,
    CompletionState,
    FXInstrument,
    InstrumentMapping,
    SourceProduct,
    TimestampMeaning,
    TimestampPolicy,
)
from mrml.data.ig import RESOLUTION, IGAllowance, IGObservation, IGPriceSample, IGQuote
from mrml.data.ig_adapter import TRANSFORMATION_VERSION
from mrml.data.provider import InsufficientHistoryError
from mrml.data.storage import (
    ApprovedTimestampPolicy,
    MarketDataRepository,
    PersistenceConflict,
    StoredHistory,
)
from mrml.data.storage_models import (
    InterpretationRow,
    MarketDataBase,
    ObservationRow,
    PolicyRow,
    RetrievalRow,
)
from mrml.journal.models import ConfigSnapshot, Run

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 20, 12, tzinfo=UTC)
RETRIEVED = START + timedelta(minutes=25, microseconds=124531)
PRODUCT = SourceProduct("IG", "CS.D.TEST.MINI.IP", "mini")
MAPPING = InstrumentMapping(FXInstrument("EUR_USD"), PRODUCT, "fx/v1")
START_POLICY = ApprovedTimestampPolicy(
    MAPPING,
    TimestampPolicy(
        "synthetic-start/v1",
        TimestampMeaning.INTERVAL_START,
        "docs/audit/IG_H1_TIMESTAMP_SEMANTICS.md (synthetic example)",
    ),
    "synthetic fixture approval",
)
END_POLICY = ApprovedTimestampPolicy(
    MAPPING,
    TimestampPolicy("synthetic-end/v1", TimestampMeaning.INTERVAL_END, "test evidence"),
    "synthetic fixture approval",
)


def observation(label: datetime = START, close: float = 10998.1) -> IGObservation:
    return IGObservation(
        label,
        IGQuote(10995.123456789013, 10996.123456789013),
        IGQuote(11010.0, 11011.0),
        IGQuote(10990.0, 10991.0),
        IGQuote(close, close + 1.0),
        raw_timestamp=label.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def sample(*rows: IGObservation) -> IGPriceSample:
    return IGPriceSample(
        PRODUCT.instrument_id,
        RESOLUTION,
        rows or (observation(),),
        IGAllowance(999, 1000, 600),
        0,
    )


def record(
    repo: MarketDataRepository,
    retrieval_id: str,
    response: IGPriceSample | None = None,
    *,
    retrieved_at: datetime = RETRIEVED,
    approval: ApprovedTimestampPolicy | None = START_POLICY,
    interpreted_at: datetime | None = None,
) -> None:
    repo.record_ig_sample(
        PRODUCT,
        response or sample(),
        retrieval_id=retrieval_id,
        retrieved_at=retrieved_at,
        approval=approval,
        interpreted_at=(interpreted_at or retrieved_at) if approval else None,
    )


def history(
    repo: MarketDataRepository,
    *,
    as_of: datetime | None = None,
    approval: ApprovedTimestampPolicy = START_POLICY,
    version: str = TRANSFORMATION_VERSION,
) -> StoredHistory:
    return repo.canonical_history(
        approval.mapping,
        policy_name=approval.policy.name,
        transformation_version=version,
        start=START - HOUR,
        end=START + 2 * HOUR,
        as_of=as_of,
    )


@pytest.fixture
def sqlite_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'market.sqlite'}")
    MarketDataBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def repo(sqlite_db: Engine) -> MarketDataRepository:
    repository = MarketDataRepository(sqlite_db)
    repository.register_mapping(MAPPING, provenance="synthetic mapping manifest")
    return repository


def test_identity_and_immutable_mapping_versions(repo: MarketDataRepository) -> None:
    product_variants = tuple(
        replace(PRODUCT, variant=variant)
        for variant in ("standard", "mini", "weekend", "forward")
    )
    for product in product_variants:
        mapping = replace(MAPPING, product=product)
        repo.register_mapping(mapping, provenance="synthetic mapping manifest")
        assert repo.resolve_mapping(product, mapping.version) == mapping
    second = replace(MAPPING, instrument=FXInstrument("GBP_USD"), version="fx/v2")
    repo.register_mapping(second, provenance="independently approved fixture")
    assert repo.resolve_mapping(PRODUCT, "fx/v1") == MAPPING
    assert repo.resolve_mapping(PRODUCT, "fx/v2") == second
    with pytest.raises(PersistenceConflict):
        repo.register_mapping(replace(second, version="fx/v1"), provenance="changed")
    record(repo, "mini-only")
    for product in product_variants:
        rows = repo.source_revisions(product, RESOLUTION, START, START + HOUR)
        assert len(rows) == int(product == PRODUCT)
    with pytest.raises(ValueError, match="registered mapping"):
        record(
            repo,
            "misassigned",
            approval=replace(
                START_POLICY, mapping=replace(MAPPING, instrument=second.instrument)
            ),
        )


def test_retrieval_revisions_round_trip_and_as_of(
    repo: MarketDataRepository, sqlite_db: Engine
) -> None:
    record(repo, "initial")
    record(repo, "initial")
    record(repo, "tie-z", sample(observation(close=10993.4)))
    record(repo, "tie-a", sample(observation(close=10994.4)))
    later = RETRIEVED + HOUR
    record(repo, "unchanged", sample(observation(close=10993.4)), retrieved_at=later)
    with pytest.raises(PersistenceConflict, match="Retrieval ID"):
        record(repo, "initial", sample(observation(close=10992.4)))

    sqlite_db.dispose()
    reopened = MarketDataRepository(sqlite_db)
    revisions = reopened.source_revisions(PRODUCT, RESOLUTION, START, START + HOUR)
    assert tuple(row.retrieval_id for row in revisions) == (
        "initial",
        "tie-a",
        "tie-z",
        "unchanged",
    )
    first = revisions[0]
    assert first.bid == tuple(
        map(
            Decimal,
            (
                "10995.123456789013",
                "11010.0",
                "10990.0",
                "10998.1",
            ),
        )
    )
    assert first.ask[-1] == Decimal("10999.1")
    assert first.bid != revisions[2].bid
    assert revisions[2].fingerprint == revisions[3].fingerprint
    assert first.fingerprint != revisions[2].fingerprint
    assert first.raw_timestamp == "2026-11-20T12:00:00"
    assert first.timestamp_field == "snapshotTimeUTC"
    assert first.source_timestamp == START and first.source_timestamp.tzinfo is UTC
    assert first.retrieved_at == RETRIEVED and first.retrieved_at.tzinfo is UTC
    assert first.response_metadata == {
        "remaining": 999,
        "total": 1000,
        "reset_seconds": 600,
    }
    assert first.volume is None
    assert (
        reopened.source_revisions(
            PRODUCT, RESOLUTION, START, START + HOUR, as_of=RETRIEVED
        )
        == revisions[:3]
    )
    [winner] = history(reopened, as_of=RETRIEVED).require_observations()
    assert winner.source == revisions[2]
    assert winner.bar.bid.close == 10993.4
    assert winner.bar.completion == Completion()
    assert winner.bar.availability == Availability()
    assert winner.bar.volume is None
    assert winner.mapping_provenance == "synthetic mapping manifest"
    assert winner.policy_approval == START_POLICY.approval
    assert history(reopened).revisions[0].source == revisions[3]
    assert (
        history(reopened).revisions[0].bar.completion.state is CompletionState.UNKNOWN
    )
    missing = history(reopened, as_of=RETRIEVED - timedelta(microseconds=1))
    assert missing.revisions == ()
    assert not missing.coverage_verified
    assert not history(reopened).coverage_verified
    with pytest.raises(InsufficientHistoryError):
        missing.require_observations()


def test_explicit_policy_mapping_and_transformation_versions(
    repo: MarketDataRepository,
) -> None:
    record(repo, "event", approval=None)
    assert not history(repo).revisions
    record(repo, "event")
    record(repo, "event", approval=END_POLICY)
    v2 = "synthetic-replay/v2"
    repo.record_ig_sample(
        PRODUCT,
        sample(),
        retrieval_id="event",
        retrieved_at=RETRIEVED,
        approval=START_POLICY,
        transformation_version=v2,
        completion={
            START: Completion(CompletionState.INCOMPLETE, "fixture completion")
        },
        availability={START: Availability(START, "fixture availability")},
    )
    second_mapping = replace(MAPPING, version="fx/v2")
    repo.register_mapping(second_mapping, provenance="fixture mapping v2")
    second_approval = replace(START_POLICY, mapping=second_mapping)
    record(repo, "event", approval=second_approval)
    start = history(repo).revisions[0]
    end = history(repo, approval=END_POLICY).revisions[0]
    alternate = history(repo, version=v2).revisions[0]
    assert start.bar.start == end.bar.end == START
    assert start.bar.end == START + HOUR
    assert end.bar.start == START - HOUR
    assert start.source == end.source == alternate.source
    assert start.bar.source_time.policy == START_POLICY.policy
    assert end.bar.source_time.policy == END_POLICY.policy
    assert alternate.bar.transformation_version == v2
    assert alternate.bar.completion.evidence == "fixture completion"
    assert alternate.bar.availability.first_available_at == START
    assert alternate.bar.availability.evidence == "fixture availability"
    assert (
        history(repo, approval=second_approval).revisions[0].bar.mapping
        == second_mapping
    )
    assert history(repo).revisions[0].bar.mapping == MAPPING
    with pytest.raises(PersistenceConflict):
        record(
            repo, "event", approval=replace(START_POLICY, approval="changed approval")
        )
    with pytest.raises(PersistenceConflict):
        repo.record_ig_sample(
            PRODUCT,
            sample(),
            retrieval_id="event",
            retrieved_at=RETRIEVED,
            approval=START_POLICY,
            transformation_version=v2,
        )


def test_as_of_excludes_late_interpretation(repo: MarketDataRepository) -> None:
    record(repo, "event", approval=None)
    interpretation_time = RETRIEVED + 2 * HOUR
    record(repo, "event", interpreted_at=interpretation_time)
    assert repo.source_revisions(
        PRODUCT, RESOLUTION, START, START + HOUR, as_of=RETRIEVED
    )
    assert not history(repo, as_of=RETRIEVED).revisions
    [known] = history(repo, as_of=interpretation_time).revisions
    assert known.interpreted_at == interpretation_time

    # With no supplied time, storage sets it once and retries preserve it.
    for _ in range(2):
        repo.record_ig_sample(
            PRODUCT,
            sample(),
            retrieval_id="default-clock",
            retrieved_at=RETRIEVED,
            approval=END_POLICY,
        )
    assert history(repo, approval=END_POLICY).revisions[0].interpreted_at.tzinfo is UTC


def test_duplicate_labels_within_retrieval_have_stable_ordinals(
    repo: MarketDataRepository,
) -> None:
    record(repo, "duplicates", sample(observation(), observation(close=10995.0)))
    revisions = repo.source_revisions(PRODUCT, RESOLUTION, START, START + HOUR)
    assert [row.ordinal for row in revisions] == [0, 1]
    [winner] = history(repo).revisions
    assert winner.source == revisions[1]


def test_batch_rollback_after_database_failure(
    repo: MarketDataRepository, sqlite_db: Engine
) -> None:
    # Abort the second interpretation after the source batch and first
    # interpretation have already been inserted in the same transaction.
    with sqlite_db.begin() as connection:
        connection.execute(
            text("""
            CREATE TRIGGER fail_second_interpretation
            BEFORE INSERT ON market_data_h1_interpretations
            WHEN NEW.ordinal = 1
            BEGIN
              SELECT RAISE(ABORT, 'second interpretation rejected');
            END
        """)
        )
    response = sample(observation(START - HOUR), observation())
    with pytest.raises(IntegrityError, match="second interpretation rejected"):
        record(repo, "rolled-back", response)
    with sqlite_db.connect() as connection:
        for model in (RetrievalRow, ObservationRow, PolicyRow, InterpretationRow):
            assert connection.scalar(select(func.count()).select_from(model)) == 0
    with sqlite_db.begin() as connection:
        connection.execute(text("DROP TRIGGER fail_second_interpretation"))
    record(repo, "rolled-back", response)
    assert len(history(repo).revisions) == 2


def test_invalid_transformation_and_utc_leave_no_retrieval(
    repo: MarketDataRepository, sqlite_db: Engine
) -> None:
    response = sample(observation(START + timedelta(minutes=1)))
    with pytest.raises(ValueError, match="align"):
        record(repo, "unaligned", response)
    with pytest.raises(ValueError, match="UTC-aware"):
        record(repo, "naive", retrieved_at=RETRIEVED.replace(tzinfo=None))
    with sqlite_db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RetrievalRow)) == 0
    record(repo, "unaligned", response, approval=None)
    assert len(repo.source_revisions(PRODUCT, RESOLUTION, START, START + HOUR)) == 1


def test_canonical_import_is_independent_of_sqlalchemy() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
sys.modules["sqlalchemy"] = None
from mrml.data.canonical import SourceBar
assert SourceBar.__name__ == "SourceBar"
""",
        ],
        check=True,
    )


@pytest.fixture
def pg_db(monkeypatch: pytest.MonkeyPatch):
    raw_url = os.environ.get("MRML_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("set MRML_TEST_POSTGRES_URL for PostgreSQL integration tests")
    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("MRML_TEST_POSTGRES_URL must use postgresql+psycopg")
    schema = f"mrml_test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict(
        {
            "options": f"-c search_path={schema} -c timezone=America/New_York",
        }
    )
    engine = create_engine(scoped_url)
    monkeypatch.setenv(
        "MRML_DATABASE_URL", scoped_url.render_as_string(hide_password=False)
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_migration_roundtrip_and_constraints(pg_db) -> None:
    engine, config = pg_db
    command.upgrade(config, "0001_operational_journal")
    with engine.begin() as connection:
        connection.execute(
            insert(Run.__table__).values(
                id="journal-fixture",
                run_date=START.date(),
                mode="fixture",
                config_hash="config",
                bundle_hash="bundle",
                code_version="version",
                status="started",
                created_at=RETRIEVED,
            )
        )
        connection.execute(
            insert(ConfigSnapshot.__table__).values(
                id="snapshot-fixture",
                run_id="journal-fixture",
                config_hash="config",
                snapshot={"retained": True},
                created_at=RETRIEVED,
            )
        )
    journal_tables = set(inspect(engine).get_table_names())
    assert not any(name.startswith("market_data_") for name in journal_tables)
    command.upgrade(config, "head")
    command.check(config)

    repo = MarketDataRepository(engine)
    repo.register_mapping(MAPPING, provenance="fixture")
    record(repo, "first")
    record(
        repo, "later", sample(observation(close=10993.4)), retrieved_at=RETRIEVED + HOUR
    )
    with engine.connect() as connection:
        price, data_type = connection.execute(
            text("""
            SELECT bid_open, pg_typeof(bid_open)::text
            FROM market_data_observations WHERE retrieval_id = 'first'
        """)
        ).one()
        assert price == Decimal("10995.123456789013") and data_type == "numeric"
        local_time, time_type = connection.execute(
            text("""
            SELECT retrieved_at, pg_typeof(retrieved_at)::text
            FROM market_data_retrievals WHERE id = 'first'
        """)
        ).one()
        assert local_time.utcoffset() == -timedelta(hours=5)
        assert time_type == "timestamp with time zone"

    with pytest.raises(IntegrityError) as conflict, engine.begin() as connection:
        row = (
            connection.execute(
                select(RetrievalRow.__table__).where(RetrievalRow.id == "first")
            )
            .mappings()
            .one()
        )
        connection.execute(insert(RetrievalRow.__table__).values(**row))
    assert conflict.value.orig.sqlstate == "23505"

    engine.dispose()
    reopened = MarketDataRepository(engine)
    [past] = history(reopened, as_of=RETRIEVED).revisions
    assert past.source.retrieved_at == RETRIEVED
    assert past.source.retrieved_at.tzinfo is UTC
    assert past.bar.start == START and past.bar.start.tzinfo is UTC
    assert past.source.bid[0] == price
    assert past.source.retrieval_id == "first"
    assert history(reopened).revisions[0].source.retrieval_id == "later"
    command.downgrade(config, "0001_operational_journal")
    assert set(inspect(engine).get_table_names()) == journal_tables
    with engine.connect() as connection:
        assert connection.scalar(select(Run.id)) == "journal-fixture"
        assert connection.scalar(select(ConfigSnapshot.snapshot)) == {"retained": True}


def test_postgres_concurrent_idempotency(pg_db) -> None:
    engine, config = pg_db
    command.upgrade(config, "head")
    repo = MarketDataRepository(engine)
    repo.register_mapping(MAPPING, provenance="fixture")
    barrier = Barrier(6)

    def attempt() -> None:
        barrier.wait(timeout=10)
        record(MarketDataRepository(engine), "shared-retrieval")

    with ThreadPoolExecutor(max_workers=6) as workers:
        futures = [workers.submit(attempt) for _ in range(6)]
        for future in futures:
            future.result(timeout=30)

    with engine.connect() as connection:
        for model in (RetrievalRow, ObservationRow, PolicyRow, InterpretationRow):
            assert connection.scalar(select(func.count()).select_from(model)) == 1
    with pytest.raises(PersistenceConflict):
        record(repo, "shared-retrieval", sample(observation(close=10992.4)))
    [winner] = history(repo).revisions
    assert winner.source.retrieval_id == "shared-retrieval"
