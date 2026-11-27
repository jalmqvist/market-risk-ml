"""Persist normalized broker reads and replay explicit opening assessments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeVar
from typing import cast as typed_cast
from uuid import UUID

from sqlalchemy import Text, bindparam, cast, insert, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from mrml.broker.evidence import (
    Acknowledgement,
    ActivityOpening,
    AffectedDeal,
    Confirmation,
    Correlation,
    CorrelationStatus,
    OpeningEvidence,
    OpeningIntent,
    Position,
    PositionRead,
    Provenance,
    Scope,
    WorkingOrder,
    WorkingOrderRead,
    correlate_opening,
)
from mrml.broker.journal import ExecutionJournal, JournalConflict, _amount, _opaque
from mrml.broker.journal_models import IntentRow
from mrml.broker.observation_models import (
    AssessmentInputRow,
    AssessmentRow,
    ObservationRow,
)

OPENING_ALGORITHM = "opening-v1"
_BUDGET = 100
_AMOUNTS = {
    "quantity",
    "level",
    "stop_level",
    "limit_level",
    "open_level",
    "order_level",
}

CollectionStatus = Literal["COMPLETE", "INCOMPLETE", "FAILED", "NOT_FOUND"]


@dataclass(frozen=True, repr=False)
class ConfirmationNotFound:
    provenance: Provenance
    requested_reference: str


TransactionEvidence = (
    Acknowledgement | Confirmation | ActivityOpening | ConfirmationNotFound
)
Observation = PositionRead | WorkingOrderRead | TransactionEvidence
_TRANSACTION_KINDS = {
    Acknowledgement: "ACKNOWLEDGEMENT",
    Confirmation: "CONFIRMATION",
    ActivityOpening: "ACTIVITY_OPENING",
    ConfirmationNotFound: "CONFIRMATION_NOT_FOUND",
}


@dataclass(frozen=True, repr=False)
class Collection:
    status: CollectionStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True, repr=False)
class StoredObservation:
    id: UUID
    intent_id: UUID | None
    value: Observation
    collection: Collection | None
    recorded_at: datetime


@dataclass(frozen=True, repr=False)
class AssessmentInputs:
    positions_id: UUID
    orders_id: UUID
    transaction_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, repr=False)
class Assessment:
    id: UUID
    inputs: AssessmentInputs
    algorithm: str
    evaluated_at: datetime
    max_age: timedelta
    result: Correlation


@dataclass(frozen=True, repr=False)
class Replay:
    stored: Assessment
    recomputed: Correlation

    @property
    def matches(self) -> bool:
        return self.stored.result == self.recomputed


class ReplayUnavailable(LookupError):
    """A stored assessment cannot be reproduced from supported inputs."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Broker observations require timezone-aware timestamps")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


def _exact_json(value: Any) -> str:
    """Encode only the normalized schema, using numeric JSON tokens for Decimals."""
    if isinstance(value, Decimal):
        return _amount(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{json.dumps(key)}:{_exact_json(value[key])}" for key in sorted(value)
            )
            + "}"
        )
    if isinstance(value, (tuple, list)):
        return "[" + ",".join(_exact_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _digest(row: ObservationRow, payload: Any) -> str:
    record = {
        "observation_id": str(row.id),
        "scope_id": str(row.scope_id),
        "original_id": row.original_id,
        "intent_id": str(row.intent_id) if row.intent_id is not None else None,
        "kind": row.kind,
        "source": row.source,
        "observed_at": _utc(row.observed_at).isoformat(),
        "started_at": _utc(row.started_at).isoformat() if row.started_at else None,
        "ended_at": _utc(row.ended_at).isoformat() if row.ended_at else None,
        "collection_status": row.collection_status,
        "target_deal_id": row.target_deal_id,
        "member_count": row.member_count,
        "schema_version": row.schema_version,
        "payload": payload,
    }
    return hashlib.sha256(_exact_json(record).encode()).hexdigest()


def _facts(value: Position | WorkingOrder | TransactionEvidence) -> dict[str, Any]:
    """Validate scalar terms before they enter the normalized document."""
    if isinstance(value, (Acknowledgement, Confirmation)):
        _opaque(value.returned_reference, 128)
    elif isinstance(value, ActivityOpening):
        _opaque(value.reference, 128)
        _opaque(value.affected_deal_id, 128)
    elif isinstance(value, ConfirmationNotFound):
        _opaque(value.requested_reference, 128)
    if isinstance(value, Confirmation):
        _opaque(value.outcome, 64)
        if value.affected_deals is not None and any(
            type(leg) is not AffectedDeal for leg in value.affected_deals
        ):
            raise ValueError("Invalid affected-deal evidence")
    result = asdict(value)
    result.pop("provenance", None)
    for key, item in result.items():
        if item is None:
            continue
        if key in _AMOUNTS:
            _amount(item)
        elif key == "affected_deals":
            if len(item) > _BUDGET:
                raise ValueError("Too many affected deals")
            for leg in item:
                _opaque(leg["deal_id"], 128)
                _opaque(leg["effect"], 64)
        elif key == "definitive_rejection":
            if type(item) is not bool:
                raise ValueError("Invalid confirmation classification")
        else:
            _opaque(item, 128)
    return result


def _collection_data(
    value: PositionRead | WorkingOrderRead,
    collection: Collection,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    status = collection.status
    if status not in ("COMPLETE", "INCOMPLETE", "FAILED", "NOT_FOUND"):
        raise ValueError("Invalid collection status")
    if value.complete is not (status in ("COMPLETE", "NOT_FOUND")):
        raise ValueError("Completeness conflicts with collection status")
    rows: tuple[Position | WorkingOrder, ...]
    if isinstance(value, PositionRead):
        kind, target, rows = "POSITIONS", value.target_deal_id, value.positions
        if any(type(row) is not Position for row in value.positions):
            raise ValueError("Invalid position collection member")
    else:
        kind, target, rows = "WORKING_ORDERS", None, value.orders
        if any(type(row) is not WorkingOrder for row in value.orders):
            raise ValueError("Invalid working-order collection member")
    if status == "NOT_FOUND" and target is None:
        raise ValueError("Not-found requires an individual position target")
    if target is not None:
        _opaque(target, 128)
        if len(rows) > 1 or any(row.deal_id != target for row in rows):
            raise ValueError(
                "Individual position coverage conflicts with returned rows"
            )
        if status == "COMPLETE" and not rows:
            raise ValueError("Empty individual position requires a not-found status")
    if len(rows) > _BUDGET or (rows and status in ("FAILED", "NOT_FOUND")):
        raise ValueError("Collection members conflict with limits or status")
    for row in rows:
        _opaque(row.deal_id, 128)
        _opaque(row.instrument, 128)
        if row.side is None or row.quantity is None:
            raise ValueError("Collection member requires direction and quantity")
    return kind, target, [_facts(row) for row in rows]


def _decode(row: ObservationRow, payload: Any, scope: Scope) -> StoredObservation:
    if row.schema_version != 1 or _digest(row, payload) != row.digest:
        raise ReplayUnavailable("Stored observation version or integrity mismatch")
    provenance = Provenance(row.original_id, scope, _utc(row.observed_at), row.source)
    value: Observation
    collection = None
    if row.collection_status is not None:
        if not isinstance(payload, list) or len(payload) != row.member_count:
            raise ReplayUnavailable("Stored collection membership mismatch")
        collection = Collection(
            typed_cast(CollectionStatus, row.collection_status),
            _optional_utc(row.started_at),
            _optional_utc(row.ended_at),
        )
        complete = collection.status in ("COMPLETE", "NOT_FOUND")
        if row.kind == "POSITIONS":
            value = PositionRead(
                provenance,
                complete,
                tuple(Position(**item) for item in payload),
                row.target_deal_id,
            )
        else:
            value = WorkingOrderRead(
                provenance,
                complete,
                tuple(WorkingOrder(**item) for item in payload),
            )
    elif row.kind == "CONFIRMATION":
        if payload["affected_deals"] is not None:
            payload["affected_deals"] = tuple(
                AffectedDeal(**leg) for leg in payload["affected_deals"]
            )
        value = Confirmation(provenance=provenance, **payload)
    elif row.kind == "ACKNOWLEDGEMENT":
        value = Acknowledgement(provenance=provenance, **payload)
    elif row.kind == "ACTIVITY_OPENING":
        value = ActivityOpening(provenance=provenance, **payload)
    elif row.kind == "CONFIRMATION_NOT_FOUND":
        value = ConfirmationNotFound(provenance=provenance, **payload)
    else:
        raise ReplayUnavailable("Unsupported stored observation type")
    return StoredObservation(
        row.id, row.intent_id, value, collection, _utc(row.recorded_at)
    )


T = TypeVar("T", bound=Observation)


def _one(rows: list[Observation], kind: type[T]) -> T | None:
    chosen = [row for row in rows if isinstance(row, kind)]
    if len(chosen) > 1:
        raise ReplayUnavailable("Assessment has ambiguous singleton evidence")
    return chosen[0] if chosen else None


def _age_us(value: timedelta) -> int:
    micros = value // timedelta(microseconds=1)
    if not 0 < micros < 2**63:
        raise ValueError("Assessment max age must be positive and representable")
    return micros


class ObservationJournal(ExecutionJournal):
    """Storage and offline replay for presupplied, scoped broker facts."""

    def record_collection(
        self,
        identity: UUID,
        value: PositionRead | WorkingOrderRead,
        collection: Collection,
    ) -> StoredObservation:
        if not isinstance(value, (PositionRead, WorkingOrderRead)):
            raise ValueError("Invalid collection type")
        kind, target, members = _collection_data(value, collection)
        start, end = (
            _optional_utc(collection.started_at),
            _optional_utc(collection.ended_at),
        )
        observed = _utc(value.provenance.observed_at)
        if (start is None) != (end is None) or (
            start is not None and end is not None and not start <= end <= observed
        ):
            raise ValueError("Invalid broker collection interval")
        with self._transaction() as session:
            account = self._ensure_account(session, value.provenance.scope)
            row = ObservationRow(
                id=identity,
                scope_id=account.id,
                kind=kind,
                schema_version=1,
                started_at=start,
                ended_at=end,
                collection_status=collection.status,
                target_deal_id=target,
                member_count=len(members),
            )
            return self._record(session, row, value.provenance, members)

    def record_transaction(
        self,
        identity: UUID,
        intent_id: UUID,
        value: TransactionEvidence,
    ) -> StoredObservation:
        kind = _TRANSACTION_KINDS.get(type(value))
        if kind is None:
            raise ValueError("Unsupported transaction evidence type")
        payload = _facts(value)
        provenance = value.provenance
        with self._transaction() as session:
            intent = self._scoped_intent(
                session, provenance.scope, intent_id, lock=True
            )
            row = ObservationRow(
                id=identity,
                scope_id=intent.scope_id,
                intent_id=intent_id,
                kind=kind,
                schema_version=1,
            )
            return self._record(session, row, provenance, payload)

    def read_observation(self, scope: Scope, identity: UUID) -> StoredObservation:
        with self._transaction() as session:
            account = self._account(session, scope)
            if account is None:
                raise ReplayUnavailable("Observation unavailable in the intended scope")
            return self._load(session, scope, account.id, identity)

    def assess_opening(
        self,
        identity: UUID,
        scope: Scope,
        intent_id: UUID,
        inputs: AssessmentInputs,
        *,
        evaluated_at: datetime,
        max_age: timedelta,
    ) -> Assessment:
        evaluated_at = _utc(evaluated_at)
        micros = _age_us(max_age)
        if len(inputs.transaction_ids) != len(set(inputs.transaction_ids)):
            raise ValueError("Assessment evidence must be listed once")
        if len(inputs.transaction_ids) > _BUDGET:
            raise ValueError("Assessment evidence exceeds the record budget")
        with self._transaction() as session:
            intent = self._scoped_intent(session, scope, intent_id, lock=True)
            result = self._evaluate(
                session, scope, intent, inputs, evaluated_at, max_age
            )
            candidate = Assessment(
                identity, inputs, OPENING_ALGORITHM, evaluated_at, max_age, result
            )
            existing = session.scalar(
                select(AssessmentRow).where(
                    AssessmentRow.id == identity,
                    AssessmentRow.scope_id == intent.scope_id,
                    AssessmentRow.intent_id == intent.id,
                )
            )
            if existing is not None:
                if self._assessment(session, existing) == candidate:
                    return candidate
                raise JournalConflict("Assessment identity conflict")
            session.add(
                AssessmentRow(
                    id=identity,
                    scope_id=intent.scope_id,
                    intent_id=intent.id,
                    positions_id=inputs.positions_id,
                    orders_id=inputs.orders_id,
                    transaction_count=len(inputs.transaction_ids),
                    algorithm=OPENING_ALGORITHM,
                    evaluated_at=evaluated_at,
                    max_age_us=micros,
                    status=result.status,
                    reason=result.reason,
                    confirmation_outcome=result.confirmation_outcome,
                    evidence_ids=list(result.evidence_ids),
                )
            )
            session.flush()
            session.add_all(
                AssessmentInputRow(
                    assessment_id=identity,
                    ordinal=index,
                    scope_id=intent.scope_id,
                    intent_id=intent.id,
                    observation_id=observation_id,
                )
                for index, observation_id in enumerate(inputs.transaction_ids)
            )
            return candidate

    def replay(self, scope: Scope, identity: UUID) -> Replay:
        with self._transaction() as session:
            account = self._account(session, scope)
            row = (
                None
                if account is None
                else session.scalar(
                    select(AssessmentRow).where(
                        AssessmentRow.id == identity,
                        AssessmentRow.scope_id == account.id,
                    )
                )
            )
            if row is None:
                raise ReplayUnavailable("Assessment unavailable in the intended scope")
            return self._replay_row(session, scope, row)

    def _replay_row(self, session: Session, scope: Scope, row: AssessmentRow) -> Replay:
        if row.algorithm != OPENING_ALGORITHM:
            raise ReplayUnavailable("Unsupported assessment algorithm version")
        stored = self._assessment(session, row)
        intent = self._scoped_intent(session, scope, row.intent_id)
        result = self._evaluate(
            session,
            scope,
            intent,
            stored.inputs,
            stored.evaluated_at,
            stored.max_age,
        )
        return Replay(stored, result)

    @staticmethod
    def _record(
        session: Session,
        row: ObservationRow,
        provenance: Provenance,
        payload: Any,
    ) -> StoredObservation:
        _opaque(provenance.evidence_id, 128)
        _opaque(provenance.source, 128)
        row.original_id = provenance.evidence_id
        row.source = provenance.source
        row.observed_at = _utc(provenance.observed_at)
        row.recorded_at = datetime.now(UTC)
        row.digest = _digest(row, payload)
        existing = list(
            session.scalars(
                select(ObservationRow).where(
                    ObservationRow.scope_id == row.scope_id,
                    (ObservationRow.id == row.id)
                    | (ObservationRow.original_id == row.original_id),
                )
            )
        )
        if existing:
            if (
                len(existing) != 1
                or existing[0].id != row.id
                or existing[0].original_id != row.original_id
                or existing[0].digest != row.digest
            ):
                raise JournalConflict("Broker observation identity conflict")
            return ObservationJournal._load(
                session, provenance.scope, row.scope_id, row.id
            )
        values = {
            column.name: getattr(row, column.name)
            for column in ObservationRow.__table__.columns
            if column.name != "payload"
        }
        session.execute(
            insert(ObservationRow).values(
                **values,
                payload=cast(
                    bindparam("normalized", _exact_json(payload), type_=Text), JSONB
                ),
            )
        )
        return ObservationJournal._load(session, provenance.scope, row.scope_id, row.id)

    @staticmethod
    def _load(
        session: Session,
        scope: Scope,
        scope_id: UUID,
        identity: UUID,
    ) -> StoredObservation:
        row = session.execute(
            select(ObservationRow, cast(ObservationRow.payload, Text)).where(
                ObservationRow.scope_id == scope_id,
                ObservationRow.id == identity,
            )
        ).one_or_none()
        if row is None:
            raise ReplayUnavailable("Observation unavailable in the intended scope")
        model, document = row
        try:
            return _decode(
                model,
                json.loads(document, parse_int=Decimal, parse_float=Decimal),
                scope,
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ReplayUnavailable(
                "Stored observation could not be reconstructed"
            ) from None

    @staticmethod
    def _assessment(session: Session, row: AssessmentRow) -> Assessment:
        links = session.execute(
            select(AssessmentInputRow.ordinal, AssessmentInputRow.observation_id)
            .where(
                AssessmentInputRow.scope_id == row.scope_id,
                AssessmentInputRow.intent_id == row.intent_id,
                AssessmentInputRow.assessment_id == row.id,
            )
            .order_by(AssessmentInputRow.ordinal)
        ).all()
        if [item.ordinal for item in links] != list(range(row.transaction_count)):
            raise ReplayUnavailable(
                "Stored assessment evidence associations are incomplete"
            )
        return Assessment(
            row.id,
            AssessmentInputs(
                row.positions_id,
                row.orders_id,
                tuple(item.observation_id for item in links),
            ),
            row.algorithm,
            _utc(row.evaluated_at),
            timedelta(microseconds=row.max_age_us),
            Correlation(
                CorrelationStatus(row.status),
                row.reason,
                str(row.intent_id),
                tuple(row.evidence_ids),
                row.confirmation_outcome,
            ),
        )

    def _evaluate(
        self,
        session: Session,
        scope: Scope,
        intent: IntentRow,
        inputs: AssessmentInputs,
        now: datetime,
        max_age: timedelta,
    ) -> Correlation:
        if intent.operation != "OPEN" or intent.request.get("version") != 1:
            raise ReplayUnavailable("Unsupported opening intent version or operation")
        instrument, side = intent.request.get("instrument"), intent.request.get("side")
        if not isinstance(instrument, str) or not isinstance(side, str):
            raise ReplayUnavailable("Stored opening intent is incomplete")
        opening = OpeningIntent(
            str(intent.id),
            scope,
            intent.request_reference,
            instrument,
            side,
            intent.quantity,
        )
        positions = self._load(
            session, scope, intent.scope_id, inputs.positions_id
        ).value
        orders = self._load(session, scope, intent.scope_id, inputs.orders_id).value
        if not isinstance(positions, PositionRead) or not isinstance(
            orders, WorkingOrderRead
        ):
            raise ReplayUnavailable(
                "Assessment collection types do not match their roles"
            )
        transactions = []
        for identity in inputs.transaction_ids:
            item = self._load(session, scope, intent.scope_id, identity)
            if item.intent_id != intent.id:
                raise ReplayUnavailable(
                    "Transaction observation is outside the intended action"
                )
            transactions.append(item.value)
        missing = _one(transactions, ConfirmationNotFound)
        if (
            missing is not None
            and missing.requested_reference != intent.request_reference
        ):
            raise ReplayUnavailable(
                "Not-found observation does not identify the issued reference"
            )
        return correlate_opening(
            opening,
            OpeningEvidence(
                positions=positions,
                working_orders=orders,
                confirmations=tuple(
                    item for item in transactions if isinstance(item, Confirmation)
                ),
                acknowledgement=_one(transactions, Acknowledgement),
                activity_openings=tuple(
                    item for item in transactions if isinstance(item, ActivityOpening)
                ),
                confirmation_not_found=missing.provenance
                if missing is not None
                else None,
            ),
            now=now,
            max_age=max_age,
        )
