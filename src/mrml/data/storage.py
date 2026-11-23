"""Transactional storage and observation-time queries for IG H1 market data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mrml.data.canonical import (
    OHLC,
    Availability,
    Completion,
    CompletionState,
    FXInstrument,
    InstrumentMapping,
    Resolution,
    SourceBar,
    SourceProduct,
    SourceTimestamp,
    TimestampMeaning,
    TimestampPolicy,
    require_text,
    require_utc,
)
from mrml.data.ig import (
    RESOLUTION,
    IGObservation,
    IGPriceSample,
    parse_snapshot_time_utc,
    validate_epic,
)
from mrml.data.ig_adapter import IG, TRANSFORMATION_VERSION, adapt_ig_sample
from mrml.data.provider import InsufficientHistoryError
from mrml.data.storage_models import (
    InterpretationRow,
    MappingRow,
    MarketDataBase,
    ObservationRow,
    PolicyRow,
    ProductRow,
    RetrievalRow,
)

IG_TIMESTAMP_FIELD = "snapshotTimeUTC"
PRICE_COMPONENTS = ("open", "high", "low", "close")


class PersistenceConflict(ValueError):
    """An existing identity was reused with different immutable content."""


@dataclass(frozen=True)
class ApprovedTimestampPolicy:
    mapping: InstrumentMapping
    policy: TimestampPolicy
    approval: str

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, InstrumentMapping) or not isinstance(
            self.policy, TimestampPolicy
        ):
            raise ValueError("Approval requires an explicit mapping and policy")
        require_text(self.approval, "timestamp policy approval")


@dataclass(frozen=True)
class SourceRevision:
    product: SourceProduct
    retrieval_id: str
    ordinal: int
    resolution: str
    timestamp_field: str
    raw_timestamp: str
    source_timestamp: datetime
    retrieved_at: datetime
    bid: tuple[Decimal, Decimal, Decimal, Decimal]
    ask: tuple[Decimal, Decimal, Decimal, Decimal]
    fingerprint: str
    response_metadata: Mapping[str, int]
    volume: None = None


@dataclass(frozen=True)
class CanonicalRevision:
    bar: SourceBar
    source: SourceRevision
    mapping_provenance: str
    policy_approval: str
    interpreted_at: datetime


@dataclass(frozen=True)
class StoredHistory:
    revisions: tuple[CanonicalRevision, ...]
    coverage_verified: bool = False

    def require_observations(self) -> tuple[CanonicalRevision, ...]:
        if not self.revisions:
            raise InsufficientHistoryError("No stored interpretations in the window")
        return self.revisions


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _source_record(row: IGObservation) -> dict[str, Any]:
    if row.raw_timestamp is None or row.timestamp.utcoffset() is None:
        raise ValueError("IG observations require a raw timestamp and parsed instant")
    instant = row.timestamp.astimezone(UTC)
    if parse_snapshot_time_utc(row.raw_timestamp) != instant:
        raise ValueError("Raw IG timestamp disagrees with the parsed observation")

    quotes = (row.open, row.high, row.low, row.close)
    prices: dict[str, Decimal] = {}
    bid = OHLC(*(quote.bid for quote in quotes))
    ask = OHLC(*(quote.ask for quote in quotes))
    for name, bid_value, ask_value in zip(
        PRICE_COMPONENTS, bid.components, ask.components, strict=True
    ):
        if bid_value > ask_value:
            raise ValueError("Bid exceeds ask for a corresponding OHLC component")
        prices[f"bid_{name}"] = Decimal(str(bid_value))
        prices[f"ask_{name}"] = Decimal(str(ask_value))

    fingerprint = _digest(
        [row.raw_timestamp, instant.isoformat(), {k: str(v) for k, v in prices.items()}]
    )
    return dict(
        raw_timestamp=row.raw_timestamp,
        source_timestamp=instant,
        fingerprint=fingerprint,
        **prices,
    )


def _source_revision(
    product: SourceProduct, retrieval: RetrievalRow, observation: ObservationRow
) -> SourceRevision:
    return SourceRevision(
        product=product,
        retrieval_id=retrieval.id,
        ordinal=observation.ordinal,
        resolution=retrieval.resolution,
        timestamp_field=retrieval.timestamp_field,
        raw_timestamp=observation.raw_timestamp,
        source_timestamp=observation.source_timestamp,
        retrieved_at=retrieval.retrieved_at,
        bid=(
            observation.bid_open,
            observation.bid_high,
            observation.bid_low,
            observation.bid_close,
        ),
        ask=(
            observation.ask_open,
            observation.ask_high,
            observation.ask_low,
            observation.ask_close,
        ),
        fingerprint=observation.fingerprint,
        response_metadata=retrieval.response_metadata,
    )


def _bar_prices(prices: tuple[Decimal, ...]) -> OHLC:
    # The A2 boundary uses Python numeric values. The revision carries Decimals.
    return OHLC(
        *(
            int(price) if price == price.to_integral_value() else float(price)
            for price in prices
        )
    )


def _validate_window(start: datetime, end: datetime, as_of: datetime | None) -> None:
    require_utc(start, "window start")
    require_utc(end, "window end")
    if start >= end:
        raise ValueError("History window must have positive duration")
    if as_of is not None:
        require_utc(as_of, "evaluation time")


def _insert_once(
    session: Session, model: type[MarketDataBase], key: dict[str, Any], **values: Any
) -> int | str | None:
    """Use the unique key as the concurrency arbiter on both supported databases."""
    insert_for = (
        pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    )
    stmt = (
        insert_for(model)
        .values(**key, **values)
        .on_conflict_do_nothing(index_elements=list(key))
    )
    primary_key = next(iter(model.__table__.primary_key))
    return cast(int | str | None, session.scalar(stmt.returning(primary_key)))


def _ensure_immutable(
    session: Session,
    model: type[MarketDataBase],
    key: dict[str, Any],
    *,
    insert_defaults: dict[str, Any] | None = None,
    **values: Any,
) -> int | str | None:
    identity = _insert_once(session, model, key, **(insert_defaults or {}), **values)
    if identity is None:
        existing = session.scalars(select(model).filter_by(**key)).one()
        if any(getattr(existing, field) != value for field, value in values.items()):
            raise PersistenceConflict(f"Conflicting content for {model.__tablename__}")
        return getattr(existing, "id", None)
    return identity


class MarketDataRepository:
    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError("Market data storage requires PostgreSQL or SQLite")
        self._sessions = sessionmaker(engine)

    def register_mapping(self, mapping: InstrumentMapping, *, provenance: str) -> None:
        require_text(provenance, "mapping provenance")
        with self._sessions.begin() as session:
            product_id = self._ensure_product(session, mapping.product)
            _ensure_immutable(
                session,
                MappingRow,
                dict(product_id=product_id, version=mapping.version),
                instrument=mapping.instrument.symbol,
                provenance=provenance,
            )

    def resolve_mapping(
        self, product: SourceProduct, version: str
    ) -> InstrumentMapping:
        with self._sessions() as session:
            mapping = session.scalar(
                select(MappingRow)
                .join(ProductRow)
                .where(
                    *self._product_conditions(product), MappingRow.version == version
                )
            )
            if mapping is None:
                raise ValueError(
                    "No registered source-product mapping for this version"
                )
            return InstrumentMapping(FXInstrument(mapping.instrument), product, version)

    def register_timestamp_policy(self, approval: ApprovedTimestampPolicy) -> None:
        with self._sessions.begin() as session:
            product_id = self._ensure_product(session, approval.mapping.product)
            self._ensure_policy(session, product_id, approval)

    def resolve_timestamp_policy(
        self, product: SourceProduct, version: str, name: str
    ) -> ApprovedTimestampPolicy:
        mapping = self.resolve_mapping(product, version)
        with self._sessions() as session:
            row = session.scalar(
                select(PolicyRow)
                .join(MappingRow)
                .join(ProductRow)
                .where(
                    *self._product_conditions(product),
                    MappingRow.version == version,
                    PolicyRow.name == name,
                )
            )
            if row is None:
                raise ValueError("No approved timestamp policy for this mapping")
            return ApprovedTimestampPolicy(
                mapping,
                TimestampPolicy(row.name, TimestampMeaning(row.meaning), row.evidence),
                row.approval,
            )

    def record_ig_sample(
        self,
        product: SourceProduct,
        sample: IGPriceSample,
        *,
        retrieval_id: str,
        retrieved_at: datetime,
        approval: ApprovedTimestampPolicy | None = None,
        transformation_version: str = TRANSFORMATION_VERSION,
        interpreted_at: datetime | None = None,
        completion: Mapping[datetime, Completion] | None = None,
        availability: Mapping[datetime, Availability] | None = None,
        session: Session | None = None,
    ) -> None:
        """Store a response and its interpretations in one transaction.

        A supplied session leaves transaction and rollback ownership to the caller.
        """
        require_text(retrieval_id, "retrieval identity")
        require_utc(retrieved_at, "retrieval time")
        require_text(transformation_version, "transformation version")
        if product.provider != IG or product.instrument_id != sample.epic:
            raise ValueError("Sample must match the exact IG source product")
        if sample.resolution != RESOLUTION:
            raise ValueError("Only IG HOUR samples are supported")
        if approval is None and (
            completion or availability or interpreted_at is not None
        ):
            raise ValueError("Canonical evidence requires an approved policy")
        if interpreted_at is not None:
            require_utc(interpreted_at, "interpretation time")
            if interpreted_at < retrieved_at:
                raise ValueError("Interpretation time precedes retrieval")
        if approval is not None:
            if not isinstance(approval, ApprovedTimestampPolicy):
                raise ValueError(
                    "Canonicalization requires an approved timestamp policy"
                )
            if approval.mapping.product != product:
                raise ValueError(
                    "Timestamp policy approval must match the source product"
                )
        records = [_source_record(row) for row in sample.observations]
        metadata = asdict(sample.allowance) if sample.allowance else {}
        fingerprint = _digest([record["fingerprint"] for record in records])

        transaction = (
            self._sessions.begin() if session is None else nullcontext(session)
        )
        with transaction as session:
            product_id = self._ensure_product(session, product)
            key = dict(id=retrieval_id)
            values = dict(
                product_id=product_id,
                resolution=sample.resolution,
                timestamp_field=IG_TIMESTAMP_FIELD,
                retrieved_at=retrieved_at,
                response_metadata=metadata,
                fingerprint=fingerprint,
            )
            if _insert_once(session, RetrievalRow, key, **values) is not None:
                if records:
                    session.execute(
                        insert(ObservationRow),
                        [
                            dict(retrieval_id=retrieval_id, ordinal=index, **record)
                            for index, record in enumerate(records)
                        ],
                    )
            else:
                existing = session.get_one(RetrievalRow, retrieval_id)
                if any(
                    getattr(existing, name) != value for name, value in values.items()
                ):
                    raise PersistenceConflict(
                        "Retrieval ID reused for a different response"
                    )

            if approval is not None:
                self._store_interpretations(
                    session,
                    product_id,
                    sample,
                    retrieval_id,
                    retrieved_at,
                    approval,
                    transformation_version,
                    interpreted_at,
                    completion,
                    availability,
                )

    def source_revisions(
        self,
        product: SourceProduct,
        resolution: str,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> tuple[SourceRevision, ...]:
        """Read every revision with source label in [start, end)."""
        _validate_window(start, end, as_of)
        query = (
            select(RetrievalRow, ObservationRow)
            .join(ObservationRow)
            .join(ProductRow)
            .where(
                *self._product_conditions(product),
                RetrievalRow.resolution == resolution,
                ObservationRow.source_timestamp >= start,
                ObservationRow.source_timestamp < end,
            )
        )
        if as_of is not None:
            query = query.where(RetrievalRow.retrieved_at <= as_of)
        with self._sessions() as session:
            revisions = [
                _source_revision(product, retrieval, observation)
                for retrieval, observation in session.execute(query)
            ]
        return tuple(
            sorted(
                revisions,
                key=lambda row: (
                    row.source_timestamp,
                    row.retrieved_at,
                    row.retrieval_id,
                    row.ordinal,
                ),
            )
        )

    def canonical_history(
        self,
        mapping: InstrumentMapping,
        *,
        policy_name: str,
        transformation_version: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> StoredHistory:
        """Read the latest eligible recorded interpretation per H1 interval."""
        _validate_window(start, end, as_of)
        require_text(policy_name, "timestamp policy")
        require_text(transformation_version, "transformation version")
        query = (
            select(
                InterpretationRow, ObservationRow, RetrievalRow, PolicyRow, MappingRow
            )
            .join_from(InterpretationRow, ObservationRow)
            .join_from(ObservationRow, RetrievalRow)
            .join_from(InterpretationRow, PolicyRow)
            .join_from(PolicyRow, MappingRow)
            .join_from(MappingRow, ProductRow)
            .where(
                *self._product_conditions(mapping.product),
                MappingRow.version == mapping.version,
                MappingRow.instrument == mapping.instrument.symbol,
                RetrievalRow.product_id == MappingRow.product_id,
                PolicyRow.name == policy_name,
                InterpretationRow.transformation_version == transformation_version,
                InterpretationRow.start >= start,
                InterpretationRow.start < end,
            )
            .order_by(InterpretationRow.start, RetrievalRow.retrieved_at.desc())
        )
        if as_of is not None:
            query = query.where(
                RetrievalRow.retrieved_at <= as_of,
                InterpretationRow.interpreted_at <= as_of,
            )
        with self._sessions() as session:
            selected: dict[datetime, CanonicalRevision] = {}
            for row, observation, retrieval, policy, registered in session.execute(
                query
            ):
                previous = selected.get(row.start)
                key = (retrieval.retrieved_at, retrieval.id, observation.ordinal)
                if previous is not None and key <= (
                    previous.source.retrieved_at,
                    previous.source.retrieval_id,
                    previous.source.ordinal,
                ):
                    continue
                source = _source_revision(mapping.product, retrieval, observation)
                bar = SourceBar(
                    mapping=mapping,
                    resolution=Resolution.H1,
                    start=row.start,
                    end=row.end,
                    bid=_bar_prices(source.bid),
                    ask=_bar_prices(source.ask),
                    source_time=SourceTimestamp(
                        source.timestamp_field,
                        source.source_timestamp,
                        TimestampPolicy(
                            policy.name,
                            TimestampMeaning(policy.meaning),
                            policy.evidence,
                        ),
                        source.raw_timestamp,
                    ),
                    retrieved_at=source.retrieved_at,
                    transformation_version=row.transformation_version,
                    completion=Completion(
                        CompletionState(row.completion_state), row.completion_evidence
                    ),
                    availability=Availability(
                        row.first_available_at, row.availability_evidence
                    ),
                )
                selected[row.start] = CanonicalRevision(
                    bar,
                    source,
                    registered.provenance,
                    policy.approval,
                    row.interpreted_at,
                )
        return StoredHistory(tuple(selected.values()))

    @staticmethod
    def _product_conditions(product: SourceProduct) -> tuple[Any, ...]:
        return (
            ProductRow.provider == product.provider,
            ProductRow.instrument_id == product.instrument_id,
            ProductRow.variant == product.variant,
        )

    @staticmethod
    def _ensure_product(session: Session, product: SourceProduct) -> int:
        if product.provider != IG:
            raise ValueError("Only IG source products are supported")
        validate_epic(product.instrument_id)
        product_id = _ensure_immutable(
            session,
            ProductRow,
            dict(
                provider=product.provider,
                instrument_id=product.instrument_id,
                variant=product.variant,
            ),
        )
        assert isinstance(product_id, int)
        return product_id

    @staticmethod
    def _ensure_policy(
        session: Session, product_id: int, approval: ApprovedTimestampPolicy
    ) -> int:
        mapping = approval.mapping
        mapping_id = session.scalar(
            select(MappingRow.id).filter_by(
                product_id=product_id,
                version=mapping.version,
                instrument=mapping.instrument.symbol,
            )
        )
        if mapping_id is None:
            raise ValueError("Canonicalization requires the registered mapping")
        policy = approval.policy
        policy_id = _ensure_immutable(
            session,
            PolicyRow,
            dict(mapping_id=mapping_id, name=policy.name),
            meaning=policy.meaning.value,
            evidence=policy.evidence,
            approval=approval.approval,
        )
        assert isinstance(policy_id, int)
        return policy_id

    @staticmethod
    def _store_interpretations(
        session: Session,
        product_id: int,
        sample: IGPriceSample,
        retrieval_id: str,
        retrieved_at: datetime,
        approval: ApprovedTimestampPolicy,
        transformation_version: str,
        interpreted_at: datetime | None,
        completion: Mapping[datetime, Completion] | None,
        availability: Mapping[datetime, Availability] | None,
    ) -> None:
        mapping = approval.mapping
        policy = approval.policy
        policy_id = MarketDataRepository._ensure_policy(session, product_id, approval)
        bars = adapt_ig_sample(
            sample,
            mappings=(mapping,),
            retrieved_at=retrieved_at,
            timestamp_policy=policy,
            completion=completion,
            availability=availability,
        )
        timing = {} if interpreted_at is None else dict(interpreted_at=interpreted_at)
        defaults = dict(interpreted_at=datetime.now(UTC)) if not timing else None
        for ordinal, bar in enumerate(bars):
            _ensure_immutable(
                session,
                InterpretationRow,
                dict(
                    retrieval_id=retrieval_id,
                    ordinal=ordinal,
                    policy_id=policy_id,
                    transformation_version=transformation_version,
                ),
                insert_defaults=defaults,
                start=bar.start,
                end=bar.end,
                completion_state=bar.completion.state.value,
                completion_evidence=bar.completion.evidence,
                first_available_at=bar.availability.first_available_at,
                availability_evidence=bar.availability.evidence,
                **timing,
            )
