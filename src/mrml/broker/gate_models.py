"""Append-only account-gate transitions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mrml.broker.journal_models import ExecutionBase


class GateTransitionRow(ExecutionBase):
    __tablename__ = "broker_gate_transitions"
    __table_args__ = (
        UniqueConstraint("scope_id", "revision"),
        ForeignKeyConstraint(
            ["scope_id", "intent_id", "assessment_id"],
            [
                "broker_assessments.scope_id",
                "broker_assessments.intent_id",
                "broker_assessments.id",
            ],
        ),
        CheckConstraint(
            "previous_revision >= 0 AND revision = previous_revision + 1",
            name="ck_gate_transition_revision",
        ),
        CheckConstraint(
            "(reason = 'OPERATIONAL_RESTRICTION' AND claim_id IS NULL "
            "AND intent_id IS NULL AND assessment_id IS NULL "
            "AND positions_id IS NULL AND orders_id IS NULL) OR "
            "(reason IN ('OPENING_RESOLVED', 'REJECTION_RESOLVED') "
            "AND claim_id IS NOT NULL "
            "AND intent_id IS NOT NULL AND assessment_id IS NOT NULL "
            "AND positions_id IS NOT NULL AND orders_id IS NOT NULL)",
            name="ck_gate_transition_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(ForeignKey("execution_accounts.id"))
    revision: Mapped[int]
    previous_revision: Mapped[int]
    reason: Mapped[str] = mapped_column(String(32))
    before_blockers: Mapped[list[str]] = mapped_column(JSON)
    after_blockers: Mapped[list[str]] = mapped_column(JSON)
    claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("execution_claims.id"))
    intent_id: Mapped[UUID | None]
    assessment_id: Mapped[UUID | None]
    positions_id: Mapped[UUID | None]
    orders_id: Mapped[UUID | None]
    actor: Mapped[str] = mapped_column(String(128))
    policy: Mapped[str] = mapped_column(String(32))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
