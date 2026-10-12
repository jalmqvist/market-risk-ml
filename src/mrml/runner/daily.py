"""Idempotent fail-closed daily run orchestration."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from mrml.config import Settings
from mrml.contracts.bundle import RecommendationBundle, load_bundle
from mrml.data.bars import D1Bar, H1Bar, build_d1_bars
from mrml.execution import DemoExecutionHandoff
from mrml.journal.repository import JournalRepository
from mrml.risk.controller import (
    RiskContext,
    RiskDecision,
    RiskLimits,
    evaluate_trade,
)
from mrml.strategy.runner import StrategyDecision, StrategyRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    run_id: str | None
    status: str
    reasons: tuple[str, ...]
    summary: Mapping[str, object]


class FileRunLock:
    """Non-blocking process lock; the journal's unique run date is final guard."""

    def __init__(self, path: str | Path = "/tmp/mrml-daily-run.lock") -> None:
        self.path = Path(path)

    @contextmanager
    def acquire(self) -> Iterator[bool]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class DailyRunner:
    """Coordinate reconciliation, bars, strategies, risk, and demo handoff."""

    def __init__(
        self,
        *,
        settings: Settings,
        journal: JournalRepository,
        strategy_runner: StrategyRunner,
        reconcile: Callable[[], bool],
        h1_provider: Callable[[date], Mapping[str, Sequence[H1Bar]]],
        bar_history_provider: Callable[[str, date], Sequence[D1Bar]],
        risk_context: Callable[[], RiskContext],
        executor: DemoExecutionHandoff | None = None,
        lock: FileRunLock | None = None,
        bundle_loader: Callable[[], RecommendationBundle] | None = None,
        code_version: str = "unknown",
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.strategy_runner = strategy_runner
        if dict(strategy_runner.pair_compositions) != dict(settings.pair_compositions):
            raise ValueError("strategy routing must match the frozen settings")
        self.reconcile = reconcile
        self.h1_provider = h1_provider
        self.bar_history_provider = bar_history_provider
        self.risk_context = risk_context
        self.executor = executor
        self.lock = lock or FileRunLock()
        self.bundle_loader = bundle_loader or self._load_bundle
        self.code_version = code_version
        self.risk_limits = RiskLimits(
            risk_per_trade=settings.risk_per_trade,
            max_positions_global=settings.max_positions_global,
            max_positions_per_pair=settings.max_positions_per_pair,
            max_currency_exposure=settings.max_currency_exposure,
            max_drawdown_pct=settings.max_drawdown_pct,
            max_daily_loss_pct=settings.max_daily_loss_pct,
        )

    def _load_bundle(self) -> RecommendationBundle:
        return load_bundle(
            self.settings.bundle_dir,
            max_evidence_age_days=self.settings.max_bundle_age_days,
            require_evidence_end_date=self.settings.require_evidence_end_date,
            evidence_end_date_field=self.settings.evidence_end_date_field,
        )

    def run(self, run_date: date) -> RunResult:
        if (
            self.settings.environment == "live"
            or self.settings.account_mode != "practice"
        ):
            return RunResult(None, "blocked", ("LIVE_MODE_FORBIDDEN",), {})
        if not self.settings.pair_universe:
            return RunResult(None, "blocked", ("PAIR_UNIVERSE_UNCONFIGURED",), {})
        try:
            with self.lock.acquire() as acquired:
                if not acquired:
                    return RunResult(None, "blocked", ("RUN_LOCK_UNAVAILABLE",), {})
                config_snapshot = asdict(self.settings)
                config_snapshot["bundle_dir"] = str(self.settings.bundle_dir)
                config_hash = _json_hash(config_snapshot)
                bundle_hash = _bundle_hash(self.settings.bundle_dir)
                try:
                    run = self.journal.create_run(
                        run_date=run_date,
                        mode=self.settings.execution_mode,
                        config_hash=config_hash,
                        bundle_hash=bundle_hash,
                        code_version=self.code_version,
                        config_snapshot=config_snapshot,
                    )
                except Exception:
                    return RunResult(None, "blocked", ("JOURNAL_UNAVAILABLE",), {})
                if not run.created:
                    return RunResult(run.run_id, "blocked", ("ALREADY_RUN",), {})
                return self._execute(run.run_id, run_date)
        except OSError:
            logger.exception("Daily run lock could not be acquired")
            return RunResult(None, "blocked", ("RUN_LOCK_UNAVAILABLE",), {})

    def _execute(self, run_id: str, run_date: date) -> RunResult:
        try:
            if not self.reconcile():
                return self._halt(run_id, "RECONCILIATION_MISMATCH")
        except Exception:
            return self._halt(run_id, "RECONCILIATION_FAILED")

        try:
            hourly = self.h1_provider(run_date)
            offset = timedelta(hours=self.settings.bar_boundary_offset_hours)
            start = datetime.combine(run_date, time.min, tzinfo=UTC) - offset
            end = start + timedelta(hours=24)
            daily = {
                pair: build_d1_bars(
                    (
                        bar
                        for bar in hourly[pair]
                        if start <= bar.timestamp.astimezone(UTC) < end
                    ),
                    boundary_offset_hours=self.settings.bar_boundary_offset_hours,
                )
                for pair in self.settings.pair_universe
            }
            if any(len(bars) != 1 for bars in daily.values()):
                return self._halt(run_id, "BAR_DATA_MISSING")
        except Exception:
            return self._halt(run_id, "BAR_BUILD_FAILED")

        strategy_bars: dict[str, tuple[D1Bar, ...]] = {}
        try:
            for pair in self.settings.pair_universe:
                history = tuple(self.bar_history_provider(pair, run_date))
                if any(
                    bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None
                    for bar in history
                ):
                    return self._halt(run_id, "BAR_HISTORY_INVALID")
                history = tuple(
                    replace(bar, timestamp=bar.timestamp.astimezone(UTC))
                    for bar in history
                )
                timestamps = tuple(bar.timestamp for bar in history)
                if (
                    not history
                    or timestamps != tuple(sorted(timestamps))
                    or len(set(timestamps)) != len(timestamps)
                    or any(
                        bar.timestamp.date() >= run_date
                        or not all(
                            math.isfinite(value)
                            for value in (
                                bar.open,
                                bar.high,
                                bar.low,
                                bar.close,
                                bar.volume,
                            )
                        )
                        or bar.low > min(bar.open, bar.close)
                        or bar.high < max(bar.open, bar.close)
                        or bar.low > bar.high
                        for bar in history
                    )
                ):
                    return self._halt(run_id, "BAR_HISTORY_INVALID")
                if (run_date - history[-1].timestamp.date()).days > (
                    self.settings.max_bar_history_age_days
                ):
                    return self._halt(run_id, "BAR_HISTORY_STALE")
                strategy_bars[pair] = (*history, daily[pair][0])
        except Exception:
            return self._halt(run_id, "BAR_HISTORY_INVALID")

        try:
            bundle = self.bundle_loader()
            if not bundle.is_trade_eligible(run_date):
                return self._halt(run_id, "BUNDLE_STALE_OR_INELIGIBLE")
        except Exception:
            return self._halt(run_id, "BUNDLE_INVALID")

        decisions: dict[str, StrategyDecision] = {}
        try:
            for pair in self.settings.pair_universe:
                decision = self.strategy_runner.decide(
                    pair, strategy_bars[pair], bundle
                )
                decisions[pair] = decision
                self.journal.record_signal(
                    run_id,
                    pair=pair,
                    composition=decision.composition,
                    direction=decision.direction,
                    evaluation_id=decision.evaluation_id,
                    candidate_rank=decision.candidate_rank,
                    rationale=decision.rationale,
                )
        except Exception:
            return self._halt(run_id, "STRATEGY_DECISION_INVALID")

        contexts: dict[str, RiskContext] = {}
        risks: dict[str, RiskDecision] = {}
        try:
            base_context = self.risk_context()
            if base_context.account_currency != self.settings.account_currency:
                raise ValueError("risk context account currency does not match config")
            reserved_positions: dict[str, int] = {}
            reserved_exposure: dict[str, float] = {}
            for pair, decision in decisions.items():
                exposure_delta = (
                    {}
                    if decision.direction == "flat"
                    else _exposure_delta(
                        pair, decision.direction, self.risk_limits.risk_per_trade
                    )
                )
                context = RiskContext(
                    equity=base_context.equity,
                    open_positions_by_pair={
                        **base_context.open_positions_by_pair,
                        **{
                            key: base_context.open_positions_by_pair.get(key, 0) + value
                            for key, value in reserved_positions.items()
                        },
                    },
                    currency_exposure={
                        **base_context.currency_exposure,
                        **{
                            key: base_context.currency_exposure.get(key, 0.0) + value
                            for key, value in reserved_exposure.items()
                        },
                    },
                    account_currency=base_context.account_currency,
                    currency_to_account=base_context.currency_to_account,
                    drawdown_pct=base_context.drawdown_pct,
                    daily_loss_pct=base_context.daily_loss_pct,
                    bundle_stale=base_context.bundle_stale,
                )
                contexts[pair] = context
                if decision.direction == "flat":
                    risk = RiskDecision(False, ("NO_SIGNAL",), 0.0, 0.0)
                else:
                    risk = evaluate_trade(
                        self.risk_limits,
                        context,
                        pair=pair,
                        stop_distance=decision.stop_distance,
                        currency_exposure_delta=exposure_delta,
                    )
                risks[pair] = risk
                self.journal.record_risk_decision(run_id, pair, risk)
                if risk.allowed:
                    reserved_positions[pair] = reserved_positions.get(pair, 0) + 1
                    for currency, delta in exposure_delta.items():
                        reserved_exposure[currency] = (
                            reserved_exposure.get(currency, 0.0) + delta
                        )
        except Exception:
            return self._halt(run_id, "RISK_DECISION_FAILED")

        submitted: list[str] = []
        try:
            for pair, risk in risks.items():
                if not risk.allowed or self.settings.execution_mode == "dry_run":
                    continue
                if self.settings.execution_mode != "demo" or self.executor is None:
                    return self._halt(run_id, "DEMO_EXECUTOR_UNAVAILABLE")
                decision = decisions[pair]
                order_id = _order_id(run_date, pair, decision)
                payload = self.executor.submit(
                    client_order_id=order_id,
                    pair=pair,
                    direction=decision.direction,
                    units=risk.units,
                )
                self.journal.record_order(
                    run_id,
                    client_order_id=order_id,
                    status=payload.get("status", "submitted"),
                    payload=payload,
                )
                submitted.append(order_id)
        except Exception:
            return self._halt(run_id, "EXECUTION_HANDOFF_FAILED")

        summary: dict[str, object] = {
            "pairs": len(decisions),
            "signals": {pair: item.direction for pair, item in decisions.items()},
            "risk_allowed": {pair: item.allowed for pair, item in risks.items()},
            "submitted_order_ids": submitted,
            "equity": {pair: contexts[pair].equity for pair in contexts},
            "drawdown_pct": {pair: contexts[pair].drawdown_pct for pair in contexts},
        }
        self.journal.finish_run(run_id, status="completed", summary=summary)
        logger.info("Daily run %s completed: %s", run_id, summary)
        return RunResult(run_id, "completed", (), summary)

    def _halt(self, run_id: str, reason: str) -> RunResult:
        summary = {"halt_reason": reason}
        self.journal.finish_run(run_id, status="halted", summary=summary)
        logger.warning("Daily run %s halted: %s", run_id, reason)
        return RunResult(run_id, "halted", (reason,), summary)


def _json_hash(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _bundle_hash(bundle_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in (
        "recommendations.parquet",
        "recommendations_per_pair.parquet",
        "strategy_evaluations.parquet",
        "experiment_manifest.json",
    ):
        path = bundle_dir / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def _exposure_delta(
    pair: str, direction: str, risk_fraction: float
) -> dict[str, float]:
    base, quote = pair.split("_", maxsplit=1)
    sign = 1.0 if direction == "long" else -1.0
    return {base: sign * risk_fraction, quote: -sign * risk_fraction}


def _order_id(run_date: date, pair: str, decision: StrategyDecision) -> str:
    key = "|".join(
        (run_date.isoformat(), pair, decision.composition, decision.direction)
    )
    return hashlib.sha256(key.encode()).hexdigest()[:64]
