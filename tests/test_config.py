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


def test_pair_composition_and_risk_configuration_are_explicit() -> None:
    settings = load_settings(
        {
            "MRML_DATABASE_URL": "postgresql://localhost/mrml",
            "MRML_PAIR_UNIVERSE": "EUR_USD, GBP_USD",
            "MRML_PAIR_COMPOSITIONS": (
                "EUR_USD=PhaseAware_TF4_MR42,GBP_USD=PhaseAware_TF4_MR2"
            ),
            "MRML_RISK_PER_TRADE": "0.01",
            "MRML_BAR_BOUNDARY_OFFSET_HOURS": "2",
            "MRML_ACCOUNT_CURRENCY": "GBP",
        }
    )

    assert settings.pair_universe == ("EUR_USD", "GBP_USD")
    assert settings.pair_compositions == (
        ("EUR_USD", "PhaseAware_TF4_MR42"),
        ("GBP_USD", "PhaseAware_TF4_MR2"),
    )
    assert settings.risk_per_trade == 0.01
    assert settings.bar_boundary_offset_hours == 2
    assert settings.account_currency == "GBP"


def test_unfrozen_or_live_account_settings_fail_closed() -> None:
    with pytest.raises(ValueError, match="every frozen pair"):
        load_settings(
            {
                "MRML_DATABASE_URL": "postgresql://localhost/mrml",
                "MRML_PAIR_UNIVERSE": "EUR_USD",
            }
        )
    with pytest.raises(ValueError, match="practice"):
        Settings(
            database_url="postgresql://localhost/mrml",
            account_mode="live",
        )
    with pytest.raises(ValueError, match="FX pairs"):
        Settings(database_url="sqlite://", pair_universe=("EURUSD",))
