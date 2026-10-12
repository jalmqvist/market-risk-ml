"""Environment-based application settings."""

from __future__ import annotations

import os
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
    )
