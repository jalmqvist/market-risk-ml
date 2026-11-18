"""Run the standalone IG demo connectivity diagnostic."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from mrml.data.ig import IGClient, IGError, validate_epic


@dataclass(frozen=True)
class ProbeConfig:
    api_key: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    epic: str | None = field(repr=False)

    @classmethod
    def from_environ(cls, values: Mapping[str, str | None]) -> ProbeConfig:
        if values.get("MRML_IG_ENVIRONMENT") != "demo":
            raise ValueError("Set MRML_IG_ENVIRONMENT=demo")
        epic = values.get("MRML_IG_EPIC") or None
        if epic is not None:
            validate_epic(epic)
        return cls(
            api_key=_required(values, "MRML_IG_API_KEY"),
            username=_required(values, "MRML_IG_USERNAME"),
            password=_required(values, "MRML_IG_PASSWORD"),
            epic=epic,
        )


def _required(values: Mapping[str, str | None], name: str) -> str:
    value = values.get(name)
    if not value or not value.strip():
        raise ValueError(f"Set {name}")
    return value


def main() -> int:
    print("MRML / IG DEMO CONNECTIVITY")
    stage = "Configuration"
    try:
        # Read only the working directory's .env. Exported values take precedence.
        with Path(".env").open(encoding="utf-8") as stream:
            local_values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        local_values = {}
    except (OSError, UnicodeError):
        print("Configuration: FAIL (could not read .env)")
        print("Result: FAIL")
        return 1
    try:
        config = ProbeConfig.from_environ({**local_values, **os.environ})
        print("Environment: demo")
        with IGClient(config.api_key, config.username, config.password) as client:
            stage = "Authentication"
            client.authenticate()
            print("Authentication: PASS (session tokens received)")

            stage = "Accounts"
            accounts = client.get_accounts()
            print(f"Accounts: PASS ({len(accounts)} found)")

            stage = "Market"
            market = client.find_market(config.epic)
            print("Market: PASS (EUR/USD EPIC validated)")
            if market.status in {"CLOSED", "OFFLINE", "SUSPENDED"}:
                print(f"Market status: {market.status}; checking available history")

            stage = "Historical prices"
            sample = client.get_h1_sample(market.epic)
            print(
                f"Historical prices: PASS ({len(sample.observations)} "
                f"{sample.resolution})"
            )
            print("Bid/ask OHLC: present; bar completeness unknown")
            print(f"Hourly gaps: {sample.gaps} (may include market closures)")
            if sample.allowance is not None:
                quota = sample.allowance
                print(
                    f"Historical allowance: {quota.remaining}/{quota.total} points "
                    f"remaining; resets in {quota.reset_seconds}s"
                )
            else:
                print("Historical allowance: unreported")
    except (ValueError, IGError) as error:
        print(f"{stage}: FAIL ({error})")
        print("Result: FAIL")
        return 1

    print("Result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
