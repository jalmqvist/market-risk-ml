"""PostgreSQL execution journal. A claim is a storage fact, never authorization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from mrml.broker.evidence import Scope
from mrml.broker.journal_models import AccountRow, ClaimRow, EvidenceRow, IntentRow

_AMOUNT_FIELDS = (
    "quantity",
    "level",
    "stop_level",
    "stop_distance",
    "limit_level",
    "limit_distance",
)
_EVIDENCE_ROLES = {
    "ACKNOWLEDGEMENT": {"RETURNED_REFERENCE"},
    "CONFIRMATION": {"RETURNED_REFERENCE", "TRANSACTION_DEAL", "AFFECTED_DEAL"},
    "EXPOSURE": {"POSITION", "WORKING_ORDER"},
    "ADMIN_REVIEW": set(),
}


class JournalConflict(ValueError):
    """A stored identity has incompatible contents."""


class JournalStorageError(RuntimeError):
    """A journal transaction failed without exposing database parameters."""


class DispatchHeld(JournalConflict):
    """The account cannot admit the proposed durable claim."""


@dataclass(frozen=True, repr=False)
class ApprovedRequest:
    operation: str
    instrument: str
    side: str
    quantity: Decimal
    order_type: str
    expiry: str
    request_reference: str | None = None
    target_deal_id: str | None = None
    currency: str | None = None
    force_open: bool | None = None
    guaranteed_stop: bool | None = None
    level: Decimal | None = None
    stop_level: Decimal | None = None
    stop_distance: Decimal | None = None
    limit_level: Decimal | None = None
    limit_distance: Decimal | None = None
    time_in_force: str | None = None


@dataclass(frozen=True, repr=False)
class ExecutionIntent:
    id: UUID
    scope: Scope
    idempotency_key: str
    operation: str
    canonical_request: str
    fingerprint: str
    request_reference: str | None
    quantity: Decimal
    authorization_id: str
    run_id: str | None
    created_at: datetime


@dataclass(frozen=True, repr=False)
class DispatchClaim:
    id: UUID
    intent_id: UUID
    scope: Scope
    claimed_at: datetime
    baseline_id: UUID | None


@dataclass(frozen=True, repr=False)
class EvidenceEvent:
    id: UUID
    deduplication_key: str
    kind: str
    reason: str
    observed_at: datetime
    broker_id: str | None = None
    broker_role: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    actor_id: str | None = None


@dataclass(frozen=True, repr=False)
class RecordedEvidence:
    event: EvidenceEvent
    recorded_at: datetime


def _opaque(value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError("Invalid execution journal identifier")


def _amount(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Execution amounts must be finite Decimals")
    if not value:
        return "0"
    if value.adjusted() >= 20 or value.adjusted() < -18:
        raise ValueError("Execution amount exceeds NUMERIC(38, 18)")
    whole, _, fractional = format(value, "f").partition(".")
    fractional = fractional.rstrip("0")
    if len(fractional) > 18:
        raise ValueError("Execution amount exceeds NUMERIC(38, 18)")
    return f"{whole}.{fractional}" if fractional else whole


def _canonical(request: ApprovedRequest) -> dict[str, str | bool | int | None]:
    if type(request) is not ApprovedRequest:
        raise ValueError("Unsupported approved request contract")
    if request.operation not in ("OPEN", "CLOSE"):
        raise ValueError("Unsupported execution operation")
    _opaque(request.instrument, 128)
    _opaque(request.expiry, 48)
    if request.side not in ("BUY", "SELL") or request.order_type not in (
        "MARKET",
        "LIMIT",
    ):
        raise ValueError("Unsupported execution direction or order type")
    if (request.level is not None) != (request.order_type == "LIMIT"):
        raise ValueError("Only limit requests require a level")
    if request.time_in_force not in (None, "FILL_OR_KILL", "EXECUTE_AND_ELIMINATE"):
        raise ValueError("Unsupported execution time in force")
    if request.operation == "OPEN":
        if (
            request.target_deal_id is not None
            or not isinstance(request.currency, str)
            or not re.fullmatch(r"[A-Z]{3}", request.currency)
            or type(request.force_open) is not bool
            or type(request.guaranteed_stop) is not bool
        ):
            raise ValueError("Incomplete or unsupported opening terms")
    else:
        if request.target_deal_id is None:
            raise ValueError("Closing requires an explicit target deal ID")
        _opaque(request.target_deal_id, 128)
        if any(
            getattr(request, name) is not None
            for name in (
                "currency",
                "force_open",
                "guaranteed_stop",
                *_AMOUNT_FIELDS[2:],
                "request_reference",
            )
        ):
            raise ValueError("Unsupported closing options")
    if request.request_reference is not None and (
        not isinstance(request.request_reference, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", request.request_reference)
    ):
        raise ValueError("Invalid allocated deal reference")
    if any(
        level is not None and distance is not None
        for level, distance in (
            (request.stop_level, request.stop_distance),
            (request.limit_level, request.limit_distance),
        )
    ):
        raise ValueError("Specify a protective level or distance once")

    payload = asdict(request)
    for name in _AMOUNT_FIELDS:
        value = payload[name]
        if value is not None:
            payload[name] = _amount(value)
    if payload["quantity"] is None or request.quantity <= 0:
        raise ValueError("Execution quantity must be positive")
    return {"version": 1, **payload}


def _json(payload: dict[str, str | bool | int | None]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_scope(scope: Scope) -> None:
    _opaque(scope.provider, 32)
    _opaque(scope.account_id, 128)
    if scope.environment != "demo":
        raise ValueError("Execution journal requires a demo scope")


def _validate_evidence(event: EvidenceEvent) -> None:
    _opaque(event.deduplication_key, 128)
    if event.deduplication_key.startswith(("claim:", "conflict:")):
        raise ValueError("Reserved execution evidence identity")
    if event.kind not in _EVIDENCE_ROLES:
        raise ValueError("Unsupported execution evidence type")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", event.reason):
        raise ValueError("Invalid execution evidence reason code")
    if event.observed_at.utcoffset() is None:
        raise ValueError("Execution evidence requires a timezone-aware observation")
    if (event.broker_id is None) != (event.broker_role is None):
        raise ValueError("Broker evidence requires an identity and role together")
    if event.broker_id is not None:
        _opaque(event.broker_id, 128)
        if event.broker_role not in _EVIDENCE_ROLES[event.kind]:
            raise ValueError("Unsupported broker evidence identity role")
    if (event.kind == "ADMIN_REVIEW") != (event.actor_id is not None):
        raise ValueError("Administrative evidence requires an operator identity")
    if event.actor_id is not None:
        _opaque(event.actor_id, 128)
    if event.kind == "ACKNOWLEDGEMENT" and event.broker_role != "RETURNED_REFERENCE":
        raise ValueError("Acknowledgement requires a returned reference")
    for value in (event.quantity, event.price):
        if value is not None:
            _amount(value)


def _intent(row: IntentRow, scope: Scope) -> ExecutionIntent:
    return ExecutionIntent(
        row.id,
        scope,
        row.idempotency_key,
        row.operation,
        _json(row.request),
        row.fingerprint,
        row.request_reference,
        row.quantity,
        row.authorization_id,
        row.run_id,
        row.created_at.astimezone(UTC),
    )


def _claim(row: ClaimRow, scope: Scope) -> DispatchClaim:
    return DispatchClaim(
        row.id, row.intent_id, scope, row.claimed_at.astimezone(UTC), row.baseline_id
    )


def _evidence(row: EvidenceRow) -> RecordedEvidence:
    return RecordedEvidence(
        EvidenceEvent(
            row.id,
            row.deduplication_key,
            row.kind,
            row.reason,
            row.observed_at.astimezone(UTC),
            row.broker_id,
            row.broker_role,
            row.quantity,
            row.price,
            row.actor_id,
        ),
        row.recorded_at.astimezone(UTC),
    )


class ExecutionJournal:
    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Execution journal requires PostgreSQL")
        self._sessions = sessionmaker(engine)

    def create_intent(
        self,
        scope: Scope,
        *,
        idempotency_key: str,
        request: ApprovedRequest,
        authorization_id: str,
        run_id: str | None = None,
    ) -> ExecutionIntent:
        _validate_scope(scope)
        _opaque(idempotency_key, 128)
        _opaque(authorization_id, 128)
        payload = _canonical(request)
        fingerprint = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._transaction() as session:
            account = self._ensure_account(session, scope)
            existing = session.scalar(
                select(IntentRow).where(
                    IntentRow.scope_id == account.id,
                    IntentRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.fingerprint == fingerprint and existing.request == payload:
                    return _intent(existing, scope)
                # Commit the incident before returning the conflict.
                incident = f"{existing.id}:{fingerprint}".encode()
                incident_key = f"conflict:{hashlib.sha256(incident).hexdigest()}"
                if (
                    session.scalar(
                        select(EvidenceRow.id).where(
                            EvidenceRow.scope_id == account.id,
                            EvidenceRow.deduplication_key == incident_key,
                        )
                    )
                    is None
                ):
                    self._append(
                        session,
                        existing,
                        EvidenceEvent(
                            uuid4(),
                            incident_key,
                            "CONFLICT",
                            "REQUEST_FINGERPRINT_CONFLICT",
                            datetime.now(UTC),
                        ),
                    )
            else:
                if (
                    request.request_reference is not None
                    and session.scalar(
                        select(IntentRow.id).where(
                            IntentRow.scope_id == account.id,
                            IntentRow.request_reference == request.request_reference,
                        )
                    )
                    is not None
                ):
                    raise JournalConflict(
                        "Allocated deal reference already exists in scope"
                    )
                row = IntentRow(
                    id=uuid4(),
                    scope_id=account.id,
                    idempotency_key=idempotency_key,
                    operation=request.operation,
                    request=payload,
                    fingerprint=fingerprint,
                    request_reference=request.request_reference,
                    authorization_id=authorization_id,
                    run_id=run_id,
                    created_at=datetime.now(UTC),
                    **{
                        name: Decimal(value)
                        if isinstance(value := payload[name], str)
                        else None
                        for name in _AMOUNT_FIELDS
                    },
                )
                session.add(row)
                session.flush()
                return _intent(row, scope)
        raise JournalConflict("Idempotency key has different approved request contents")

    def claim_dispatch(
        self,
        scope: Scope,
        intent_id: UUID,
        *,
        baseline_id: UUID | None = None,
    ) -> DispatchClaim:
        with self._transaction() as session:
            row = self._scoped_intent(session, scope, intent_id, lock=True)
            if row.operation != "OPEN" or session.scalar(
                select(ClaimRow.id).where(ClaimRow.scope_id == row.scope_id).limit(1)
            ):
                raise DispatchHeld(
                    "Only the first preparatory opening claim is enabled"
                )
            if (
                baseline_id is not None
                and session.scalar(
                    select(EvidenceRow.id).where(
                        EvidenceRow.scope_id == row.scope_id,
                        EvidenceRow.intent_id == row.id,
                        EvidenceRow.id == baseline_id,
                        EvidenceRow.kind == "EXPOSURE",
                    )
                )
                is None
            ):
                raise ValueError(
                    "Baseline must be exposure evidence of the scoped intent"
                )
            identity, now = uuid4(), datetime.now(UTC)
            self._append(
                session,
                row,
                EvidenceEvent(
                    identity,
                    f"claim:{identity}",
                    "DISPATCH_CLAIM",
                    "DISPATCH_CLAIMED",
                    now,
                ),
            )
            claim = ClaimRow(
                id=identity,
                scope_id=row.scope_id,
                intent_id=row.id,
                baseline_id=baseline_id,
                claimed_at=now,
            )
            session.add(claim)
            session.flush()
            return _claim(claim, scope)

    def append_evidence(
        self,
        scope: Scope,
        intent_id: UUID,
        event: EvidenceEvent,
    ) -> RecordedEvidence:
        _validate_evidence(event)
        event = replace(event, observed_at=event.observed_at.astimezone(UTC))
        with self._transaction() as session:
            intent = self._scoped_intent(session, scope, intent_id, lock=True)
            # The global UUID constraint also catches collisions in other scopes.
            existing = list(
                session.scalars(
                    select(EvidenceRow).where(
                        EvidenceRow.scope_id == intent.scope_id,
                        (EvidenceRow.id == event.id)
                        | (EvidenceRow.deduplication_key == event.deduplication_key),
                    )
                )
            )
            if existing:
                if (
                    len(existing) == 1
                    and existing[0].intent_id == intent.id
                    and _evidence(existing[0]).event == event
                ):
                    return _evidence(existing[0])
                raise JournalConflict("Execution evidence identity conflict")
            return _evidence(self._append(session, intent, event))

    def claim_history(self, scope: Scope) -> tuple[DispatchClaim, ...]:
        """Return all durable claims in their account admission order."""
        with self._transaction() as session:
            account = self._account(session, scope)
            return () if account is None else self._claims(session, scope, account.id)

    @staticmethod
    def _claims(
        session: Session, scope: Scope, account_id: UUID
    ) -> tuple[DispatchClaim, ...]:
        return tuple(
            _claim(row, scope)
            for row in session.scalars(
                select(ClaimRow)
                .where(ClaimRow.scope_id == account_id)
                .order_by(ClaimRow.claim_number)
            )
        )

    def account_hold(self, scope: Scope) -> DispatchClaim | None:
        """Return the original irreversible hold for legacy callers."""
        with self._transaction() as session:
            account = self._account(session, scope)
            if account is None:
                return None
            claim = session.scalar(
                select(ClaimRow)
                .where(ClaimRow.scope_id == account.id)
                .order_by(ClaimRow.claim_number)
                .limit(1)
            )
            return _claim(claim, scope) if claim is not None else None

    def recover_unresolved(self, scope: Scope) -> tuple[DispatchClaim, ...]:
        """Return the legacy hold. Use recover_gate for current uncertainty."""
        claim = self.account_hold(scope)
        return () if claim is None else (claim,)

    def read_intent(
        self,
        scope: Scope,
        intent_id: UUID,
    ) -> tuple[ExecutionIntent, tuple[RecordedEvidence, ...]]:
        with self._transaction() as session:
            row = self._scoped_intent(session, scope, intent_id)
            evidence = session.scalars(
                select(EvidenceRow)
                .where(
                    EvidenceRow.scope_id == row.scope_id,
                    EvidenceRow.intent_id == row.id,
                )
                .order_by(EvidenceRow.recorded_at, EvidenceRow.id)
            )
            return _intent(row, scope), tuple(_evidence(item) for item in evidence)

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        try:
            with self._sessions.begin() as session:
                yield session
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise JournalConflict("Execution journal uniqueness conflict") from None
            raise JournalStorageError("Execution journal transaction failed") from None
        except SQLAlchemyError:
            raise JournalStorageError("Execution journal transaction failed") from None

    def _ensure_account(self, session: Session, scope: Scope) -> AccountRow:
        _validate_scope(scope)
        session.execute(
            insert(AccountRow)
            .values(
                id=uuid4(),
                provider=scope.provider,
                environment=scope.environment,
                account_id=scope.account_id,
            )
            .on_conflict_do_nothing(
                index_elements=["provider", "environment", "account_id"]
            )
        )
        account = self._account(session, scope, lock=True)
        assert account is not None
        return account

    @staticmethod
    def _account(
        session: Session, scope: Scope, *, lock: bool = False
    ) -> AccountRow | None:
        _validate_scope(scope)
        query = select(AccountRow).where(
            AccountRow.provider == scope.provider,
            AccountRow.environment == scope.environment,
            AccountRow.account_id == scope.account_id,
        )
        return session.scalar(query.with_for_update() if lock else query)

    def _scoped_intent(
        self,
        session: Session,
        scope: Scope,
        intent_id: UUID,
        *,
        lock: bool = False,
    ) -> IntentRow:
        account = self._account(session, scope, lock=lock)
        if account is not None:
            row = session.scalar(
                select(IntentRow).where(
                    IntentRow.scope_id == account.id,
                    IntentRow.id == intent_id,
                )
            )
            if row is not None:
                return row
        raise LookupError("Execution intent is unavailable in the intended scope")

    @staticmethod
    def _append(
        session: Session, intent: IntentRow, event: EvidenceEvent
    ) -> EvidenceRow:
        fields = asdict(event)
        for name in ("quantity", "price"):
            if fields[name] is not None:
                fields[name] = Decimal(_amount(fields[name]))
        row = EvidenceRow(
            **fields,
            scope_id=intent.scope_id,
            intent_id=intent.id,
            recorded_at=datetime.now(UTC),
        )
        # This also verifies the event can be durably associated before claiming.
        session.add(row)
        session.flush()
        return row
