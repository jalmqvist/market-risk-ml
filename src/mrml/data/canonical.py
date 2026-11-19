"""Provider-neutral market-data values and validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from sys import float_info

HOUR = timedelta(hours=1)

# Currency codes supported by the FX contract. Pairs are composed independently.
FX_CURRENCIES = frozenset(
    "AUD BRL CAD CHF CLP CNH CNY COP CZK DKK EUR GBP HKD HUF IDR ILS INR ISK "
    "JPY KRW MXN MYR NOK NZD PEN PHP PLN RON SAR SEK SGD THB TRY TWD ZAR USD".split()
)


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{field} must be nonempty text without surrounding whitespace"
        )


def require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC-aware")


def require_hour(value: datetime, field: str) -> None:
    require_utc(value, field)
    if value.minute or value.second or value.microsecond:
        raise ValueError(f"{field} must align to a UTC hour")


@dataclass(frozen=True)
class FXInstrument:
    symbol: str

    def __post_init__(self) -> None:
        require_text(self.symbol, "FX instrument")
        parts = self.symbol.split("_")
        if (
            len(parts) != 2
            or parts[0] == parts[1]
            or any(code not in FX_CURRENCIES for code in parts)
        ):
            raise ValueError("Expected two distinct supported FX codes as BASE_QUOTE")


@dataclass(frozen=True)
class SourceProduct:
    provider: str
    instrument_id: str
    variant: str

    def __post_init__(self) -> None:
        require_text(self.provider, "provider")
        require_text(self.instrument_id, "source instrument")
        require_text(self.variant, "product variant")


@dataclass(frozen=True)
class InstrumentMapping:
    instrument: FXInstrument
    product: SourceProduct
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, FXInstrument) or not isinstance(
            self.product, SourceProduct
        ):
            raise ValueError(
                "An instrument mapping requires an FX instrument and product"
            )
        require_text(self.version, "mapping version")


class Resolution(StrEnum):
    H1 = "H1"


class TimestampMeaning(StrEnum):
    INTERVAL_START = "INTERVAL_START"
    INTERVAL_END = "INTERVAL_END"


@dataclass(frozen=True)
class TimestampPolicy:
    name: str
    meaning: TimestampMeaning
    evidence: str

    def __post_init__(self) -> None:
        require_text(self.name, "timestamp policy")
        require_text(self.evidence, "timestamp policy evidence")
        if not isinstance(self.meaning, TimestampMeaning):
            raise ValueError("Unresolved timestamp interpretation")


@dataclass(frozen=True)
class SourceTimestamp:
    field: str
    instant: datetime
    policy: TimestampPolicy
    raw: str | None = None

    def __post_init__(self) -> None:
        require_text(self.field, "source timestamp field")
        if not isinstance(self.policy, TimestampPolicy):
            raise ValueError("Source timestamp requires a resolved policy")
        if not isinstance(self.instant, datetime) or self.instant.utcoffset() is None:
            raise ValueError("Source timestamp must be timezone-aware")
        if self.raw is not None:
            require_text(self.raw, "raw source timestamp")


class CompletionState(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Completion:
    state: CompletionState = CompletionState.UNKNOWN
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, CompletionState):
            raise ValueError("Invalid completion state")
        if self.state is not CompletionState.UNKNOWN or self.evidence is not None:
            require_text(self.evidence or "", "completion evidence")


@dataclass(frozen=True)
class Availability:
    first_available_at: datetime | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.first_available_at is not None:
            require_utc(self.first_available_at, "first availability")
            require_text(self.evidence or "", "availability evidence")
        elif self.evidence is not None:
            require_text(self.evidence, "availability evidence")


@dataclass(frozen=True)
class OHLC:
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if any(
            type(value) not in (int, float) or not 0 < value <= float_info.max
            for value in self.components
        ):
            raise ValueError("OHLC prices must be finite positive numbers")
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("Invalid OHLC range")

    @property
    def components(self) -> tuple[float, float, float, float]:
        return self.open, self.high, self.low, self.close


@dataclass(frozen=True)
class DerivedOHLC:
    method: str
    prices: OHLC


@dataclass(frozen=True)
class SourceBar:
    mapping: InstrumentMapping
    resolution: Resolution
    start: datetime
    end: datetime
    bid: OHLC
    ask: OHLC
    source_time: SourceTimestamp
    retrieved_at: datetime
    transformation_version: str
    completion: Completion = Completion()
    availability: Availability = Availability()
    volume: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, InstrumentMapping):
            raise ValueError("A source bar requires an instrument mapping")
        if not isinstance(self.bid, OHLC) or not isinstance(self.ask, OHLC):
            raise ValueError("A source bar requires bid and ask OHLC")
        if not isinstance(self.source_time, SourceTimestamp):
            raise ValueError("A source bar requires timestamp provenance")
        if not isinstance(self.completion, Completion) or not isinstance(
            self.availability, Availability
        ):
            raise ValueError(
                "A source bar requires completion and availability metadata"
            )
        if self.resolution is not Resolution.H1:
            raise ValueError("Unsupported bar resolution")
        require_hour(self.start, "interval start")
        require_hour(self.end, "interval end")
        require_utc(self.retrieved_at, "retrieval time")
        require_text(self.transformation_version, "transformation version")
        if self.end - self.start != HOUR:
            raise ValueError("H1 interval must have exactly one hour")
        boundary = (
            self.start
            if self.source_time.policy.meaning is TimestampMeaning.INTERVAL_START
            else self.end
        )
        if self.source_time.instant.astimezone(UTC) != boundary:
            raise ValueError("Source timestamp does not match its interpreted boundary")
        if self.retrieved_at < self.start:
            raise ValueError("Retrieval precedes the interval")
        if (
            self.completion.state is CompletionState.COMPLETE
            and self.retrieved_at < self.end
        ):
            raise ValueError("Complete candle retrieved before interval end")
        first = self.availability.first_available_at
        if first is not None and not self.start <= first <= self.retrieved_at:
            raise ValueError("First availability must be within start and retrieval")
        if any(
            bid > ask
            for bid, ask in zip(self.bid.components, self.ask.components, strict=True)
        ):
            raise ValueError("Bid exceeds ask for a corresponding OHLC component")
        if self.volume is not None and (
            type(self.volume) is not int or self.volume < 0
        ):
            raise ValueError("Tick volume must be a nonnegative integer or unknown")

    def componentwise_midpoint(self) -> DerivedOHLC:
        """Average each component. The result does not describe a midpoint path."""
        values = tuple(
            _average(bid, ask)
            for bid, ask in zip(self.bid.components, self.ask.components, strict=True)
        )
        return DerivedOHLC("componentwise_bid_ask_midpoint/v1", OHLC(*values))


def _average(bid: float, ask: float) -> float:
    total = bid + ask
    if total > float_info.max:
        return bid / 2 + ask / 2
    return total / 2
