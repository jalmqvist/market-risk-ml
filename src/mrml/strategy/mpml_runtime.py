"""Binding of MRML's PhaseAware boundary to MPML's causal runtime.

MRML supplies only completed D1 OHLCV bars. MPML's
``mpml.runtime.phaseaware.latest_decision`` performs the causal preparation,
phase detection and canonical TF4/MR42 evaluation. Nothing is calculated here.
"""

from __future__ import annotations

import importlib
import subprocess
from collections.abc import Sequence
from datetime import UTC, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd

from mrml.data.bars import D1Bar
from mrml.strategy.runner import SignalIntent
from mrml.strategy.runtime import (
    PHASEAWARE_V0_COMPOSITION,
    PINNED_MPML_REVISION,
    CanonicalPhaseAwareRuntime,
    CompletedD1History,
    InsufficientStrategyHistory,
    InvalidStrategyHistory,
    InvalidStrategyOutput,
    PhaseAwareDecision,
    PhaseAwareDecisionAdapter,
)

MPML_RUNTIME_MODULE = "mpml.runtime.phaseaware"
MPML_RUNTIME_PROVENANCE = f"{MPML_RUNTIME_MODULE}.latest_decision"
_GIT_TIMEOUT_SECONDS = 10


class MpmlRuntimeUnavailable(RuntimeError):
    """Raised when the pinned MPML runtime cannot be imported or verified."""


class UnexecutableDecision(InvalidStrategyOutput):
    """A non-flat MPML decision supplies no fixed stop MRML could risk-size."""


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MpmlRuntimeUnavailable(
            f"cannot verify MPML checkout at {root}: git {args[0]} failed"
        ) from exc
    return completed.stdout.strip()


def verify_mpml_checkout(module: ModuleType) -> str:
    """Return the pinned revision after verifying the imported source tree.

    The revision is read from git for the directory the runtime was imported
    from. MPML's own ``MPML_REVISION`` override is deliberately not trusted.
    """
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise MpmlRuntimeUnavailable("MPML runtime module has no source file")
    root = Path(module_file).resolve().parents[2]
    head = _git(root, "rev-parse", "HEAD")
    if head != PINNED_MPML_REVISION:
        raise MpmlRuntimeUnavailable(
            f"MPML checkout {root} is at {head!r}, expected {PINNED_MPML_REVISION}"
        )
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise MpmlRuntimeUnavailable(
            f"MPML checkout {root} has uncommitted changes to tracked files"
        )
    return head


def _history_frame(history: CompletedD1History) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [bar.open for bar in history.bars],
            "High": [bar.high for bar in history.bars],
            "Low": [bar.low for bar in history.bars],
            "Close": [bar.close for bar in history.bars],
            "Volume": [bar.volume for bar in history.bars],
        },
        index=pd.DatetimeIndex([bar.timestamp for bar in history.bars]),
    )


class MpmlPhaseAwareRuntime:
    """``CanonicalPhaseAwareRuntime`` backed by the real MPML implementation."""

    def __init__(self) -> None:
        try:
            self._module = importlib.import_module(MPML_RUNTIME_MODULE)
        except ImportError as exc:
            raise MpmlRuntimeUnavailable(
                f"{MPML_RUNTIME_MODULE} is not importable; put the pinned MPML "
                f"checkout ({PINNED_MPML_REVISION}) on PYTHONPATH"
            ) from exc
        verify_mpml_checkout(self._module)

    def latest_decision(self, history: CompletedD1History) -> PhaseAwareDecision:
        revision = verify_mpml_checkout(self._module)
        module = self._module
        try:
            result = module.latest_decision(_history_frame(history))
        except module.InsufficientHistory as exc:
            raise InsufficientStrategyHistory(str(exc)) from exc
        except module.InvalidHistory as exc:
            raise InvalidStrategyHistory(str(exc)) from exc
        except (module.UnmatchedPhase, module.NonFiniteOutput) as exc:
            raise InvalidStrategyOutput(str(exc)) from exc

        if result.mpml_revision != revision:
            raise InvalidStrategyOutput(
                "MPML reported revision "
                f"{result.mpml_revision!r}, not the verified {revision}"
            )
        if result.input_bars != len(history.bars) or not result.warmup_complete:
            raise InvalidStrategyOutput("MPML did not evaluate the supplied history")
        return PhaseAwareDecision(
            pair=history.pair,
            signal_bar_timestamp=pd.Timestamp(result.timestamp).to_pydatetime(),
            decision_cutoff=history.decision_cutoff,
            actionable_after=history.decision_cutoff,
            canonical_signal=result.signal,
            phase=result.phase,
            stop_loss_pct=result.sl_pct,
            take_profit_pct=result.tp_pct,
            composition=result.composition,
            mpml_revision=result.mpml_revision,
            runtime_provenance=MPML_RUNTIME_PROVENANCE,
        )


def signal_intent_from_decision(
    decision: PhaseAwareDecision, *, reference_close: float
) -> SignalIntent:
    """Represent an MPML decision as a ``SignalIntent`` without executing it.

    Direction is the signal's sign and ``size_multiplier`` its magnitude (the
    canonical phase multiplier); neither is applied here. MPML's ``sl_pct`` is
    a fraction of entry price, so MRML's price-distance ``stop_distance`` is
    that fraction of the latest completed close. This is a unit conversion, not
    an execution price: the entry price is decided by MRML execution. MPML sets
    no fixed stop when TF4 holds a position; MRML will not invent one.
    """
    signal = decision.canonical_signal
    stop_loss_pct = decision.stop_loss_pct or 0.0
    if signal > 0:
        direction = "long"
    elif signal < 0:
        direction = "short"
    else:
        direction = "flat"
    if direction != "flat" and stop_loss_pct <= 0:
        raise UnexecutableDecision(
            f"{decision.phase} signal {signal} has no fixed stop (sl_pct=0); "
            "MRML will not invent a stop distance"
        )
    return SignalIntent(
        direction=direction,
        stop_distance=0.0 if direction == "flat" else stop_loss_pct * reference_close,
        rationale=(
            f"{decision.composition} {decision.phase} signal={signal} "
            f"@ {decision.signal_bar_timestamp.isoformat()}"
        ),
        size_multiplier=abs(signal),
        stop_loss_pct=decision.stop_loss_pct,
        take_profit_pct=decision.take_profit_pct,
        signal_bar_timestamp=decision.signal_bar_timestamp,
        decision_cutoff=decision.decision_cutoff,
    )


class MpmlPhaseAwareStrategy:
    """``StrategyRunner`` strategy seam over the MPML-backed adapter.

    ``bars`` must be completed D1 bars (``DailyRunner`` builds them from
    complete H1 days). A D1 bar's timestamp is its open, so the decision is
    available ``bar_period`` later, at the close; the signal is not shifted.
    """

    def __init__(
        self,
        runtime: CanonicalPhaseAwareRuntime | None = None,
        *,
        bar_period: timedelta = timedelta(days=1),
    ) -> None:
        if bar_period <= timedelta(0):
            raise ValueError("bar_period must be positive")
        self._adapter = PhaseAwareDecisionAdapter(runtime or MpmlPhaseAwareRuntime())
        self._bar_period = bar_period

    def generate_signal(self, pair: str, bars: Sequence[D1Bar]) -> SignalIntent:
        bars = tuple(bars)
        if not bars:
            raise InsufficientStrategyHistory(
                "at least one completed D1 bar is required"
            )
        if bars[-1].timestamp.tzinfo is None:
            raise InvalidStrategyHistory("bar timestamp must be timezone-aware")
        decision = self._adapter.decide(
            pair=pair,
            bars=bars,
            bar_completed=(True,) * len(bars),
            decision_cutoff=bars[-1].timestamp.astimezone(UTC) + self._bar_period,
        )
        if decision.composition != PHASEAWARE_V0_COMPOSITION:
            raise InvalidStrategyOutput("unexpected PhaseAware composition")
        return signal_intent_from_decision(decision, reference_close=bars[-1].close)
