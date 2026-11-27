"""Additive execution journal schema, independent of the legacy order tables."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mrml.journal.models import Run


class ExecutionBase(DeclarativeBase):
    pass


class AccountRow(ExecutionBase):
    __tablename__ = "execution_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "environment", "account_id"),
        CheckConstraint("environment = 'demo'", name="ck_execution_demo"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    environment: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(128))


class IntentRow(ExecutionBase):
    __tablename__ = "execution_intents"
    __table_args__ = (
        UniqueConstraint("scope_id", "id"),
        UniqueConstraint("scope_id", "idempotency_key"),
        UniqueConstraint("scope_id", "request_reference"),
        CheckConstraint(
            "operation IN ('OPEN', 'CLOSE')", name="ck_execution_operation"
        ),
        CheckConstraint("quantity > 0", name="ck_execution_positive_quantity"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(ForeignKey("execution_accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(16))
    request: Mapped[dict[str, str | bool | int | None]] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    request_reference: Mapped[str | None] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    level: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    stop_level: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    stop_distance: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    limit_level: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    limit_distance: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    authorization_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(ForeignKey(Run.__table__.c.id))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceRow(ExecutionBase):
    __tablename__ = "execution_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        UniqueConstraint("scope_id", "intent_id", "id"),
        UniqueConstraint("scope_id", "intent_id", "id", "kind"),
        UniqueConstraint("scope_id", "deduplication_key"),
        CheckConstraint(
            "kind IN ('DISPATCH_CLAIM', 'ACKNOWLEDGEMENT', 'CONFIRMATION', "
            "'EXPOSURE', 'ADMIN_REVIEW', 'CONFLICT')",
            name="ck_execution_evidence_kind",
        ),
        CheckConstraint(
            "(kind = 'ADMIN_REVIEW') = (actor_id IS NOT NULL)",
            name="ck_execution_review_actor",
        ),
        CheckConstraint(
            "(broker_id IS NULL) = (broker_role IS NULL)",
            name="ck_execution_evidence_identity",
        ),
        CheckConstraint(
            "broker_role IN ('RETURNED_REFERENCE', 'TRANSACTION_DEAL', "
            "'AFFECTED_DEAL', 'POSITION', 'WORKING_ORDER')",
            name="ck_execution_broker_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column()
    intent_id: Mapped[UUID] = mapped_column()
    deduplication_key: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))
    broker_id: Mapped[str | None] = mapped_column(String(128))
    broker_role: Mapped[str | None] = mapped_column(String(24))
    actor_id: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClaimRow(ExecutionBase):
    __tablename__ = "execution_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        ForeignKeyConstraint(
            ["scope_id", "intent_id", "id", "event_kind"],
            [
                "execution_evidence.scope_id",
                "execution_evidence.intent_id",
                "execution_evidence.id",
                "execution_evidence.kind",
            ],
        ),
        ForeignKeyConstraint(
            ["scope_id", "intent_id", "baseline_id"],
            [
                "execution_evidence.scope_id",
                "execution_evidence.intent_id",
                "execution_evidence.id",
            ],
        ),
        UniqueConstraint("scope_id", "claim_number", name="uq_execution_claim_number"),
        CheckConstraint("claim_number > 0", name="ck_execution_claim_number"),
        UniqueConstraint("intent_id"),
        CheckConstraint(
            "event_kind = 'DISPATCH_CLAIM'", name="ck_execution_claim_kind"
        ),
    )

    # Keep the legacy first-claim writer usable when migrating a 0005–0007 journal.
    __mapper_args__ = {"eager_defaults": False}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column()
    intent_id: Mapped[UUID] = mapped_column()
    claim_number: Mapped[int] = mapped_column(
        Integer, server_default="1", deferred=True
    )
    event_kind: Mapped[str] = mapped_column(String(24), server_default="DISPATCH_CLAIM")
    baseline_id: Mapped[UUID | None] = mapped_column()
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
