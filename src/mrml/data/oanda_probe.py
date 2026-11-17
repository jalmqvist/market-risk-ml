"""Standalone, read-only OANDA practice connectivity diagnostic."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from mrml.data.oanda import (
    OandaClient,
    OandaError,
    validate_account_id,
    validate_instrument,
)


@dataclass(frozen=True)
class ProbeConfig:
    token: str = field(repr=False)
    account_id: str | None = field(default=None, repr=False)
    instrument: str = "EUR_USD"

    def __post_init__(self) -> None:
        if not self.token.strip() or any(char.isspace() for char in self.token):
            raise ValueError("MRML_OANDA_TOKEN is required; whitespace is not allowed")
        if self.account_id is not None:
            validate_account_id(self.account_id)
        validate_instrument(self.instrument)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> ProbeConfig:
        return cls(
            token=environ.get("MRML_OANDA_TOKEN", ""),
            account_id=environ.get("MRML_OANDA_ACCOUNT_ID"),
            instrument=environ.get("MRML_OANDA_INSTRUMENT", "EUR_USD"),
        )


def run_probe(config: ProbeConfig, client: OandaClient, *, now: datetime) -> str:
    """Run only GET operations; return a compact report without account identifiers."""
    if now.utcoffset() is None:
        raise ValueError("Probe clock must be timezone-aware")
    account_id = config.account_id
    if account_id is None:
        accounts = client.get_accounts()
        if len(accounts) != 1:
            raise ValueError(
                "Set MRML_OANDA_ACCOUNT_ID: expected exactly one discovered account"
            )
        account_id = accounts[0]
    summary = client.get_account_summary(account_id)
    instruments = client.get_account_instruments(account_id)
    selected = next(
        (item for item in instruments if item.name == config.instrument), None
    )
    if selected is None:
        raise ValueError("Requested instrument is unavailable to the selected account")
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=96)
    bars = client.get_h1_candles(config.instrument, start=start, end=end)
    if not bars or len(bars) > 96:
        raise ValueError("Expected a nonempty H1 sample of at most 96 candles")
    complete = sum(bar.complete for bar in bars)
    return (
        "PASS OANDA practice (read-only): account validated; "
        f"currency={summary.currency}; "
        f"instruments={len(instruments)}\n"
        f"{selected.name} H1 mid: candles={len(bars)} complete={complete} "
        f"incomplete={len(bars) - complete}; "
        f"range={bars[0].timestamp.isoformat()}..{bars[-1].timestamp.isoformat()}"
    )


def main() -> int:
    try:
        config = ProbeConfig.from_environ(os.environ)
        report = run_probe(config, OandaClient(config.token), now=datetime.now(UTC))
        # Even unusual credentials that happen to match displayed metadata stay private.
        for secret in (config.token, config.account_id):
            if secret:
                report = report.replace(secret, "[redacted]")
        print(report)
        return 0
    except (ValueError, OandaError) as error:
        # Errors contain fixed messages rather than input or response bodies.
        reason = str(error)
        for name in ("MRML_OANDA_TOKEN", "MRML_OANDA_ACCOUNT_ID"):
            secret = os.environ.get(name)
            if secret:
                reason = reason.replace(secret, "[redacted]")
        print(f"FAIL OANDA practice (read-only): {reason}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
