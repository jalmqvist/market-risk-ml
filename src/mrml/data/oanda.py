"""Small, practice-only OANDA v20 REST client."""

from __future__ import annotations

import math
import re
import time
from datetime import datetime
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


class OandaClient:
    """Fetch H1 mid candles from the practice API; never targets live endpoints."""

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

    def get_h1_candles(
        self, instrument: str, *, start: datetime, end: datetime
    ) -> tuple[H1Bar, ...]:
        """Fetch complete/incomplete H1 mid candles in a half-open time range."""
        if re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", instrument) is None:
            raise ValueError("instrument must be an OANDA instrument name")
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
            return tuple(
                H1Bar(
                    timestamp=datetime.fromisoformat(
                        candle["time"].replace("Z", "+00:00")
                    ),
                    open=float(candle["mid"]["o"]),
                    high=float(candle["mid"]["h"]),
                    low=float(candle["mid"]["l"]),
                    close=float(candle["mid"]["c"]),
                    volume=float(candle["volume"]),
                    complete=candle["complete"] is True,
                )
                for candle in payload["candles"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OandaRequestError("OANDA returned malformed candle data") from error

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        for attempt in range(self._retries + 1):
            try:
                response = self._session.get(
                    f"{self.BASE_URL}{path}",
                    params=params,
                    headers={"Authorization": "Bearer " + self._token},
                    timeout=self._timeout,
                )
            except (Timeout, ConnectionError) as error:
                if attempt == self._retries:
                    message = (
                        "OANDA request timed out"
                        if isinstance(error, Timeout)
                        else "OANDA connection failed"
                    )
                    raise OandaRequestError(message) from error
                self._sleep(self._backoff * (2**attempt))
                continue
            except RequestException as error:
                raise OandaRequestError("OANDA request failed") from error

            if response.status_code == 401:
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
            if not response.ok:
                raise OandaRequestError(f"OANDA returned HTTP {response.status_code}")
            try:
                data = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as error:
                raise OandaRequestError("OANDA returned invalid JSON") from error
            if not isinstance(data, dict):
                raise OandaRequestError("OANDA returned an unexpected response")
            return data
        raise AssertionError("unreachable")
