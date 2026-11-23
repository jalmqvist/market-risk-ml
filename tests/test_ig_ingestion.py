from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
import test_market_data_storage
from alembic import command
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_market_data_storage import (  # noqa: F401
    MAPPING,
    PRODUCT,
    RETRIEVED,
    START,
    START_POLICY,
    history,
    observation,
    sample,
)

from mrml.data.canonical import HOUR, CompletionState
from mrml.data.history import GapKind, SessionEvidence
from mrml.data.ig import (
    IGAllowance,
    IGAuthenticationError,
    IGError,
    IGMarketError,
    IGQuotaError,
    IGSessionError,
)
from mrml.data.ig_ingest import main
from mrml.data.ig_ingestion import (
    IGIngestion,
    IngestionBudget,
    IngestionRefused,
    SessionPolicy,
)
from mrml.data.storage import MarketDataRepository
from mrml.data.storage_models import IGIngestionAttemptRow, ObservationRow, RetrievalRow

Attempt = IGIngestionAttemptRow
pg_db = test_market_data_storage.pg_db
sqlite_db = test_market_data_storage.sqlite_db


class Clock:
    now = RETRIEVED

    def __call__(self) -> datetime:
        return self.now


def forbid_fetch(*args):
    pytest.fail("Unexpected broker request")


def source_only(db, clock=None, **options):
    return IGIngestion(db, PRODUCT, clock=clock or Clock(), **options)


def approve(db):
    repo = MarketDataRepository(db)
    approval = replace(
        START_POLICY, policy=replace(START_POLICY.policy, evidence="test")
    )
    repo.register_mapping(MAPPING, provenance="fixture")
    repo.register_timestamp_policy(approval)
    return approval


def rows(db):
    with db.connect() as connection:
        return list(connection.execute(select(Attempt.__table__)).mappings())


def test_plan_initial_clock_boundary_and_catchup(sqlite_db):
    clock = Clock()
    ingestion = source_only(sqlite_db, clock)
    plan = ingestion.plan()
    assert plan.request is not None
    assert plan.request.start == START - 3 * HOUR
    assert plan.request.end == START + HOUR
    assert plan.request.request_end == RETRIEVED.replace(microsecond=0)
    assert plan.request.point_limit == 4
    assert plan.quota_remaining is None
    start = START - 20 * HOUR
    request = ingestion.plan(start=start).request
    assert request is not None
    assert (request.start, request.end, request.point_limit) == (
        start,
        start + 6 * HOUR,
        7,
    )
    with pytest.raises(IngestionRefused, match="horizon"):
        ingestion.plan(start=START - 48 * HOUR)
    assert rows(sqlite_db) == []
    clock.now = START
    boundary = ingestion.plan(start=START)
    assert boundary.request is None
    clock.now = START + timedelta(seconds=1)
    assert ingestion.plan(start=START).request.point_limit == 1


def test_skip_stored_labels_and_attempted_weekend_gap(sqlite_db):
    ingestion = source_only(sqlite_db)
    ingestion.repo.record_ig_sample(
        PRODUCT,
        sample(observation(START - HOUR), observation(START)),
        retrieval_id="baseline",
        retrieved_at=RETRIEVED,
    )
    clock = Clock()
    clock.now = datetime(2026, 11, 21, 4, 5, tzinfo=UTC)  # Saturday
    ingestion = source_only(sqlite_db, clock)
    first = START - 3 * HOUR
    request = ingestion.plan(start=first).request
    assert request.end == START - HOUR
    assert request.request_end == request.end - timedelta(seconds=1)
    _, empty = ingestion.run(
        lambda *args: replace(sample(), observations=(), allowance=None), start=first
    )
    assert empty.outcome["observation_count"] == 0
    assert empty.outcome["coverage_verified"] is False
    next_request = ingestion.plan(start=first).request
    assert next_request.start == START + HOUR
    assert all(gap["kind"] == GapKind.UNKNOWN for gap in ingestion.status()["gaps"])


def test_known_reserve_and_uncertain_debit(sqlite_db):
    ingestion = source_only(sqlite_db)
    ingestion.repo.record_ig_sample(
        PRODUCT,
        replace(sample(), observations=(), allowance=IGAllowance(25, 100, 3600)),
        retrieval_id="allowance",
        retrieved_at=RETRIEVED - timedelta(seconds=1),
    )
    assert ingestion.plan().quota_remaining == 25

    def transport(*args):
        raise IGError("IG transport request failed")

    with pytest.raises(IGError):
        ingestion.run(transport)
    [row] = rows(sqlite_db)
    assert row["state"] == "requesting"
    retry = ingestion.plan(retry_job=row["job_id"])
    assert retry.request is None
    assert retry.refused and retry.quota_remaining == 21
    assert "reserve" in retry.message


def test_historical_exhaustion_and_throttle_hold(sqlite_db):
    clock = Clock()
    ingestion = source_only(sqlite_db, clock)
    delay = clock.now + timedelta(minutes=10)

    def throttle(*args):
        raise IGQuotaError("throttle", retry_at=delay)

    with pytest.raises(IGQuotaError):
        ingestion.run(throttle)
    assert ingestion.plan().retry_after == delay
    assert ingestion.run(forbid_fetch)[1] is None
    clock.now = delay
    assert ingestion.plan().retry_after is None

    def exhausted(*args):
        raise IGQuotaError("historical", historical=True)

    clock.now += HOUR
    with pytest.raises(IGQuotaError):
        ingestion.run(exhausted)
    assert ingestion.plan().refused
    assert not ingestion.plan(reset_confirmed=True).refused


@pytest.mark.parametrize(
    "exception", [IGAuthenticationError, IGSessionError, IGMarketError]
)
def test_failed_before_staging(sqlite_db, exception):
    ingestion = source_only(sqlite_db)

    def failure(*args):
        raise exception("broker failure")

    with pytest.raises(exception):
        ingestion.run(failure)
    [row] = rows(sqlite_db)
    assert row["state"] == "failed"
    assert not row["retrieval_id"]
    assert ingestion.status()["last_failed"]["attempt_id"] == row["id"]


def test_response_lost_before_staging_and_restart(sqlite_db, monkeypatch):
    clock = Clock()
    ingestion = source_only(sqlite_db, clock)

    def crash(*args):
        raise RuntimeError("process failed before staging")

    monkeypatch.setattr(ingestion, "_stage", crash)
    with pytest.raises(RuntimeError):
        ingestion.run(lambda *args: sample(), start=START)
    [first] = rows(sqlite_db)
    assert first["state"] == "requesting" and first["received_at"] is None
    restart = source_only(sqlite_db, clock)
    assert restart.recover() is None
    assert restart.run(forbid_fetch, start=START)[1] is None
    clock.now += timedelta(seconds=2)
    _, replacement = restart.run(lambda *args: sample(), retry_job=first["job_id"])
    assert replacement.id != first["id"]
    assert replacement.retrieval_id == replacement.id
    assert replacement.job_id == first["job_id"]
    assert replacement.attempt_number == 2
    assert rows(sqlite_db)[0]["state"] == "requesting"
    with pytest.raises(IngestionRefused, match="replacement"):
        restart.plan(retry_job=first["job_id"])


def test_existing_source_rows_avoid_replacement(sqlite_db):
    ingestion = source_only(sqlite_db)

    def uncertain(*args):
        raise IGError("transport")

    with pytest.raises(IGError):
        ingestion.run(uncertain, start=START)
    [row] = rows(sqlite_db)
    ingestion.repo.record_ig_sample(
        PRODUCT,
        sample(),
        retrieval_id="external",
        retrieved_at=RETRIEVED,
    )
    assert ingestion.run(forbid_fetch, retry_job=row["job_id"])[1] is None


def test_partially_satisfied_replacement_narrows_broker_request(sqlite_db):
    ingestion = source_only(sqlite_db)

    def uncertain(*args):
        raise IGError("transport")

    start = START - 3 * HOUR
    with pytest.raises(IGError):
        ingestion.run(uncertain, start=start)
    [row] = rows(sqlite_db)
    ingestion.repo.record_ig_sample(
        PRODUCT,
        sample(observation(start), observation(start + 2 * HOUR)),
        retrieval_id="external",
        retrieved_at=RETRIEVED,
    )
    retry = ingestion.plan(retry_job=row["job_id"]).request
    assert retry.start == start + HOUR
    assert retry.end == start + 2 * HOUR
    assert retry.point_limit == 1


def test_ingestion_rejects_out_of_range_injected_observation(sqlite_db):
    ingestion = source_only(sqlite_db)
    with pytest.raises(IGMarketError, match="window or budget"):
        ingestion.run(lambda *args: sample(observation(START + HOUR)), start=START)
    [row] = rows(sqlite_db)
    assert row["state"] == "failed" and row["retrieval_id"] is None


def verify_stage_recovery(db, monkeypatch):
    clock = Clock()
    approval = approve(db)
    ingestion = source_only(db, clock, approval=approval)
    writer = ingestion.repo.record_ig_sample

    def fail_after_inserts(*args, **kwargs):
        writer(*args, **kwargs)
        raise RuntimeError("commit interrupted")

    monkeypatch.setattr(ingestion.repo, "record_ig_sample", fail_after_inserts)
    with pytest.raises(RuntimeError, match="interrupted"):
        ingestion.run(lambda *args: sample(), start=START)
    [staged] = rows(db)
    assert staged["state"] == "staged"
    assert staged["retrieval_id"] is None
    assert staged["received_at"] == RETRIEVED
    assert staged["received_at"].tzinfo is UTC
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ObservationRow)) == 0
        assert connection.scalar(select(func.count()).select_from(RetrievalRow)) == 0
    clock.now += timedelta(minutes=15)
    # No operator approval or credentials are needed to replay immutable staged data.
    restart = source_only(db, clock)
    committed = restart.recover()
    assert committed.id == staged["id"]
    assert committed.retrieval_id == staged["id"]
    assert committed.staged_response is None
    assert restart.recover() is None
    assert restart._commit(staged["id"]).committed_at == committed.committed_at
    assert not history(restart.repo, as_of=RETRIEVED, approval=approval).revisions
    assert not history(
        restart.repo, as_of=RETRIEVED - timedelta(seconds=1), approval=approval
    ).revisions
    assert not restart.repo.source_revisions(
        PRODUCT,
        "HOUR",
        START,
        START + HOUR,
        as_of=RETRIEVED - timedelta(seconds=1),
    )
    [canonical] = history(restart.repo, as_of=clock.now, approval=approval).revisions
    assert canonical.source.retrieved_at == RETRIEVED
    assert canonical.interpreted_at == clock.now
    assert canonical.bar.completion.state is CompletionState.UNKNOWN
    assert restart.status()["verified_contiguous_end"] is None
    return committed.id


def test_rollback_duplicate_write_and_restart(sqlite_db, monkeypatch):
    verify_stage_recovery(sqlite_db, monkeypatch)


@pytest.mark.parametrize("changed", [False, True])
def test_controlled_provisional_recheck_preserves_revisions(sqlite_db, changed):
    clock = Clock()
    approval = approve(sqlite_db)
    ingestion = source_only(sqlite_db, clock, approval=approval)
    _, initial = ingestion.run(lambda *args: sample(), start=START)
    assert ingestion.status()["recheck_candidates"][0]["due"] is False
    assert ingestion.plan(recheck=True).request is None
    clock.now = START + HOUR
    plan, recheck = ingestion.run(
        lambda *args: sample(observation(close=10999.1 if changed else 10998.1)),
        recheck=True,
    )
    assert plan.request.point_limit == 1 and recheck.id != initial.id
    assert recheck.outcome["recheck_result"] == ("changed" if changed else "unchanged")
    assert ingestion.plan(recheck=True).request is None
    revisions = ingestion.repo.source_revisions(PRODUCT, "HOUR", START, START + HOUR)
    assert len(revisions) == 2
    assert revisions[0].retrieved_at < revisions[1].retrieved_at
    canonical = history(ingestion.repo, approval=approval)
    assert not canonical.coverage_verified
    assert canonical.revisions[0].bar.completion.state is CompletionState.UNKNOWN


def test_exact_session_evidence_and_scoped_checkpoint(sqlite_db):
    approval = approve(sqlite_db)
    calls = []
    closed = START - 3 * HOUR

    def session(product, start, end):
        calls.append((product, start, end))
        if start == closed:
            return SessionEvidence(GapKind.EXPECTED_CLOSURE, "exact closure record")
        if start == closed + HOUR:
            return SessionEvidence(
                GapKind.MISSING_EXPECTED_TRADING, "exact trading record"
            )
        return None

    ingestion = source_only(
        sqlite_db, approval=approval, sessions=SessionPolicy(PRODUCT, "v1", session)
    )
    ingestion.run(lambda *args: replace(sample(), observations=()), start=closed)
    result = ingestion.status()
    assert calls[0] == (PRODUCT, closed, closed + HOUR)
    assert result["verified_contiguous_start"] == closed
    assert result["verified_contiguous_end"] == closed + HOUR
    assert [gap["kind"] for gap in result["gaps"]] == [
        GapKind.EXPECTED_CLOSURE,
        GapKind.MISSING_EXPECTED_TRADING,
        GapKind.UNKNOWN,
        GapKind.UNKNOWN,
    ]
    different = source_only(
        sqlite_db, approval=approval, sessions=SessionPolicy(PRODUCT, "v2", session)
    )
    assert different.status()["verified_contiguous_end"] is None
    with pytest.raises(ValueError, match="exact approved"):
        source_only(
            sqlite_db,
            approval=approval,
            sessions=SessionPolicy(replace(PRODUCT, variant="other"), "v1", session),
        )


def test_unapproved_source_only_and_other_mapping_versions(sqlite_db):
    approval = approve(sqlite_db)
    ingestion = source_only(sqlite_db)
    assert "Source only" in ingestion.plan().message
    ingestion.run(lambda *args: sample(), start=START)
    assert not history(ingestion.repo, approval=approval).revisions
    with pytest.raises(IngestionRefused, match="approved policy"):
        ingestion.plan(recheck=True)
    with pytest.raises(ValueError, match="No registered"):
        source_only(
            sqlite_db,
            approval=replace(approval, mapping=replace(MAPPING, version="v2")),
        )
    with pytest.raises(ValueError, match="exact registered"):
        IGIngestion(sqlite_db, replace(PRODUCT, variant="other"), approval=approval)


def test_cli_plan_and_status_do_not_load_ig_configuration(
    sqlite_db, monkeypatch, capsys
):
    monkeypatch.setenv("MRML_DATABASE_URL", str(sqlite_db.url))
    monkeypatch.setattr("mrml.data.ig_ingest.load_probe_values", forbid_fetch)
    identity = ["--epic", PRODUCT.instrument_id, "--variant", PRODUCT.variant]
    assert main(["plan", *identity]) == 0
    assert json.loads(capsys.readouterr().out)["budget"]["max_requests"] == 1
    assert main(["status", *identity]) == 0
    assert (
        json.loads(capsys.readouterr().out)["historical_allowance"]["visibility"]
        == "unknown"
    )
    assert main(["recover", *identity]) == 0
    assert rows(sqlite_db) == []


def test_postgres_migration_recovery_and_referential_integrity(pg_db, monkeypatch):
    engine, config = pg_db
    command.upgrade(config, "0002_market_data")
    before = set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    command.check(config)
    committed = verify_stage_recovery(engine, monkeypatch)
    with pytest.raises(IntegrityError) as error, engine.begin() as connection:
        connection.execute(
            text("DELETE FROM market_data_retrievals WHERE id = :id"), {"id": committed}
        )
    assert error.value.orig.sqlstate == "23503"
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE market_data_ig_ingestion_attempts SET state = 'requesting'")
        )
    command.downgrade(config, "0002_market_data")
    assert before == set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(select(RetrievalRow.id)) == committed


def test_postgres_concurrent_attempt_and_recovery(pg_db, monkeypatch):
    engine, config = pg_db
    command.upgrade(config, "head")
    entered, release = Event(), Event()
    worker = source_only(engine)
    concurrent = source_only(engine)

    def fetch(*args):
        entered.set()
        assert release.wait(timeout=5)
        return sample()

    def interrupted(*args):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(worker, "_commit", interrupted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.run, fetch, start=START)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(IngestionRefused, match="active"):
                concurrent.run(forbid_fetch, start=START)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="interrupted"):
            first.result(timeout=5)

    [staged] = rows(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(item.recover) for item in (concurrent, source_only(engine))
        ]
        committed = []
        for result in results:
            try:
                committed.append(result.result(timeout=5))
            except IngestionRefused:
                pass
    [row] = [result for result in committed if result is not None]
    assert row.id == staged["id"]
    assert concurrent.recover() is None
    assert concurrent.run(forbid_fetch, start=START)[1] is None


def test_audited_policy_requires_exact_epic_and_start_meaning(sqlite_db):
    repo = MarketDataRepository(sqlite_db)
    evidence = "docs/audit/IG_H1_TIMESTAMP_SEMANTICS.md"
    policy = replace(START_POLICY.policy, evidence=evidence)
    unaudited = replace(START_POLICY, policy=policy)
    repo.register_mapping(MAPPING, provenance="fixture")
    repo.register_timestamp_policy(unaudited)
    with pytest.raises(ValueError, match="audit does not support"):
        source_only(sqlite_db, approval=unaudited)
    audited_product = replace(PRODUCT, instrument_id="CS.D.EURUSD.CEE.IP")
    audited_mapping = replace(MAPPING, product=audited_product)
    audited = replace(unaudited, mapping=audited_mapping)
    repo.register_mapping(audited_mapping, provenance="demo audit fixture")
    repo.register_timestamp_policy(audited)
    ingestion = IGIngestion(sqlite_db, audited_product, approval=audited, clock=Clock())
    assert ingestion.plan().message == "Canonical interpretation approved"


def test_postgres_cli_demo_and_replacement(pg_db, monkeypatch, capsys):
    engine, config = pg_db
    command.upgrade(config, "head")
    clock = Clock()

    def transport(*args):
        raise IGError("transport")

    ingestion = source_only(engine, clock)
    with pytest.raises(IGError):
        ingestion.run(transport, start=START)
    [uncertain] = rows(engine)
    clock.now += timedelta(seconds=2)

    class DemoClient:
        def __init__(self, key, username, password):
            self.authenticated = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def authenticate(self):
            self.authenticated = True

        def get_h1_range(self, epic, start, end, *, point_limit):
            assert self.authenticated
            assert (epic, start, end, point_limit) == (
                PRODUCT.instrument_id,
                START,
                RETRIEVED.replace(microsecond=0),
                1,
            )
            return sample()

    def with_test_clock(*args, **kwargs):
        return IGIngestion(*args, clock=clock, **kwargs)

    monkeypatch.setattr("mrml.data.ig_ingest.IGIngestion", with_test_clock)
    monkeypatch.setattr("mrml.data.ig_ingest.IGClient", DemoClient)
    identity = ["--epic", PRODUCT.instrument_id, "--variant", PRODUCT.variant]
    params = ["ingest", *identity, "--retry-job", uncertain["job_id"]]
    monkeypatch.setenv("MRML_IG_ENVIRONMENT", "live")
    assert main(params) == 1
    assert len(rows(engine)) == 1
    capsys.readouterr()
    for name, value in {
        "MRML_IG_ENVIRONMENT": "demo",
        "MRML_IG_API_KEY": "test-key",
        "MRML_IG_USERNAME": "test-user",
        "MRML_IG_PASSWORD": "test-password",
        "MRML_IG_EPIC": PRODUCT.instrument_id,
    }.items():
        monkeypatch.setenv(name, value)
    assert main(params) == 0
    output = capsys.readouterr().out
    assert "test-key" not in output and "test-password" not in output
    attempt = json.loads(output)["attempt"]
    assert attempt["state"] == "committed"
    assert attempt["attempt_number"] == 2
    assert attempt["retrieval_id"] != uncertain["id"]


@pytest.mark.parametrize("partial", [True, False])
def test_committed_gap_requires_explicit_revisit(sqlite_db, partial):
    ingestion = source_only(sqlite_db)
    label = START - HOUR
    response = sample() if partial else replace(sample(), observations=())
    _, original = ingestion.run(lambda *args: response, start=label)
    assert original.outcome["source_labels_complete"] is False
    assert ingestion.run(forbid_fetch, start=label)[1] is None

    planned = ingestion.plan(revisit_gap=label).request
    assert planned.kind == "gap_revisit"
    assert planned.start == label
    assert planned.request_end == START - timedelta(seconds=1)
    assert planned.point_limit == 1
    assert len(rows(sqlite_db)) == 1
    _, revisit = ingestion.run(
        lambda *args: sample(observation(label)), revisit_gap=label
    )
    assert revisit.retrieval_id != original.retrieval_id
    assert revisit.job_id != original.job_id
    assert rows(sqlite_db)[0]["outcome"] == original.outcome
    result = ingestion.describe_attempt(revisit)["outcome"]
    assert result["source_labels_complete"] is True
    assert result["session_closure_coverage_verified"] is False
    assert result["broker_candle_finality"] == "unknown"
    assert ingestion.status()["verified_contiguous_end"] is None
    assert label.isoformat() not in {
        gap["source_label"] for gap in ingestion.status()["gaps"]
    }
    with pytest.raises(IngestionRefused, match="already has"):
        ingestion.run(forbid_fetch, revisit_gap=label)


def test_gap_revisit_refuses_unattempted_other_product_horizon_and_closure(sqlite_db):
    closed = START - HOUR
    approval = approve(sqlite_db)

    def session(product, start, end):
        return SessionEvidence(GapKind.EXPECTED_CLOSURE, "verified fixture")

    permitted = source_only(
        sqlite_db,
        approval=approval,
        sessions=SessionPolicy(PRODUCT, "closure/v1", session),
    )
    permitted.run(lambda *args: replace(sample(), observations=()), start=closed)
    ingestion = source_only(sqlite_db)
    with pytest.raises(IngestionRefused, match="verified session closure"):
        ingestion.run(forbid_fetch, revisit_gap=closed)
    with pytest.raises(IngestionRefused, match="No committed incremental gap"):
        ingestion.run(forbid_fetch, revisit_gap=closed - HOUR)
    other = IGIngestion(sqlite_db, replace(PRODUCT, variant="another"), clock=Clock())
    with pytest.raises(IngestionRefused, match="No committed incremental gap"):
        other.run(forbid_fetch, revisit_gap=closed)
    with pytest.raises(IngestionRefused, match="catch-up horizon"):
        ingestion.run(forbid_fetch, revisit_gap=closed - 48 * HOUR)


def test_gap_revisit_respects_quota_and_throttle(sqlite_db):
    label = START - HOUR
    ingestion = source_only(sqlite_db)
    ingestion.run(
        lambda *args: replace(
            sample(),
            observations=(),
            allowance=IGAllowance(20, 100, 3600),
        ),
        start=label,
    )
    plan, row = ingestion.run(forbid_fetch, revisit_gap=label)
    assert row is None and plan.refused and "reserve" in plan.message
    assert len(rows(sqlite_db)) == 1
    unrestricted = source_only(sqlite_db, budget=IngestionBudget(minimum_reserve=0))

    def throttle(*args):
        raise IGQuotaError("throttle", retry_at=RETRIEVED + HOUR)

    with pytest.raises(IGQuotaError):
        unrestricted.run(throttle, start=label - HOUR)
    plan, row = unrestricted.run(forbid_fetch, revisit_gap=label)
    assert row is None and plan.retry_after == RETRIEVED + HOUR


def verify_gap_revisit_recovery(db, monkeypatch, *, upgrade=None):
    ingestion = source_only(db)
    _, original_attempt = ingestion.run(
        lambda *args: replace(sample(), observations=()), start=START
    )
    if upgrade:
        upgrade()
    writer = ingestion.repo.record_ig_sample

    def interrupt(*args, **kwargs):
        writer(*args, **kwargs)
        raise RuntimeError("interrupted after write")

    monkeypatch.setattr(ingestion.repo, "record_ig_sample", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        ingestion.run(lambda *args: sample(), revisit_gap=START)
    attempts = {row["kind"]: row for row in rows(db)}
    original, staged = attempts["incremental"], attempts["gap_revisit"]
    assert original["id"] == original_attempt.id
    assert (
        original["state"] == "committed"
        and original["outcome"] == original_attempt.outcome
    )
    assert staged["state"] == "staged"
    assert not ingestion.repo.source_revisions(PRODUCT, "HOUR", START, START + HOUR)
    restart = source_only(db)
    plan, recovered = restart.run(forbid_fetch, revisit_gap=START)
    assert plan.staged_attempt == recovered.id == staged["id"]
    assert recovered.retrieval_id == staged["id"]
    assert recovered.outcome["source_labels_complete"] is True
    assert restart.recover() is None


def test_gap_revisit_rollback_and_staged_recovery(sqlite_db, monkeypatch):
    verify_gap_revisit_recovery(sqlite_db, monkeypatch)


def test_postgres_gap_revisit_rollback_and_staged_recovery(pg_db, monkeypatch):
    engine, config = pg_db
    command.upgrade(config, "0003_ig_ingestion")
    verify_gap_revisit_recovery(
        engine, monkeypatch, upgrade=lambda: command.upgrade(config, "head")
    )
    with pytest.raises(IntegrityError):
        command.downgrade(config, "0003_ig_ingestion")
    assert any(row["kind"] == "gap_revisit" for row in rows(engine))


def test_uncertain_gap_revisit_consumes_limit(sqlite_db):
    ingestion = source_only(sqlite_db)
    ingestion.run(lambda *args: replace(sample(), observations=()), start=START)

    def transport(*args):
        raise IGError("transport")

    with pytest.raises(IGError):
        ingestion.run(transport, revisit_gap=START)
    restart = source_only(sqlite_db)
    assert restart.recover() is None
    with pytest.raises(IngestionRefused, match="already attempted"):
        restart.run(forbid_fetch, revisit_gap=START)
    with pytest.raises(IngestionRefused, match="cannot be retried"):
        restart.run(forbid_fetch, retry_job=rows(sqlite_db)[1]["job_id"])


def test_cli_gap_revisit_dry_run_never_loads_credentials(
    sqlite_db, monkeypatch, capsys
):
    ingestion = source_only(sqlite_db)
    ingestion.run(lambda *args: replace(sample(), observations=()), start=START)
    monkeypatch.setenv("MRML_DATABASE_URL", str(sqlite_db.url))
    monkeypatch.setattr("mrml.data.ig_ingest.load_probe_values", forbid_fetch)
    monkeypatch.setattr(
        "mrml.data.ig_ingest.IGIngestion",
        lambda *args, **kwargs: IGIngestion(*args, **kwargs, clock=Clock()),
    )
    assert (
        main(
            [
                "plan",
                "--epic",
                PRODUCT.instrument_id,
                "--variant",
                PRODUCT.variant,
                "--revisit-gap",
                START.isoformat(),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["request"]["kind"] == "gap_revisit"
    assert len(rows(sqlite_db)) == 1
