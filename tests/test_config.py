"""Tests for environment settings and safe defaults."""

from __future__ import annotations

import pytest

from mrml.config import Settings, load_settings


def test_environment_defaults_to_dev() -> None:
    settings = load_settings({"MRML_DATABASE_URL": "postgresql://localhost/mrml"})
    assert settings.environment == "dev"
    assert settings.oanda_environment == "practice"


def test_database_url_is_required() -> None:
    with pytest.raises(ValueError, match="MRML_DATABASE_URL"):
        load_settings({})


def test_live_environment_requires_explicit_override() -> None:
    with pytest.raises(ValueError, match="intentionally unsupported"):
        Settings(environment="live", database_url="postgresql://localhost/mrml")


def test_explicit_live_override_is_accepted() -> None:
    settings = load_settings(
        {
            "MRML_DATABASE_URL": "postgresql://localhost/mrml",
            "MRML_ENVIRONMENT": "live",
            "MRML_ALLOW_LIVE": "true",
        }
    )
    assert settings.environment == "live"
    assert settings.allow_live


def test_unsupported_oanda_environment_fails() -> None:
    with pytest.raises(ValueError, match="practice"):
        Settings(
            database_url="postgresql://localhost/mrml",
            oanda_environment="live",
        )


def test_invalid_boolean_fails() -> None:
    with pytest.raises(ValueError, match="MRML_ALLOW_LIVE"):
        load_settings(
            {
                "MRML_DATABASE_URL": "postgresql://localhost/mrml",
                "MRML_ALLOW_LIVE": "sometimes",
            }
        )
