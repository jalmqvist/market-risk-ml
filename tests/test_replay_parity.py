"""CI scaffold; TODO replace synthetic bars with pinned MPML replay artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mrml.data.bars import D1Bar
from mrml.data.parity import compare_bars


@pytest.mark.parity
def test_stub_replay_has_zero_bar_diff() -> None:
    # TODO: add the real historical overlap fixture and require signal diff = 0.
    expected = (D1Bar(datetime(2026, 1, 1, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5, 10.0),)
    replayed = tuple(expected)

    assert compare_bars(expected, replayed) == ()
