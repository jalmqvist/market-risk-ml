"""Deterministic hourly-to-daily candle aggregation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable


class BarGapError(ValueError):
    """Raised when an input day does not contain the expected hourly candles."""


@dataclass(frozen=True)
class H1Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class D1Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def build_d1_bars(
    bars: Iterable[H1Bar],
    *,
    boundary_offset_hours: int = 1,
    require_complete_days: bool = True,
) -> tuple[D1Bar, ...]:
    """Aggregate UTC H1 bars into fixed-offset days (MPML's UTC+1 default).

    H1 timestamps identify the candle's open. A UTC+1 day therefore starts at
    23:00 UTC on the prior date. Partial edge days are rejected by default.
    """
    if not -23 <= boundary_offset_hours <= 23:
        raise ValueError("boundary_offset_hours must be between -23 and 23")
    grouped: dict[date, list[H1Bar]] = defaultdict(list)
    offset = timedelta(hours=boundary_offset_hours)
    for bar in bars:
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("H1 timestamps must be timezone-aware")
        timestamp = bar.timestamp.astimezone(timezone.utc)
        if timestamp.minute or timestamp.second or timestamp.microsecond:
            raise ValueError("H1 timestamps must align to the hour")
        grouped[(timestamp + offset).date()].append(
            H1Bar(timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume)
        )

    daily: list[D1Bar] = []
    for day in sorted(grouped):
        day_bars = sorted(grouped[day], key=lambda bar: bar.timestamp)
        if require_complete_days:
            expected_start = datetime.combine(
                day, time.min, tzinfo=timezone.utc
            ) - offset
            expected = tuple(expected_start + timedelta(hours=index) for index in range(24))
            actual = tuple(bar.timestamp for bar in day_bars)
            if actual != expected:
                raise BarGapError(
                    f"Incomplete H1 data for {day}: expected 24 consecutive bars"
                )
        if len({bar.timestamp for bar in day_bars}) != len(day_bars):
            raise BarGapError(f"Duplicate H1 timestamp for {day}")
        daily.append(
            D1Bar(
                timestamp=datetime.combine(day, time.min, tzinfo=timezone.utc),
                open=day_bars[0].open,
                high=max(bar.high for bar in day_bars),
                low=min(bar.low for bar in day_bars),
                close=day_bars[-1].close,
                volume=sum(bar.volume for bar in day_bars),
            )
        )
    return tuple(daily)
