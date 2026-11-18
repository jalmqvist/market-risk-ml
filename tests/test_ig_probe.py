"""Offline tests for the IG demo source contract and diagnostic."""

from __future__ import annotations

import logging
import subprocess
import sys
import traceback
from copy import deepcopy
from http.client import HTTPConnection
from typing import Any, cast

import pytest
from requests import Session
from requests.exceptions import RequestException

from mrml.data import ig_probe
from mrml.data.ig import (
    IGAuthenticationError,
    IGClient,
    IGError,
    IGMarketError,
    IGQuotaError,
    IGSessionError,
)

KEY, USER, PASSWORD = "synthetic-key", "synthetic-user", "synthetic-password"
CST, TOKEN = "synthetic-cst", "synthetic-security-token"
ACCOUNT = "synthetic-account-id"
EPIC = "CS.D.EURUSD.CFD.IP"
GATEWAY = "https://demo-api.ig.com/gateway/deal"
LOGIN = {"clientId": "synthetic-client", "currentAccountId": ACCOUNT}
ACCOUNTS = {
    "accounts": [
        {
            "accountId": ACCOUNT,
            "currency": "EUR",
            "accountType": "CFD",
            "status": "ENABLED",
            "balance": {"balance": 98765.4321},
        },
        {"accountId": "another-id"},
    ]
}
MARKET = {
    "instrumentName": "EUR/USD",
    "instrumentType": "CURRENCIES",
    "epic": EPIC,
    "expiry": "-",
    "marketStatus": "CLOSED",
}
BAR = {
    "snapshotTimeUTC": "2026-11-17T10:00:00",
    "openPrice": {"bid": 101, "ask": 102},
    "highPrice": {"bid": 105, "ask": 106},
    "lowPrice": {"bid": 99, "ask": 100},
    "closePrice": {"bid": 103, "ask": 104},
}
PRICES = {
    "prices": [
        BAR,
        {**BAR, "snapshotTimeUTC": "2026-11-17T12:00:00Z"},
    ],
    "metadata": {
        "pageData": {"pageNumber": 1, "totalPages": 1},
        "allowance": {
            "remainingAllowance": 998,
            "totalAllowance": 1000,
            "allowanceExpiry": 42,
        },
    },
}


class Reply:
    def __init__(self, payload: Any, status: int = 200, **headers: str) -> None:
        self.payload = payload
        self.status_code = status
        self.headers = headers

    def json(self) -> Any:
        if isinstance(self.payload, ValueError):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses: tuple[Any, ...]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Reply:
        self.calls.append({"method": method, "url": url, **kwargs})
        logging.getLogger("urllib3.connectionpool").debug(
            "Headers: %s", kwargs["headers"]
        )
        logging.getLogger("urllib3.util.retry").debug("Retry: %s", PASSWORD)
        response = next(self.responses)
        if isinstance(response, RequestException):
            raise response
        return response if isinstance(response, Reply) else Reply(response)


def login() -> Reply:
    return Reply(LOGIN, cst=CST, **{"x-security-token": TOKEN})


def client_for(
    *responses: Any, delays: list[float] | None = None
) -> tuple[IGClient, FakeSession]:
    session = FakeSession(responses)
    sleeps: list[float] = [] if delays is None else delays
    return IGClient(
        KEY, USER, PASSWORD, session=cast(Session, session), sleep=sleeps.append
    ), session


def test_workflow_and_exact_http_contract(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, session = client_for(login(), ACCOUNTS, {"markets": [MARKET]}, PRICES)
    with client:
        client.authenticate()
        accounts = client.get_accounts()
        market = client.find_market()
        sample = client.get_h1_sample(market.epic)
    assert len(accounts) == 2
    assert accounts[0].account_id == ACCOUNT
    assert (accounts[0].currency, accounts[0].account_type, accounts[0].status) == (
        "EUR",
        "CFD",
        "ENABLED",
    )
    assert ACCOUNT not in repr(accounts)
    assert sample.epic == EPIC
    assert sample.resolution == "HOUR"
    assert sample.gaps == 1
    assert sample.allowance is not None and sample.allowance.remaining == 998
    assert sample.observations[0].open.bid == 101
    assert sample.observations[0].open.ask == 102
    assert sample.observations[0].timestamp.isoformat() == "2026-11-17T10:00:00+00:00"
    expected = [
        ("POST", "/session", "2", None),
        ("GET", "/accounts", "1", None),
        (
            "GET",
            "/markets",
            "2",
            {"searchTerm": "EUR/USD", "pageNumber": 0, "pageSize": 50},
        ),
        (
            "GET",
            f"/prices/{EPIC}",
            "3",
            {
                "resolution": "HOUR",
                "max": 32,
                "pageSize": 32,
                "pageNumber": 1,
            },
        ),
    ]
    for call, (method, path, version, params) in zip(
        session.calls, expected, strict=True
    ):
        assert (call["method"], call["url"], call["params"]) == (
            method,
            GATEWAY + path,
            params,
        )
        assert call["timeout"] == (5, 10)
        assert call["allow_redirects"] is False
        assert call["headers"]["VERSION"] == version
        assert call["headers"]["X-IG-API-KEY"] == KEY
        if method == "POST":
            assert call["json"] == {"identifier": USER, "password": PASSWORD}
            assert "CST" not in call["headers"]
        else:
            assert call["json"] is None
            assert call["headers"]["CST"] == CST
            assert call["headers"]["X-SECURITY-TOKEN"] == TOKEN
    assert all(secret not in caplog.text for secret in (KEY, CST, TOKEN, PASSWORD))
    with pytest.raises(IGSessionError):
        client.get_accounts()


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (Reply(LOGIN, CST=CST), IGAuthenticationError),
        (Reply(LOGIN, **{"X-SECURITY-TOKEN": TOKEN}), IGAuthenticationError),
        (
            Reply({"errorCode": "error.security.invalid-details"}, 401),
            IGAuthenticationError,
        ),
        (
            Reply({"errorCode": "error.security.api-key-invalid"}, 403),
            IGAuthenticationError,
        ),
        (Reply([], CST=CST, **{"X-SECURITY-TOKEN": TOKEN}), IGAuthenticationError),
        (Reply({}, CST=CST, **{"X-SECURITY-TOKEN": TOKEN}), IGAuthenticationError),
        (Reply(ValueError(PASSWORD)), IGAuthenticationError),
        (Reply({}, 429), IGQuotaError),
        (Reply({}, 503), IGError),
        (RequestException(f"{KEY} {USER} {PASSWORD}"), IGError),
        (Reply({}, 302, Location="https://api.ig.com/gateway/deal/session"), IGError),
    ],
)
def test_login_fails_closed_and_never_retries(reply: Any, error: type[IGError]) -> None:
    client, session = client_for(reply)
    with pytest.raises(error) as exc:
        client.authenticate()
    assert len(session.calls) == 1
    assert exc.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc.value))
    assert all(secret not in rendered for secret in (KEY, USER, PASSWORD, CST, TOKEN))
    with pytest.raises(IGSessionError):
        client.get_accounts()


def test_session_expiration_clears_tokens_without_reauth() -> None:
    client, session = client_for(
        login(), Reply({"errorCode": "error.security.client-token-invalid"}, 401)
    )
    client.authenticate()
    for _ in range(2):
        with pytest.raises(IGSessionError):
            client.get_accounts()
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        {"accounts": []},
        {"accounts": [{}]},
        {"accounts": {}},
        {
            "accounts": [
                {"accountId": ACCOUNT, "currency": 8},
            ]
        },
    ],
)
def test_rejects_malformed_accounts(reply: Any) -> None:
    client, _ = client_for(login(), reply)
    client.authenticate()
    with pytest.raises(IGError, match="account data"):
        client.get_accounts()


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ({"markets": []}, "no EUR/USD"),
        ({"markets": {}}, "malformed"),
        ({"markets": [{**MARKET, "epic": None}]}, "malformed"),
        ({"markets": [MARKET, {**MARKET, "epic": "ANOTHER.EPIC"}]}, "multiple"),
        ({"markets": [MARKET, {**MARKET, "expiry": "JUN-27"}]}, "multiple"),
        ({"markets": [MARKET], "totalPages": 2}, "exceeds one page"),
        ({"markets": [MARKET] * 50}, "exceeds one page"),
        (
            Reply({"errorCode": "endpoint.unavailable.for.api-key"}, 403),
            "access denied",
        ),
    ],
)
def test_market_search_fails_without_guessing(reply: Any, message: str) -> None:
    client, session = client_for(login(), reply)
    client.authenticate()
    with pytest.raises(IGMarketError, match=message):
        client.find_market()
    assert len(session.calls) == 2


def test_configured_epic_validates_details_and_skips_search() -> None:
    client, session = client_for(
        login(),
        {
            "instrument": {"epic": EPIC, "name": "EUR/USD", "type": "CURRENCIES"},
            "snapshot": {"marketStatus": "TRADEABLE"},
        },
    )
    client.authenticate()
    assert client.find_market(EPIC).epic == EPIC
    assert session.calls[-1]["url"] == f"{GATEWAY}/markets/{EPIC}"
    assert session.calls[-1]["headers"]["VERSION"] == "2"


@pytest.mark.parametrize(
    "epic", ["..", "../accounts", "A%2fB", "A?x", "https://api.ig.com"]
)
def test_unsafe_epic_rejected_before_network(epic: str) -> None:
    client, session = client_for()
    for operation in (client.find_market, client.get_h1_sample):
        with pytest.raises(ValueError, match="EPIC syntax"):
            operation(epic)
    assert not session.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshotTimeUTC": None},
        {"snapshotTimeUTC": "2026-02-30T10:00:00"},
        {"snapshotTimeUTC": "2026-11-17T10:00:00+02:00"},
        {"openPrice": {"bid": 101}},
        {"lowPrice": None},
        {"closePrice": {"bid": True, "ask": 2}},
        {"highPrice": {"bid": 100, "ask": 101}},
        {"lowPrice": {"bid": 1, "ask": float("nan")}},
    ],
)
def test_malformed_observation(changes: dict[str, Any]) -> None:
    client, _ = client_for(login(), {"prices": [{**BAR, **changes}]})
    client.authenticate()
    with pytest.raises(IGMarketError, match="malformed historical"):
        client.get_h1_sample(EPIC)


@pytest.mark.parametrize(
    "prices", [[], [BAR, BAR], list(reversed(PRICES["prices"])), [BAR] * 33]
)
def test_empty_unordered_duplicate_or_oversized_prices(prices: list[Any]) -> None:
    client, session = client_for(login(), {"prices": prices})
    client.authenticate()
    with pytest.raises(
        IGMarketError, match="history is unavailable|malformed historical"
    ):
        client.get_h1_sample(EPIC)
    assert len(session.calls) == 2


def test_unexpected_price_pagination_never_fetches_next_page() -> None:
    reply = deepcopy(PRICES)
    reply["metadata"]["pageData"]["totalPages"] = 2
    client, session = client_for(login(), reply)
    client.authenticate()
    with pytest.raises(IGMarketError):
        client.get_h1_sample(EPIC)
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("exceeded-api-key-allowance", "API-key allowance"),
        ("exceeded-account-allowance", "account allowance"),
        ("exceeded-account-historical-data-allowance", "historical-price allowance"),
    ],
)
def test_documented_403_allowances_are_distinct(code: str, message: str) -> None:
    client, session = client_for(
        login(), Reply({"errorCode": f"error.public-api.{code}"}, 403)
    )
    client.authenticate()
    with pytest.raises(IGQuotaError, match=message):
        client.get_h1_sample(EPIC)
    assert len(session.calls) == 2


def test_bounded_throttle_retry_and_recovery() -> None:
    delays: list[float] = []
    throttle = Reply({}, 429, **{"Retry-After": "1"})
    client, session = client_for(
        login(), throttle, ACCOUNTS, throttle, throttle, delays=delays
    )
    client.authenticate()
    assert len(client.get_accounts()) == 2
    with pytest.raises(IGQuotaError, match="throttled"):
        client.get_accounts()
    assert delays == [1, 1]
    assert len(session.calls) == 5


def test_prolonged_retry_after_stops_and_unavailable_history_fails() -> None:
    delays: list[float] = []
    client, session = client_for(
        login(),
        Reply({}, 503, **{"Retry-After": "30"}),
        Reply({"errorCode": "error.unsupported.epic"}, 400),
        delays=delays,
    )
    client.authenticate()
    with pytest.raises(IGError, match="503"):
        client.get_h1_sample(EPIC)
    with pytest.raises(IGMarketError, match="unavailable"):
        client.get_h1_sample(EPIC)
    assert not delays
    assert len(session.calls) == 3


def test_wire_debug_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HTTPConnection, "debuglevel", 1)
    client, session = client_for()
    with pytest.raises(IGError, match="wire debugging"):
        client.authenticate()
    assert not session.calls


def test_cli_configuration_precedence_and_sanitized_report(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"MRML_IG_ENVIRONMENT=demo\nMRML_IG_API_KEY=wrong\n"
        f"MRML_IG_USERNAME={USER}\nMRML_IG_PASSWORD={PASSWORD}\n"
    )
    monkeypatch.setenv("MRML_IG_API_KEY", KEY)
    monkeypatch.delenv("MRML_IG_EPIC", raising=False)
    monkeypatch.delenv("MRML_DATABASE_URL", raising=False)
    client, session = client_for(login(), ACCOUNTS, {"markets": [MARKET]}, PRICES)

    def make_client(api_key: str, username: str, password: str) -> IGClient:
        assert (api_key, username, password) == (KEY, USER, PASSWORD)
        return client

    monkeypatch.setattr(ig_probe, "IGClient", make_client)
    monkeypatch.setenv("MRML_IG_ENVIRONMENT", "live")
    assert ig_probe.main() == 1
    assert not session.calls
    assert "Configuration: FAIL" in capsys.readouterr().out
    monkeypatch.delenv("MRML_IG_ENVIRONMENT")
    assert ig_probe.main() == 0
    output = capsys.readouterr().out
    for text in (
        "Authentication: PASS",
        "Accounts: PASS (2 found)",
        "Market: PASS",
        "Market status: CLOSED",
        "Historical prices: PASS (2 HOUR)",
        "998/1000 points",
        "bar completeness unknown",
        "Result: PASS",
    ):
        assert text in output
    for secret in (KEY, USER, PASSWORD, CST, TOKEN, ACCOUNT, "98765.4321"):
        assert secret not in output


def test_cli_fails_after_successful_authentication(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for key, value in (
        ("ENVIRONMENT", "demo"),
        ("API_KEY", KEY),
        ("USERNAME", USER),
        ("PASSWORD", PASSWORD),
        ("EPIC", ""),
    ):
        monkeypatch.setenv(f"MRML_IG_{key}", value)
    client, _ = client_for(login(), ACCOUNTS, {"markets": [MARKET]}, {"prices": []})
    monkeypatch.setattr(ig_probe, "IGClient", lambda *_: client)
    assert ig_probe.main() == 1
    out = capsys.readouterr().out
    assert "Authentication: PASS" in out
    assert "Historical prices: FAIL" in out
    assert "Result: FAIL" in out
    assert "Result: PASS" not in out


def test_probe_imports_without_application_runtime() -> None:
    script = """
import sys
for module in ("mpml", "msml", "sqlalchemy", "psycopg", "mrml.journal",
               "mrml.config", "mrml.risk", "mrml.strategy", "mrml.runner"):
    sys.modules[module] = None
import mrml.data.ig_probe
"""
    subprocess.run([sys.executable, "-c", script], check=True)
