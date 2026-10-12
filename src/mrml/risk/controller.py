"""Deterministic risk-limit checks independent of storage and broker I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.005
    max_positions_global: int = 3
    max_positions_per_pair: int = 1
    max_currency_exposure: float = 3.0
    max_drawdown_pct: float = 0.20
    max_daily_loss_pct: float = 0.03

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (
                self.risk_per_trade,
                self.max_currency_exposure,
                self.max_drawdown_pct,
                self.max_daily_loss_pct,
            )
        ):
            raise ValueError("risk limits must be finite")
        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if (
            not isinstance(self.max_positions_global, int)
            or isinstance(self.max_positions_global, bool)
            or not isinstance(self.max_positions_per_pair, int)
            or isinstance(self.max_positions_per_pair, bool)
            or self.max_positions_global < 1
            or self.max_positions_per_pair < 1
        ):
            raise ValueError("position limits must be at least one")
        if self.max_currency_exposure <= 0:
            raise ValueError("max_currency_exposure must be positive")
        if not 0 < self.max_drawdown_pct <= 1:
            raise ValueError("max_drawdown_pct must be in (0, 1]")
        if not 0 < self.max_daily_loss_pct <= 1:
            raise ValueError("max_daily_loss_pct must be in (0, 1]")


@dataclass(frozen=True)
class RiskContext:
    equity: float
    open_positions_by_pair: dict[str, int] = field(default_factory=dict)
    currency_exposure: dict[str, float] = field(default_factory=dict)
    account_currency: str = "USD"
    currency_to_account: dict[str, float] = field(default_factory=lambda: {"USD": 1.0})
    drawdown_pct: float = 0.0
    daily_loss_pct: float = 0.0
    bundle_stale: bool = False


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    risk_amount: float
    units: float


def evaluate_trade(
    limits: RiskLimits,
    context: RiskContext,
    *,
    pair: str,
    stop_distance: float,
    currency_exposure_delta: dict[str, float] | None = None,
) -> RiskDecision:
    """Return a flat-sized decision and every applicable machine reason."""
    reasons: list[str] = []
    if not pair or "_" not in pair:
        reasons.append("INVALID_PAIR")
    quote_currency = pair.split("_", maxsplit=1)[-1]
    conversion_rate = (
        1.0
        if quote_currency == context.account_currency
        else context.currency_to_account.get(quote_currency)
    )
    finite_context = all(
        math.isfinite(value)
        for value in (
            context.equity,
            context.drawdown_pct,
            context.daily_loss_pct,
            *context.currency_exposure.values(),
            *context.currency_to_account.values(),
            *(currency_exposure_delta or {}).values(),
        )
    )
    if (
        not finite_context
        or context.drawdown_pct < 0
        or context.daily_loss_pct < 0
        or len(context.account_currency) != 3
        or any(
            not currency or not math.isfinite(rate) or rate <= 0
            for currency, rate in context.currency_to_account.items()
        )
        or any(count < 0 for count in context.open_positions_by_pair.values())
    ):
        reasons.append("INVALID_CONTEXT")
    if not math.isfinite(context.equity) or context.equity <= 0:
        reasons.append("INVALID_EQUITY")
    if not math.isfinite(stop_distance) or stop_distance <= 0:
        reasons.append("INVALID_STOP_DISTANCE")
    if conversion_rate is None:
        reasons.append("MISSING_CURRENCY_CONVERSION")
    if context.bundle_stale:
        reasons.append("STALE_BUNDLE")
    if context.drawdown_pct >= limits.max_drawdown_pct:
        reasons.append("DRAWDOWN_KILL_SWITCH")
    if context.daily_loss_pct >= limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    position_count = sum(context.open_positions_by_pair.values())
    if position_count >= limits.max_positions_global:
        reasons.append("MAX_POSITIONS_GLOBAL")
    if context.open_positions_by_pair.get(pair, 0) >= limits.max_positions_per_pair:
        reasons.append("MAX_POSITIONS_PER_PAIR")
    deltas = currency_exposure_delta or {}
    for currency in context.currency_exposure.keys() | deltas.keys():
        current = context.currency_exposure.get(currency, 0.0)
        delta = deltas.get(currency, 0.0)
        if abs(current + delta) > limits.max_currency_exposure:
            reasons.append("CURRENCY_EXPOSURE_CAP")
            break

    risk_amount = (
        context.equity * limits.risk_per_trade
        if math.isfinite(context.equity) and context.equity > 0
        else 0.0
    )
    units = (
        risk_amount / (stop_distance * conversion_rate)
        if stop_distance > 0 and conversion_rate is not None
        else 0.0
    )
    if not math.isfinite(units):
        reasons.append("INVALID_STOP_DISTANCE")
        units = 0.0
    return RiskDecision(not reasons, tuple(dict.fromkeys(reasons)), risk_amount, units)
