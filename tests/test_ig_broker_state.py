from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
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

from mrml.data import ig_broker_probe
from mrml.data.ig import (
    DEMO_GATEWAY,
    IGAuthenticationError,
    IGError,
    IGSessionError,
)

START = datetime(2026, 11, 24, 10, tzinfo=UTC)
END = START + timedelta(hours=1)
DEAL = "Ab-001 "
REFERENCE = "ReF-012"
ACTIVITY_ID = "activity-002"
AMOUNT = "123456.123456789012345678901"
ACCOUNTS = {
    "accounts": [
        {"accountId": "preferred", "preferred": True, "status": "ENABLED"},
        {
            "accountId": ACCOUNT,
            "currency": "EUR",
            "status": "ENABLED",
            "accountType": "CFD",
            "balance": {"balance": AMOUNT, "available": "0.0001", "profitLoss": -2},
        },
    ]
}
POSITION = {
    "market": {"epic": EPIC, "marketStatus": "TRADEABLE"},
    "position": {
        "dealId": DEAL,
        "dealReference": REFERENCE,
        "dealSize": AMOUNT,
        "direction": "BUY",
        "openLevel": "100.25",
    },
}
ORDER = {
    "marketData": {"epic": EPIC, "marketStatus": "CLOSED"},
    "workingOrderData": {
        "dealId": "another-deal",
        "epic": EPIC,
        "direction": "SELL",
        "orderSize": AMOUNT,
        "orderType": "STOP",
        "timeInForce": "GOOD_TILL_DATE",
        "orderLevel": "99.5",
        "status": "FUTURE_STATUS",
    },
}
ACTIVITY = {
    "date": "2026-11-24T10:15:00.123",
    "dealId": DEAL,
    "activityHistoryId": ACTIVITY_ID,
    "epic": EPIC,
    "status": "REJECTED",
    "type": "WORKING_ORDER",
    "channel": "PUBLIC_WEB_API",
    "details": {
        "dealReference": REFERENCE,
        "direction": "BUY",
        "size": AMOUNT,
        "actions": [{"affectedDealId": "another-deal", "actionType": "UNKNOWN"}],
    },
}


def activity_payload(rows, **paging):
    return {
        "activities": rows,
        "metadata": {"paging": {"size": 50, "next": None, **paging}},
    }


def start_client(*payloads, accounts=ACCOUNTS):
    client, transport = client_for(
        login(), Stream(accounts), *(Stream(payload) for payload in payloads)
    )
    client.authenticate()
    return client, transport


def test_account_scope_and_wire_decimal_precision():
    raw = json.dumps(ACCOUNTS).replace(f'"{AMOUNT}"', AMOUNT).encode()
    client, transport = start_client(accounts=raw)

    listed, active = client.get_broker_accounts()

    assert listed.account_id == "preferred"
    assert not listed.active and listed.balances is None
    assert active.active and active.account_id == ACCOUNT
    assert active.balances.balance == Decimal(AMOUNT)
    assert active.balances.available == Decimal("0.0001")
    assert active.balances.deposit is None
    assert active.balances.profit_loss == Decimal("-2")
    assert ACCOUNT not in repr(active) and AMOUNT not in repr(active)
    assert [
        (call["method"], call["url"], call["headers"]["VERSION"])
        for call in transport.calls
    ] == [
        ("POST", DEMO_GATEWAY + "/session", "2"),
        ("GET", DEMO_GATEWAY + "/accounts", "1"),
    ]


def test_position_preserves_broker_identity_and_leaves_status_absent():
    client, transport = start_client({"positions": [POSITION]})
    [row] = client.get_positions()
    assert (row.deal_id, row.deal_reference, row.epic) == (DEAL, REFERENCE, EPIC)
    assert row.direction == "BUY" and row.size == Decimal(AMOUNT)
    assert row.open_level == Decimal("100.25") and row.status is None
    call = transport.calls[-1]
    assert (call["method"], call["url"], call["headers"]["VERSION"]) == (
        "GET",
        DEMO_GATEWAY + "/positions",
        "1",
    )


def test_working_order_contract_and_unknown_enum():
    client, transport = start_client({"workingOrders": [ORDER]})
    [row] = client.get_working_orders()
    assert (row.deal_id, row.deal_reference, row.epic) == ("another-deal", None, EPIC)
    assert row.direction == "SELL" and row.size == Decimal(AMOUNT)
    assert (row.order_type, row.time_in_force, row.status) == (
        "STOP",
        "GOOD_TILL_DATE",
        "FUTURE_STATUS",
    )
    call = transport.calls[-1]
    assert (call["method"], call["url"], call["headers"]["VERSION"]) == (
        "GET",
        DEMO_GATEWAY + "/workingorders",
        "2",
    )


def test_activity_request_and_distinct_identifiers():
    client, transport = start_client(
        activity_payload([ACTIVITY, {"date": "broker-local-label", "type": "SYSTEM"}])
    )
    row, system = client.get_activity(START, END)
    assert row.raw_date == ACTIVITY["date"]
    assert (row.deal_id, row.deal_reference, row.activity_id) == (
        DEAL,
        REFERENCE,
        ACTIVITY_ID,
    )
    assert row.actions[0].affected_deal_id == "another-deal"
    assert row.status == "REJECTED" and row.size == Decimal(AMOUNT)
    assert system.raw_date == "broker-local-label"
    assert system.deal_id is system.epic is system.status is system.actions is None
    call = transport.calls[-1]
    assert (call["method"], call["url"], call["headers"]["VERSION"]) == (
        "GET",
        DEMO_GATEWAY + "/history/activity",
        "3",
    )
    assert call["params"] == {
        "from": "2026-11-24T10:00:00Z",
        "to": "2026-11-24T11:00:00Z",
        "pageSize": 50,
        "detailed": "true",
    }
    for call in transport.calls[1:]:
        assert call["method"] == "GET"
        assert call["stream"] and not call["allow_redirects"] and call["json"] is None


@pytest.mark.parametrize(
    ("method", "payload", "args"),
    [
        ("get_positions", {"positions": []}, ()),
        ("get_working_orders", {"workingOrders": []}, ()),
        ("get_activity", activity_payload([], size=0), (START, END)),
    ],
)
def test_empty_collections_are_valid(method, payload, args):
    client, _ = start_client(payload)
    assert getattr(client, method)(*args) == ()


@pytest.mark.parametrize(
    "patch",
    [
        {"dealId": ""},
        {"direction": "buy"},
        {"dealSize": None},
        {"dealSize": True},
        {"dealSize": "NaN"},
        {"dealSize": " 1 "},
        {"dealSize": "1e9999999999999999999"},
    ],
)
def test_invalid_position_fields_refused_without_echoing_values(patch):
    bad_position = {**POSITION, "position": {**POSITION["position"], **patch}}
    client, _ = start_client({"positions": [bad_position]})
    with pytest.raises(IGError, match="malformed position data") as failure:
        client.get_positions()
    assert DEAL not in str(failure.value)


@pytest.mark.parametrize(
    ("method", "payload", "args"),
    [
        ("get_positions", {"positions": {}}, ()),
        ("get_working_orders", {"workingOrders": [{"workingOrderData": {}}]}, ()),
        ("get_working_orders", {"workingOrders": [None]}, ()),
        (
            "get_working_orders",
            {"workingOrders": [{**ORDER, "marketData": {"epic": "other"}}]},
            (),
        ),
        ("get_activity", activity_payload([{"date": None}]), (START, END)),
        ("get_activity", activity_payload([{**ACTIVITY, "details": []}]), (START, END)),
        (
            "get_activity",
            activity_payload([{**ACTIVITY, "details": {"actions": {}}}]),
            (START, END),
        ),
        (
            "get_activity",
            {"activities": [], "metadata": {"paging": {"size": 50}}},
            (START, END),
        ),
        ("get_activity", activity_payload([], size=51), (START, END)),
        ("get_activity", activity_payload([ACTIVITY], size=0), (START, END)),
        ("get_activity", activity_payload([], next=True), (START, END)),
    ],
)
def test_refuse_malformed_collections_and_paging(method, payload, args):
    client, _ = start_client(payload)
    with pytest.raises(IGError, match="malformed"):
        getattr(client, method)(*args)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START, START),
        (END, START),
        (START, START + timedelta(hours=25)),
        (START.replace(tzinfo=None), END),
        (START.replace(microsecond=1), END),
        (START, END.astimezone(timezone(timedelta(hours=1)))),
    ],
)
def test_invalid_activity_bounds_refused_before_any_request(start, end):
    client, transport = client_for()
    with pytest.raises(ValueError, match="UTC bounds"):
        client.get_activity(start, end)
    assert transport.calls == []


def test_activity_keeps_an_explicit_activity_id():
    client, _ = start_client(activity_payload([{"date": "raw", "activityId": "A1"}]))
    [entry] = client.get_activity(START, END)
    assert entry.activity_id == "A1"
    assert entry.deal_id is None


def test_positions_refuse_more_than_the_record_budget():
    client, _ = start_client({"positions": [POSITION] * 101})
    with pytest.raises(IGError, match="malformed position"):
        client.get_positions()


def test_cli_rejects_unbounded_activity_before_loading_credentials(monkeypatch, capsys):
    def unexpected_config_load():
        pytest.fail("invalid bounds must fail before loading credentials")

    monkeypatch.setattr(ig_broker_probe, "load_probe_values", unexpected_config_load)
    args = [
        "activity",
        "--from",
        "2026-11-24T10:00:00Z",
        "--to",
        "2026-11-26T10:00:00Z",
    ]
    assert ig_broker_probe.main(args) == 1
    assert "UTC bounds" in capsys.readouterr().out


def test_activity_refuses_continuation_without_following_or_echoing_it():
    next_url = "https://example.invalid/private-account"
    client, transport = start_client(activity_payload([], next=next_url))
    with pytest.raises(IGError, match="exceeds one page") as failure:
        client.get_activity(START, END)
    assert next_url not in str(failure.value)
    assert len(transport.calls) == 3


def test_activity_refuses_more_than_requested_rows():
    client, _ = start_client(activity_payload([ACTIVITY] * 51))
    with pytest.raises(IGError, match="oversized"):
        client.get_activity(START, END)


def test_broker_response_byte_limit_closes_stream():
    reply = Stream(b" " * 262_145)
    client, transport = client_for(login(), Stream(ACCOUNTS), reply)
    client.authenticate()
    with pytest.raises(IGError, match="byte budget"):
        client.get_positions()
    assert reply.closed
    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "accounts",
    [
        [{"accountId": "another", "status": "ENABLED", "preferred": True}],
        [{"accountId": ACCOUNT, "status": "DISABLED"}],
        [{"accountId": ACCOUNT, "status": "NEW_STATUS"}],
        [{"accountId": ACCOUNT}],
        [ACCOUNTS["accounts"][1]] * 2,
        [],
    ],
)
def test_no_broker_state_call_for_invalid_active_account(accounts):
    client, transport = start_client(accounts={"accounts": accounts})
    with pytest.raises(IGError) as failure:
        client.get_positions()
    assert ACCOUNT not in str(failure.value)
    assert len(transport.calls) == 2


def test_invalid_login_and_expired_session_refuse_broker_state():
    client, transport = client_for(
        Stream(
            {**LOGIN, "currentAccountId": ""}, cst=CST, **{"x-security-token": TOKEN}
        )
    )
    with pytest.raises(IGAuthenticationError):
        client.authenticate(bounded=True)
    with pytest.raises(IGSessionError):
        client.get_working_orders()
    assert len(transport.calls) == 1

    expired = Stream({"errorCode": "error.security.account-token-invalid"}, status=401)
    client, transport = client_for(login(), expired)
    client.authenticate()
    with pytest.raises(IGSessionError, match="expired"):
        client.get_positions()
    with pytest.raises(IGSessionError, match="authenticate explicitly"):
        client.get_broker_accounts()
    assert len(transport.calls) == 2


def test_cli_redacted_offline_workflow(monkeypatch, capsys, caplog):
    session = Stream(LOGIN, cst=CST, **{"x-security-token": TOKEN})
    client, transport = client_for(
        session, Stream(ACCOUNTS), Stream(activity_payload([ACTIVITY]))
    )
    values = {
        "MRML_IG_ENVIRONMENT": "demo",
        "MRML_IG_API_KEY": KEY,
        "MRML_IG_USERNAME": USER,
        "MRML_IG_PASSWORD": PASSWORD,
        "MRML_IG_EPIC": "unrelated invalid EPIC",
    }
    monkeypatch.setattr(ig_broker_probe, "load_probe_values", lambda: values)
    monkeypatch.setattr(ig_broker_probe, "IGClient", lambda *_: client)
    caplog.set_level("DEBUG")

    args = [
        "activity",
        "--from",
        "2026-11-24T10:00:00Z",
        "--to",
        "2026-11-24T11:00:00Z",
    ]
    assert ig_broker_probe.main(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "command": "activity",
        "result": "pass",
        "count": 1,
        "with_activity_id": 1,
        "with_deal_id": 1,
        "with_deal_reference": 1,
        "affected_deals_matching_activity_deal": 0,
    }
    assert session.closed
    for secret in (
        ACCOUNT,
        DEAL,
        REFERENCE,
        EPIC,
        ACTIVITY_ID,
        KEY,
        USER,
        PASSWORD,
        CST,
        TOKEN,
    ):
        assert secret not in output + caplog.text

    values["MRML_IG_ENVIRONMENT"] = "live"
    assert ig_broker_probe.main(args) == 1
    assert "MRML_IG_ENVIRONMENT=demo" in capsys.readouterr().out
    assert len(transport.calls) == 3
