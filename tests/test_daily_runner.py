"""Offline integration coverage for daily-run fail-closed orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mrml.config import Settings
from mrml.contracts.bundle import load_bundle
from mrml.data.bars import D1Bar, H1Bar
from mrml.journal.models import (
    ConfigSnapshot,
    Fill,
    Order,
    RiskDecisionRow,
    Run,
    Signal,
)
from mrml.journal.repository import JournalRepository
from mrml.risk.controller import RiskContext
from mrml.runner.daily import DailyRunner, FileRunLock
from mrml.strategy.runner import SignalIntent, StrategyRunner

FIXTURES = Path(__file__).parent / "fixtures"
RUN_DATE = date(2026, 10, 1)


class StubStrategy:
    def __init__(self, direction: str = "long") -> None:
        self.direction = direction
        self.calls = 0

    def generate_signal(self, pair: str, bars: Sequence[D1Bar]) -> SignalIntent:
        self.calls += 1
        return SignalIntent(self.direction, 0.01, "stubbed PhaseAware output")


class StubExecutor:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []

    def submit(
        self, *, client_order_id: str, pair: str, direction: str, units: float
    ) -> dict[str, str]:
        self.orders.append(
            {
                "client_order_id": client_order_id,
                "pair": pair,
                "direction": direction,
                "units": units,
            }
        )
        return {"status": "accepted", "broker_order_id": "demo-order-1"}


def _hourly_bars(run_date: date = RUN_DATE) -> tuple[H1Bar, ...]:
    start = datetime.combine(run_date, time.min, tzinfo=UTC) - timedelta(hours=1)
    return tuple(
        H1Bar(
            start + timedelta(hours=hour),
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            volume=100,
        )
        for hour in range(24)
    )


def _settings(*, execution_mode: str = "dry_run") -> Settings:
    return Settings(
        database_url="sqlite://",
        bundle_dir=FIXTURES,
        execution_mode=execution_mode,
        environment="demo" if execution_mode == "demo" else "dev",
        pair_universe=("EUR_USD",),
        pair_compositions=(("EUR_USD", "strategy-a"),),
    )


def _runner(
    tmp_path: Path,
    *,
    execution_mode: str = "dry_run",
    reconcile: object = lambda: True,
    bars: tuple[H1Bar, ...] | None = None,
    direction: str = "long",
    executor: StubExecutor | None = None,
    run_date: date = RUN_DATE,
    context: RiskContext | None = None,
) -> tuple[DailyRunner, JournalRepository, StubStrategy]:
    engine = create_engine("sqlite://")
    journal = JournalRepository(engine)
    journal.create_schema()
    strategy = StubStrategy(direction)
    runner = DailyRunner(
        settings=_settings(execution_mode=execution_mode),
        journal=journal,
        strategy_runner=StrategyRunner(
            pair_compositions={"EUR_USD": "strategy-a"},
            strategies={"strategy-a": strategy},
        ),
        reconcile=reconcile,  # type: ignore[arg-type]
        h1_provider=lambda _: {
            "EUR_USD": bars if bars is not None else _hourly_bars(run_date)
        },
        risk_context=lambda _: context or RiskContext(equity=10_000),
        executor=executor,
        lock=FileRunLock(tmp_path / "daily.lock"),
        bundle_loader=lambda: load_bundle(FIXTURES),
        code_version="test-version",
    )
    return runner, journal, strategy


def _run_row(engine: object) -> Run:
    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(Run))
        assert row is not None
        return row


def test_daily_run_happy_path_is_journaled_and_idempotent(tmp_path: Path) -> None:
    runner, journal, strategy = _runner(tmp_path)

    first = runner.run(RUN_DATE)
    second = runner.run(RUN_DATE)

    assert first.status == "completed"
    assert first.summary["risk_allowed"] == {"EUR_USD": True}
    assert second.reasons == ("ALREADY_RUN",)
    assert strategy.calls == 1
    row = _run_row(journal._engine)
    assert row.status == "completed"
    assert len(row.config_hash) == 64
    assert len(row.bundle_hash) == 64
    assert row.code_version == "test-version"


def test_flat_strategy_signal_is_not_traded_even_when_candidate_is_rank_one(
    tmp_path: Path,
) -> None:
    runner, _, _ = _runner(tmp_path, direction="flat")

    result = runner.run(RUN_DATE)

    assert result.status == "completed"
    assert result.summary["signals"] == {"EUR_USD": "flat"}
    assert result.summary["risk_allowed"] == {"EUR_USD": False}


def test_reconciliation_mismatch_halts_before_data_or_strategy(tmp_path: Path) -> None:
    runner, journal, strategy = _runner(
        tmp_path,
        reconcile=lambda: False,
        bars=(),
    )

    result = runner.run(RUN_DATE)

    assert result.status == "halted"
    assert result.reasons == ("RECONCILIATION_MISMATCH",)
    assert strategy.calls == 0
    assert _run_row(journal._engine).status == "halted"


def test_bar_gap_and_stale_bundle_each_fail_closed(tmp_path: Path) -> None:
    runner, _, _ = _runner(tmp_path, bars=_hourly_bars()[:-1])
    result = runner.run(RUN_DATE)
    assert result.reasons == ("BAR_BUILD_FAILED",)

    stale_runner, _, _ = _runner(tmp_path / "stale", run_date=date(2026, 11, 1))
    stale = stale_runner.run(date(2026, 11, 1))
    assert stale.reasons == ("BUNDLE_STALE_OR_INELIGIBLE",)


def test_demo_mode_hands_off_only_risk_approved_order(tmp_path: Path) -> None:
    executor = StubExecutor()
    runner, _, _ = _runner(tmp_path, execution_mode="demo", executor=executor)

    result = runner.run(RUN_DATE)

    assert result.status == "completed"
    assert len(executor.orders) == 1
    assert executor.orders[0]["pair"] == "EUR_USD"
    assert len(str(executor.orders[0]["client_order_id"])) == 64
    with Session(runner.journal._engine) as session:
        assert len(session.scalars(select(Signal)).all()) == 1
        assert len(session.scalars(select(RiskDecisionRow)).all()) == 1
        assert len(session.scalars(select(ConfigSnapshot)).all()) == 1
        order = session.scalar(select(Order))
        assert order is not None
        order_id = order.id
    runner.journal.record_fill(
        result.run_id,
        order_id,
        units=100,
        price=1.2,
        slippage=0.0001,
    )
    with Session(runner.journal._engine) as session:
        fill = session.scalar(select(Fill))
        assert fill is not None
        assert fill.run_id == result.run_id


def test_demo_mode_does_not_handoff_risk_denied_signal(tmp_path: Path) -> None:
    executor = StubExecutor()
    runner, _, _ = _runner(
        tmp_path,
        execution_mode="demo",
        executor=executor,
        context=RiskContext(equity=10_000, drawdown_pct=0.20),
    )

    result = runner.run(RUN_DATE)

    assert result.status == "completed"
    assert result.summary["risk_allowed"] == {"EUR_USD": False}
    assert executor.orders == []
