"""Contract tests for the MPML binding using a fake MPML module.

These do not exercise the real MPML runtime; see
``test_mpml_runtime_integration.py`` for that (it needs ``MPML_ROOT``).
"""

from __future__ import annotations

import subprocess
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from mrml.contracts.bundle import load_bundle
from mrml.data.bars import D1Bar
from mrml.strategy import mpml_runtime
from mrml.strategy.mpml_runtime import (
    MPML_RUNTIME_PROVENANCE,
    MpmlPhaseAwareRuntime,
    MpmlPhaseAwareStrategy,
    MpmlRuntimeUnavailable,
    UnexecutableDecision,
    signal_intent_from_decision,
    verify_mpml_checkout,
)
from mrml.strategy.runner import StrategyRunner
from mrml.strategy.runtime import (
    PHASEAWARE_V0_COMPOSITION,
    PINNED_MPML_REVISION,
    InsufficientStrategyHistory,
    InvalidStrategyHistory,
    InvalidStrategyOutput,
    PhaseAwareDecision,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _bars(count: int) -> tuple[D1Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        D1Bar(start + timedelta(days=i), 1.0, 1.1, 0.9, 1.0, 100.0)
        for i in range(count)
    )


class _Fake(types.ModuleType):
    class InvalidHistory(ValueError): ...

    class InsufficientHistory(ValueError): ...

    class UnmatchedPhase(Exception): ...

    class NonFiniteOutput(Exception): ...

    def __init__(self) -> None:
        super().__init__("mpml.runtime.phaseaware")
        self.__file__ = "/nonexistent/mpml/runtime/phaseaware.py"
        self.frames: list[pd.DataFrame] = []
        self.overrides: dict[str, Any] = {}
        self.raises: Exception | None = None

    def latest_decision(self, history: pd.DataFrame) -> Any:
        self.frames.append(history)
        if self.raises is not None:
            raise self.raises
        values: dict[str, Any] = {
            "timestamp": history.index[-1],
            "phase": "LV_Ranging",
            "signal": 1.0,
            "sl_pct": 0.025,
            "tp_pct": 0.0125,
            "required_history": 126,
            "input_bars": len(history),
            "composition": PHASEAWARE_V0_COMPOSITION,
            "mpml_revision": PINNED_MPML_REVISION,
        }
        values.update(self.overrides)
        result = types.SimpleNamespace(**values)
        result.warmup_complete = result.input_bars >= result.required_history
        return result


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _Fake:
    module = _Fake()
    monkeypatch.setitem(sys.modules, "mpml.runtime.phaseaware", module)
    monkeypatch.setattr(
        mpml_runtime, "verify_mpml_checkout", lambda _: PINNED_MPML_REVISION
    )
    return module


def _decide(count: int = 130) -> Any:
    return MpmlPhaseAwareStrategy().generate_signal("EUR_USD", _bars(count))


def test_runtime_sends_only_ohlcv_with_utc_index(fake: _Fake) -> None:
    _decide(130)

    frame = fake.frames[0]
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(frame.index.tz) == "UTC"
    assert len(frame) == 130


def test_decision_maps_through_strategy_seam(fake: _Fake) -> None:
    bars = _bars(130)
    intent = MpmlPhaseAwareStrategy().generate_signal("EUR_USD", bars)

    assert intent.direction == "long"
    assert intent.size_multiplier == 1.0
    assert intent.stop_loss_pct == 0.025
    assert intent.take_profit_pct == 0.0125
    assert intent.stop_distance == pytest.approx(0.025 * bars[-1].close)
    assert intent.signal_bar_timestamp == bars[-1].timestamp
    # Same-bar: available at the completed bar's close, with no shift.
    assert intent.decision_cutoff == bars[-1].timestamp + timedelta(days=1)


@pytest.mark.parametrize(
    ("signal", "direction", "multiplier"),
    [(-1.5, "short", 1.5), (0.5, "long", 0.5), (0.0, "flat", 0.0)],
)
def test_sign_is_direction_and_magnitude_is_multiplier(
    fake: _Fake, signal: float, direction: str, multiplier: float
) -> None:
    fake.overrides["signal"] = signal

    intent = _decide()

    assert (intent.direction, intent.size_multiplier) == (direction, multiplier)
    assert (intent.stop_distance > 0) == (direction != "flat")


def test_non_flat_decision_without_fixed_stop_is_not_invented(fake: _Fake) -> None:
    fake.overrides.update(signal=-0.5, phase="HV_Trend", sl_pct=0.0, tp_pct=0.0)

    with pytest.raises(UnexecutableDecision, match="no fixed stop"):
        _decide()


def test_flat_decision_with_no_stop_is_allowed(fake: _Fake) -> None:
    fake.overrides.update(signal=0.0, phase="LV_Trend", sl_pct=0.0, tp_pct=0.0)

    assert _decide().direction == "flat"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (_Fake.InsufficientHistory("short"), InsufficientStrategyHistory),
        (_Fake.InvalidHistory("bad"), InvalidStrategyHistory),
        (_Fake.UnmatchedPhase("x"), InvalidStrategyOutput),
        (_Fake.NonFiniteOutput("x"), InvalidStrategyOutput),
    ],
)
def test_mpml_errors_fail_closed(
    fake: _Fake, raised: Exception, expected: type[Exception]
) -> None:
    fake.raises = raised

    with pytest.raises(expected):
        _decide()


@pytest.mark.parametrize(
    "override",
    [
        {"mpml_revision": "0" * 40},
        {"input_bars": 5},
        {"required_history": 1000},
        {"composition": "PhaseAware_TF3_MR3"},
    ],
)
def test_inconsistent_mpml_result_is_rejected(
    fake: _Fake, override: dict[str, Any]
) -> None:
    fake.overrides.update(override)

    with pytest.raises(InvalidStrategyOutput):
        _decide()


def test_decision_records_runtime_provenance(fake: _Fake) -> None:
    from mrml.strategy.runtime import CompletedD1History

    bars = _bars(130)
    history = CompletedD1History(
        "EUR_USD", bars[-1].timestamp + timedelta(days=1), bars, (True,) * 130
    )

    decision: PhaseAwareDecision = MpmlPhaseAwareRuntime().latest_decision(history)

    assert decision.runtime_provenance == MPML_RUNTIME_PROVENANCE
    assert decision.mpml_revision == PINNED_MPML_REVISION


def test_missing_mpml_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mpml.runtime.phaseaware", None)

    with pytest.raises(MpmlRuntimeUnavailable, match="not importable"):
        MpmlPhaseAwareRuntime()


def test_empty_and_naive_history_is_rejected(fake: _Fake) -> None:
    strategy = MpmlPhaseAwareStrategy()

    with pytest.raises(InsufficientStrategyHistory):
        strategy.generate_signal("EUR_USD", ())
    naive = (D1Bar(datetime(2026, 1, 1), 1.0, 1.1, 0.9, 1.0, 1.0),)
    with pytest.raises(InvalidStrategyHistory):
        strategy.generate_signal("EUR_USD", naive)
    assert fake.frames == []


def test_strategy_runner_carries_mpml_provenance(fake: _Fake) -> None:
    runner = StrategyRunner(
        pair_compositions={"EUR_USD": "strategy-a"},
        strategies={"strategy-a": MpmlPhaseAwareStrategy()},
    )
    bars = _bars(130)

    decision = runner.decide("EUR_USD", bars, load_bundle(FIXTURES))

    assert decision.direction == "long"
    assert decision.size_multiplier == 1.0
    assert decision.stop_loss_pct == 0.025
    assert decision.take_profit_pct == 0.0125
    assert decision.signal_bar_timestamp == bars[-1].timestamp


def test_signal_intent_mapping_requires_a_decision() -> None:
    decision = PhaseAwareDecision(
        "EUR_USD",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
        -1.0,
        "LV_Ranging",
        0.025,
        0.0125,
        PHASEAWARE_V0_COMPOSITION,
        PINNED_MPML_REVISION,
        "test",
    )

    intent = signal_intent_from_decision(decision, reference_close=2.0)

    assert (intent.direction, intent.stop_distance) == ("short", 0.05)


def _git_repo(path: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    module = path / "mpml" / "runtime"
    module.mkdir(parents=True)
    (module / "phaseaware.py").write_text("X = 1\n")
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    return git("rev-parse", "HEAD")


def _module_at(path: Path) -> types.ModuleType:
    module = types.ModuleType("mpml.runtime.phaseaware")
    module.__file__ = str(path / "mpml" / "runtime" / "phaseaware.py")
    return module


def test_checkout_revision_is_read_from_git_not_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _git_repo(tmp_path)
    module = _module_at(tmp_path)
    monkeypatch.setenv("MPML_REVISION", PINNED_MPML_REVISION)

    with pytest.raises(MpmlRuntimeUnavailable, match="expected"):
        verify_mpml_checkout(module)

    monkeypatch.setattr(mpml_runtime, "PINNED_MPML_REVISION", head)
    assert verify_mpml_checkout(module) == head


def test_dirty_or_non_git_checkout_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    head = _git_repo(repo)
    monkeypatch.setattr(mpml_runtime, "PINNED_MPML_REVISION", head)
    module = _module_at(repo)
    (repo / "mpml" / "runtime" / "phaseaware.py").write_text("X = 2\n")

    with pytest.raises(MpmlRuntimeUnavailable, match="uncommitted"):
        verify_mpml_checkout(module)

    plain = tmp_path / "plain"
    (plain / "mpml" / "runtime").mkdir(parents=True)
    with pytest.raises(MpmlRuntimeUnavailable, match="git rev-parse failed"):
        verify_mpml_checkout(_module_at(plain))
