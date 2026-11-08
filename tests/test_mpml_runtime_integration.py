"""Real-MPML integration tests for the PhaseAware runtime binding.

These tests exercise the actual MPML ``mpml.runtime.phaseaware`` code, not a
test double. They need a clean checkout of MPML at ``PINNED_MPML_REVISION``:

    MPML_ROOT=/path/to/market-phase-ml python -m pytest -m parity -rs

and an environment with pandas, NumPy and ``ta``. Without ``MPML_ROOT`` every
test here is SKIPPED, and a skip is not parity evidence. A set ``MPML_ROOT``
that is not at the pinned revision (or not clean) fails rather than skips.

Evidence kinds are kept separate:
* fixture prefixes: frozen EURUSD expected signals vs the runtime;
* current-decision parity and prefix causality: runtime vs MPML's canonical
  detector and strategy on synthetic bars.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mrml.data.bars import D1Bar
from mrml.strategy.mpml_runtime import (
    MPML_RUNTIME_PROVENANCE,
    MpmlPhaseAwareRuntime,
    MpmlPhaseAwareStrategy,
    UnexecutableDecision,
)
from mrml.strategy.runtime import (
    PINNED_MPML_REVISION,
    InsufficientStrategyHistory,
    PhaseAwareDecision,
    PhaseAwareDecisionAdapter,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "PARITY" / "phaseaware_replay_fixture_eurusd"
)
MIN_BARS = 126
BAR = timedelta(days=1)

pytestmark = pytest.mark.parity


@pytest.fixture(scope="module")
def mpml_root() -> Iterator[Path]:
    value = os.environ.get("MPML_ROOT")
    if not value:
        pytest.skip("Set MPML_ROOT to a checkout at the pinned MPML revision")
    root = Path(value).expanduser().resolve()
    # A regular ``src`` package in MPML must win over any namespace ``src``.
    sys.path.insert(0, str(root))
    try:
        yield root
    finally:
        sys.path.remove(str(root))


@pytest.fixture(scope="module")
def runtime(mpml_root: Path) -> MpmlPhaseAwareRuntime:
    return MpmlPhaseAwareRuntime()  # verifies the git revision and cleanliness


def _bars(frame: pd.DataFrame) -> tuple[D1Bar, ...]:
    return tuple(
        D1Bar(
            ts.to_pydatetime().astimezone(UTC),
            float(row.Open),
            float(row.High),
            float(row.Low),
            float(row.Close),
            float(row.Volume),
        )
        for ts, row in frame.iterrows()
    )


def _decide(runtime: MpmlPhaseAwareRuntime, frame: pd.DataFrame) -> PhaseAwareDecision:
    bars = _bars(frame)
    return PhaseAwareDecisionAdapter(runtime).decide(
        pair="EUR_USD",
        bars=bars,
        bar_completed=(True,) * len(bars),
        decision_cutoff=bars[-1].timestamp + BAR,
    )


def _tuple(decision: PhaseAwareDecision) -> tuple[object, ...]:
    return (
        decision.phase,
        decision.canonical_signal,
        decision.stop_loss_pct,
        decision.take_profit_pct,
    )


def test_runtime_is_the_verified_pinned_checkout(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    frame = _synthetic(MIN_BARS)

    decision = _decide(runtime, frame)

    assert decision.mpml_revision == PINNED_MPML_REVISION
    assert decision.runtime_provenance == MPML_RUNTIME_PROVENANCE
    assert decision.signal_bar_timestamp == frame.index[-1]
    assert decision.actionable_after == decision.decision_cutoff


# --- frozen EURUSD fixture prefixes (historical-slice evidence) -------------


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    inputs = pd.read_csv(FIXTURE_DIR / "input_bars.csv", parse_dates=["timestamp"])
    expected = pd.read_csv(
        FIXTURE_DIR / "expected" / "phaseaware_signals.csv",
        parse_dates=["timestamp"],
    )
    ohlcv = inputs.set_index("timestamp")[["Open", "High", "Low", "Close", "Volume"]]
    ohlcv.index = ohlcv.index.tz_localize("UTC")
    expected = expected.set_index("timestamp")
    expected.index = expected.index.tz_localize("UTC")
    return ohlcv, expected


def _fixture_comparison(
    runtime: MpmlPhaseAwareRuntime,
) -> list[tuple[pd.Timestamp, tuple[object, ...], tuple[object, ...]]]:
    ohlcv, expected = _fixture()
    rows = []
    for count in range(MIN_BARS, len(ohlcv) + 1):
        decision = _decide(runtime, ohlcv.iloc[:count])  # full history, fixed start
        row = expected.loc[pd.Timestamp(decision.signal_bar_timestamp)]
        rows.append(
            (
                pd.Timestamp(decision.signal_bar_timestamp),
                (row.phase, row.raw_signal, row.sl_pct, row.tp_pct),
                _tuple(decision),
            )
        )
    return rows


def test_fixture_prefixes_start_at_the_warmup_boundary(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    ohlcv, _ = _fixture()

    with pytest.raises(InsufficientStrategyHistory):
        _decide(runtime, ohlcv.iloc[: MIN_BARS - 1])
    assert (
        _decide(runtime, ohlcv.iloc[:MIN_BARS]).signal_bar_timestamp
        == (ohlcv.index[MIN_BARS - 1])
    )
    assert len(ohlcv) - MIN_BARS + 1 == 32


def test_fixture_prefix_divergence_is_phase_only(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    """Characterize, do not hide, the fixture/runtime phase disagreement.

    The frozen input's ``adx``/``atr``/``phase`` were computed upstream on a
    longer history than the 157 stored bars, so the OHLCV-only runtime cannot
    reproduce them from this slice. SL/TP and signal direction still agree.
    """
    rows = _fixture_comparison(runtime)
    mismatches = [row for row in rows if row[1] != row[2]]

    assert len(rows) == 32
    assert len(mismatches) == 22
    for _, expected, actual in mismatches:
        assert expected[0] != actual[0]
        assert np.sign(float(expected[1])) == np.sign(float(actual[1]))  # type: ignore[arg-type]
        assert expected[2:] == actual[2:]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "frozen fixture phases come from a longer upstream history; the "
        "OHLCV-only runtime recomputes them from the 157-bar slice"
    ),
)
def test_fixture_prefix_parity_with_frozen_expected_signals(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    mismatches = [row for row in _fixture_comparison(runtime) if row[1] != row[2]]

    assert mismatches == []


# --- current-decision parity and prefix causality (synthetic bars) ----------


def _synthetic(count: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = np.arange(count)
    vol = np.where((steps // 150) % 2 == 0, 0.004, 0.010)
    drift = np.where((steps // 100) % 2 == 0, 0.0006, 0.0)
    close = 1.1 * np.exp(np.cumsum(drift + rng.normal(0, vol)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2)))
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": rng.uniform(1_000, 2_000, count),
        },
        index=pd.date_range("2015-01-01", periods=count, freq="D", tz="UTC"),
    )


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    """MPML's own detector and strategy, used as the oracle."""
    from src.phases import MarketPhaseDetector
    from src.strategies import PhaseAwareStrategy

    phased = MarketPhaseDetector().detect_phases(frame)
    signals, sl_pct, tp_pct = PhaseAwareStrategy("TF4", "MR42").generate_signals(phased)
    return pd.DataFrame(
        {"phase": phased["phase"], "signal": signals, "sl": sl_pct, "tp": tp_pct}
    )


CUTOFFS = (*range(MIN_BARS, 601, 7), 600)


def test_current_decisions_match_canonical_prefixes_and_are_causal(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    frame = _synthetic(600)
    full = _canonical(frame)
    phases: set[str] = set()
    non_flat = 0

    for count in CUTOFFS:
        prefix = frame.iloc[:count]  # includes the cutoff bar itself
        decision = _decide(runtime, prefix)
        oracle = _canonical(prefix).iloc[-1]
        later = full.iloc[count - 1]  # same bar, with later bars present

        actual = _tuple(decision)
        assert actual == (oracle.phase, oracle.signal, oracle.sl, oracle.tp), count
        assert actual == (later.phase, later.signal, later.sl, later.tp), count
        assert decision.signal_bar_timestamp == prefix.index[-1]
        phases.add(decision.phase)
        non_flat += decision.canonical_signal != 0

    assert phases == {"HV_Trend", "LV_Trend", "HV_Ranging", "LV_Ranging"}
    assert non_flat > 0


def test_strategy_seam_maps_real_decisions_or_refuses_without_a_stop(
    runtime: MpmlPhaseAwareRuntime,
) -> None:
    frame = _synthetic(600)
    strategy = MpmlPhaseAwareStrategy(runtime)
    seen: set[str] = set()

    for count in CUTOFFS:
        prefix = frame.iloc[:count]
        decision = _decide(runtime, prefix)
        signal = decision.canonical_signal
        if signal != 0 and not decision.stop_loss_pct:
            with pytest.raises(UnexecutableDecision):
                strategy.generate_signal("EUR_USD", _bars(prefix))
            seen.add("no_stop")
            continue
        intent = strategy.generate_signal("EUR_USD", _bars(prefix))
        assert intent.size_multiplier == abs(signal)
        assert intent.stop_loss_pct == decision.stop_loss_pct
        assert intent.take_profit_pct == decision.take_profit_pct
        assert intent.signal_bar_timestamp == decision.signal_bar_timestamp
        assert intent.decision_cutoff == decision.decision_cutoff
        assert intent.direction == (
            "flat" if signal == 0 else "long" if signal > 0 else "short"
        )
        seen.add(intent.direction)

    assert {"long", "flat", "no_stop"} <= seen
