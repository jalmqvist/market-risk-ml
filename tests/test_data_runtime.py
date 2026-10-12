"""Offline tests for OANDA requests and deterministic candle transformations."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

import pytest
from requests import Session
from requests.exceptions import Timeout

from mrml.data.bars import BarGapError, D1Bar, H1Bar, build_d1_bars
from mrml.data.oanda import (
    OandaAuthenticationError,
    OandaClient,
    OandaRateLimitError,
    OandaRequestError,
    OandaServerError,
)
from mrml.data.parity import BarDifference, compare_bars


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.ok = status_code < 400

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _h1_day(run_date: date) -> tuple[H1Bar, ...]:
    start = datetime.combine(run_date, time.min, tzinfo=UTC) - timedelta(hours=1)
    return tuple(
        H1Bar(
            timestamp=start + timedelta(hours=index),
            open=float(index + 1),
            high=float(index + 3),
            low=float(index) + 0.5,
            close=float(index + 2),
            volume=1.0,
        )
        for index in range(24)
    )


def test_h1_to_d1_uses_fixed_utc_plus_one_boundaries() -> None:
    target_date = date(2026, 10, 2)
    daily = build_d1_bars(_h1_day(target_date))

    assert len(daily) == 1
    assert daily[0].timestamp == datetime(2026, 10, 2, tzinfo=UTC)
    assert daily[0].open == 1
    assert daily[0].high == 26
    assert daily[0].low == 0.5
    assert daily[0].close == 25
    assert daily[0].volume == 24


def test_bar_builder_accepts_configured_boundary_offset() -> None:
    target_date = date(2026, 10, 2)
    start = datetime.combine(target_date, time.min, tzinfo=UTC)
    bars = tuple(
        H1Bar(start + timedelta(hours=index), 1, 2, 0.5, 1.5) for index in range(24)
    )

    daily = build_d1_bars(bars, boundary_offset_hours=0)

    assert len(daily) == 1
    assert daily[0].timestamp.date() == target_date


def test_bar_builder_fails_closed_on_gaps_duplicates_and_partial_candles() -> None:
    bars = _h1_day(date(2026, 10, 2))
    with pytest.raises(BarGapError, match="24 consecutive"):
        build_d1_bars(bars[:-1])
    with pytest.raises(BarGapError, match="24 consecutive"):
        build_d1_bars((*bars, bars[-1]))
    with pytest.raises(BarGapError, match="Incomplete H1"):
        build_d1_bars((*bars[:-1], H1Bar(**{**bars[-1].__dict__, "complete": False})))


def test_bar_builder_rejects_naive_or_invalid_prices() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_d1_bars((H1Bar(datetime(2026, 1, 1), 1, 2, 0, 1),))
    invalid = H1Bar(datetime(2026, 1, 1, tzinfo=UTC), open=5, high=2, low=0, close=1)
    with pytest.raises(ValueError, match="values are invalid"):
        build_d1_bars((invalid,))


def test_parity_comparison_is_sorted_and_uses_only_overlap() -> None:
    bars = _h1_day(date(2026, 10, 2))
    expected = build_d1_bars(bars)
    actual = (
        D1Bar(expected[0].timestamp, 2, 26, 0.5, 25, 24),
        D1Bar(datetime(2026, 10, 3, tzinfo=UTC), 1, 2, 0, 1, 1),
    )

    assert compare_bars(expected, actual) == (
        BarDifference(expected[0].timestamp, "open", 1, 2),
    )
    assert compare_bars(expected, actual, tolerance=1.0) == ()


def test_oanda_client_retries_transient_errors_and_uses_practice_endpoint() -> None:
    candle = {
        "time": "2026-10-01T23:00:00.000000000Z",
        "mid": {"o": "1", "h": "2", "l": "0.5", "c": "1.5"},
        "volume": 12,
        "complete": True,
    }
    session = FakeSession([FakeResponse(500), FakeResponse(200, {"candles": [candle]})])
    delays: list[float] = []
    client = OandaClient(
        "practice-token",
        retries=1,
        backoff_seconds=0.1,
        session=cast(Session, session),
        sleep=delays.append,
    )
    result = client.get_h1_candles(
        "EUR_USD",
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 10, 2, tzinfo=UTC),
    )

    assert len(session.calls) == 2
    assert session.calls[0]["url"].startswith("https://api-fxpractice.oanda.com/")
    assert session.calls[0]["timeout"] > 0
    assert session.calls[0]["headers"]["Authorization"].startswith("Bearer ")
    assert session.calls[0]["headers"]["Authorization"].endswith("practice-token")
    assert delays == [0.1]
    assert result[0].complete
    assert result[0].volume == 12


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(401), OandaAuthenticationError),
        (FakeResponse(503), OandaServerError),
        (FakeResponse(429), OandaRateLimitError),
    ],
)
def test_oanda_client_maps_http_failures_to_typed_errors(
    response: FakeResponse, error: type[Exception]
) -> None:
    client = OandaClient(
        "practice-token",
        retries=0,
        session=cast(Session, FakeSession([response])),
        sleep=lambda _: None,
    )
    with pytest.raises(error):
        client._get("/accounts", {})


def test_oanda_client_retries_timeouts_and_fails_with_typed_error() -> None:
    session = FakeSession([Timeout(), Timeout()])
    client = OandaClient(
        "practice-token",
        retries=1,
        session=cast(Session, session),
        sleep=lambda _: None,
    )
    with pytest.raises(OandaRequestError, match="timed out"):
        client._get("/accounts", {})
    assert len(session.calls) == 2


def test_oanda_client_rejects_malformed_instrument_and_payload() -> None:
    client = OandaClient(
        "practice-token",
        session=cast(Session, FakeSession([FakeResponse(200, {"candles": [{}]})])),
        sleep=lambda _: None,
    )
    with pytest.raises(ValueError, match="instrument"):
        client.get_h1_candles(
            "../accounts",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(OandaRequestError, match="malformed"):
        client.get_h1_candles(
            "EUR_USD",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        )
