"""Small, practice-only OANDA v20 REST client."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from requests import Session
from requests.exceptions import ConnectionError, RequestException, Timeout

from mrml.data.bars import H1Bar


class OandaError(RuntimeError):
    """Base class for typed OANDA failures."""


class OandaAuthenticationError(OandaError):
    """The configured token was rejected."""


class OandaRateLimitError(OandaError):
    """OANDA rate-limited the client after retries."""


class OandaServerError(OandaError):
    """OANDA returned a server error after retries."""


class OandaRequestError(OandaError):
    """A request timed out or returned an invalid response."""


def validate_account_id(account_id: str) -> None:
    """Accept only the v20 account identifier shape, never path fragments."""
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}-[0-9]{7}-[0-9]{3}", account_id) is None:
        raise ValueError("Invalid OANDA account identifier")


def validate_instrument(instrument: str) -> None:
    """Allow OANDA FX, metal and CFD names without URL metacharacters."""
    if re.fullmatch(r"[A-Z0-9]{2,16}_[A-Z0-9]{2,16}", instrument) is None:
        raise ValueError("Invalid OANDA instrument identifier")


@dataclass(frozen=True)
class AccountSummary:
    account_id: str = field(repr=False)
    currency: str
    balance: Decimal
    nav: Decimal
    open_trade_count: int
    open_position_count: int


@dataclass(frozen=True)
class InstrumentMetadata:
    name: str
    instrument_type: str
    display_precision: int
    pip_location: int
    trade_units_precision: int
    minimum_trade_size: Decimal


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Expected decimal string")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Expected finite decimal")
    return result


def _integer(value: Any, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError("Expected integer")
    return value


@contextmanager
def _private_http_logs() -> Iterator[None]:
    """Keep transport debug URLs/retry exception text out of application logs."""

    class PrivateRequest(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return False

    guard = PrivateRequest()
    loggers = [
        logging.getLogger(name)
        for name in (
            "urllib3.connectionpool",
            "urllib3.util.retry",
            "requests.sessions",
        )
    ]
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)


class OandaClient:
    """Read account metadata and H1 mid candles from the practice API."""

    BASE_URL = "https://api-fxpractice.oanda.com/v3"

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 10.0,
        retries: int = 3,
        backoff_seconds: float = 0.25,
        session: Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not token.strip():
            raise ValueError("OANDA practice token must not be empty")
        if (
            not math.isfinite(timeout_seconds)
            or not math.isfinite(backoff_seconds)
            or timeout_seconds <= 0
            or retries < 0
            or backoff_seconds < 0
        ):
            raise ValueError("timeouts/backoff must be non-negative and retries >= 0")
        self._token = token
        self._timeout = timeout_seconds
        self._retries = retries
        self._backoff = backoff_seconds
        self._session = session or requests.Session()
        self._sleep = sleep

    def get_accounts(self) -> tuple[str, ...]:
        """List account identifiers accessible to the practice token."""
        payload = self._get("/accounts", {})
        try:
            accounts = payload["accounts"]
            if not isinstance(accounts, list):
                raise ValueError("Expected accounts list")
            result = []
            for account in accounts:
                account_id = account["id"]
                validate_account_id(account_id)
                result.append(account_id)
            if len(set(result)) != len(result):
                raise ValueError("Duplicate accounts")
            return tuple(result)
        except (KeyError, TypeError, ValueError):
            raise OandaRequestError("OANDA returned malformed account data") from None

    def get_account_summary(self, account_id: str) -> AccountSummary:
        """Validate an account and retrieve basic balances and open counts."""
        validate_account_id(account_id)
        payload = self._get(f"/accounts/{account_id}/summary", {})
        try:
            account = payload["account"]
            if (
                account["id"] != account_id
                or re.fullmatch(r"[A-Z]{3}", account["currency"]) is None
            ):
                raise ValueError("Unexpected account identity or currency")
            return AccountSummary(
                account_id=account_id,
                currency=account["currency"],
                balance=_decimal(account["balance"]),
                nav=_decimal(account["NAV"]),
                open_trade_count=_integer(account["openTradeCount"], minimum=0),
                open_position_count=_integer(account["openPositionCount"], minimum=0),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise OandaRequestError(
                "OANDA returned malformed account summary"
            ) from None

    def get_account_instruments(
        self, account_id: str
    ) -> tuple[InstrumentMetadata, ...]:
        """Retrieve the account's available instruments and trading precision."""
        validate_account_id(account_id)
        payload = self._get(f"/accounts/{account_id}/instruments", {})
        try:
            instruments = payload["instruments"]
            if not isinstance(instruments, list):
                raise ValueError("Expected instruments list")
            result = []
            for item in instruments:
                validate_instrument(item["name"])
                if item["type"] not in {"CURRENCY", "CFD", "METAL"}:
                    raise ValueError("Unexpected instrument type")
                minimum = _decimal(item["minimumTradeSize"])
                if minimum <= 0:
                    raise ValueError("Invalid minimum trade size")
                result.append(
                    InstrumentMetadata(
                        name=item["name"],
                        instrument_type=item["type"],
                        display_precision=_integer(item["displayPrecision"], minimum=0),
                        pip_location=_integer(item["pipLocation"]),
                        trade_units_precision=_integer(
                            item["tradeUnitsPrecision"], minimum=0
                        ),
                        minimum_trade_size=minimum,
                    )
                )
            if len({item.name for item in result}) != len(result):
                raise ValueError("Duplicate instruments")
            return tuple(result)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise OandaRequestError(
                "OANDA returned malformed instrument data"
            ) from None

    def get_h1_candles(
        self, instrument: str, *, start: datetime, end: datetime
    ) -> tuple[H1Bar, ...]:
        """Fetch complete/incomplete H1 mid candles in a half-open time range."""
        validate_instrument(instrument)
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("start/end must be aware timestamps with start < end")
        payload = self._get(
            f"/instruments/{instrument}/candles",
            {
                "granularity": "H1",
                "price": "M",
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        try:
            candles = payload["candles"]
            if not isinstance(candles, list):
                raise ValueError("Expected candles list")
            if "instrument" in payload and payload["instrument"] != instrument:
                raise ValueError("Unexpected instrument")
            if "granularity" in payload and payload["granularity"] != "H1":
                raise ValueError("Unexpected granularity")
            bars: list[H1Bar] = []
            for candle in candles:
                timestamp = datetime.fromisoformat(
                    candle["time"].replace("Z", "+00:00")
                )
                if timestamp.utcoffset() is None:
                    raise ValueError("Expected aware timestamp")
                timestamp = timestamp.astimezone(UTC)
                prices = tuple(float(_decimal(candle["mid"][key])) for key in "ohlc")
                volume = _integer(candle["volume"], minimum=0)
                if (
                    not start <= timestamp < end
                    or timestamp.minute
                    or timestamp.second
                    or timestamp.microsecond
                    or not all(math.isfinite(price) and price > 0 for price in prices)
                    or prices[2] > min(prices[0], prices[3])
                    or prices[1] < max(prices[0], prices[3])
                    or type(candle["complete"]) is not bool
                    or (bars and timestamp <= bars[-1].timestamp)
                ):
                    raise ValueError("Invalid candle values or ordering")
                bars.append(
                    H1Bar(
                        timestamp,
                        prices[0],
                        prices[1],
                        prices[2],
                        prices[3],
                        volume,
                        candle["complete"],
                    )
                )
            return tuple(bars)
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
            raise OandaRequestError("OANDA returned malformed candle data") from None

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        for attempt in range(self._retries + 1):
            try:
                with _private_http_logs():
                    response = self._session.get(
                        f"{self.BASE_URL}{path}",
                        params=params,
                        headers={"Authorization": "Bearer " + self._token},
                        timeout=self._timeout,
                        allow_redirects=False,
                    )
            except (Timeout, ConnectionError) as error:
                if attempt == self._retries:
                    message = (
                        "OANDA request timed out"
                        if isinstance(error, Timeout)
                        else "OANDA connection failed"
                    )
                    raise OandaRequestError(message) from None
                self._sleep(self._backoff * (2**attempt))
                continue
            except RequestException:
                raise OandaRequestError("OANDA request failed") from None

            if response.status_code in {401, 403}:
                raise OandaAuthenticationError("OANDA rejected the practice token")
            if response.status_code == 429:
                if attempt == self._retries:
                    raise OandaRateLimitError(
                        "OANDA rate limit persisted after retries"
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = (
                        float(retry_after)
                        if retry_after
                        else self._backoff * (2**attempt)
                    )
                except ValueError:
                    delay = self._backoff * (2**attempt)
                self._sleep(delay if math.isfinite(delay) and delay > 0 else 0.0)
                continue
            if 500 <= response.status_code < 600:
                if attempt == self._retries:
                    raise OandaServerError("OANDA server error persisted after retries")
                self._sleep(self._backoff * (2**attempt))
                continue
            if not 200 <= response.status_code < 300:
                raise OandaRequestError(f"OANDA returned HTTP {response.status_code}")
            try:
                data = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                raise OandaRequestError("OANDA returned invalid JSON") from None
            if not isinstance(data, dict):
                raise OandaRequestError("OANDA returned an unexpected response")
            return data
        raise AssertionError("unreachable")
