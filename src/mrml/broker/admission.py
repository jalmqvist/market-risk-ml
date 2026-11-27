"""Read-only offline evaluation of an exact IG demo intent.

These inputs describe fixtures. No adapter in this module authenticates their
origin, and no decision from this module can admit a dispatch claim.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from mrml.broker.evidence import Scope
from mrml.broker.gate import (
    MAX_AGE,
    Blocker,
    GateEvidenceError,
    GateJournal,
    GateState,
)
from mrml.broker.journal import (
    ApprovedRequest,
    ExecutionIntent,
    JournalConflict,
    _amount,
    _canonical,
    _intent,
    _json,
)
from mrml.broker.journal_models import ClaimRow, IntentRow
from mrml.broker.observations import StoredObservation, _utc

POLICY = "ig-demo-offline-v1"
MAX_COLLECTION_SPAN = timedelta(seconds=10)


class Outcome(StrEnum):
    REFUSED = "REFUSED"
    INCOMPLETE = "INCOMPLETE"
    ADMISSIBLE = "ADMISSIBLE"  # Reserved for a future trusted implementation.


class Finding(StrEnum):
    BROKER_VERIFIER_UNAVAILABLE = "BROKER_VERIFIER_UNAVAILABLE"
    AUTHORIZATION_VERIFIER_UNAVAILABLE = "AUTHORIZATION_VERIFIER_UNAVAILABLE"
    WRONG_SCOPE = "WRONG_SCOPE"
    INTENT_UNAVAILABLE = "INTENT_UNAVAILABLE"
    INTENT_MISMATCH = "INTENT_MISMATCH"
    SESSION_MISSING = "SESSION_MISSING"
    SESSION_INVALID = "SESSION_INVALID"
    EXPOSURE_MISSING = "EXPOSURE_MISSING"
    EXPOSURE_CHANGED = "EXPOSURE_CHANGED"
    EXPOSURE_TIME_INVALID = "EXPOSURE_TIME_INVALID"
    GATE_CHANGED = "GATE_CHANGED"
    LIFECYCLE_UNSUPPORTED = "LIFECYCLE_UNSUPPORTED"
    CLOSE_MISMATCH = "CLOSE_MISMATCH"
    RULES_MISSING = "RULES_MISSING"
    RULES_INVALID = "RULES_INVALID"
    SIZE_INVALID = "SIZE_INVALID"
    STOP_INVALID = "STOP_INVALID"
    RISK_MISSING = "RISK_MISSING"
    RISK_INVALID = "RISK_INVALID"
    RISK_LIMIT = "RISK_LIMIT"
    MARGIN_INSUFFICIENT = "MARGIN_INSUFFICIENT"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    AUTHORIZATION_REUSED = "AUTHORIZATION_REUSED"


@dataclass(frozen=True, repr=False)
class OfflineSession:
    # An opaque fixture generation, never a credential or a broker token.
    generation: UUID
    active_scope: Scope
    accounts: tuple[OfflineAccount, ...]
    checked_at: datetime
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class OfflineAccount:
    scope: Scope
    status: str
    currency: str


@dataclass(frozen=True, repr=False)
class OfflineExposure:
    session_generation: UUID
    gate_revision: int
    claim_ids: tuple[UUID, ...]
    positions_id: UUID
    orders_id: UUID


@dataclass(frozen=True, repr=False)
class OfflineDealingRules:
    evidence_id: UUID
    session_generation: UUID
    observed_at: datetime
    instrument: str
    currency: str
    size_unit: str
    contract_size: Decimal
    minimum_size: Decimal
    size_increment: Decimal
    size_precision: int
    operations: tuple[str, ...]
    order_types: tuple[str, ...]
    dealing_available: bool
    executable_level: Decimal
    minimum_stop_distance: Decimal
    maximum_stop_distance: Decimal


@dataclass(frozen=True, repr=False)
class OfflineRisk:
    evidence_id: UUID
    session_generation: UUID
    observed_at: datetime
    intent_id: UUID
    fingerprint: str
    account_currency: str
    instrument_to_account: Decimal
    funds: Decimal
    estimated_margin: Decimal
    stop_budget: Decimal
    max_quantity: Decimal
    max_notional: Decimal
    max_positions: int
    daily_loss: Decimal
    daily_loss_limit: Decimal
    drawdown: Decimal
    drawdown_limit: Decimal


@dataclass(frozen=True, repr=False)
class OfflineAuthorization:
    identity: str
    operator: str
    scope: Scope
    intent_id: UUID
    fingerprint: str
    request: ApprovedRequest
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class OfflineEvidence:
    session: OfflineSession | None = None
    exposure: OfflineExposure | None = None
    dealing: OfflineDealingRules | None = None
    risk: OfflineRisk | None = None
    authorization: OfflineAuthorization | None = None


@dataclass(frozen=True, repr=False)
class OfflineDecision:
    # A complete input and gate binding for offline diagnostics only.
    scope: Scope
    intent_id: UUID
    request: ApprovedRequest
    intent: ExecutionIntent | None
    gate: GateState | None
    evidence: OfflineEvidence
    evaluated_at: datetime
    expires_at: datetime
    refusals: tuple[Finding | Blocker, ...]
    missing: tuple[Finding, ...]
    policy: str = POLICY

    @property
    def outcome(self) -> Outcome:
        return Outcome.REFUSED if self.refusals else Outcome.INCOMPLETE


def _currency(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None


def _number(value: Decimal, *, zero: bool = False) -> bool:
    try:
        _amount(value)
    except ValueError:
        return False
    return value >= 0 if zero else value > 0


def _window(start: datetime, end: datetime, at: datetime) -> bool:
    try:
        return at - MAX_AGE <= _utc(start) <= at < _utc(end)
    except (AttributeError, TypeError, ValueError):
        return False


def _session_valid(session: OfflineSession, scope: Scope, at: datetime) -> bool:
    return (
        session.active_scope == scope
        and len(session.accounts) == 1
        and session.accounts[0].scope == scope
        and session.accounts[0].status == "ENABLED"
        and _currency(session.accounts[0].currency)
        and _window(session.checked_at, session.expires_at, at)
    )


def _evidence_time(
    generation: UUID, observed_at: datetime, session: OfflineSession, at: datetime
) -> bool:
    try:
        return generation == session.generation and (
            max(at - MAX_AGE, _utc(session.checked_at)) <= _utc(observed_at) <= at
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _collection_start(read: StoredObservation) -> datetime | None:
    return read.collection.started_at if read.collection else None


def _dealing(
    rules: OfflineDealingRules, request: ApprovedRequest
) -> tuple[Finding | None, Decimal | None]:
    # The fixture uses absolute quote-level distances and contracts. Other
    # broker units and guaranteed-stop schedules require a reviewed adapter.
    if (
        rules.instrument != request.instrument
        or not _currency(rules.currency)
        or rules.size_unit != "CONTRACTS"
        or rules.dealing_available is not True
        or request.operation not in rules.operations
        or request.order_type not in rules.order_types
        or request.order_type != "MARKET"
        or type(rules.size_precision) is not int
        or not 0 <= rules.size_precision <= 18
        or not all(
            _number(x)
            for x in (
                rules.contract_size,
                rules.minimum_size,
                rules.size_increment,
                rules.executable_level,
                rules.minimum_stop_distance,
                rules.maximum_stop_distance,
            )
        )
        or rules.minimum_stop_distance > rules.maximum_stop_distance
        or (request.operation == "OPEN" and request.currency != rules.currency)
    ):
        return Finding.RULES_INVALID, None
    with localcontext(prec=192):
        size = request.quantity
        if (
            size < rules.minimum_size
            or (size - rules.minimum_size) % rules.size_increment
            or size != size.quantize(Decimal(1).scaleb(-rules.size_precision))
        ):
            return Finding.SIZE_INVALID, None
        if request.operation == "CLOSE":
            return None, None
        if request.guaranteed_stop or request.limit_distance or request.limit_level:
            return Finding.STOP_INVALID, None
        distance = request.stop_distance
        if request.stop_level is not None:
            distance = rules.executable_level - request.stop_level
            if request.side == "SELL":
                distance = -distance
        if distance is None or not (
            rules.minimum_stop_distance <= distance <= rules.maximum_stop_distance
        ):
            return Finding.STOP_INVALID, None
        if request.side == "BUY" and distance >= rules.executable_level:
            return Finding.STOP_INVALID, None
        return None, distance


def _risk(
    risk: OfflineRisk,
    rules: OfflineDealingRules,
    request: ApprovedRequest,
    account_currency: str,
    distance: Decimal | None,
) -> Finding | None:
    if (
        risk.account_currency != account_currency
        or not all(
            _number(x)
            for x in (
                risk.instrument_to_account,
                risk.stop_budget,
                risk.max_quantity,
                risk.max_notional,
                risk.daily_loss_limit,
                risk.drawdown_limit,
            )
        )
        or not all(
            _number(x, zero=True)
            for x in (
                risk.funds,
                risk.estimated_margin,
                risk.daily_loss,
                risk.drawdown,
            )
        )
        or type(risk.max_positions) is not int
        or risk.max_positions < 1
        or (rules.currency == account_currency and risk.instrument_to_account != 1)
    ):
        return Finding.RISK_INVALID
    if risk.estimated_margin > risk.funds:
        return Finding.MARGIN_INSUFFICIENT
    if request.operation == "CLOSE":
        return None  # Full position reduction does not consume opening risk budget.
    if not risk.estimated_margin or distance is None:
        return Finding.RISK_INVALID
    with localcontext(prec=192):
        value_per_level = (
            request.quantity * rules.contract_size * risk.instrument_to_account
        )
        stop_risk = distance * value_per_level
        notional = rules.executable_level * value_per_level
    if (
        request.quantity > risk.max_quantity
        or notional > risk.max_notional
        or stop_risk > risk.stop_budget
        or risk.daily_loss >= risk.daily_loss_limit
        or risk.drawdown >= risk.drawdown_limit
    ):
        return Finding.RISK_LIMIT
    return None


class AdmissionJournal(GateJournal):
    """Evaluate fixture consistency without creating admission or dispatch records."""

    def evaluate_offline(
        self,
        scope: Scope,
        intent_id: UUID,
        request: ApprovedRequest,
        evidence: OfflineEvidence,
        *,
        at: datetime,
    ) -> OfflineDecision:
        at = _utc(at)
        canonical = _json(_canonical(request))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        refusals: list[Finding | Blocker] = []
        missing = [
            Finding.BROKER_VERIFIER_UNAVAILABLE,
            Finding.AUTHORIZATION_VERIFIER_UNAVAILABLE,
        ]
        expires = [at + MAX_AGE]
        intent = None
        state = None
        session = evidence.session
        if session is None:
            missing.append(Finding.SESSION_MISSING)
        elif _session_valid(session, scope, at):
            expires.extend((session.expires_at, session.checked_at + MAX_AGE))
        else:
            refusals.append(Finding.SESSION_INVALID)
        if (scope.provider, scope.environment) != ("IG", "demo"):
            refusals.append(Finding.WRONG_SCOPE)
        else:
            with self._transaction() as transaction:
                account = self._account(transaction, scope, lock=True)
                row = (
                    transaction.scalar(
                        select(IntentRow).where(
                            IntentRow.scope_id == account.id, IntentRow.id == intent_id
                        )
                    )
                    if account is not None
                    else None
                )
                if row is None or account is None:
                    refusals.append(Finding.INTENT_UNAVAILABLE)
                else:
                    intent = _intent(row, scope)
                    state = self._recover(transaction, scope, account.id, at)
                    if (
                        intent.fingerprint != fingerprint
                        or intent.canonical_request != canonical
                        or intent.quantity != request.quantity
                        or intent.operation != request.operation
                    ):
                        refusals.append(Finding.INTENT_MISMATCH)
                    blockers = set(state.blockers) - {Blocker.IDENTITY_UNVERIFIED}
                    if request.operation == "CLOSE":
                        blockers.discard(Blocker.KNOWN_POSITION)
                        if not blockers:
                            try:
                                self._closing_preparation(transaction, row, state, at)
                            except (GateEvidenceError, JournalConflict):
                                refusals.append(Finding.CLOSE_MISMATCH)
                    elif state.claims:
                        refusals.append(Finding.LIFECYCLE_UNSUPPORTED)
                    refusals.extend(sorted(blockers))
                    if (
                        transaction.scalar(
                            select(ClaimRow.id)
                            .join(IntentRow, IntentRow.id == ClaimRow.intent_id)
                            .where(
                                ClaimRow.scope_id == account.id,
                                IntentRow.authorization_id == row.authorization_id,
                            )
                            .limit(1)
                        )
                        is not None
                    ):
                        refusals.append(Finding.AUTHORIZATION_REUSED)

        exposure = evidence.exposure
        if exposure is None:
            missing.append(Finding.EXPOSURE_MISSING)
        elif state is not None:
            if exposure.gate_revision != state.revision or exposure.claim_ids != tuple(
                claim.id for claim in state.claims
            ):
                refusals.append(Finding.GATE_CHANGED)
            if (
                state.positions is None
                or state.orders is None
                or exposure.positions_id != state.positions.id
                or exposure.orders_id != state.orders.id
                or (
                    session is not None
                    and exposure.session_generation != session.generation
                )
            ):
                refusals.append(Finding.EXPOSURE_CHANGED)
            elif session is not None:
                start_positions = _collection_start(state.positions)
                start_orders = _collection_start(state.orders)
                end = max(
                    state.positions.value.provenance.observed_at,
                    state.orders.value.provenance.observed_at,
                )
                if (
                    start_positions is None
                    or start_orders is None
                    or not _evidence_time(
                        exposure.session_generation,
                        min(start_positions, start_orders),
                        session,
                        at,
                    )
                    or end - min(start_positions, start_orders) > MAX_COLLECTION_SPAN
                ):
                    refusals.append(Finding.EXPOSURE_TIME_INVALID)
                else:
                    expires.append(min(start_positions, start_orders) + MAX_AGE)

        rules = evidence.dealing
        risk = evidence.risk
        distance = None
        dealing_valid = False
        if rules is None:
            missing.append(Finding.RULES_MISSING)
        elif session is not None:
            if not _evidence_time(
                rules.session_generation, rules.observed_at, session, at
            ):
                refusals.append(Finding.RULES_INVALID)
            else:
                issue, distance = _dealing(rules, request)
                dealing_valid = issue is None
                if issue is not None:
                    refusals.append(issue)
                expires.append(rules.observed_at + MAX_AGE)
        if risk is None:
            missing.append(Finding.RISK_MISSING)
        elif session is not None and _session_valid(session, scope, at):
            if (
                risk.intent_id != intent_id
                or risk.fingerprint != fingerprint
                or not _evidence_time(
                    risk.session_generation, risk.observed_at, session, at
                )
            ):
                refusals.append(Finding.RISK_INVALID)
            elif rules is not None and dealing_valid:
                issue = _risk(
                    risk, rules, request, session.accounts[0].currency, distance
                )
                if issue is not None:
                    refusals.append(issue)
                expires.append(risk.observed_at + MAX_AGE)

        approval = evidence.authorization
        if approval is None:
            missing.append(Finding.AUTHORIZATION_MISSING)
        elif (
            intent is None
            or approval.identity != intent.authorization_id
            or not approval.operator.strip()
            or approval.scope != scope
            or approval.intent_id != intent_id
            or approval.fingerprint != fingerprint
            or approval.request != request
            or not _window(approval.issued_at, approval.expires_at, at)
        ):
            refusals.append(Finding.AUTHORIZATION_INVALID)
        else:
            expires.extend((approval.expires_at, approval.issued_at + MAX_AGE))

        return OfflineDecision(
            scope,
            intent_id,
            request,
            intent,
            state,
            evidence,
            at,
            min(expires),
            tuple(dict.fromkeys(refusals)),
            tuple(missing),
        )
