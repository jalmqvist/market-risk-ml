"""Read contract for canonical H1 history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mrml.data.canonical import FXInstrument, SourceBar, require_hour


@dataclass(frozen=True)
class H1HistoryRequest:
    instrument: FXInstrument
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, FXInstrument):
            raise ValueError("History request requires a canonical FX instrument")
        require_hour(self.start, "request start")
        require_hour(self.end, "request end")
        if self.start >= self.end:
            raise ValueError("History window must have positive duration")


class InsufficientHistoryError(ValueError):
    """The provider cannot substantiate coverage of the requested window."""


class MarketDataProvider(Protocol):
    def read_h1(self, request: H1HistoryRequest, /) -> tuple[SourceBar, ...]:
        """Return one ordered source series for the instrument and [start, end).

        Raise InsufficientHistoryError when requested coverage cannot be
        established, including at the edges. Documented closures may have
        no bars. Raise ValueError for invalid requests or source data.
        """
        ...
