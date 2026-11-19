"""Offline conversion of the bounded IG REST historical price sample."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from mrml.data.canonical import (
    HOUR,
    OHLC,
    Availability,
    Completion,
    InstrumentMapping,
    Resolution,
    SourceBar,
    SourceTimestamp,
    TimestampMeaning,
    TimestampPolicy,
    require_hour,
    require_utc,
)
from mrml.data.ig import (
    RESOLUTION,
    IGObservation,
    IGPriceSample,
    parse_snapshot_time_utc,
    validate_epic,
)

IG = "IG"
TRANSFORMATION_VERSION = "ig-rest-prices-v3/h1/v1"


def _mapping_for(epic: str, mappings: Iterable[InstrumentMapping]) -> InstrumentMapping:
    validate_epic(epic)
    by_epic: dict[str, InstrumentMapping] = {}
    for mapping in mappings:
        if mapping.product.provider != IG:
            raise ValueError("Expected an IG instrument mapping")
        instrument_id = mapping.product.instrument_id
        validate_epic(instrument_id)
        if by_epic.setdefault(instrument_id, mapping) != mapping:
            raise ValueError(f"Conflicting mappings for IG EPIC {instrument_id}")
    if epic not in by_epic:
        raise ValueError("No explicit mapping for the sample's IG EPIC")
    return by_epic[epic]


def adapt_ig_sample(
    sample: IGPriceSample,
    *,
    mappings: Iterable[InstrumentMapping],
    retrieved_at: datetime,
    timestamp_policy: TimestampPolicy,
    completion: Mapping[datetime, Completion] | None = None,
    availability: Mapping[datetime, Availability] | None = None,
) -> tuple[SourceBar, ...]:
    """Preserve source order and revisions for independent sequence validation.

    Evidence is keyed by the source timestamp in UTC. IG describes the field
    as snapshot time without specifying an interval boundary, so callers
    must explicitly supply the interpretation and its supporting evidence.
    """
    if sample.resolution != RESOLUTION:
        raise ValueError("Unsupported IG resolution")
    if not isinstance(timestamp_policy, TimestampPolicy):
        raise ValueError("An explicit timestamp interpretation policy is required")
    require_utc(retrieved_at, "retrieval time")
    mapping = _mapping_for(sample.epic, mappings)
    completion = completion or {}
    availability = availability or {}
    bars = tuple(
        _adapt_observation(
            row, mapping, retrieved_at, timestamp_policy, completion, availability
        )
        for row in sample.observations
    )
    timestamps = Counter(bar.source_time.instant.astimezone(UTC) for bar in bars)
    for evidence in (completion, availability):
        for instant in evidence:
            require_utc(instant, "evidence key")
            if timestamps[instant] != 1:
                raise ValueError("Evidence key must identify exactly one observation")
    return bars


def _adapt_observation(
    row: IGObservation,
    mapping: InstrumentMapping,
    retrieved_at: datetime,
    policy: TimestampPolicy,
    completion: Mapping[datetime, Completion],
    availability: Mapping[datetime, Availability],
) -> SourceBar:
    source_time = SourceTimestamp(
        "snapshotTimeUTC", row.timestamp, policy, row.raw_timestamp
    )
    instant = row.timestamp.astimezone(UTC)
    require_hour(instant, "IG timestamp")
    if (
        row.raw_timestamp is not None
        and parse_snapshot_time_utc(row.raw_timestamp) != instant
    ):
        raise ValueError("Raw IG timestamp disagrees with the parsed observation")
    start = (
        instant if policy.meaning is TimestampMeaning.INTERVAL_START else instant - HOUR
    )
    return SourceBar(
        mapping=mapping,
        resolution=Resolution.H1,
        start=start,
        end=start + HOUR,
        bid=OHLC(row.open.bid, row.high.bid, row.low.bid, row.close.bid),
        ask=OHLC(row.open.ask, row.high.ask, row.low.ask, row.close.ask),
        source_time=source_time,
        retrieved_at=retrieved_at,
        transformation_version=TRANSFORMATION_VERSION,
        completion=completion.get(instant, Completion()),
        availability=availability.get(instant, Availability()),
    )
