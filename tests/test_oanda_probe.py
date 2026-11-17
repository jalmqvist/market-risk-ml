"""Offline practice contract, diagnostic and secret-handling tests."""

from __future__ import annotations

import logging
import traceback
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from requests import Session
from requests.exceptions import ConnectionError, RequestException, Timeout

from mrml.data import oanda_probe
from mrml.data.oanda import (
    OandaAuthenticationError,
    OandaClient,
    OandaRateLimitError,
    OandaRequestError,
    OandaServerError,
)
from mrml.data.oanda_probe import ProbeConfig, run_probe

ACCOUNT = "101-001-1234567-001"  # Synthetic identifier, never a real account.
TOKEN = "synthetic-secret-token"
NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
SUMMARY = {
    "account": {
        "id": ACCOUNT,
        "currency": "USD",
        "balance": "10000.00",
        "NAV": "10001.25",
        "openTradeCount": 1,
        "openPositionCount": 1,
    }
}
INSTRUMENTS = {
    "instruments": [
        {
            "name": "EUR_USD",
            "type": "CURRENCY",
            "displayPrecision": 5,
            "pipLocation": -4,
            "tradeUnitsPrecision": 0,
            "minimumTradeSize": "1",
        }
    ]
}
CANDLE = {
    "time": "2026-11-17T11:00:00.000000000Z",
    "complete": True,
    "volume": 12,
    "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
}
CANDLES = {"instrument": "EUR_USD", "granularity": "H1", "candles": [CANDLE]}


class Response:
    def __init__(self, payload: Any, status: int = 200, **headers: str) -> None:
        self.payload = payload
        self.status_code = status
        self.headers = headers

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        self.calls.append({"url": url, **kwargs})
        # Simulate urllib3 DEBUG logging, including a retry exception with secrets.
        logging.getLogger("urllib3.connectionpool").debug("GET %s", url)
        logging.getLogger("urllib3.util.retry").debug("retry: %s", TOKEN)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client_for(*responses: Any, retries: int = 0) -> tuple[OandaClient, FakeSession]:
    session = FakeSession(
        [
            item if isinstance(item, (Response, Exception)) else Response(item)
            for item in responses
        ]
    )
    return OandaClient(
        TOKEN, session=cast(Session, session), retries=retries, sleep=lambda _: None
    ), session


def test_public_account_and_instrument_contracts() -> None:
    client, session = client_for({"accounts": [{"id": ACCOUNT}]}, SUMMARY, INSTRUMENTS)
    assert client.get_accounts() == (ACCOUNT,)
    summary = client.get_account_summary(ACCOUNT)
    assert summary.balance == Decimal("10000.00")
    assert summary.nav == Decimal("10001.25")
    assert summary.open_trade_count == summary.open_position_count == 1
    assert ACCOUNT not in repr(summary)
    metadata = client.get_account_instruments(ACCOUNT)[0]
    assert metadata.name == "EUR_USD"
    assert metadata.display_precision == 5
    assert metadata.pip_location == -4
    assert metadata.trade_units_precision == 0
    assert metadata.minimum_trade_size == Decimal(1)
    assert [call["url"] for call in session.calls] == [
        f"{OandaClient.BASE_URL}/accounts",
        f"{OandaClient.BASE_URL}/accounts/{ACCOUNT}/summary",
        f"{OandaClient.BASE_URL}/accounts/{ACCOUNT}/instruments",
    ]


@pytest.mark.parametrize("configured", [True, False])
def test_complete_probe_is_bounded_read_only_and_private(configured, caplog) -> None:
    responses = [SUMMARY, INSTRUMENTS, CANDLES]
    if not configured:
        responses.insert(0, {"accounts": [{"id": ACCOUNT}]})
    client, session = client_for(*responses)
    caplog.set_level(logging.DEBUG)
    report = run_probe(
        ProbeConfig(TOKEN, ACCOUNT if configured else None), client, now=NOW
    )
    assert "PASS" in report and "candles=1 complete=1 incomplete=0" in report
    assert "2026-11-17T11:00:00+00:00" in report
    assert ACCOUNT not in report + caplog.text
    assert TOKEN not in report + caplog.text
    for call in session.calls:
        assert call["url"].startswith("https://api-fxpractice.oanda.com/v3/")
        assert call["headers"] == {"Authorization": f"Bearer {TOKEN}"}
        assert call["allow_redirects"] is False
        assert call["timeout"] == 10
        assert all(
            word not in call["url"] for word in ("orders", "positions", "trades")
        )
    params = session.calls[-1]["params"]
    assert params["granularity"] == "H1" and params["price"] == "M"
    assert datetime.fromisoformat(params["to"]) - datetime.fromisoformat(
        params["from"]
    ) == timedelta(hours=96)
    assert (
        "MRML_DATABASE_URL"
        not in ProbeConfig.from_environ({"MRML_OANDA_TOKEN": TOKEN}).__dict__
    )


@pytest.mark.parametrize("token", ["", " ", "\n", "has space"])
def test_missing_or_invalid_token(token) -> None:
    with pytest.raises(ValueError):
        ProbeConfig.from_environ({"MRML_OANDA_TOKEN": token})
    with pytest.raises(ValueError):
        ProbeConfig.from_environ({})


@pytest.mark.parametrize(
    "account", ["", "../accounts", ACCOUNT + "/orders", "123", "é"]
)
def test_invalid_account_cannot_reach_transport(account) -> None:
    client, session = client_for()
    for method in (client.get_account_summary, client.get_account_instruments):
        with pytest.raises(ValueError):
            method(account)
    with pytest.raises(ValueError):
        ProbeConfig(TOKEN, account)
    assert not session.calls


@pytest.mark.parametrize("instrument", ["", "../accounts", "eur_usd", "EUR_USD?x=1"])
def test_invalid_instrument_cannot_reach_transport(instrument) -> None:
    client, session = client_for()
    with pytest.raises(ValueError):
        client.get_h1_candles(instrument, start=NOW - timedelta(hours=1), end=NOW)
    with pytest.raises(ValueError):
        ProbeConfig(TOKEN, instrument=instrument)
    assert not session.calls


@pytest.mark.parametrize(
    "accounts", [[], [{"id": ACCOUNT}, {"id": "101-001-7654321-001"}]]
)
def test_discovery_requires_unambiguous_account(accounts) -> None:
    client, session = client_for({"accounts": accounts})
    with pytest.raises(ValueError, match="MRML_OANDA_ACCOUNT_ID"):
        run_probe(ProbeConfig(TOKEN), client, now=NOW)
    assert len(session.calls) == 1


def test_unavailable_instrument_fails_before_candles() -> None:
    client, session = client_for(SUMMARY, {"instruments": []})
    with pytest.raises(ValueError, match="unavailable"):
        run_probe(ProbeConfig(TOKEN, ACCOUNT), client, now=NOW)
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("get_accounts", {}),
        ("get_accounts", {"accounts": {}}),
        ("get_accounts", {"accounts": [None]}),
        ("get_accounts", {"accounts": [{"id": "bad"}]}),
        ("get_accounts", {"accounts": [{"id": ACCOUNT}] * 2}),
        ("get_account_summary", {}),
        ("get_account_summary", {"account": []}),
        ("get_account_instruments", {}),
        ("get_account_instruments", {"instruments": {}}),
        ("get_account_instruments", {"instruments": [None]}),
    ],
)
def test_malformed_account_and_instrument_envelopes(method, payload) -> None:
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError, match="malformed"):
        getattr(client, method)(*(() if method == "get_accounts" else (ACCOUNT,)))


@pytest.mark.parametrize("field", list(SUMMARY["account"]))
def test_summary_requires_all_fields(field) -> None:
    payload = deepcopy(SUMMARY)
    del payload["account"][field]
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError):
        client.get_account_summary(ACCOUNT)


@pytest.mark.parametrize("field", list(INSTRUMENTS["instruments"][0]))
def test_metadata_requires_all_fields(field) -> None:
    payload = deepcopy(INSTRUMENTS)
    del payload["instruments"][0][field]
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError):
        client.get_account_instruments(ACCOUNT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("NAV", "NaN"),
        ("balance", "Infinity"),
        ("balance", 100),
        ("openTradeCount", True),
        ("openPositionCount", -1),
        ("id", "101-001-7654321-001"),
        ("currency", None),
    ],
)
def test_invalid_summary_fields(field, value) -> None:
    payload = deepcopy(SUMMARY)
    payload["account"][field] = value
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError):
        client.get_account_summary(ACCOUNT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimumTradeSize", "NaN"),
        ("minimumTradeSize", "0"),
        ("displayPrecision", -1),
        ("pipLocation", True),
        ("type", []),
        ("name", "../x"),
    ],
)
def test_invalid_metadata_fields(field, value) -> None:
    payload = deepcopy(INSTRUMENTS)
    payload["instruments"][0][field] = value
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError):
        client.get_account_instruments(ACCOUNT)


@pytest.mark.parametrize("payload", [None, [], "body", 1, ValueError(TOKEN + ACCOUNT)])
def test_invalid_json_or_response_type_does_not_leak(payload) -> None:
    client, _ = client_for(Response(payload))
    with pytest.raises(OandaRequestError) as caught:
        client.get_accounts()
    rendered = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in rendered and ACCOUNT not in rendered


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, OandaAuthenticationError),
        (403, OandaAuthenticationError),
        (404, OandaRequestError),
        (400, OandaRequestError),
        (302, OandaRequestError),
        (429, OandaRateLimitError),
        (503, OandaServerError),
    ],
)
def test_http_errors_and_retry_exhaustion(status, error) -> None:
    retries = 2 if status in (429, 503) else 0
    client, session = client_for(
        *[Response({"errorMessage": TOKEN + ACCOUNT}, status)] * (retries + 1),
        retries=retries,
    )
    with pytest.raises(error) as caught:
        client.get_accounts()
    assert len(session.calls) == retries + 1
    assert TOKEN not in str(caught.value) and ACCOUNT not in str(caught.value)


@pytest.mark.parametrize("retry_after", ["0.2", "bad", "nan", "-1"])
def test_rate_limit_recovers_with_safe_retry_delay(retry_after) -> None:
    client, session = client_for(
        Response({}, 429, **{"Retry-After": retry_after}),
        {"accounts": [{"id": ACCOUNT}]},
        retries=1,
    )
    delays = []
    client._sleep = delays.append
    assert client.get_accounts() == (ACCOUNT,)
    assert len(session.calls) == 2
    assert len(delays) == 1 and 0 <= delays[0] <= 0.25


@pytest.mark.parametrize("failure", [Timeout, ConnectionError, RequestException])
def test_transport_failures_hide_underlying_secrets(failure) -> None:
    client, session = client_for(*[failure(TOKEN + ACCOUNT)] * 2, retries=1)
    with pytest.raises(OandaRequestError) as caught:
        client.get_account_summary(ACCOUNT)
    rendered = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in rendered and ACCOUNT not in rendered
    assert len(session.calls) == (1 if failure is RequestException else 2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("time", "2026-11-17T11:00:00"),
        ("time", None),
        ("time", "bad"),
        ("complete", "true"),
        ("volume", -1),
        ("volume", True),
        ("mid", {"o": "nan", "h": "2", "l": "1", "c": "1"}),
        ("mid", {"o": "2", "h": "1", "l": "1", "c": "1"}),
    ],
)
def test_malformed_candles_fail_closed(field, value) -> None:
    candle = deepcopy(CANDLE)
    candle[field] = value
    client, _ = client_for({"candles": [candle]})
    with pytest.raises(OandaRequestError):
        client.get_h1_candles("EUR_USD", start=NOW - timedelta(hours=96), end=NOW)


@pytest.mark.parametrize("candles", [[], [CANDLE] * 2])
def test_empty_or_duplicate_sample_fails(candles) -> None:
    client, _ = client_for(SUMMARY, INSTRUMENTS, {"candles": candles})
    with pytest.raises((ValueError, OandaRequestError)):
        run_probe(ProbeConfig(TOKEN, ACCOUNT), client, now=NOW)


@pytest.mark.parametrize("status", [200, 401, 403, 404, 429, 503])
def test_cli_exit_status_and_redaction(status, monkeypatch, capsys, caplog) -> None:
    monkeypatch.setenv("MRML_OANDA_TOKEN", TOKEN)
    monkeypatch.setenv("MRML_OANDA_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("MRML_DATABASE_URL", raising=False)
    caplog.set_level(logging.DEBUG)
    # Use candle timestamps relative to the real CLI clock, without network access.
    candle = deepcopy(CANDLE)
    candle["time"] = (
        datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        - timedelta(hours=1)
    ).isoformat()
    client, _ = client_for(
        *(
            [SUMMARY, INSTRUMENTS, {"candles": [candle]}]
            if status == 200
            else [Response({"errorMessage": TOKEN + ACCOUNT}, status)]
        )
    )
    monkeypatch.setattr(oanda_probe, "OandaClient", lambda _: client)
    assert oanda_probe.main() == (0 if status == 200 else 1)
    output = capsys.readouterr()
    assert ("PASS" if status == 200 else "FAIL") in output.out
    assert TOKEN not in output.out + output.err + caplog.text
    assert ACCOUNT not in output.out + output.err + caplog.text


def test_cli_missing_configuration_returns_failure(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MRML_OANDA_TOKEN", raising=False)
    assert oanda_probe.main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_incomplete_candles_are_reported_without_daily_aggregation() -> None:
    candle = {**CANDLE, "complete": False}
    client, _ = client_for(SUMMARY, INSTRUMENTS, {"candles": [candle]})
    report = run_probe(ProbeConfig(TOKEN, ACCOUNT), client, now=NOW)
    assert "complete=0 incomplete=1" in report


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"candles": {}},
        {"candles": [None]},
        {**CANDLES, "instrument": "GBP_USD"},
        {**CANDLES, "granularity": "D"},
    ],
)
def test_malformed_candle_envelopes(payload) -> None:
    client, _ = client_for(payload)
    with pytest.raises(OandaRequestError):
        client.get_h1_candles("EUR_USD", start=NOW - timedelta(hours=96), end=NOW)


def test_cfd_instrument_metadata_is_accepted() -> None:
    payload = deepcopy(INSTRUMENTS)
    payload["instruments"][0].update(name="DE30_EUR", type="CFD")
    client, _ = client_for(payload)
    assert client.get_account_instruments(ACCOUNT)[0].name == "DE30_EUR"


@pytest.mark.parametrize(
    "failure",
    [
        Response(ValueError(TOKEN + ACCOUNT)),
        Response({"accounts": "invalid"}),
        Timeout(TOKEN + ACCOUNT),
    ],
)
def test_cli_malformed_or_transport_failure_is_nonzero(
    failure, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MRML_OANDA_TOKEN", TOKEN)
    monkeypatch.delenv("MRML_OANDA_ACCOUNT_ID", raising=False)
    client, _ = client_for(failure)
    monkeypatch.setattr(oanda_probe, "OandaClient", lambda _: client)
    assert oanda_probe.main() == 1
    output = capsys.readouterr()
    assert "FAIL" in output.out
    assert TOKEN not in output.out + output.err
    assert ACCOUNT not in output.out + output.err
