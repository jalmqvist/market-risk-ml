"""Operator-triggered planning and recovery for IG demo historical prices."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mrml.data.canonical import (
    HOUR,
    SourceProduct,
    TimestampMeaning,
    require_hour,
    require_text,
    require_utc,
)
from mrml.data.history import GapKind, SessionLookup
from mrml.data.ig import (
    MAX_RANGE_INTERVALS,
    RESOLUTION,
    IGAllowance,
    IGAuthenticationError,
    IGError,
    IGMarketError,
    IGObservation,
    IGPriceSample,
    IGQuotaError,
    IGQuote,
    IGSessionError,
    validate_epic,
)
from mrml.data.ig_adapter import IG, TRANSFORMATION_VERSION
from mrml.data.storage import ApprovedTimestampPolicy, MarketDataRepository
from mrml.data.storage_models import (
    IGIngestionAttemptRow,
    InterpretationRow,
    MappingRow,
    ObservationRow,
    PolicyRow,
    ProductRow,
    RetrievalRow,
)

Attempt = IGIngestionAttemptRow
Fetch = Callable[[str, datetime, datetime, int], IGPriceSample]
Clock = Callable[[], datetime]
_SQLITE_LOCK = Lock()
# A historical allowance may be shared by products. Serialize this workflow
# across a database, including while its short transactions are closed.
_INGESTION_LOCK = 0x4D524D4C4831


class IngestionRefused(ValueError):
    """The requested operation would exceed policy or conflict with durable state."""


@dataclass(frozen=True)
class IngestionBudget:
    max_intervals: int = 6
    max_points: int = 7
    catch_up_hours: int = 48
    minimum_reserve: int = 20
    max_pages: int = 1
    max_requests: int = 1
    max_rechecks: int = 1

    def __post_init__(self) -> None:
        limits = (
            (self.max_intervals, 1, MAX_RANGE_INTERVALS),
            (self.max_points, 1, MAX_RANGE_INTERVALS + 1),
            (self.catch_up_hours, 1, 168),
            (self.minimum_reserve, 0, 2**31 - 1),
            (self.max_pages, 1, 1),
            (self.max_requests, 1, 1),
            (self.max_rechecks, 0, 1),
        )
        if any(
            type(value) is not int or not low <= value <= high
            for value, low, high in limits
        ):
            raise ValueError("Invalid IG ingestion budget")


@dataclass(frozen=True)
class PlannedRequest:
    start: datetime
    end: datetime
    request_end: datetime
    point_limit: int
    kind: str
    replaces: str | None = None


@dataclass(frozen=True)
class Plan:
    request: PlannedRequest | None
    quota_remaining: int | None
    message: str
    staged_attempt: str | None = None
    retry_after: datetime | None = None
    refused: bool = False


@dataclass(frozen=True)
class SessionPolicy:
    product: SourceProduct
    version: str
    lookup: SessionLookup

    def __post_init__(self) -> None:
        require_text(self.version, "session policy version")


def _hour(value: datetime) -> datetime:
    require_utc(value, "ingestion clock")
    return value.replace(minute=0, second=0, microsecond=0)


def _labels(start: datetime, end: datetime) -> Iterator[datetime]:
    while start < end:
        yield start
        start += HOUR


def _response_data(sample: IGPriceSample) -> dict[str, Any]:
    return {
        "epic": sample.epic,
        "resolution": sample.resolution,
        "allowance": asdict(sample.allowance) if sample.allowance else None,
        "gaps": sample.gaps,
        "observations": [
            {**asdict(row), "timestamp": row.timestamp.isoformat()}
            for row in sample.observations
        ],
    }


def _restore_response(data: dict[str, Any]) -> IGPriceSample:
    observations = tuple(
        IGObservation(
            datetime.fromisoformat(row["timestamp"]),
            IGQuote(**row["open"]),
            IGQuote(**row["high"]),
            IGQuote(**row["low"]),
            IGQuote(**row["close"]),
            row["raw_timestamp"],
        )
        for row in data["observations"]
    )
    return IGPriceSample(
        data["epic"],
        data["resolution"],
        observations,
        IGAllowance(**data["allowance"]) if data["allowance"] else None,
        data["gaps"],
    )


class IGIngestion:
    def __init__(
        self,
        engine: Engine,
        product: SourceProduct,
        *,
        budget: IngestionBudget = IngestionBudget(),
        approval: ApprovedTimestampPolicy | None = None,
        sessions: SessionPolicy | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if product.provider != IG:
            raise ValueError("Ingestion requires an explicit IG source product")
        validate_epic(product.instrument_id)
        self.engine = engine
        self.product = product
        self.budget = budget
        self.repo = MarketDataRepository(engine)
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self.clock = clock
        self.approval = approval
        self.session_policy = sessions
        if sessions and (not approval or sessions.product != product):
            raise ValueError("Session evidence requires the exact approved product")
        if approval:
            if approval.mapping.product != product or approval != (
                self.repo.resolve_timestamp_policy(
                    product, approval.mapping.version, approval.policy.name
                )
            ):
                raise ValueError("Ingestion requires the exact registered approval")
            if "IG_H1_TIMESTAMP_SEMANTICS.md" in approval.policy.evidence and (
                product.instrument_id != "CS.D.EURUSD.CEE.IP"
                or approval.mapping.instrument.symbol != "EUR_USD"
                or approval.policy.meaning is not TimestampMeaning.INTERVAL_START
            ):
                raise ValueError("The IG H1 audit does not support this interpretation")

    @contextmanager
    def _writer(self) -> Iterator[None]:
        if self.engine.dialect.name == "sqlite":
            with _SQLITE_LOCK:
                yield
            return
        with self.engine.connect() as connection:
            if not connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _INGESTION_LOCK}
            ):
                raise IngestionRefused("Another IG ingestion invocation is active")
            connection.commit()
            try:
                yield
            finally:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": _INGESTION_LOCK},
                    )
                    connection.commit()
                except BaseException:
                    connection.invalidate()
                    raise

    def _product_id(self, session: Session) -> int | None:
        return session.scalar(
            select(ProductRow.id).filter_by(
                provider=IG,
                instrument_id=self.product.instrument_id,
                variant=self.product.variant,
            )
        )

    def _policy_id(self, session: Session, product_id: int | None) -> int | None:
        if self.approval is None:
            return None
        return session.scalars(
            select(PolicyRow.id)
            .join(MappingRow)
            .where(
                MappingRow.product_id == product_id,
                MappingRow.version == self.approval.mapping.version,
                PolicyRow.name == self.approval.policy.name,
            )
        ).one()

    @staticmethod
    def _observed(
        session: Session,
        product_id: int | None,
        start: datetime,
        end: datetime,
    ) -> dict[datetime, datetime]:
        rows = session.execute(
            select(ObservationRow.source_timestamp, func.max(RetrievalRow.retrieved_at))
            .join(RetrievalRow)
            .where(
                RetrievalRow.product_id == product_id,
                RetrievalRow.resolution == RESOLUTION,
                ObservationRow.source_timestamp >= start,
                ObservationRow.source_timestamp < end,
            )
            .group_by(ObservationRow.source_timestamp)
        )
        return {label: retrieved for label, retrieved in rows}

    @staticmethod
    def _rechecks(
        session: Session,
        policy_id: int | None,
        observed: dict[datetime, datetime],
        attempts: list[Attempt],
    ) -> dict[datetime, datetime]:
        if policy_id is None or not observed:
            return {}
        used = {
            row.window_start
            for row in attempts
            if row.kind == "recheck" and row.policy_id == policy_id
        }
        rows = session.execute(
            select(ObservationRow.source_timestamp, InterpretationRow.end)
            .join(
                ObservationRow,
                (InterpretationRow.retrieval_id == ObservationRow.retrieval_id)
                & (InterpretationRow.ordinal == ObservationRow.ordinal),
            )
            .join(RetrievalRow, RetrievalRow.id == ObservationRow.retrieval_id)
            .where(
                InterpretationRow.policy_id == policy_id,
                InterpretationRow.transformation_version == TRANSFORMATION_VERSION,
                RetrievalRow.retrieved_at < InterpretationRow.end,
                ObservationRow.source_timestamp.in_(observed),
            )
            .distinct()
        )
        return {
            label: end
            for label, end in rows
            if label not in used and observed[label] < end
        }

    @staticmethod
    def _latest_allowance(session: Session) -> RetrievalRow | None:
        return session.scalar(
            select(RetrievalRow)
            .join(ProductRow)
            .where(
                ProductRow.provider == IG,
                RetrievalRow.resolution == RESOLUTION,
                RetrievalRow.response_metadata["remaining"].as_integer().is_not(None),
            )
            .order_by(RetrievalRow.retrieved_at.desc(), RetrievalRow.id.desc())
            .limit(1)
        )

    @staticmethod
    def _quota(
        session: Session,
        now: datetime,
        reset_confirmed: bool,
    ) -> tuple[int | None, str | None, datetime | None]:
        # Include A3-S1 observations, even those written outside ingestion.
        latest = IGIngestion._latest_allowance(session)
        remaining = None
        reset = None
        if latest:
            reset = latest.retrieved_at + timedelta(
                seconds=min(latest.response_metadata["reset_seconds"], 2**31 - 1)
            )
            if now < reset:
                remaining = latest.response_metadata["remaining"]
                debit = session.scalar(
                    select(func.sum(Attempt.point_limit)).where(
                        Attempt.requested_at >= latest.retrieved_at,
                        Attempt.id != latest.id,
                    )
                )
                remaining = max(0, remaining - (debit or 0))
        blocked = session.scalar(
            select(func.max(Attempt.retry_after)).where(Attempt.retry_after > now)
        )
        if blocked is not None:
            return remaining, "IG retry-after or throttle cooldown is active", blocked
        exhausted = session.scalar(
            select(func.max(Attempt.requested_at)).where(
                Attempt.failure == "historical_allowance",
                Attempt.retry_after.is_(None),
            )
        )
        if (
            not reset_confirmed
            and exhausted is not None
            and (latest is None or exhausted >= latest.retrieved_at)
            and (reset is None or exhausted >= reset or now < reset)
        ):
            deadline = reset if reset is not None and exhausted < reset else None
            return remaining, "Historical allowance exhausted", deadline
        return remaining, None, None

    @staticmethod
    def _staged_attempt(session: Session, product_id: int | None) -> str | None:
        return session.scalar(
            select(Attempt.id)
            .where(
                Attempt.product_id == product_id,
                Attempt.state == "staged",
            )
            .order_by(Attempt.received_at, Attempt.id)
            .limit(1)
        )

    def recover(self) -> Attempt | None:
        with self._writer():
            with self._sessions() as session:
                attempt_id = self._staged_attempt(session, self._product_id(session))
            return self._commit(attempt_id) if attempt_id else None

    def _plan(
        self,
        session: Session,
        now: datetime,
        *,
        start: datetime | None,
        recheck: bool,
        retry_job: str | None,
        reset_confirmed: bool,
    ) -> Plan:
        current = _hour(now)
        horizon = current - (self.budget.catch_up_hours - 1) * HOUR
        end = current + HOUR
        product_id = self._product_id(session)
        policy_id = self._policy_id(session, product_id)
        staged = self._staged_attempt(session, product_id)
        if staged:
            return Plan(None, None, "Resume durable response without IG", staged)
        start = start if start is not None else max(horizon, current - 3 * HOUR)
        require_hour(start, "first source label")
        if not horizon <= start <= current:
            raise IngestionRefused("Start is outside the bounded catch-up horizon")
        balance, hold, retry_after = self._quota(session, now, reset_confirmed)
        if hold:
            return Plan(None, balance, hold, retry_after=retry_after, refused=True)
        attempts = list(
            session.scalars(
                select(Attempt).where(
                    Attempt.product_id == product_id,
                    Attempt.window_end > horizon,
                )
            )
        )
        observed = self._observed(session, product_id, horizon, end)
        limit = self.budget.max_intervals
        if retry_job:
            previous = [row for row in attempts if row.job_id == retry_job]
            if len(previous) != 1:
                raise IngestionRefused("No eligible job or replacement already used")
            row = previous[0]
            if (
                row.state not in {"requesting", "failed"}
                or row.policy_id != policy_id
                or row.window_start < horizon
                or row.request_end > now
                or row.window_end - row.window_start > limit * HOUR
                or row.point_limit > self.budget.max_points
                or (row.kind == "recheck" and not self.budget.max_rechecks)
            ):
                raise IngestionRefused("Job cannot be retried under the current policy")
            candidates = self._rechecks(
                session,
                policy_id,
                observed,
                [item for item in attempts if item.id != row.id],
            )
            if (row.kind == "recheck" and row.window_start not in candidates) or (
                row.kind == "incremental"
                and all(
                    label in observed
                    for label in _labels(row.window_start, row.window_end)
                )
            ):
                return Plan(
                    None, balance, "Committed observations already satisfy the job"
                )
            first, stop, requested_end, points = (
                row.window_start,
                row.window_end,
                row.request_end,
                row.point_limit,
            )
            if row.kind == "incremental" and any(
                label in observed for label in _labels(first, stop)
            ):
                first = next(
                    label for label in _labels(first, stop) if label not in observed
                )
                stop = next(
                    (label for label in _labels(first, stop) if label in observed),
                    stop,
                )
                requested_end = min(requested_end, stop - timedelta(seconds=1))
                points = (requested_end - first) // HOUR + 1
            request = PlannedRequest(
                first, stop, requested_end, points, row.kind, row.id
            )
        elif recheck:
            if not self.budget.max_rechecks or policy_id is None:
                raise IngestionRefused("Rechecks require an approved policy and budget")
            ready = sorted(
                label
                for label, interval_end in self._rechecks(
                    session, policy_id, observed, attempts
                ).items()
                if interval_end <= now
            )
            if not ready:
                return Plan(None, balance, "No due rechecks")
            label = ready[0]
            requested_end = min(
                label + HOUR - timedelta(seconds=1), now.replace(microsecond=0)
            )
            if requested_end <= label:
                return Plan(None, balance, "Wait for the source label boundary to pass")
            request = PlannedRequest(label, label + HOUR, requested_end, 1, "recheck")
        else:
            attempted = {
                label
                for row in attempts
                if row.kind == "incremental"
                for label in _labels(row.window_start, row.window_end)
            }
            missing = [
                label
                for label in _labels(start, end)
                if label not in observed
                and label not in attempted
                and label < now.replace(microsecond=0)
            ]
            if not missing:
                return Plan(None, balance, "No unattempted missing source labels")
            first = missing[0]
            stop = first

            def bounded_end(stop: datetime) -> datetime:
                if (
                    stop in observed
                    or stop in attempted
                    or ((stop - first) // HOUR == self.budget.max_points)
                ):
                    stop -= timedelta(seconds=1)
                return min(stop, now.replace(microsecond=0))

            for label in missing[:limit]:
                if label != stop:
                    break
                candidate_end = bounded_end(label + HOUR)
                if (candidate_end - first) // HOUR + 1 > self.budget.max_points:
                    break
                stop += HOUR
            if stop == first:
                return Plan(
                    None,
                    balance,
                    "Point budget cannot fit the next source label",
                    refused=True,
                )
            request_end = bounded_end(stop)
            points = (request_end - first) // HOUR + 1
            request = PlannedRequest(first, stop, request_end, points, "incremental")
        if (
            balance is not None
            and balance < request.point_limit + self.budget.minimum_reserve
        ):
            return Plan(
                None,
                balance,
                "Request would breach the historical allowance reserve",
                refused=True,
            )
        message = (
            "Canonical interpretation approved"
            if policy_id is not None
            else ("Source only; no timestamp policy approved for this invocation")
        )
        return Plan(request, balance, message)

    def plan(
        self,
        *,
        start: datetime | None = None,
        recheck: bool = False,
        retry_job: str | None = None,
        reset_confirmed: bool = False,
    ) -> Plan:
        if sum((start is not None, recheck, retry_job is not None)) > 1:
            raise ValueError("Choose one of start, recheck or retry-job")
        with self._sessions() as session:
            return self._plan(
                session,
                self.clock(),
                start=start,
                recheck=recheck,
                retry_job=retry_job,
                reset_confirmed=reset_confirmed,
            )

    def _begin_attempt(self, request: PlannedRequest) -> Attempt:
        with self._sessions.begin() as session:
            product_id = self.repo._ensure_product(session, self.product)
            policy_id = self._policy_id(session, product_id)
            attempt_id = str(uuid4())
            old = (
                session.get_one(Attempt, request.replaces) if request.replaces else None
            )
            row = Attempt(
                id=attempt_id,
                job_id=old.job_id if old else attempt_id,
                attempt_number=2 if old else 1,
                product_id=product_id,
                policy_id=policy_id,
                resolution=RESOLUTION,
                kind=request.kind,
                state="requesting",
                window_start=request.start,
                window_end=request.end,
                request_end=request.request_end,
                requested_at=self.clock(),
                point_limit=request.point_limit,
            )
            session.add(row)
        return row

    def _record_error(self, attempt_id: str, error: IGError) -> None:
        with self._sessions.begin() as session:
            row = session.get_one(Attempt, attempt_id)
            retry_after = None
            if isinstance(error, IGQuotaError):
                failure = "historical_allowance" if error.historical else "throttled"
                retry_after = error.retry_at
                if not error.historical and retry_after is None:
                    retry_after = self.clock() + timedelta(minutes=1)
            elif isinstance(error, (IGAuthenticationError, IGSessionError)):
                failure = "authentication_or_session"
            elif isinstance(error, IGMarketError):
                failure = "market_or_invalid_response"
            else:
                # Transport and unexpected server outcomes cannot establish
                # whether IG consumed the request.
                return
            row.state, row.failure, row.retry_after = "failed", failure, retry_after

    def _stage(
        self, attempt_id: str, sample: IGPriceSample, received: datetime
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get_one(Attempt, attempt_id)
            if row.state != "requesting":
                raise IngestionRefused("Only a requesting attempt can stage a response")
            if (
                sample.epic != self.product.instrument_id
                or sample.resolution != RESOLUTION
            ):
                raise IGMarketError("IG response does not match the requested series")
            labels = [item.timestamp for item in sample.observations]
            if (
                len(labels) > row.point_limit
                or labels != sorted(set(labels))
                or any(
                    not row.window_start <= label <= row.request_end
                    or label != _hour(label)
                    for label in labels
                )
            ):
                raise IGMarketError(
                    "IG observations violate the requested window or budget"
                )
            observed = self._observed(
                session, row.product_id, row.window_start, row.window_end
            )
            offset = (
                HOUR
                if self.approval
                and self.approval.policy.meaning is TimestampMeaning.INTERVAL_END
                else timedelta(0)
            )
            gaps: list[dict[str, Any]] = []
            for label in _labels(row.window_start, row.window_end):
                if label in observed or label in labels:
                    continue
                interval_start = label - offset
                evidence = None
                if self.session_policy and interval_start + HOUR <= received:
                    evidence = self.session_policy.lookup(
                        self.product, interval_start, interval_start + HOUR
                    )
                gaps.append(
                    {
                        "source_label": label.isoformat(),
                        "interval_start": interval_start.isoformat()
                        if self.approval
                        else None,
                        "kind": evidence.kind if evidence else GapKind.UNKNOWN,
                        "evidence": evidence.reference if evidence else None,
                        "session_policy": self.session_policy.version
                        if evidence and self.session_policy
                        else None,
                        "verified_closure": evidence is not None
                        and evidence.kind is GapKind.EXPECTED_CLOSURE,
                    }
                )
            change = None
            if row.kind == "recheck":
                previous = self.repo.source_revisions(
                    self.product,
                    RESOLUTION,
                    row.window_start,
                    row.window_end,
                )
                if sample.observations and previous:
                    old = previous[-1]
                    newest = sample.observations[0]
                    sides = tuple(
                        tuple(
                            getattr(quote, side)
                            for quote in (
                                newest.open,
                                newest.high,
                                newest.low,
                                newest.close,
                            )
                        )
                        for side in ("bid", "ask")
                    )
                    change = (
                        "unchanged"
                        if tuple(
                            tuple(Decimal(str(price)) for price in side)
                            for side in sides
                        )
                        == (old.bid, old.ask)
                        else "changed"
                    )
                else:
                    change = "observation_missing"
            row.outcome = {
                "observation_count": len(labels),
                "gap_evidence": gaps,
                "recheck_result": change,
                "coverage_verified": len(gaps)
                == (row.window_end - row.window_start) // HOUR
                and all(gap["verified_closure"] for gap in gaps),
            }
            row.staged_response = _response_data(sample)
            row.received_at, row.state = received, "staged"

    def _commit(self, attempt_id: str) -> Attempt:
        with self._sessions.begin() as session:
            row = session.scalars(
                select(Attempt).where(Attempt.id == attempt_id).with_for_update()
            ).one()
            if row.state == "committed":
                return row
            if (
                row.state != "staged"
                or row.staged_response is None
                or row.received_at is None
            ):
                raise IngestionRefused("No durable response for this attempt")
            approval = None
            if row.policy_id is not None:
                policy = session.get_one(PolicyRow, row.policy_id)
                mapping = session.get_one(MappingRow, policy.mapping_id)
                approval = self.repo.resolve_timestamp_policy(
                    self.product, mapping.version, policy.name
                )
            self.repo.record_ig_sample(
                self.product,
                _restore_response(row.staged_response),
                retrieval_id=row.id,
                retrieved_at=row.received_at,
                approval=approval,
                interpreted_at=self.clock() if approval else None,
                session=session,
            )
            assert row.outcome is not None
            observed = self._observed(
                session, row.product_id, row.window_start, row.window_end
            )
            gaps = [
                gap
                for gap in row.outcome["gap_evidence"]
                if datetime.fromisoformat(gap["source_label"]) not in observed
            ]
            row.outcome = {
                **row.outcome,
                "coverage_verified": row.outcome["coverage_verified"]
                and len(gaps) == len(row.outcome["gap_evidence"]),
                "gap_evidence": gaps,
            }
            row.staged_response = None
            row.state, row.retrieval_id, row.committed_at = (
                "committed",
                row.id,
                self.clock(),
            )
        return row

    def run(
        self,
        fetch: Fetch,
        *,
        start: datetime | None = None,
        recheck: bool = False,
        retry_job: str | None = None,
        reset_confirmed: bool = False,
    ) -> tuple[Plan, Attempt | None]:
        with self._writer():
            plan = self.plan(
                start=start,
                recheck=recheck,
                retry_job=retry_job,
                reset_confirmed=reset_confirmed,
            )
            if plan.staged_attempt:
                return plan, self._commit(plan.staged_attempt)
            request = plan.request
            if request is None:
                return plan, None
            row = self._begin_attempt(request)
            try:
                sample = fetch(
                    self.product.instrument_id,
                    request.start,
                    request.request_end,
                    request.point_limit,
                )
                self._stage(row.id, sample, self.clock())
            except IGError as error:
                self._record_error(row.id, error)
                raise
            return plan, self._commit(row.id)

    @staticmethod
    def describe_attempt(row: Attempt | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "job_id": row.job_id,
            "attempt_id": row.id,
            "attempt_number": row.attempt_number,
            "kind": row.kind,
            "state": "uncertain_or_in_flight"
            if row.state == "requesting"
            else row.state,
            "window_start": row.window_start,
            "window_end": row.window_end,
            "request_end": row.request_end,
            "requested_at": row.requested_at,
            "response_received_at": row.received_at,
            "committed_at": row.committed_at,
            "retrieval_id": row.retrieval_id,
            "failure": row.failure,
            "retry_after": row.retry_after,
            "outcome": row.outcome if row.state == "committed" else None,
        }

    def status(self) -> dict[str, Any]:
        """Read status with gap and recheck lists scoped to the catch-up horizon."""
        now = self.clock()
        current = _hour(now)
        horizon = current - (self.budget.catch_up_hours - 1) * HOUR
        with self._sessions() as session:
            product_id = self._product_id(session)
            policy_id = self._policy_id(session, product_id)
            query = select(Attempt).where(Attempt.product_id == product_id)
            attempts = list(
                session.scalars(
                    query.where(Attempt.window_end > horizon).order_by(
                        Attempt.requested_at, Attempt.attempt_number, Attempt.id
                    )
                )
            )
            last_attempt = session.scalar(
                query.order_by(
                    Attempt.requested_at.desc(),
                    Attempt.attempt_number.desc(),
                    Attempt.id.desc(),
                ).limit(1)
            )
            last_commit = session.scalar(
                query.where(Attempt.state == "committed")
                .order_by(Attempt.committed_at.desc(), Attempt.id.desc())
                .limit(1)
            )
            last_failed = session.scalar(
                query.where(Attempt.state == "failed")
                .order_by(Attempt.requested_at.desc(), Attempt.id.desc())
                .limit(1)
            )
            latest_label = session.scalar(
                select(func.max(ObservationRow.source_timestamp))
                .join(RetrievalRow)
                .where(
                    RetrievalRow.product_id == product_id,
                    RetrievalRow.resolution == RESOLUTION,
                )
            )
            allowance = self._latest_allowance(session)
            remaining, hold, retry_after = self._quota(session, now, False)
            observed = self._observed(session, product_id, horizon, current + HOUR)
            rechecks = self._rechecks(session, policy_id, observed, attempts)
            gaps: dict[datetime, dict[str, Any]] = {}
            verified_start = None
            verified_until = None
            verified_labels = set()
            for row in attempts:
                if row.state != "committed" or not row.outcome:
                    continue
                for gap in row.outcome["gap_evidence"]:
                    label = datetime.fromisoformat(gap["source_label"])
                    if label < horizon or label in observed:
                        continue
                    # Keep historical classifications scoped to the timestamp and
                    # session policies that produced them.
                    if row.policy_id != policy_id:
                        gaps.setdefault(
                            label,
                            {
                                **gap,
                                "interval_start": None,
                                "kind": GapKind.UNKNOWN,
                                "evidence": None,
                                "session_policy": None,
                                "verified_closure": False,
                            },
                        )
                        continue
                    version = (
                        self.session_policy.version if self.session_policy else None
                    )
                    if version is not None and gap["session_policy"] not in (
                        None,
                        version,
                    ):
                        continue
                    gaps[label] = gap
                if row.policy_id == policy_id and row.kind == "incremental":
                    anchor = max(row.window_start, horizon)
                    verified_start = (
                        min(anchor, verified_start) if verified_start else anchor
                    )
            # The IG client supplies no completion evidence. Only a fully evidenced
            # closure can count towards the contiguous prefix for this workflow.
            if self.session_policy is not None:
                verified_labels = {
                    label
                    for label, evidence in gaps.items()
                    if evidence["verified_closure"]
                    and evidence["session_policy"] == self.session_policy.version
                }
            if verified_start in verified_labels:
                assert verified_start is not None
                cursor = verified_start
                while cursor in verified_labels:
                    cursor += HOUR
                offset = (
                    HOUR
                    if self.approval
                    and self.approval.policy.meaning is TimestampMeaning.INTERVAL_END
                    else timedelta(0)
                )
                verified_until = cursor - offset
                verified_start -= offset
            else:
                verified_start = None
            return {
                "latest_source_label": latest_label,
                "last_attempt": self.describe_attempt(last_attempt),
                "last_committed": self.describe_attempt(last_commit),
                "last_failed": self.describe_attempt(last_failed),
                "outstanding_attempts": [
                    self.describe_attempt(row)
                    for row in attempts
                    if row.state in {"requesting", "staged"}
                ],
                "canonical_policy": self.approval.policy.name
                if self.approval
                else None,
                "mapping_version": self.approval.mapping.version
                if self.approval
                else None,
                "verified_contiguous_start": verified_start,
                "verified_contiguous_end": verified_until,
                "gaps": [gaps[label] for label in sorted(gaps)],
                "latest_provisional_label": max(rechecks, default=None),
                "recheck_candidates": [
                    {"source_label": label, "interval_end": end, "due": end <= now}
                    for label, end in sorted(rechecks.items())
                ],
                "historical_allowance": {
                    "visibility": "known" if remaining is not None else "unknown",
                    "locally_available_points": remaining,
                    "retrieval_id": allowance.id if allowance else None,
                    "reported_at": allowance.retrieved_at if allowance else None,
                    "last_reported": allowance.response_metadata if allowance else None,
                    "hold": hold,
                    "retry_after": retry_after,
                },
            }
