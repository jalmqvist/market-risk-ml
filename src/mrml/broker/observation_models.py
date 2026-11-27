"""Immutable broker observations and exact reconciliation input associations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mrml.broker.journal_models import ExecutionBase


class ObservationRow(ExecutionBase):
    __tablename__ = "broker_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        UniqueConstraint("scope_id", "original_id"),
        UniqueConstraint("scope_id", "id", "kind"),
        UniqueConstraint("scope_id", "intent_id", "id"),
        CheckConstraint(
            "(kind IN ('POSITIONS', 'WORKING_ORDERS') AND intent_id IS NULL "
            "AND collection_status IS NOT NULL "
            "AND collection_status IN ('COMPLETE', 'INCOMPLETE', "
            "'FAILED', 'NOT_FOUND') "
            "AND member_count IS NOT NULL AND member_count BETWEEN 0 AND 100) OR "
            "(kind IN ('ACKNOWLEDGEMENT', 'CONFIRMATION', 'ACTIVITY_OPENING', "
            "'CONFIRMATION_NOT_FOUND') AND intent_id IS NOT NULL "
            "AND collection_status IS NULL AND member_count IS NULL "
            "AND target_deal_id IS NULL AND started_at IS NULL)",
            name="ck_broker_observation_shape",
        ),
        CheckConstraint(
            "((started_at IS NULL AND ended_at IS NULL) OR "
            "(started_at IS NOT NULL AND ended_at IS NOT NULL "
            "AND started_at <= ended_at AND ended_at <= observed_at))",
            name="ck_broker_collection_interval",
        ),
        CheckConstraint(
            "(target_deal_id IS NULL OR (kind = 'POSITIONS' AND member_count <= 1)) "
            "AND (collection_status <> 'NOT_FOUND' OR target_deal_id IS NOT NULL) "
            "AND (target_deal_id IS NULL OR collection_status <> 'COMPLETE' "
            "OR member_count = 1) "
            "AND (collection_status NOT IN ('FAILED', 'NOT_FOUND') "
            "OR member_count = 0)",
            name="ck_broker_collection_coverage",
        ),
        CheckConstraint("schema_version = 1", name="ck_broker_observation_version"),
        CheckConstraint(
            "CASE WHEN collection_status IS NULL THEN jsonb_typeof(payload) = 'object' "
            "WHEN jsonb_typeof(payload) = 'array' "
            "THEN jsonb_array_length(payload) = member_count ELSE false END",
            name="ck_broker_observation_document",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(ForeignKey("execution_accounts.id"))
    intent_id: Mapped[UUID | None]
    original_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_status: Mapped[str | None] = mapped_column(String(16))
    target_deal_id: Mapped[str | None] = mapped_column(String(128))
    member_count: Mapped[int | None]
    schema_version: Mapped[int]
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        deferred=True,
        deferred_raiseload=True,
    )


class AssessmentRow(ExecutionBase):
    __tablename__ = "broker_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        ForeignKeyConstraint(
            ["scope_id", "positions_id", "positions_kind"],
            [
                "broker_observations.scope_id",
                "broker_observations.id",
                "broker_observations.kind",
            ],
        ),
        ForeignKeyConstraint(
            ["scope_id", "orders_id", "orders_kind"],
            [
                "broker_observations.scope_id",
                "broker_observations.id",
                "broker_observations.kind",
            ],
        ),
        UniqueConstraint("scope_id", "intent_id", "id"),
        CheckConstraint(
            "positions_kind = 'POSITIONS' AND orders_kind = 'WORKING_ORDERS'",
            name="ck_broker_assessment_collection_kinds",
        ),
        CheckConstraint(
            "max_age_us > 0 AND transaction_count BETWEEN 0 AND 100",
            name="ck_broker_assessment_parameters",
        ),
        CheckConstraint(
            "status IN ('MATCHED', 'MISMATCH', 'UNRESOLVED')",
            name="ck_broker_assessment_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID]
    intent_id: Mapped[UUID]
    positions_id: Mapped[UUID]
    orders_id: Mapped[UUID]
    positions_kind: Mapped[str] = mapped_column(String(24), server_default="POSITIONS")
    orders_kind: Mapped[str] = mapped_column(
        String(24), server_default="WORKING_ORDERS"
    )
    transaction_count: Mapped[int]
    algorithm: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_age_us: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    confirmation_outcome: Mapped[str | None] = mapped_column(String(16))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)


class AssessmentInputRow(ExecutionBase):
    __tablename__ = "broker_assessment_inputs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "intent_id", "assessment_id"],
            [
                "broker_assessments.scope_id",
                "broker_assessments.intent_id",
                "broker_assessments.id",
            ],
        ),
        ForeignKeyConstraint(
            ["scope_id", "intent_id", "observation_id"],
            [
                "broker_observations.scope_id",
                "broker_observations.intent_id",
                "broker_observations.id",
            ],
        ),
        UniqueConstraint("assessment_id", "observation_id"),
        CheckConstraint("ordinal BETWEEN 0 AND 99", name="ck_broker_input_ordinal"),
    )

    assessment_id: Mapped[UUID] = mapped_column(primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID]
    intent_id: Mapped[UUID]
    observation_id: Mapped[UUID]
