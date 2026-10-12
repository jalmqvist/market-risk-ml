"""Stable overlap comparisons for independently built market bars."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from mrml.data.bars import D1Bar


@dataclass(frozen=True, order=True)
class BarDifference:
    timestamp: datetime
    field: str
    expected: float | None
    actual: float | None


def compare_bars(
    expected: Iterable[D1Bar],
    actual: Iterable[D1Bar],
    *,
    tolerance: float = 0.0,
) -> tuple[BarDifference, ...]:
    """Return sorted field diffs over overlapping timestamps only."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and non-negative")
    expected_rows = tuple(expected)
    actual_rows = tuple(actual)
    expected_by_time = {bar.timestamp: bar for bar in expected_rows}
    actual_by_time = {bar.timestamp: bar for bar in actual_rows}
    if len(expected_by_time) != len(expected_rows) or len(actual_by_time) != len(
        actual_rows
    ):
        raise ValueError("bar inputs must have unique timestamps")
    fields = ("open", "high", "low", "close", "volume")
    differences: list[BarDifference] = []
    for timestamp in sorted(expected_by_time.keys() & actual_by_time.keys()):
        left = expected_by_time[timestamp]
        right = actual_by_time[timestamp]
        for field in fields:
            expected_value = getattr(left, field)
            actual_value = getattr(right, field)
            if (
                not math.isfinite(expected_value)
                or not math.isfinite(actual_value)
                or abs(expected_value - actual_value) > tolerance
            ):
                differences.append(
                    BarDifference(timestamp, field, expected_value, actual_value)
                )
    return tuple(differences)
