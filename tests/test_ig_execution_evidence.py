from __future__ import annotations

import json
import logging
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_ig_broker_state import ACCOUNTS, AMOUNT, ORDER, POSITION
from test_ig_h1_range import Stream
from test_ig_probe import (
    ACCOUNT,
    CST,
    EPIC,
    KEY,
    LOGIN,
    PASSWORD,
    TOKEN,
    USER,
    client_for,
)

from mrml.data import ig, ig_broker_probe
from mrml.data.ig import DEMO_GATEWAY, IGError, IGSessionError

SESSION = {"accountId": ACCOUNT, "currency": "EUR", "clientId": "private-client"}
MARKET = {
    "instrument": {
        "name": "EUR/USD",
        "type": "CURRENCIES",
        "epic": EPIC,
        "expiry": "-",
        "unit": "CONTRACTS",
        "contractSize": "100000.00",
        "lotSize": "10.000000000000000001",
        "currencies": [{"code": "USD", "isDefault": True, "symbol": "private-symbol"}],
        "marginFactor": "3.33",
        "marginFactorUnit": "PERCENTAGE",
        "forceOpenAllowed": True,
        "stopsLimitsAllowed": True,
        "controlledRiskAllowed": False,
        "limitedRiskPremium": {"unit": "POINTS", "value": "1.2"},
        "slippageFactor": {"unit": "PERCENTAGE", "value": "25"},
        "marginDepositBands": [
            {"currency": "USD", "margin": "3.33", "min": 0, "max": 10}
        ],
        "specialInfo": ["private-special-info"],
    },
    "snapshot": {"marketStatus": "TRADEABLE", "decimalPlacesFactor": 2},
    "dealingRules": {
        "marketOrderPreference": "AVAILABLE_DEFAULT_ON",
        "minDealSize": {"unit": "POINTS", "value": "0.25"},
        "minNormalStopOrLimitDistance": {"unit": "POINTS", "value": "5.5"},
        "minControlledRiskStopDistance": {"unit": "PERCENTAGE", "value": "1.5"},
        "maxStopOrLimitDistance": {"unit": "PERCENTAGE", "value": 20},
        "minStepDistance": {"unit": "POINTS", "value": 1},
    },
}


def responses(
    *,
    login_payload=LOGIN,
    session=SESSION,
    accounts=ACCOUNTS,
    positions=None,
    orders=None,
    market=MARKET,
    final_session=SESSION,
):
    return (
        Stream(login_payload, cst=CST, **{"x-security-token": TOKEN}),
        Stream(session),
        Stream(accounts),
        Stream(market),
        Stream({"positions": []} if positions is None else positions),
        Stream({"workingOrders": []} if orders is None else orders),
        final_session if isinstance(final_session, Stream) else Stream(final_session),
    )


def configure(monkeypatch, client, **overrides):
    values = {
        "MRML_IG_ENVIRONMENT": "demo",
        "MRML_IG_API_KEY": KEY,
        "MRML_IG_USERNAME": USER,
        "MRML_IG_PASSWORD": PASSWORD,
        "MRML_IG_EXECUTION_ACCOUNT_ID": ACCOUNT,
        "MRML_IG_EPIC": EPIC,
        **overrides,
    }
    monkeypatch.setattr(ig_broker_probe, "load_probe_values", lambda: values)
    monkeypatch.setattr(ig_broker_probe, "IGClient", lambda *_: client)


def test_bounded_diagnostic_and_redaction(monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    client, transport = client_for(
        *responses(
            login_payload={**LOGIN, "dealingEnabled": True},
            positions={"positions": [POSITION]},
            orders={"workingOrders": [ORDER]},
        )
    )
    configure(monkeypatch, client)
    assert ig_broker_probe.main(["execution-evidence"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["result"] == "observed"
    assert report["session"]["dealing_enabled"] == "enabled"
    assert report["session"]["active_account_matched_before_and_after"] is True
    assert report["account"]["balance_fields"]["available"] == "available"
    assert report["exposure"]["positions"]["count"] == 1
    assert report["exposure"]["working_orders"]["count"] == 1
    native = report["market"]
    assert native["size_unit"]["value"] == "CONTRACTS"
    assert native["size_increment"] == {"status": "unsupported", "value": None}
    assert native["size_precision"] == {"status": "unsupported", "value": None}
    assert native["lot_size"]["value"] == "10.000000000000000001"
    assert native["minimum_normal_stop_or_limit"]["value"]["unit"]["value"] == "POINTS"
    assert (
        native["minimum_controlled_risk_stop"]["value"]["unit"]["value"] == "PERCENTAGE"
    )
    assert native["margin_bands"]["value"][0]["margin_percent"]["value"] == "3.33"
    for secret in (
        ACCOUNT,
        CST,
        TOKEN,
        KEY,
        USER,
        PASSWORD,
        AMOUNT,
        "private-client",
        "private-symbol",
        "private-special-info",
        POSITION["position"]["dealId"],
        ORDER["workingOrderData"]["dealId"],
    ):
        assert secret not in output
        assert secret not in caplog.text
    assert [
        (
            row["method"],
            row["url"].removeprefix(DEMO_GATEWAY),
            row["headers"]["VERSION"],
        )
        for row in transport.calls
    ] == [
        ("POST", "/session", "2"),
        ("GET", "/session", "1"),
        ("GET", "/accounts", "1"),
        ("GET", f"/markets/{EPIC}", "2"),
        ("GET", "/positions", "1"),
        ("GET", "/workingorders", "2"),
        ("GET", "/session", "1"),
    ]
    assert all(row["stream"] and not row["allow_redirects"] for row in transport.calls)
    assert all(row["json"] is None for row in transport.calls[1:])
    for index in (1, 6):
        assert transport.calls[index]["params"] == {"fetchSessionTokens": "false"}
    assert all(row["url"].startswith(DEMO_GATEWAY) for row in transport.calls)


@pytest.mark.parametrize(
    "override",
    [
        {"MRML_IG_ENVIRONMENT": "live"},
        {"MRML_IG_EPIC": ""},
        {"MRML_IG_EPIC": "../orders"},
        {"MRML_IG_EXECUTION_ACCOUNT_ID": ""},
    ],
)
def test_invalid_configuration_fails_before_authentication(
    monkeypatch, capsys, override
):
    client, transport = client_for()
    configure(monkeypatch, client, **override)
    assert ig_broker_probe.main(["execution-evidence"]) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "fail"
    assert not transport.calls


@pytest.mark.parametrize(
    "case", ["login", "disabled", "switched", "expired", "currency"]
)
def test_invalid_account_scope_stops_or_invalidates_capture(case):
    login_payload, accounts, final_session = LOGIN, ACCOUNTS, SESSION
    if case == "login":
        login_payload = {**LOGIN, "currentAccountId": "other-account"}
    if case == "disabled":
        accounts = {"accounts": [{**ACCOUNTS["accounts"][1], "status": "DISABLED"}]}
    if case == "switched":
        final_session = {**SESSION, "accountId": "other-account"}
    if case == "expired":
        final_session = Stream(
            {"errorCode": "error.security.account-token-invalid"}, 401
        )
    if case == "currency":
        final_session = {**SESSION, "currency": "USD"}
    client, transport = client_for(
        *responses(
            login_payload=login_payload,
            accounts=accounts,
            final_session=final_session,
        )
    )
    client.authenticate(bounded=True)
    with pytest.raises(IGSessionError):
        client.discover_execution_evidence(EPIC, account_id=ACCOUNT)
    previous = len(transport.calls)
    with pytest.raises(IGSessionError):
        client.discover_execution_evidence(EPIC, account_id=ACCOUNT)
    assert len(transport.calls) == previous


def test_missing_dealing_status_and_unmapped_fields_remain_explicit(
    monkeypatch, capsys
):
    market = deepcopy(MARKET)
    market["instrument"].update(unit="UNKNOWN_UNIT", lotSize=True)
    market["instrument"].pop("marginDepositBands")
    market["dealingRules"]["minNormalStopOrLimitDistance"] = {"value": "invalid"}
    client, _ = client_for(*responses(market=market))
    configure(monkeypatch, client)
    assert ig_broker_probe.main(["execution-evidence"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["session"]["dealing_enabled"] == "missing"
    native = report["market"]
    assert native["size_unit"]["status"] == "unsupported"
    assert native["lot_size"]["status"] == "malformed"
    assert native["margin_bands"]["status"] == "missing"
    rule = native["minimum_normal_stop_or_limit"]["value"]
    assert rule["unit"]["status"] == "missing"
    assert rule["value"]["status"] == "malformed"


def test_native_numeric_types_and_fresh_session_generation():
    client, _ = client_for(*responses(), *responses())
    client.authenticate(bounded=True)
    initial = client.discover_execution_evidence(EPIC, account_id=ACCOUNT)
    client.authenticate(bounded=True)
    renewed = client.discover_execution_evidence(EPIC, account_id=ACCOUNT)
    assert initial.session.generation != renewed.session.generation
    assert renewed.market.lot_size.value == Decimal("10.000000000000000001")
    assert renewed.market.margin_bands.value[0].margin_percent.value == Decimal("3.33")


@pytest.mark.parametrize(
    ("key", "payload"),
    [
        ("positions", {"positions": [], "metadata": {"paging": {"next": "opaque"}}}),
        ("positions", {"positions": [POSITION, POSITION]}),
        ("orders", {"workingOrders": [], "accountId": "conflicting-account"}),
        ("orders", {}),
    ],
)
def test_incomplete_or_conflicting_exposure_fails_closed(key, payload):
    client, _ = client_for(*responses(**{key: payload}))
    client.authenticate(bounded=True)
    with pytest.raises(IGError):
        client.discover_execution_evidence(EPIC, account_id=ACCOUNT)


@pytest.mark.parametrize("parent", ["instrument", "snapshot"])
def test_rejects_conflicting_market_identity(parent):
    market = deepcopy(MARKET)
    market[parent]["epic"] = "CS.D.OTHER.IP"
    client, _ = client_for(*responses(market=market))
    client.authenticate(bounded=True)
    with pytest.raises(IGError, match="did not validate"):
        client.discover_execution_evidence(EPIC, account_id=ACCOUNT)


@pytest.mark.parametrize(
    "ticks",
    [
        [0, 1, 9, 12, 13],  # Exposure collection span exceeds ten seconds.
        [0, 1, 2, 3, 32],  # Report is stale even though reads are close together.
        [0, 25, 26, 27, 31],  # The session itself is outside the window.
    ],
)
def test_stale_evidence_is_inconclusive(monkeypatch, capsys, ticks):
    clock = iter(ticks)
    monkeypatch.setattr(ig, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    client, _ = client_for(*responses())
    configure(monkeypatch, client)
    assert ig_broker_probe.main(["execution-evidence"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "inconclusive"
    assert report["exposure"]["freshness"] == "outside_window"


def test_market_response_size_is_bounded(monkeypatch, capsys):
    client, transport = client_for(*responses(market=b"x" * (262_144 + 1)))
    configure(monkeypatch, client)
    assert ig_broker_probe.main(["execution-evidence"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "IG broker-state response exceeds the byte budget"
    assert len(transport.calls) == 4
