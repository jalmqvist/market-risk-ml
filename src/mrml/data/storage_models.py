"""SQLAlchemy schema for source observations and canonical H1 storage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from mrml.data.canonical import require_utc


class UTCInstant(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is not None:
            require_utc(value, "stored timestamp")
        return value

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ExactPrice(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric())

    def process_bind_param(
        self, value: Decimal | None, dialect: Dialect
    ) -> Decimal | str | None:
        return str(value) if value is not None and dialect.name == "sqlite" else value

    def process_result_value(
        self, value: Decimal | str | None, dialect: Dialect
    ) -> Decimal | None:
        return Decimal(value) if value is not None else None


class MarketDataBase(DeclarativeBase):
    pass


class ProductRow(MarketDataBase):
    __tablename__ = "market_data_products"
    __table_args__ = (
        UniqueConstraint(
            "provider", "instrument_id", "variant", name="uq_md_product_identity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[str] = mapped_column(Text)
    variant: Mapped[str] = mapped_column(Text)


class MappingRow(MarketDataBase):
    __tablename__ = "market_data_mappings"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_md_mapping_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("market_data_products.id"))
    version: Mapped[str] = mapped_column(Text)
    instrument: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(Text)


class RetrievalRow(MarketDataBase):
    __tablename__ = "market_data_retrievals"
    __table_args__ = (
        Index(
            "ix_md_retrieval_product_history",
            "product_id",
            "resolution",
            "retrieved_at",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("market_data_products.id"))
    resolution: Mapped[str] = mapped_column(Text)
    timestamp_field: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(UTCInstant())
    response_metadata: Mapped[dict[str, int]] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(Text)


class ObservationRow(MarketDataBase):
    __tablename__ = "market_data_observations"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_md_observation_ordinal"),
        Index("ix_md_observation_source_label", "source_timestamp", "retrieval_id"),
    )

    retrieval_id: Mapped[str] = mapped_column(
        ForeignKey("market_data_retrievals.id"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    raw_timestamp: Mapped[str] = mapped_column(Text)
    source_timestamp: Mapped[datetime] = mapped_column(UTCInstant())
    fingerprint: Mapped[str] = mapped_column(Text)
    bid_open: Mapped[Decimal] = mapped_column(ExactPrice())
    bid_high: Mapped[Decimal] = mapped_column(ExactPrice())
    bid_low: Mapped[Decimal] = mapped_column(ExactPrice())
    bid_close: Mapped[Decimal] = mapped_column(ExactPrice())
    ask_open: Mapped[Decimal] = mapped_column(ExactPrice())
    ask_high: Mapped[Decimal] = mapped_column(ExactPrice())
    ask_low: Mapped[Decimal] = mapped_column(ExactPrice())
    ask_close: Mapped[Decimal] = mapped_column(ExactPrice())


class PolicyRow(MarketDataBase):
    __tablename__ = "market_data_timestamp_policies"
    __table_args__ = (
        UniqueConstraint("mapping_id", "name", name="uq_md_policy_name"),
        CheckConstraint(
            "meaning IN ('INTERVAL_START', 'INTERVAL_END')", name="ck_md_policy_meaning"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_id: Mapped[int] = mapped_column(ForeignKey("market_data_mappings.id"))
    name: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    approval: Mapped[str] = mapped_column(Text)


class InterpretationRow(MarketDataBase):
    __tablename__ = "market_data_h1_interpretations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["retrieval_id", "ordinal"],
            [
                "market_data_observations.retrieval_id",
                "market_data_observations.ordinal",
            ],
            name="fk_md_h1_observation",
        ),
        Index("ix_md_h1_history", "policy_id", "transformation_version", "start"),
        CheckConstraint('"end" > start', name="ck_md_h1_interval"),
        CheckConstraint(
            "completion_state IN ('COMPLETE', 'INCOMPLETE', 'UNKNOWN')",
            name="ck_md_h1_completion_state",
        ),
        CheckConstraint(
            "completion_state = 'UNKNOWN' OR completion_evidence IS NOT NULL",
            name="ck_md_h1_completion_evidence",
        ),
        CheckConstraint(
            "first_available_at IS NULL OR availability_evidence IS NOT NULL",
            name="ck_md_h1_availability_evidence",
        ),
    )

    retrieval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("market_data_timestamp_policies.id"), primary_key=True
    )
    transformation_version: Mapped[str] = mapped_column(Text, primary_key=True)
    interpreted_at: Mapped[datetime] = mapped_column(UTCInstant())
    start: Mapped[datetime] = mapped_column(UTCInstant())
    end: Mapped[datetime] = mapped_column(UTCInstant())
    completion_state: Mapped[str] = mapped_column(Text)
    completion_evidence: Mapped[str | None] = mapped_column(Text)
    first_available_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    availability_evidence: Mapped[str | None] = mapped_column(Text)
