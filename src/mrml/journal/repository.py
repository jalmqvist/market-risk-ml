"""Journal repository interface backed by SQLAlchemy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from mrml.journal.models import (
    ConfigSnapshot,
    Fill,
    Order,
    RiskDecisionRow,
    Run,
    Signal,
)
from mrml.risk.controller import RiskDecision


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    created: bool


class JournalRepository:
    """Persist daily run metadata and strategy/risk audit records."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, database_url: str) -> JournalRepository:
        """Create a repository using a configured SQLAlchemy database URL."""
        return cls(create_engine(database_url, pool_pre_ping=True))

    def create_schema(self) -> None:
        """Create tables for local tests/development; deployments use Alembic."""
        from mrml.journal.models import Base

        Base.metadata.create_all(self._engine)

    def create_run(
        self,
        *,
        run_date: date,
        mode: str,
        config_hash: str,
        bundle_hash: str,
        code_version: str,
        config_snapshot: dict[str, Any],
    ) -> RunRecord:
        run_id = str(uuid4())
        with self._sessions() as session, session.begin():
            existing = session.scalar(
                select(Run).where(Run.run_date == run_date, Run.mode == mode)
            )
            if existing:
                return RunRecord(existing.id, False)
            session.add(
                Run(
                    id=run_id,
                    run_date=run_date,
                    mode=mode,
                    config_hash=config_hash,
                    bundle_hash=bundle_hash,
                    code_version=code_version,
                    status="running",
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                ConfigSnapshot(
                    id=str(uuid4()),
                    run_id=run_id,
                    config_hash=config_hash,
                    snapshot=config_snapshot,
                    created_at=datetime.now(UTC),
                )
            )
        return RunRecord(run_id, True)

    def record_signal(
        self,
        run_id: str,
        *,
        pair: str,
        composition: str,
        direction: str,
        evaluation_id: str,
        candidate_rank: int,
        rationale: str,
    ) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                Signal(
                    id=str(uuid4()),
                    run_id=run_id,
                    pair=pair,
                    composition=composition,
                    direction=direction,
                    evaluation_id=evaluation_id,
                    candidate_rank=candidate_rank,
                    rationale=rationale,
                )
            )

    def record_risk_decision(
        self, run_id: str, pair: str, decision: RiskDecision
    ) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                RiskDecisionRow(
                    id=str(uuid4()),
                    run_id=run_id,
                    pair=pair,
                    allowed=decision.allowed,
                    reasons=list(decision.reasons),
                    risk_amount=decision.risk_amount,
                    units=decision.units,
                )
            )

    def record_order(
        self, run_id: str, *, client_order_id: str, status: str, payload: dict[str, Any]
    ) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                Order(
                    id=str(uuid4()),
                    run_id=run_id,
                    client_order_id=client_order_id,
                    status=status,
                    payload=payload,
                )
            )

    def record_fill(
        self,
        run_id: str,
        order_id: str,
        *,
        units: float,
        price: float,
        slippage: float | None,
    ) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                Fill(
                    id=str(uuid4()),
                    run_id=run_id,
                    order_id=order_id,
                    units=units,
                    price=price,
                    slippage=slippage,
                )
            )

    def finish_run(self, run_id: str, *, status: str, summary: dict[str, Any]) -> None:
        with self._sessions() as session, session.begin():
            run = session.get(Run, run_id)
            if run is None:
                raise ValueError(f"unknown run id {run_id}")
            run.status = status
            run.summary = summary
