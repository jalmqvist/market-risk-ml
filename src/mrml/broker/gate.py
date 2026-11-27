"""Offline account-gate decisions and deterministic restart recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mrml.broker.evidence import (
    Confirmation,
    Position,
    PositionRead,
    Scope,
    WorkingOrderRead,
)
from mrml.broker.gate_models import GateTransitionRow
from mrml.broker.journal import (
    ApprovedRequest,
    DispatchClaim,
    JournalConflict,
    _canonical,
    _opaque,
)
from mrml.broker.journal_models import IntentRow
from mrml.broker.observation_models import AssessmentRow, ObservationRow
from mrml.broker.observations import (
    Assessment,
    ObservationJournal,
    Replay,
    ReplayUnavailable,
    StoredObservation,
    _utc,
)

POLICY = "account-gate-v1"
MAX_AGE = timedelta(seconds=30)


class Blocker(StrEnum):
    IDENTITY_UNVERIFIED = "BROKER_IDENTITY_UNVERIFIED"
    EXPOSURE_UNVERIFIED = "EXPOSURE_UNVERIFIED"
    EXPOSURE_STALE = "EXPOSURE_STALE"
    CLAIM_UNRESOLVED = "CLAIM_UNRESOLVED"
    CONFIRMATION_MISSING = "CONFIRMATION_MISSING"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    EVIDENCE_UNREPLAYABLE = "EVIDENCE_UNREPLAYABLE"
    UNSUPPORTED_OUTCOME = "UNSUPPORTED_OUTCOME"
    KNOWN_POSITION = "KNOWN_POSITION"
    UNATTRIBUTED_POSITION = "UNATTRIBUTED_POSITION"
    CONTINGENT_ORDER = "CONTINGENT_ORDER"
    OPERATIONAL_RESTRICTION = "OPERATIONAL_RESTRICTION"


class TransitionReason(StrEnum):
    OPENING_RESOLVED = "OPENING_RESOLVED"
    REJECTION_RESOLVED = "REJECTION_RESOLVED"
    OPERATIONAL_RESTRICTION = "OPERATIONAL_RESTRICTION"


@dataclass(frozen=True, repr=False)
class GateTransition:
    id: UUID
    revision: int
    previous_revision: int
    reason: TransitionReason
    before: tuple[Blocker, ...]
    after: tuple[Blocker, ...]
    claim_id: UUID | None
    intent_id: UUID | None
    assessment_id: UUID | None
    positions_id: UUID | None
    orders_id: UUID | None
    actor: str
    policy: str
    decided_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, repr=False)
class GateState:
    scope: Scope
    history: tuple[GateTransition, ...]
    claim: DispatchClaim | None
    blockers: tuple[Blocker, ...]
    positions: StoredObservation | None = None
    orders: StoredObservation | None = None
    latest_assessment: Assessment | None = None
    policy: str = POLICY
    claims: tuple[DispatchClaim, ...] = ()
    outstanding_claims: tuple[DispatchClaim, ...] = ()
    attributable_positions: tuple[Position, ...] = ()
    unmatched_positions: tuple[Position, ...] = ()

    @property
    def latest_claim(self) -> DispatchClaim | None:
        return self.claim

    @property
    def revision(self) -> int:
        return self.history[-1].revision if self.history else 0

    @property
    def outstanding_claim(self) -> DispatchClaim | None:
        return self.outstanding_claims[-1] if self.outstanding_claims else None


class Prerequisite(StrEnum):
    VERIFIED_SESSION = "VERIFIED_SESSION"
    CURRENT_EXPOSURE = "CURRENT_EXPOSURE"
    RISK_AND_MARGIN = "RISK_AND_MARGIN"
    OPERATOR_AUTHORIZATION = "OPERATOR_AUTHORIZATION"
    PERMITTED_EXECUTION_CONFIGURATION = "PERMITTED_EXECUTION_CONFIGURATION"


@dataclass(frozen=True, repr=False)
class Consideration:
    state: GateState
    missing_prerequisites: tuple[Prerequisite, ...]

    @property
    def can_consider(self) -> bool:
        return not self.state.blockers and not self.missing_prerequisites


@dataclass(frozen=True, repr=False)
class ClosingPreparation:
    """An offline evidence binding with no dispatch authority."""

    scope: Scope
    opening_claim: DispatchClaim
    closing_intent_id: UUID
    authorization_id: str
    target_deal_id: str
    quantity: Decimal
    positions_id: UUID
    orders_id: UUID
    gate_revision: int
    checked_at: datetime


class GateEvidenceError(ValueError):
    """A restrictive, redacted rejection of evidence for a gate transition."""

    def __init__(self, blocker: Blocker) -> None:
        self.blocker = blocker
        super().__init__(blocker.value)


@dataclass(frozen=True, repr=False)
class _Resolution:
    assessment: Assessment
    reason: TransitionReason
    position_deal_id: str | None


_RESOLUTIONS = {
    "OPEN_POSITION_MATCHED": TransitionReason.OPENING_RESOLVED,
    "REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE": TransitionReason.REJECTION_RESOLVED,
}


def _transition(row: GateTransitionRow) -> GateTransition:
    return GateTransition(
        row.id,
        row.revision,
        row.previous_revision,
        TransitionReason(row.reason),
        tuple(Blocker(value) for value in row.before_blockers),
        tuple(Blocker(value) for value in row.after_blockers),
        row.claim_id,
        row.intent_id,
        row.assessment_id,
        row.positions_id,
        row.orders_id,
        row.actor,
        row.policy,
        _utc(row.decided_at),
        _utc(row.recorded_at),
    )


def _full(read: StoredObservation | None) -> bool:
    return (
        read is not None
        and read.collection is not None
        and read.collection.status == "COMPLETE"
        and (
            not isinstance(read.value, PositionRead)
            or read.value.target_deal_id is None
        )
    )


def _fresh(read: StoredObservation, at: datetime) -> bool:
    observed = read.value.provenance.observed_at
    start = read.collection.started_at if read.collection else None
    return timedelta(0) <= at - observed <= at - (start or observed) <= MAX_AGE


class GateJournal(ObservationJournal):
    """Record and recover scoped gate decisions without any broker access."""

    def recover_gate(self, scope: Scope, *, at: datetime) -> GateState:
        at = _utc(at)
        with self._transaction() as session:
            account = self._account(session, scope, lock=True)
            if account is None:
                return GateState(
                    scope,
                    (),
                    None,
                    (Blocker.EXPOSURE_UNVERIFIED, Blocker.IDENTITY_UNVERIFIED),
                )
            return self._recover(session, scope, account.id, at)

    def consider_action(
        self,
        scope: Scope,
        request: ApprovedRequest,
        *,
        at: datetime,
    ) -> Consideration:
        _canonical(request)
        return Consideration(self.recover_gate(scope, at=at), tuple(Prerequisite))

    def prepare_closing(
        self, scope: Scope, closing_intent_id: UUID, *, at: datetime
    ) -> ClosingPreparation:
        """Bind a persisted full close to current evidence under the account lock."""
        at = _utc(at)
        with self._transaction() as session:
            closing = self._scoped_intent(session, scope, closing_intent_id, lock=True)
            state = self._recover(session, scope, closing.scope_id, at)
            return self._closing_preparation(session, closing, state, at)

    def _closing_preparation(
        self, session: Session, closing: IntentRow, state: GateState, at: datetime
    ) -> ClosingPreparation:
        blocking = set(state.blockers) - {
            Blocker.KNOWN_POSITION,
            Blocker.IDENTITY_UNVERIFIED,
        }
        if blocking:
            raise GateEvidenceError(min(blocking))
        claim = state.latest_claim
        if (
            len(state.claims) != 1
            or claim is None
            or len(state.attributable_positions) != 1
            or state.positions is None
            or state.orders is None
        ):
            raise GateEvidenceError(Blocker.EXPOSURE_UNVERIFIED)
        opening = self._scoped_intent(session, state.scope, claim.intent_id)
        position = state.attributable_positions[0]
        if (
            opening.operation != "OPEN"
            or closing.operation != "CLOSE"
            or closing.authorization_id == opening.authorization_id
            or closing.request.get("target_deal_id") != position.deal_id
            or closing.request.get("instrument") != position.instrument
            or (closing.request.get("side"), position.side)
            not in {("BUY", "SELL"), ("SELL", "BUY")}
            or closing.quantity != position.quantity
        ):
            raise JournalConflict(
                "Closing intent does not match scoped full-close terms"
            )
        return ClosingPreparation(
            state.scope,
            claim,
            closing.id,
            closing.authorization_id,
            position.deal_id,
            position.quantity,
            state.positions.id,
            state.orders.id,
            state.revision,
            at,
        )

    def restrict(
        self,
        identity: UUID,
        scope: Scope,
        *,
        expected_revision: int,
        actor: str,
        decided_at: datetime,
    ) -> GateTransition:
        return self._change(
            identity,
            scope,
            expected_revision,
            actor,
            decided_at,
            claim_id=None,
            assessment_id=None,
        )

    def resolve_claim(
        self,
        identity: UUID,
        scope: Scope,
        claim_id: UUID,
        assessment_id: UUID,
        *,
        expected_revision: int,
        actor: str,
        decided_at: datetime,
    ) -> GateTransition:
        return self._change(
            identity,
            scope,
            expected_revision,
            actor,
            decided_at,
            claim_id=claim_id,
            assessment_id=assessment_id,
        )

    def _change(
        self,
        identity: UUID,
        scope: Scope,
        expected_revision: int,
        actor: str,
        decided_at: datetime,
        *,
        claim_id: UUID | None,
        assessment_id: UUID | None,
    ) -> GateTransition:
        decided_at = _utc(decided_at)
        _opaque(actor, 128)
        with self._transaction() as session:
            account = self._ensure_account(session, scope)
            existing = session.scalar(
                select(GateTransitionRow).where(
                    GateTransitionRow.scope_id == account.id,
                    GateTransitionRow.id == identity,
                )
            )
            if existing is not None:
                saved = _transition(existing)
                if (
                    saved.previous_revision == expected_revision
                    and saved.actor == actor
                    and saved.decided_at == decided_at
                    and saved.claim_id == claim_id
                    and saved.assessment_id == assessment_id
                    and saved.policy == POLICY
                ):
                    return saved
                raise JournalConflict("Gate transition identity conflict")
            before = self._recover(session, scope, account.id, decided_at)
            if before.revision != expected_revision or (
                before.history and decided_at < before.history[-1].decided_at
            ):
                raise JournalConflict("Gate revision or decision time conflict")
            resolution = None
            claim = None
            reason = TransitionReason.OPERATIONAL_RESTRICTION
            if assessment_id is not None:
                claim = next(
                    (item for item in before.claims if item.id == claim_id), None
                )
                if claim is None:
                    raise GateEvidenceError(Blocker.CLAIM_UNRESOLVED)
                if claim not in before.outstanding_claims:
                    raise JournalConflict("Claim uncertainty is already resolved")
                resolution = self._verify(
                    session,
                    scope,
                    account.id,
                    claim,
                    assessment_id,
                    decided_at,
                )
                reason = resolution.reason
            elif Blocker.OPERATIONAL_RESTRICTION in before.blockers:
                raise JournalConflict("Account restriction is already recorded")
            inputs = resolution.assessment.inputs if resolution else None
            row = GateTransitionRow(
                id=identity,
                scope_id=account.id,
                previous_revision=expected_revision,
                revision=expected_revision + 1,
                reason=reason,
                actor=actor,
                policy=POLICY,
                claim_id=claim_id,
                intent_id=claim.intent_id if claim is not None else None,
                assessment_id=assessment_id,
                positions_id=inputs.positions_id if inputs else None,
                orders_id=inputs.orders_id if inputs else None,
                decided_at=decided_at,
                recorded_at=datetime.now(UTC),
                before_blockers=list(before.blockers),
                after_blockers=list(self._after(before, resolution)),
            )
            session.add(row)
            session.flush()
            return _transition(row)

    @staticmethod
    def _after(
        before: GateState, resolution: _Resolution | None
    ) -> tuple[Blocker, ...]:
        if resolution is None:
            return tuple(sorted({*before.blockers, Blocker.OPERATIONAL_RESTRICTION}))
        blockers = set(before.blockers)
        if len(before.outstanding_claims) == 1:
            blockers.difference_update(
                {
                    Blocker.CLAIM_UNRESOLVED,
                    Blocker.CONFIRMATION_MISSING,
                    Blocker.EVIDENCE_CONFLICT,
                    Blocker.UNSUPPORTED_OUTCOME,
                }
            )
        if resolution.position_deal_id is not None:
            blockers.add(Blocker.KNOWN_POSITION)
            position_read = before.positions.value if before.positions else None
            if isinstance(position_read, PositionRead) and all(
                row.deal_id == resolution.position_deal_id
                for row in position_read.positions
            ):
                blockers.discard(Blocker.UNATTRIBUTED_POSITION)
        return tuple(sorted(blockers))

    def _recover(
        self, session: Session, scope: Scope, account_id: UUID, at: datetime
    ) -> GateState:
        history = tuple(
            _transition(row)
            for row in session.scalars(
                select(GateTransitionRow)
                .where(GateTransitionRow.scope_id == account_id)
                .order_by(GateTransitionRow.revision)
            )
        )
        claims = self._claims(session, scope, account_id)
        latest_claim = claims[-1] if claims else None
        blockers = {Blocker.IDENTITY_UNVERIFIED}
        reads: list[StoredObservation | None] = []
        for kind in ("POSITIONS", "WORKING_ORDERS"):
            try:
                reads.append(self._latest_collection(session, scope, account_id, kind))
            except ReplayUnavailable:
                reads.append(None)
                blockers.add(Blocker.EVIDENCE_UNREPLAYABLE)
        positions, orders = reads
        if any(not _full(read) for read in reads):
            blockers.add(Blocker.EXPOSURE_UNVERIFIED)
        if any(read is not None and not _fresh(read, at) for read in reads):
            blockers.add(Blocker.EXPOSURE_STALE)
        if any(
            item.reason == TransitionReason.OPERATIONAL_RESTRICTION for item in history
        ):
            blockers.add(Blocker.OPERATIONAL_RESTRICTION)

        latest = None
        outstanding: list[DispatchClaim] = []
        attributed_deals: set[str] = set()
        for claim in claims:
            resolution = None
            assessment = self._latest_assessment(session, claim)
            if assessment is None:
                blockers.add(Blocker.CONFIRMATION_MISSING)
            else:
                try:
                    replay = self._replay_row(session, scope, assessment)
                    if claim == latest_claim:
                        latest = replay.stored
                    blockers.update(self._assessment_blockers(replay))
                except ReplayUnavailable:
                    blockers.add(Blocker.EVIDENCE_UNREPLAYABLE)
            recorded = next(
                (item for item in reversed(history) if item.claim_id == claim.id), None
            )
            if recorded is not None:
                try:
                    resolution = self._recover_resolution(
                        session,
                        scope,
                        account_id,
                        claim,
                        recorded,
                        at,
                    )
                except (GateEvidenceError, ReplayUnavailable) as error:
                    blockers.add(
                        error.blocker
                        if isinstance(error, GateEvidenceError)
                        else Blocker.EVIDENCE_UNREPLAYABLE
                    )
            if resolution is None:
                outstanding.append(claim)
            elif resolution.position_deal_id is not None:
                attributed_deals.add(resolution.position_deal_id)
        if outstanding:
            blockers.add(Blocker.CLAIM_UNRESOLVED)
        current = (
            positions.value.positions
            if positions is not None and isinstance(positions.value, PositionRead)
            else ()
        )
        attributable = tuple(row for row in current if row.deal_id in attributed_deals)
        unmatched = tuple(row for row in current if row.deal_id not in attributed_deals)
        if attributable:
            blockers.add(Blocker.KNOWN_POSITION)
        if unmatched:
            blockers.add(Blocker.UNATTRIBUTED_POSITION)
        if (
            orders is not None
            and isinstance(orders.value, WorkingOrderRead)
            and orders.value.orders
        ):
            blockers.add(Blocker.CONTINGENT_ORDER)
        return GateState(
            scope,
            history,
            latest_claim,
            tuple(sorted(blockers)),
            positions,
            orders,
            latest,
            claims=claims,
            outstanding_claims=tuple(outstanding),
            attributable_positions=attributable,
            unmatched_positions=unmatched,
        )

    @staticmethod
    def _assessment_blockers(replay: Replay) -> set[Blocker]:
        if not replay.matches:
            return {Blocker.EVIDENCE_UNREPLAYABLE}
        if replay.recomputed.status == "MISMATCH":
            return {Blocker.EVIDENCE_CONFLICT}
        if replay.recomputed.reason in (
            "CONFIRMATION_NOT_FOUND",
            "CONFIRMATION_PENDING",
            "SUBMISSION_UNCERTAIN",
            "REFERENCE_UNAVAILABLE",
        ):
            return {Blocker.CONFIRMATION_MISSING}
        if replay.recomputed.reason == "UNSUPPORTED_AFFECTED_DEALS":
            return {Blocker.UNSUPPORTED_OUTCOME}
        return set()

    def _recover_resolution(
        self,
        session: Session,
        scope: Scope,
        account_id: UUID,
        claim: DispatchClaim,
        recorded: GateTransition,
        at: datetime,
    ) -> _Resolution:
        if (
            recorded.assessment_id is None
            or recorded.policy != POLICY
            or recorded.decided_at > at
        ):
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
        result = self._verify(
            session,
            scope,
            account_id,
            claim,
            recorded.assessment_id,
            recorded.decided_at,
            recorded_at=recorded.recorded_at,
        )
        if (
            result.reason != recorded.reason
            or recorded.intent_id != claim.intent_id
            or result.assessment.inputs.positions_id != recorded.positions_id
            or result.assessment.inputs.orders_id != recorded.orders_id
        ):
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
        return result

    @staticmethod
    def _latest_assessment(
        session: Session, claim: DispatchClaim
    ) -> AssessmentRow | None:
        return session.scalar(
            select(AssessmentRow)
            .where(AssessmentRow.intent_id == claim.intent_id)
            .order_by(AssessmentRow.evaluated_at.desc(), AssessmentRow.id.desc())
            .limit(1)
        )

    def _latest_collection(
        self,
        session: Session,
        scope: Scope,
        account_id: UUID,
        kind: str,
    ) -> StoredObservation | None:
        row = session.scalar(
            select(ObservationRow)
            .where(
                ObservationRow.scope_id == account_id,
                ObservationRow.kind == kind,
            )
            .order_by(
                ObservationRow.observed_at.desc(),
                ObservationRow.recorded_at.desc(),
                ObservationRow.id.desc(),
            )
            .limit(1)
        )
        if row is None:
            return None
        return self._load(session, scope, account_id, row.id)

    def _verify(
        self,
        session: Session,
        scope: Scope,
        account_id: UUID,
        claim: DispatchClaim,
        assessment_id: UUID,
        at: datetime,
        *,
        recorded_at: datetime | None = None,
    ) -> _Resolution:
        row = session.scalar(
            select(AssessmentRow).where(
                AssessmentRow.id == assessment_id,
                AssessmentRow.scope_id == account_id,
                AssessmentRow.intent_id == claim.intent_id,
            )
        )
        if row is None:
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
        try:
            replay = self._replay_row(session, scope, row)
        except ReplayUnavailable:
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE) from None
        if not replay.matches:
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
        result = replay.recomputed
        reason = _RESOLUTIONS.get(result.reason)
        if result.status != "MATCHED" or reason is None:
            blockers = self._assessment_blockers(replay)
            raise GateEvidenceError(
                min(blockers) if blockers else Blocker.CLAIM_UNRESOLVED
            )
        saved = replay.stored
        if not claim.claimed_at <= saved.evaluated_at <= at:
            raise GateEvidenceError(Blocker.EXPOSURE_UNVERIFIED)

        later_rows = session.scalars(
            select(AssessmentRow).where(
                AssessmentRow.scope_id == account_id,
                AssessmentRow.intent_id == claim.intent_id,
                AssessmentRow.id != assessment_id,
                AssessmentRow.evaluated_at >= saved.evaluated_at,
            )
        )
        for later in later_rows:
            try:
                comparison = self._replay_row(session, scope, later)
            except ReplayUnavailable:
                raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE) from None
            if not comparison.matches:
                raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
            if (
                comparison.recomputed != result
                or comparison.stored.inputs != saved.inputs
            ):
                raise GateEvidenceError(Blocker.EVIDENCE_CONFLICT)

        transactions = tuple(
            session.scalars(
                select(ObservationRow.id).where(
                    ObservationRow.scope_id == account_id,
                    ObservationRow.intent_id == claim.intent_id,
                )
            )
        )
        if set(transactions) != set(saved.inputs.transaction_ids):
            raise GateEvidenceError(Blocker.EVIDENCE_CONFLICT)
        facts = [
            self._load(session, scope, account_id, identity).value
            for identity in saved.inputs.transaction_ids
        ]
        confirmations = [fact for fact in facts if isinstance(fact, Confirmation)]
        if not confirmations or any(
            not claim.claimed_at <= fact.provenance.observed_at <= saved.evaluated_at
            for fact in facts
        ):
            raise GateEvidenceError(Blocker.EXPOSURE_UNVERIFIED)
        confirmed_at = max(item.provenance.observed_at for item in confirmations)
        self._verify_collection(
            session,
            scope,
            account_id,
            saved.inputs.positions_id,
            confirmed_at,
            at,
            recorded_at=recorded_at,
        )
        self._verify_collection(
            session,
            scope,
            account_id,
            saved.inputs.orders_id,
            confirmed_at,
            at,
            recorded_at=recorded_at,
        )
        opened = next(
            (
                leg.deal_id
                for leg in (confirmations[0].affected_deals or ())
                if leg.effect == "OPENED"
            ),
            None,
        )
        return _Resolution(saved, reason, opened)

    def _verify_collection(
        self,
        session: Session,
        scope: Scope,
        account_id: UUID,
        identity: UUID,
        confirmed_at: datetime,
        at: datetime,
        *,
        recorded_at: datetime | None,
    ) -> None:
        row = session.scalar(
            select(ObservationRow).where(
                ObservationRow.scope_id == account_id,
                ObservationRow.id == identity,
            )
        )
        if row is None:
            raise GateEvidenceError(Blocker.EVIDENCE_UNREPLAYABLE)
        read = self._load(session, scope, account_id, identity)
        if not _full(read):
            raise GateEvidenceError(Blocker.EXPOSURE_UNVERIFIED)
        assert read.collection is not None
        earliest = read.collection.started_at or read.value.provenance.observed_at
        if earliest <= confirmed_at or not _fresh(read, at):
            raise GateEvidenceError(Blocker.EXPOSURE_STALE)
        others = session.scalars(
            select(ObservationRow).where(
                ObservationRow.scope_id == account_id,
                ObservationRow.kind == row.kind,
                ObservationRow.id != row.id,
                (
                    (ObservationRow.observed_at >= row.observed_at)
                    | (ObservationRow.recorded_at >= row.recorded_at)
                ),
            )
        )
        for other in others:
            if (
                recorded_at is None
                or _utc(other.recorded_at) <= recorded_at
                or other.observed_at <= row.observed_at
            ):
                raise GateEvidenceError(Blocker.EVIDENCE_CONFLICT)
            updated = self._load(session, scope, account_id, other.id)
            if (
                not _full(updated)
                or replace(updated.value, provenance=read.value.provenance)
                != read.value
            ):
                raise GateEvidenceError(Blocker.EVIDENCE_CONFLICT)
