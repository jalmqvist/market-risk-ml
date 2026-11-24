from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest
from requests.exceptions import ConnectionError
from test_ig_broker_state import ACCOUNTS, start_client
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
    login,
)

from mrml.broker.evidence import Provenance, Scope
from mrml.data import ig_broker_probe
from mrml.data.ig import (
    DEMO_GATEWAY,
    IGError,
    IGEvidenceConflict,
    IGLookupNotFound,
    IGMalformedResponse,
    IGSessionError,
    IGTransportError,
)
from mrml.data.ig_broker_state import confirmation_evidence, position_evidence

REFERENCE = "Ref-01_Ab"
DEAL_ID = "Broker-Deal-02"
QUANTITY = "1.000000000000000001"
CONFIRMATION = {
    "dealReference": REFERENCE,
    "dealId": "transaction-id",
    "dealStatus": "ACCEPTED",
    "status": "OPEN",
    "epic": EPIC,
    "direction": "BUY",
    "size": QUANTITY,
    "level": "101.12345678901234567890",
    "date": "raw broker date",
    "affectedDeals": [{"dealId": DEAL_ID, "status": "OPENED"}],
}
POSITION = {
    "market": {"epic": EPIC},
    "position": {
        "dealId": DEAL_ID,
        "direction": "BUY",
        "size": QUANTITY,
        "level": "101.25",
        "createdDate": "local",
        "createdDateUTC": "utc-label",
        "stopLevel": "99.9",
    },
}


def test_confirm_and_position_wire_contract_and_decimal_precision():
    client, transport = start_client(CONFIRMATION, ACCOUNTS, POSITION)
    confirmation = client.get_confirmation(REFERENCE, account_id=ACCOUNT)
    position = client.get_position(DEAL_ID, account_id=ACCOUNT)
    assert confirmation.deal_reference == REFERENCE
    assert confirmation.deal_id == "transaction-id"
    assert confirmation.affected_deals[0].deal_id == DEAL_ID
    assert confirmation.deal_status == "ACCEPTED" and confirmation.status == "OPEN"
    assert confirmation.size == Decimal(QUANTITY)
    assert confirmation.level == Decimal("101.12345678901234567890")
    assert confirmation.raw_date == "raw broker date"
    assert position.position.deal_id == DEAL_ID
    assert position.position.size == Decimal(QUANTITY)
    assert position.position.open_level == Decimal("101.25")
    assert position.position.created_date == "local"
    assert position.created_date_utc == "utc-label"
    assert position.observed_at.tzinfo is UTC
    assert position.stop_level == Decimal("99.9")
    source = Provenance(
        "synthetic-confirm", Scope("IG", "demo", ACCOUNT), position.observed_at, "REST"
    )
    evidence = confirmation_evidence(confirmation, source)
    assert evidence.transaction_deal_id == "transaction-id"
    assert (
        evidence.affected_deals[0].deal_id
        == position_evidence(position.position).deal_id
    )
    assert evidence.level == confirmation.level
    assert evidence.raw_date == confirmation.raw_date
    assert [call["url"] for call in transport.calls] == [
        DEMO_GATEWAY + "/session",
        DEMO_GATEWAY + "/accounts",
        DEMO_GATEWAY + "/confirms/" + REFERENCE,
        DEMO_GATEWAY + "/accounts",
        DEMO_GATEWAY + "/positions/" + DEAL_ID,
    ]
    for call in transport.calls[1:]:
        assert call["method"] == "GET" and call["headers"]["VERSION"] == "1"
        assert call["stream"] and not call["allow_redirects"] and call["json"] is None
    assert REFERENCE not in repr(confirmation) and DEAL_ID not in repr(position)


def test_rejection_preserves_optional_absence_and_reason():
    client, _ = start_client(
        {
            "dealReference": REFERENCE,
            "dealStatus": "REJECTED",
            "reason": "MARKET_CLOSED",
        }
    )
    row = client.get_confirmation(REFERENCE, account_id=ACCOUNT)
    assert row.deal_status == "REJECTED" and row.reason == "MARKET_CLOSED"
    assert row.deal_id is row.affected_deals is row.size is None


@pytest.mark.parametrize(
    ("method", "identifier", "code"),
    [
        ("get_confirmation", REFERENCE, "error.confirms.deal-not-found"),
        ("get_position", DEAL_ID, "error.position.notfound"),
    ],
)
def test_only_documented_endpoint_404_is_inconclusive(method, identifier, code):
    for status, error_code in [(404, code), (403, code), (404, "unknown"), (200, code)]:
        client, transport = client_for(
            login(), Stream(ACCOUNTS), Stream({"errorCode": error_code}, status=status)
        )
        client.authenticate()
        with pytest.raises(IGError) as failure:
            getattr(client, method)(identifier, account_id=ACCOUNT)
        assert isinstance(failure.value, IGLookupNotFound) == (
            status == 404 and error_code == code
        )
        assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "value", ["", "..", "a/b", "a%2fb", "x?y", "x#y", "a b", "a\\b", "ä"]
)
@pytest.mark.parametrize("method", ["get_confirmation", "get_position"])
def test_invalid_path_segments_never_reach_transport(value, method):
    client, transport = client_for()
    with pytest.raises(ValueError):
        getattr(client, method)(value, account_id=ACCOUNT)
    assert not transport.calls


@pytest.mark.parametrize(
    ("method", "identifier", "payload"),
    [
        ("get_confirmation", REFERENCE, []),
        ("get_confirmation", REFERENCE, {**CONFIRMATION, "dealStatus": "UNKNOWN"}),
        ("get_confirmation", REFERENCE, {**CONFIRMATION, "affectedDeals": [{}]}),
        (
            "get_position",
            DEAL_ID,
            {
                **POSITION,
                "position": {"dealId": DEAL_ID, "dealSize": 1, "direction": "BUY"},
            },
        ),
        (
            "get_position",
            DEAL_ID,
            {**POSITION, "position": {**POSITION["position"], "size": True}},
        ),
    ],
)
def test_malformed_lookup_responses(method, identifier, payload):
    client, _ = start_client(payload)
    with pytest.raises(IGMalformedResponse):
        getattr(client, method)(identifier, account_id=ACCOUNT)


@pytest.mark.parametrize(
    ("method", "identifier", "payload"),
    [
        ("get_confirmation", REFERENCE, {**CONFIRMATION, "dealReference": "other-ref"}),
        (
            "get_position",
            DEAL_ID,
            {**POSITION, "position": {**POSITION["position"], "dealId": "other-deal"}},
        ),
    ],
)
def test_conflicting_returned_id_is_refused(method, identifier, payload):
    client, _ = start_client(payload)
    with pytest.raises(IGEvidenceConflict) as failure:
        getattr(client, method)(identifier, account_id=ACCOUNT)
    assert identifier not in str(failure.value)


def test_lookup_requires_scope_and_does_not_retry_or_redirect(monkeypatch):
    monkeypatch.setattr(Stream, "close", lambda self: None, raising=False)
    client, transport = start_client()
    with pytest.raises(IGSessionError):
        client.get_confirmation(REFERENCE, account_id="wrong-account")
    assert len(transport.calls) == 2

    for response, expected in [
        (ConnectionError("contains sensitive URL"), IGTransportError),
        (Stream({}, status=302, Location="https://api.ig.com"), IGError),
        (Stream({}, status=503, **{"Retry-After": "1"}), IGError),
        (Stream({}, status=401), IGSessionError),
        (Stream(b" " * 262_145), IGError),
    ]:
        client, transport = client_for(login(), Stream(ACCOUNTS), response)
        client.authenticate()
        with pytest.raises(expected):
            client.get_position(DEAL_ID, account_id=ACCOUNT)
        assert len(transport.calls) == 3
        assert all(call["url"].startswith(DEMO_GATEWAY) for call in transport.calls)


def test_cli_redacts_lookup_and_classifies_missing(monkeypatch, capsys, caplog):
    values = {
        "MRML_IG_ENVIRONMENT": "demo",
        "MRML_IG_API_KEY": KEY,
        "MRML_IG_USERNAME": USER,
        "MRML_IG_PASSWORD": PASSWORD,
        "MRML_IG_LOOKUP_ACCOUNT_ID": ACCOUNT,
        "MRML_IG_LOOKUP_IDENTIFIER": REFERENCE,
    }
    client, transport = client_for(
        Stream(LOGIN, cst=CST, **{"x-security-token": TOKEN}),
        Stream(ACCOUNTS),
        Stream({"errorCode": "error.confirms.deal-not-found"}, status=404),
    )
    monkeypatch.setattr(ig_broker_probe, "load_probe_values", lambda: values)
    monkeypatch.setattr(ig_broker_probe, "IGClient", lambda *_: client)
    caplog.set_level("DEBUG")
    assert ig_broker_probe.main(["confirmation"]) == 2
    output = capsys.readouterr().out
    assert "inconclusive" in output
    assert all(
        value not in output + caplog.text for value in (ACCOUNT, REFERENCE, KEY, TOKEN)
    )
    values["MRML_IG_ENVIRONMENT"] = "live"
    assert ig_broker_probe.main(["confirmation"]) == 1
    assert len(transport.calls) == 3
