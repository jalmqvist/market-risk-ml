"""Safe logging configuration."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure standard output logging; raises ValueError for an invalid level."""
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
