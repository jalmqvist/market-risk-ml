"""Environment-based application settings."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Validated settings; direct construction enforces the live-environment guard."""

    environment: str = "dev"
    database_url: str = ""
    bundle_dir: Path = Path("data/bundle")
    max_bundle_age_days: int = 30
    oanda_environment: str = "practice"
    log_level: str = "INFO"
    allow_live: bool = False
    require_evidence_end_date: bool = False
    evidence_end_date_field: str = "evidence_end_date"
    account_mode: str = "practice"
    execution_mode: str = "dry_run"
    pair_universe: tuple[str, ...] = ()
    pair_compositions: tuple[tuple[str, str], ...] = ()
    risk_per_trade: float = 0.005
    max_positions_global: int = 3
    max_positions_per_pair: int = 1
    max_currency_exposure: float = 3.0
    max_drawdown_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    bar_boundary_offset_hours: int = 1

    def __post_init__(self) -> None:
        """Validate values and raise ValueError for unsupported settings."""
        if self.environment not in {"dev", "demo", "live"}:
            raise ValueError("MRML_ENVIRONMENT must be one of: dev, demo, live")
        if self.environment == "live" and not self.allow_live:
            raise ValueError(
                "environment='live' is intentionally unsupported; set "
                "MRML_ALLOW_LIVE=true explicitly to override this guard"
            )
        if not self.database_url.strip():
            raise ValueError("MRML_DATABASE_URL is required")
        if self.max_bundle_age_days < 0:
            raise ValueError("MRML_MAX_BUNDLE_AGE_DAYS must be non-negative")
        if self.oanda_environment != "practice":
            raise ValueError(
                "MRML_OANDA_ENVIRONMENT must be 'practice' in this release"
            )
        if self.account_mode != "practice":
            raise ValueError("MRML_ACCOUNT_MODE must be 'practice' in this release")
        if self.execution_mode not in {"dry_run", "demo"}:
            raise ValueError("MRML_EXECUTION_MODE must be dry_run or demo")
        if self.log_level.upper() not in {
            "CRITICAL",
            "ERROR",
            "WARNING",
            "INFO",
            "DEBUG",
        }:
            raise ValueError("MRML_LOG_LEVEL must be a standard logging level")
        if not self.evidence_end_date_field.strip():
            raise ValueError("MRML_EVIDENCE_END_DATE_FIELD must not be empty")
        if len(set(self.pair_universe)) != len(self.pair_universe):
            raise ValueError("MRML_PAIR_UNIVERSE must not contain duplicates")
        if any(
            re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", pair) is None
            for pair in self.pair_universe
        ):
            raise ValueError("MRML_PAIR_UNIVERSE entries must be FX pairs like EUR_USD")
        compositions = dict(self.pair_compositions)
        if len(compositions) != len(self.pair_compositions):
            raise ValueError("MRML_PAIR_COMPOSITIONS must not contain duplicates")
        if set(compositions) != set(self.pair_universe):
            raise ValueError(
                "MRML_PAIR_COMPOSITIONS must configure every frozen pair exactly once"
            )
        if any(
            not pair or not composition for pair, composition in self.pair_compositions
        ):
            raise ValueError("pair and composition values must not be empty")
        numeric_limits = (
            self.risk_per_trade,
            self.max_currency_exposure,
            self.max_drawdown_pct,
            self.max_daily_loss_pct,
        )
        if not all(math.isfinite(value) for value in numeric_limits):
            raise ValueError("MRML risk limits must be finite")
        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("MRML_RISK_PER_TRADE must be in (0, 1]")
        if self.max_positions_global < 1 or self.max_positions_per_pair < 1:
            raise ValueError("MRML position limits must be at least one")
        if self.max_currency_exposure <= 0:
            raise ValueError("MRML_MAX_CURRENCY_EXPOSURE must be positive")
        if not 0 < self.max_drawdown_pct <= 1:
            raise ValueError("MRML_MAX_DRAWDOWN_PCT must be in (0, 1]")
        if not 0 < self.max_daily_loss_pct <= 1:
            raise ValueError("MRML_MAX_DAILY_LOSS_PCT must be in (0, 1]")
        if not -23 <= self.bar_boundary_offset_hours <= 23:
            raise ValueError("MRML_BAR_BOUNDARY_OFFSET_HOURS must be in [-23, 23]")


def _bool_value(name: str, raw_value: str | None, default: bool) -> bool:
    """Parse a boolean environment value; raises ValueError for unrecognized text."""
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _float_value(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(values.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be numeric") from error


def _int_value(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(values.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _csv_values(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _composition_values(raw: str) -> tuple[tuple[str, str], ...]:
    compositions: list[tuple[str, str]] = []
    for entry in _csv_values(raw):
        pair, separator, composition = entry.partition("=")
        if not separator:
            raise ValueError("MRML_PAIR_COMPOSITIONS entries must use PAIR=STRATEGY")
        compositions.append((pair.strip(), composition.strip()))
    return tuple(compositions)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load MRML-prefixed settings; raises ValueError for missing or invalid values."""
    values = os.environ if environ is None else environ
    database_url = values.get("MRML_DATABASE_URL", "")
    if not database_url.strip():
        raise ValueError("Missing required environment variable MRML_DATABASE_URL")
    raw_age = values.get("MRML_MAX_BUNDLE_AGE_DAYS", "30")
    try:
        max_age = int(raw_age)
    except ValueError as error:
        raise ValueError("MRML_MAX_BUNDLE_AGE_DAYS must be an integer") from error

    return Settings(
        environment=values.get("MRML_ENVIRONMENT", "dev").strip().lower(),
        database_url=database_url,
        bundle_dir=Path(values.get("MRML_BUNDLE_DIR", "data/bundle")),
        max_bundle_age_days=max_age,
        oanda_environment=values.get("MRML_OANDA_ENVIRONMENT", "practice").strip(),
        log_level=values.get("MRML_LOG_LEVEL", "INFO").strip().upper(),
        allow_live=_bool_value("MRML_ALLOW_LIVE", values.get("MRML_ALLOW_LIVE"), False),
        require_evidence_end_date=_bool_value(
            "MRML_REQUIRE_EVIDENCE_END_DATE",
            values.get("MRML_REQUIRE_EVIDENCE_END_DATE"),
            False,
        ),
        evidence_end_date_field=values.get(
            "MRML_EVIDENCE_END_DATE_FIELD", "evidence_end_date"
        ).strip(),
        account_mode=values.get("MRML_ACCOUNT_MODE", "practice").strip().lower(),
        execution_mode=values.get("MRML_EXECUTION_MODE", "dry_run").strip().lower(),
        pair_universe=_csv_values(values.get("MRML_PAIR_UNIVERSE", "")),
        pair_compositions=_composition_values(values.get("MRML_PAIR_COMPOSITIONS", "")),
        risk_per_trade=_float_value(values, "MRML_RISK_PER_TRADE", 0.005),
        max_positions_global=_int_value(values, "MRML_MAX_POSITIONS_GLOBAL", 3),
        max_positions_per_pair=_int_value(values, "MRML_MAX_POSITIONS_PER_PAIR", 1),
        max_currency_exposure=_float_value(values, "MRML_MAX_CURRENCY_EXPOSURE", 3.0),
        max_drawdown_pct=_float_value(values, "MRML_MAX_DRAWDOWN_PCT", 0.20),
        max_daily_loss_pct=_float_value(values, "MRML_MAX_DAILY_LOSS_PCT", 0.03),
        bar_boundary_offset_hours=_int_value(
            values, "MRML_BAR_BOUNDARY_OFFSET_HOURS", 1
        ),
    )
