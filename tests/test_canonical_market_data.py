import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from mrml.data.canonical import (
    HOUR,
    OHLC,
    Availability,
    Completion,
    CompletionState,
    FXInstrument,
    InstrumentMapping,
    Resolution,
    SourceBar,
    SourceProduct,
    TimestampMeaning,
    TimestampPolicy,
)
from mrml.data.history import (
    EligibilityPolicy,
    GapKind,
    Refusal,
    SequenceIssue,
    SessionEvidence,
    diagnose_history,
    evaluate_history,
)
from mrml.data.ig import IGObservation, IGPriceSample, IGQuote, _observation
from mrml.data.ig_adapter import TRANSFORMATION_VERSION, adapt_ig_sample
from mrml.data.provider import (
    H1HistoryRequest,
    InsufficientHistoryError,
    MarketDataProvider,
)

FRIDAY = datetime(2026, 3, 27, 20, tzinfo=UTC)
MONDAY = datetime(2026, 3, 30, 0, tzinfo=UTC)
RETRIEVED = MONDAY + 2 * HOUR
PAIR = FXInstrument("EUR_USD")
EPIC = "CS.D.TESTFX.STD.IP"
MAPPING = InstrumentMapping(PAIR, SourceProduct("IG", EPIC, "standard"), "1")
POLICY = TimestampPolicy(
    "synthetic-open/v1", TimestampMeaning.INTERVAL_START, "fixture"
)
STRICT = EligibilityPolicy(True, True, True)
PERMISSIVE = EligibilityPolicy(False, False, False)


def observation(stamp: datetime) -> IGObservation:
    return IGObservation(
        stamp, IGQuote(10, 12), IGQuote(14, 16), IGQuote(8, 9), IGQuote(11, 13)
    )


def sample(*stamps: datetime) -> IGPriceSample:
    return IGPriceSample(
        EPIC, "HOUR", tuple(observation(stamp) for stamp in stamps), None, 0
    )


def bars_at(*stamps: datetime) -> tuple[SourceBar, ...]:
    return adapt_ig_sample(
        sample(*stamps),
        mappings=(MAPPING,),
        retrieved_at=RETRIEVED,
        timestamp_policy=POLICY,
    )


@pytest.mark.parametrize(
    "symbol", ["EUR_USD", "USD_JPY", "AUD_NZD", "USD_SEK", "USD_CNH"]
)
def test_fx_identity(symbol: str) -> None:
    assert FXInstrument(symbol).symbol == symbol


@pytest.mark.parametrize(
    "symbol", ["eur_usd", "EUR/USD", "EUR_EUR", "USD_ZZZ", "EUR_XXX"]
)
def test_invalid_fx_identity(symbol: str) -> None:
    with pytest.raises(ValueError, match="BASE_QUOTE"):
        FXInstrument(symbol)


def test_ig_mapping_distinguishes_variants() -> None:
    mappings = tuple(
        InstrumentMapping(PAIR, SourceProduct("IG", epic, variant), "1")
        for epic, variant in [
            (EPIC, "standard"),
            ("CS.D.TESTFX.MINI.IP", "mini"),
            ("CS.D.TESTFX.WEEKEND.IP", "weekend"),
            ("CS.D.TESTFX.FWD.MAR", "forward"),
        ]
    )
    products = tuple(
        adapt_ig_sample(
            replace(sample(FRIDAY), epic=mapping.product.instrument_id),
            mappings=mappings,
            retrieved_at=RETRIEVED,
            timestamp_policy=POLICY,
        )[0].mapping.product
        for mapping in mappings
    )
    assert len(set(products)) == 4
    assert all(mapping.instrument == PAIR for mapping in mappings)


@pytest.mark.parametrize(
    ("mappings", "message"),
    [
        ((), "No explicit mapping"),
        (
            (replace(MAPPING, product=SourceProduct("OTHER", EPIC, "standard")),),
            "Expected an IG",
        ),
        (
            (replace(MAPPING, product=SourceProduct("IG", "bad/epic", "standard")),),
            "EPIC syntax",
        ),
        (
            (replace(MAPPING, product=SourceProduct("IG", "OTHER", "standard")),),
            "No explicit mapping",
        ),
        (
            (MAPPING, replace(MAPPING, instrument=FXInstrument("EUR_GBP"))),
            "Conflicting mappings",
        ),
        ((MAPPING, replace(MAPPING, version="2")), "Conflicting mappings"),
    ],
)
def test_invalid_mapping(mappings: tuple[InstrumentMapping, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        adapt_ig_sample(
            sample(FRIDAY),
            mappings=mappings,
            retrieved_at=RETRIEVED,
            timestamp_policy=POLICY,
        )


@pytest.mark.parametrize(
    "invalid", [None, True, "1.2", 0, -1, float("nan"), float("inf")]
)
def test_invalid_prices(invalid: object) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        OHLC(cast(float, invalid), 4, 1, 2)


def test_price_invariants_and_explicit_derivation() -> None:
    (bar,) = bars_at(FRIDAY)
    assert bar.bid == OHLC(10, 14, 8, 11)
    assert bar.ask == OHLC(12, 16, 9, 13)
    assert bar.volume is None
    assert bar.transformation_version == TRANSFORMATION_VERSION
    midpoint = bar.componentwise_midpoint()
    assert midpoint.method == "componentwise_bid_ask_midpoint/v1"
    assert midpoint.prices == OHLC(11, 15, 8.5, 12)
    with pytest.raises(FrozenInstanceError):
        setattr(bar, "volume", 0)  # noqa: B010
    with pytest.raises(ValueError, match="OHLC range"):
        OHLC(12, 10, 5, 9)
    with pytest.raises(ValueError, match="Bid exceeds ask"):
        replace(bar, ask=OHLC(12, 13, 9, 13))
    with pytest.raises(ValueError, match="requires bid and ask"):
        replace(bar, bid=cast(OHLC, None))


@pytest.mark.parametrize(
    "raw", ["2026-03-27T20:00:00", "2026-03-27T20:00:00Z", "2026-03-27T20:00:00+00:00"]
)
def test_rest_timestamp_roundtrip(raw: str) -> None:
    row = _observation(
        {
            "snapshotTimeUTC": raw,
            "openPrice": {"bid": 1.123456789012345, "ask": 1.2},
            "highPrice": {"bid": 1.3, "ask": 1.4},
            "lowPrice": {"bid": 1.0, "ask": 1.1},
            "closePrice": {"bid": 1.2, "ask": 1.3},
        }
    )
    data = replace(sample(), observations=(row,))
    args = dict(mappings=(MAPPING,), retrieved_at=RETRIEVED, timestamp_policy=POLICY)
    result = adapt_ig_sample(data, **args)
    (bar,) = result
    assert result == adapt_ig_sample(data, **args)
    assert bar.bid.open == row.open.bid
    assert (bar.start, bar.end) == (FRIDAY, FRIDAY + HOUR)
    assert bar.start.tzinfo is bar.end.tzinfo is UTC
    assert bar.source_time.raw == raw
    assert bar.source_time.field == "snapshotTimeUTC"
    assert bar.source_time.policy == POLICY
    assert bar.retrieved_at == RETRIEVED
    assert bar.completion.state is CompletionState.UNKNOWN
    assert bar.availability.first_available_at is None


def test_end_labeled_offset_input_and_dst_fold() -> None:
    stockholm = ZoneInfo("Europe/Stockholm")
    label = datetime(2026, 10, 25, 2, tzinfo=stockholm)
    policy = TimestampPolicy(
        "synthetic-close/v1", TimestampMeaning.INTERVAL_END, "fixture"
    )
    bars = adapt_ig_sample(
        sample(label, label.replace(fold=1)),
        mappings=(MAPPING,),
        retrieved_at=datetime(2026, 10, 25, 3, tzinfo=UTC),
        timestamp_policy=policy,
    )
    assert bars[0].end == datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert bars[1].end == datetime(2026, 10, 25, 1, tzinfo=UTC)
    assert bars[0].end == bars[1].start
    assert bars[1].source_time.instant.fold == 1


@pytest.mark.parametrize(
    "stamp", [FRIDAY.replace(tzinfo=None), FRIDAY.replace(minute=1)]
)
def test_invalid_observation_timestamp(stamp: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware|align"):
        bars_at(stamp)


@pytest.mark.parametrize(
    "raw",
    ["bad", "2026-02-30T20:00:00", "2026-03-27T21:00:00Z", "2026-03-27T21:00:00+01:00"],
)
def test_malformed_or_inconsistent_rest_timestamp(raw: str) -> None:
    data = replace(
        sample(), observations=(replace(observation(FRIDAY), raw_timestamp=raw),)
    )
    with pytest.raises(ValueError):
        adapt_ig_sample(
            data, mappings=(MAPPING,), retrieved_at=RETRIEVED, timestamp_policy=POLICY
        )


def test_unresolved_policy_and_invalid_intervals() -> None:
    (bar,) = bars_at(FRIDAY)
    with pytest.raises(ValueError, match="Unresolved"):
        replace(POLICY, meaning=cast(TimestampMeaning, "UNKNOWN"))
    with pytest.raises(ValueError, match="explicit timestamp interpretation"):
        adapt_ig_sample(
            sample(FRIDAY),
            mappings=(MAPPING,),
            retrieved_at=RETRIEVED,
            timestamp_policy=cast(TimestampPolicy, None),
        )
    for end in (FRIDAY, FRIDAY + 2 * HOUR):
        with pytest.raises(ValueError, match="exactly one hour"):
            replace(bar, end=end)
    with pytest.raises(ValueError, match="align"):
        replace(bar, start=FRIDAY.replace(minute=30))
    with pytest.raises(ValueError, match="UTC-aware"):
        replace(bar, retrieved_at=RETRIEVED.astimezone(timezone(HOUR)))
    with pytest.raises(ValueError, match="Unsupported bar resolution"):
        replace(bar, resolution=cast(Resolution, "D1"))
    with pytest.raises(ValueError, match="Unsupported IG resolution"):
        adapt_ig_sample(
            replace(sample(), resolution="DAY"),
            mappings=(MAPPING,),
            retrieved_at=RETRIEVED,
            timestamp_policy=POLICY,
        )


def test_validated_completion_and_availability_evidence() -> None:
    first = FRIDAY + HOUR + timedelta(seconds=1)
    complete = Completion(CompletionState.COMPLETE, "fixture finalized flag")
    known = Availability(first, "fixture publication log")
    (bar,) = adapt_ig_sample(
        sample(FRIDAY),
        mappings=(MAPPING,),
        retrieved_at=RETRIEVED,
        timestamp_policy=POLICY,
        completion={FRIDAY: complete},
        availability={FRIDAY: known},
    )
    assert bar.completion == complete
    assert bar.availability == known
    assert evaluate_history(
        (bar,),
        policy=STRICT,
        evaluated_at=RETRIEVED,
        validated_mapping=MAPPING,
    ).eligible
    with pytest.raises(ValueError, match="completion evidence"):
        Completion(CompletionState.COMPLETE)
    with pytest.raises(ValueError, match="availability evidence"):
        Availability(first)
    with pytest.raises(ValueError, match="within"):
        replace(bar, availability=Availability(FRIDAY - HOUR, "fixture"))
    with pytest.raises(ValueError, match="before interval end"):
        replace(bar, retrieved_at=FRIDAY)
    for stamps in ((), (FRIDAY, FRIDAY)):
        with pytest.raises(ValueError, match="exactly one"):
            adapt_ig_sample(
                sample(*stamps),
                mappings=(MAPPING,),
                retrieved_at=RETRIEVED,
                timestamp_policy=POLICY,
                completion={FRIDAY: complete},
            )


def test_eligibility_reports_unknown_and_incomplete() -> None:
    (bar,) = bars_at(FRIDAY)
    args = dict(evaluated_at=RETRIEVED, validated_mapping=MAPPING)
    relaxed = evaluate_history((bar,), policy=PERMISSIVE, **args)
    assert (
        relaxed.eligible and relaxed.unknown_completion and relaxed.unknown_availability
    )
    assert set(evaluate_history((bar,), policy=STRICT, **args).refusals) == {
        Refusal.UNKNOWN_COMPLETION,
        Refusal.UNKNOWN_AVAILABILITY,
    }
    incomplete = replace(
        bar, completion=Completion(CompletionState.INCOMPLETE, "fixture flag")
    )
    assert evaluate_history((incomplete,), policy=PERMISSIVE, **args).refusals == (
        Refusal.INCOMPLETE,
    )


def test_interval_end_requirement_is_opt_in() -> None:
    previous, current = bars_at(MONDAY - HOUR, MONDAY)
    evaluated_at = MONDAY + timedelta(minutes=30)
    bars = tuple(replace(bar, retrieved_at=evaluated_at) for bar in (previous, current))
    args = dict(evaluated_at=evaluated_at, validated_mapping=MAPPING)

    assert evaluate_history(bars, policy=PERMISSIVE, **args).eligible
    policy = replace(PERMISSIVE, require_ended_intervals=True)
    assert evaluate_history(bars, policy=policy, **args).refusals == (
        Refusal.INTERVAL_NOT_ENDED,
    )


@pytest.mark.parametrize("elapsed_since_end", [timedelta(0), timedelta(minutes=1)])
def test_ended_interval_still_requires_completion_evidence(
    elapsed_since_end: timedelta,
) -> None:
    (bar,) = bars_at(MONDAY)
    bar = replace(bar, retrieved_at=bar.end)
    args = dict(evaluated_at=bar.end + elapsed_since_end, validated_mapping=MAPPING)
    policy = replace(PERMISSIVE, require_ended_intervals=True)

    result = evaluate_history((bar,), policy=policy, **args)
    assert result.eligible
    assert result.unknown_completion
    complete_only = replace(policy, require_complete=True)
    assert evaluate_history((bar,), policy=complete_only, **args).refusals == (
        Refusal.UNKNOWN_COMPLETION,
    )

    completed = replace(
        bar, completion=Completion(CompletionState.COMPLETE, "fixture finalized flag")
    )
    assert evaluate_history((completed,), policy=complete_only, **args).eligible


def test_freshness_as_of_mapping_and_history_refusals() -> None:
    bars = bars_at(MONDAY)
    policy = replace(PERMISSIVE, max_age=HOUR)
    assert evaluate_history(
        bars, policy=policy, evaluated_at=RETRIEVED, validated_mapping=MAPPING
    ).eligible
    assert set(
        evaluate_history(
            (*bars, *bars),
            policy=policy,
            evaluated_at=RETRIEVED + HOUR,
            validated_mapping=None,
            resolution="D1",
        ).refusals
    ) == {
        Refusal.INVALID_HISTORY,
        Refusal.STALE,
        Refusal.UNVALIDATED_MAPPING,
        Refusal.UNSUPPORTED_RESOLUTION,
    }
    assert evaluate_history(
        bars,
        policy=replace(policy, minimum_bars=2),
        evaluated_at=RETRIEVED - HOUR,
        validated_mapping=MAPPING,
    ).refusals == (Refusal.INSUFFICIENT_HISTORY, Refusal.NOT_RETRIEVED_AT_EVALUATION)


def test_sequence_defects() -> None:
    first, second = bars_at(FRIDAY, FRIDAY + HOUR)
    assert (
        diagnose_history((first, second)).issues
        == diagnose_history((first, second)).gaps
        == ()
    )
    assert diagnose_history((second, first)).issues == (SequenceIssue.UNORDERED,)
    revision = replace(first, bid=replace(first.bid, close=10))
    assert diagnose_history((first, revision)).issues == (
        SequenceIssue.DUPLICATE,
        SequenceIssue.OVERLAP,
    )
    other = replace(second, mapping=replace(MAPPING, version="2"))
    assert diagnose_history((first, other)).issues == (SequenceIssue.MIXED_SERIES,)


def test_weekend_gap_classification_depends_on_product_session_evidence() -> None:
    bars = bars_at(FRIDAY, MONDAY)
    (gap,) = diagnose_history(bars).gaps
    assert (gap.start, gap.end, gap.kind) == (FRIDAY + HOUR, MONDAY, GapKind.UNKNOWN)
    args = dict(evaluated_at=RETRIEVED, validated_mapping=MAPPING)
    assert evaluate_history(bars, policy=PERMISSIVE, **args).eligible
    policy = replace(PERMISSIVE, require_session_coverage=True)
    assert evaluate_history(bars, policy=policy, **args).refusals == (
        Refusal.UNVERIFIED_SESSION_COVERAGE,
    )

    def closure(
        product: SourceProduct, start: datetime, end: datetime
    ) -> SessionEvidence:
        assert (product, start, end) == (MAPPING.product, FRIDAY + HOUR, MONDAY)
        return SessionEvidence(GapKind.EXPECTED_CLOSURE, "fixture product calendar")

    closed = evaluate_history(bars, policy=policy, session_lookup=closure, **args)
    assert closed.eligible
    assert closed.sequence.gaps[0].kind is GapKind.EXPECTED_CLOSURE
    missing = evaluate_history(
        bars_at(MONDAY - 2 * HOUR, MONDAY),
        policy=PERMISSIVE,
        session_lookup=lambda *_: SessionEvidence(
            GapKind.MISSING_EXPECTED_TRADING, "fixture schedule"
        ),
        **args,
    )
    assert missing.refusals == (Refusal.MISSING_EXPECTED_TRADING,)


def test_provider_can_serve_an_offline_window() -> None:
    stored = bars_at(MONDAY, MONDAY + HOUR)

    class InMemoryProvider:
        def read_h1(self, request: H1HistoryRequest, /) -> tuple[SourceBar, ...]:
            rows = tuple(
                bar
                for bar in stored
                if bar.mapping.instrument == request.instrument
                and request.start <= bar.start < request.end
            )
            if len(rows) * HOUR != request.end - request.start:
                raise InsufficientHistoryError("Fixture does not cover this window")
            return rows

    provider: MarketDataProvider = InMemoryProvider()
    assert provider.read_h1(H1HistoryRequest(PAIR, MONDAY, MONDAY + HOUR)) == stored[:1]
    with pytest.raises(InsufficientHistoryError):
        provider.read_h1(H1HistoryRequest(PAIR, MONDAY, MONDAY + 3 * HOUR))
    with pytest.raises(ValueError, match="positive duration"):
        H1HistoryRequest(PAIR, MONDAY, MONDAY)


def test_contract_and_adapter_import_without_persistence_or_external_runtime() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from datetime import UTC, datetime
from unittest.mock import patch

class BlockDependencies:
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"sqlalchemy", "mpml", "msml", "psycopg"}:
            raise AssertionError(fullname)

sys.meta_path.insert(0, BlockDependencies())
from mrml.data import history, provider
from mrml.data.canonical import (
    FXInstrument, HOUR, InstrumentMapping, SourceProduct, TimestampMeaning,
    TimestampPolicy,
)
from mrml.data.ig import IGObservation, IGPriceSample, IGQuote
from mrml.data.ig_adapter import adapt_ig_sample

label = datetime(2026, 1, 12, tzinfo=UTC)
quote = IGQuote(1.1, 1.2)
row = IGObservation(label, quote, quote, quote, quote)
sample = IGPriceSample("TEST.A", "HOUR", (row,), None, 0)
mapping = InstrumentMapping(
    FXInstrument("EUR_USD"), SourceProduct("IG", "TEST.A", "standard"), "1"
)
policy = TimestampPolicy("fixture/v1", TimestampMeaning.INTERVAL_START, "fixture")
with patch("socket.socket", side_effect=AssertionError("network access")):
    bar, = adapt_ig_sample(
        sample, mappings=(mapping,), retrieved_at=label + HOUR,
        timestamp_policy=policy,
    )
assert bar.bid.open == 1.1
""",
        ],
        env={"PYTHONPATH": "src"},
        check=True,
    )
