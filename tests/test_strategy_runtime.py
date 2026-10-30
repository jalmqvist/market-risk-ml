"""Tests for the fail-closed boundary around a pinned PhaseAware runtime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from mrml.data.bars import D1Bar
from mrml.strategy.runtime import (
    PHASEAWARE_V0_COMPOSITION,
    PINNED_MPML_REVISION,
    InsufficientStrategyHistory,
    InvalidStrategyHistory,
    InvalidStrategyOutput,
    PhaseAwareDecision,
    PhaseAwareDecisionAdapter,
    PreparedPhaseAwareHistory,
)


def _bars(count: int) -> tuple[D1Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        D1Bar(
            start + timedelta(days=index),
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            volume=100.0,
        )
        for index in range(count)
    )


def _fields(count: int) -> dict[str, tuple[object, ...]]:
    return {
        "phase": ("LV_Ranging",) * count,
        "adx": (18.0,) * count,
        "atr": (0.01,) * count,
    }


def _decision(
    history: PreparedPhaseAwareHistory, **overrides: Any
) -> PhaseAwareDecision:
    values: dict[str, object] = {
        "pair": history.pair,
        "signal_bar_timestamp": history.bars[-1].timestamp,
        "decision_cutoff": history.decision_cutoff,
        "actionable_after": history.decision_cutoff,
        "canonical_signal": 0.75,
        "phase": history.canonical_fields["phase"][-1],
        "stop_loss_pct": 0.025,
        "take_profit_pct": 0.0125,
        "composition": PHASEAWARE_V0_COMPOSITION,
        "mpml_revision": PINNED_MPML_REVISION,
        "runtime_provenance": "test-double",
    }
    values.update(overrides)
    return PhaseAwareDecision(**values)  # type: ignore[arg-type]


class FixedRuntime:
    def __init__(
        self,
        result: Callable[[PreparedPhaseAwareHistory], object] | None = None,
    ) -> None:
        self.histories: list[PreparedPhaseAwareHistory] = []
        self.result = result

    def latest_decision(self, history: PreparedPhaseAwareHistory) -> object:
        self.histories.append(history)
        if self.result is not None:
            return self.result(history)
        return _decision(history)


def _call(
    runtime: FixedRuntime,
    bars: tuple[D1Bar, ...] | None = None,
    *,
    fields: dict[str, tuple[object, ...]] | None = None,
    cutoff: datetime | None = None,
    bar_completed: tuple[bool, ...] | None = None,
) -> PhaseAwareDecision:
    selected_bars = _bars(3) if bars is None else bars
    return PhaseAwareDecisionAdapter(runtime).decide(
        pair="EUR_USD",
        bars=selected_bars,
        bar_completed=(
            (True,) * len(selected_bars) if bar_completed is None else bar_completed
        ),
        decision_cutoff=cutoff or selected_bars[-1].timestamp + timedelta(days=1),
        canonical_fields=(_fields(len(selected_bars)) if fields is None else fields),
    )


def test_boundary_returns_pinned_raw_decision_and_provenance() -> None:
    runtime = FixedRuntime()
    bars = _bars(3)

    result = _call(runtime, bars)

    assert result.pair == "EUR_USD"
    assert result.signal_bar_timestamp == bars[-1].timestamp
    assert result.canonical_signal == 0.75
    assert result.phase == "LV_Ranging"
    assert result.stop_loss_pct == 0.025
    assert result.take_profit_pct == 0.0125
    assert result.composition == PHASEAWARE_V0_COMPOSITION
    assert result.mpml_revision == PINNED_MPML_REVISION
    assert result.runtime_provenance == "test-double"
    assert result.actionable_after == result.decision_cutoff
    assert not hasattr(result, "state_id")
    assert _call(runtime, bars) == result


def test_boundary_passes_only_each_validated_cutoff_prefix() -> None:
    bars = _bars(5)
    lengths_seen: list[int] = []
    runtime = FixedRuntime(lambda history: _decision(history))
    adapter = PhaseAwareDecisionAdapter(runtime)

    for cutoff_index in (1, 2, 4):
        prefix = bars[:cutoff_index]
        result = adapter.decide(
            pair="EUR_USD",
            bars=prefix,
            bar_completed=(True,) * len(prefix),
            decision_cutoff=prefix[-1].timestamp + timedelta(days=1),
            canonical_fields=_fields(len(prefix)),
        )
        lengths_seen.append(len(runtime.histories[-1].bars))
        assert result.signal_bar_timestamp == prefix[-1].timestamp
        assert runtime.histories[-1].bars == prefix

    assert lengths_seen == [1, 2, 4]
    assert all(len(history.bars) < len(bars) for history in runtime.histories)


@pytest.mark.parametrize(
    ("bars", "fields", "cutoff", "error", "message"),
    [
        (
            (),
            {},
            datetime(2026, 2, 1, tzinfo=UTC),
            InsufficientStrategyHistory,
            "at least one",
        ),
        (
            _bars(2),
            {},
            datetime(2026, 1, 4, tzinfo=UTC),
            InvalidStrategyHistory,
            "phase",
        ),
        (
            _bars(2),
            {"phase": ("LV_Ranging",)},
            datetime(2026, 1, 4, tzinfo=UTC),
            InvalidStrategyHistory,
            "does not align",
        ),
        (
            _bars(2),
            {"phase": ("LV_Ranging", "LV_Ranging"), "adx": (18.0, float("nan"))},
            datetime(2026, 1, 4, tzinfo=UTC),
            InvalidStrategyHistory,
            "invalid value",
        ),
        (
            _bars(2),
            {
                "phase": ("LV_Ranging", "LV_Ranging"),
                "next_return": (0.01, 0.02),
            },
            datetime(2026, 1, 4, tzinfo=UTC),
            InvalidStrategyHistory,
            "forward-looking",
        ),
        (
            _bars(2),
            {"phase": ("LV_Ranging", "LV_Ranging")},
            datetime(2026, 1, 3),
            InvalidStrategyHistory,
            "timezone-aware",
        ),
    ],
)
def test_history_contract_rejects_missing_or_invalid_inputs(
    bars: tuple[D1Bar, ...],
    fields: dict[str, tuple[object, ...]],
    cutoff: datetime,
    error: type[ValueError],
    message: str,
) -> None:
    runtime = FixedRuntime()

    with pytest.raises(error, match=message):
        _call(runtime, bars, fields=fields, cutoff=cutoff)

    assert runtime.histories == []


def test_history_contract_rejects_incomplete_bar_attestation() -> None:
    runtime = FixedRuntime()

    with pytest.raises(InvalidStrategyHistory, match="explicitly marked complete"):
        _call(runtime, _bars(2), bar_completed=(True, False))

    assert runtime.histories == []


def test_history_contract_rejects_duplicate_unordered_and_future_bars() -> None:
    bars = _bars(3)
    duplicate = (bars[0], bars[0])
    unordered = (bars[1], bars[0])
    future = (bars[0], bars[1])
    runtime = FixedRuntime()

    for invalid_bars, cutoff, message in (
        (duplicate, bars[2].timestamp, "duplicate"),
        (unordered, bars[2].timestamp, "strictly increasing"),
        (future, bars[1].timestamp, "at or after"),
    ):
        with pytest.raises(InvalidStrategyHistory, match=message):
            _call(runtime, invalid_bars, cutoff=cutoff)

    assert runtime.histories == []


@pytest.mark.parametrize(
    "invalid_bar",
    [
        D1Bar(
            datetime(2026, 1, 1, tzinfo=UTC),
            open=float("nan"),
            high=1.1,
            low=0.9,
            close=1.0,
            volume=100.0,
        ),
        D1Bar(
            datetime(2026, 1, 1, tzinfo=UTC),
            open=1.0,
            high=0.9,
            low=0.8,
            close=1.0,
            volume=100.0,
        ),
        D1Bar(
            datetime(2026, 1, 1),
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            volume=100.0,
        ),
    ],
)
def test_history_contract_rejects_invalid_bars(invalid_bar: D1Bar) -> None:
    runtime = FixedRuntime()
    cutoff = datetime(2026, 1, 3, tzinfo=UTC)

    with pytest.raises(InvalidStrategyHistory):
        _call(runtime, (invalid_bar,), cutoff=cutoff)

    assert runtime.histories == []


@pytest.mark.parametrize(
    "bad_result",
    [
        object(),
        {
            "pair": "EUR_USD",
        },
    ],
)
def test_adapter_rejects_unexpected_runtime_output(bad_result: object) -> None:
    runtime = FixedRuntime(lambda _: bad_result)

    with pytest.raises(InvalidStrategyOutput, match="must return"):
        _call(runtime)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pair", "GBP_USD", "pair does not match"),
        (
            "signal_bar_timestamp",
            datetime(2026, 1, 2, tzinfo=UTC),
            "latest completed bar",
        ),
        (
            "decision_cutoff",
            datetime(2026, 1, 5, tzinfo=UTC),
            "cutoff does not match",
        ),
    ],
)
def test_adapter_rejects_mismatched_runtime_metadata(
    field: str, value: object, message: str
) -> None:
    def mismatched_result(history: PreparedPhaseAwareHistory) -> PhaseAwareDecision:
        overrides = {field: value}
        if field == "decision_cutoff":
            overrides["actionable_after"] = value
        return _decision(history, **overrides)

    runtime = FixedRuntime(mismatched_result)

    with pytest.raises(InvalidStrategyOutput, match=message):
        _call(runtime)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonical_signal", float("nan"), "canonical_signal"),
        ("stop_loss_pct", -0.01, "stop_loss_pct"),
        ("take_profit_pct", float("inf"), "take_profit_pct"),
        ("composition", "PhaseAware_TF4_MR2", "composition"),
        ("mpml_revision", "different-revision", "revision"),
        ("runtime_provenance", "", "runtime_provenance"),
    ],
)
def test_decision_contract_rejects_invalid_canonical_values(
    field: str, value: object, message: str
) -> None:
    history = PreparedPhaseAwareHistory(
        "EUR_USD",
        datetime(2026, 1, 4, tzinfo=UTC),
        _bars(3),
        (True, True, True),
        _fields(3),
    )

    with pytest.raises(InvalidStrategyOutput, match=message):
        _decision(history, **{field: value})
