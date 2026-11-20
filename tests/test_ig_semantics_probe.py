"""Synthetic HTTP and CLI coverage for the bounded IG semantics audit."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_ig_probe import (
    ACCOUNT,
    BAR,
    CST,
    EPIC,
    GATEWAY,
    KEY,
    LOGIN,
    PASSWORD,
    PRICES,
    TOKEN,
    USER,
    Reply,
    client_for,
    login,
)

from mrml.data import ig_semantics_probe as probe
from mrml.data.ig import IGMarketError, IGQuotaError

NOW = datetime(2026, 11, 17, 10, 3, 30, tzinfo=UTC)
ENV = {
    "MRML_IG_ENVIRONMENT": "demo",
    "MRML_IG_EPIC": EPIC,
    "MRML_IG_API_KEY": KEY,
    "MRML_IG_USERNAME": USER,
    "MRML_IG_PASSWORD": PASSWORD,
}
MARKET = {
    "instrument": {
        "epic": EPIC,
        "type": "CURRENCIES",
        "name": "EUR/USD Mini",
        "expiry": "-",
        "contractSize": "10000",
        "lotSize": 1,
        "openingHours": {
            "marketTimes": [{"openTime": "00:00", "closeTime": "23:00", "CST": CST}]
        },
        "apiKey": KEY,
    },
    "snapshot": {"marketStatus": "TRADEABLE", "accountId": ACCOUNT},
}
H1 = {**PRICES, "prices": [BAR], "password": PASSWORD}
M1 = {
    "prices": [
        {**BAR, "snapshotTimeUTC": f"2026-11-17T10:0{minute}:00Z"}
        for minute in range(4)
    ]
}


def run(*history: Any, market: Any = MARKET) -> dict[str, Any]:
    client, _ = client_for(login(), market, *history)
    with client:
        return probe.collect(client, EPIC, now=lambda: NOW)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"MRML_IG_ENVIRONMENT": "live"}, "MRML_IG_ENVIRONMENT=demo"),
        ({"MRML_IG_EPIC": ""}, "Set MRML_IG_EPIC explicitly"),
        ({"MRML_IG_EPIC": "../orders"}, "Invalid IG EPIC syntax"),
    ],
)
def test_invalid_configuration_stops_before_login(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    message: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["probe"])
    monkeypatch.setattr(probe, "load_probe_values", lambda: {**ENV, **overrides})

    def unexpected(*args: object) -> None:
        pytest.fail("client must not be created")

    monkeypatch.setattr(probe, "IGClient", unexpected)
    assert probe.main() == 1
    assert message in capsys.readouterr().err


def test_request_budget_market_validation_and_redaction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client, session = client_for(login(), MARKET, H1, M1)
    monkeypatch.setattr(sys, "argv", ["probe", "--json"])
    monkeypatch.setattr(probe, "load_probe_values", lambda: ENV)
    monkeypatch.setattr(probe, "IGClient", lambda *_: client)
    assert probe.main() == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["market"]["name"] == "EUR/USD Mini"
    assert report["market"]["expiry"] == "-"
    assert report["market"]["openingHours"] == [
        {"openTime": "00:00", "closeTime": "23:00"}
    ]
    assert all(
        secret not in output
        for secret in (KEY, USER, PASSWORD, CST, TOKEN, ACCOUNT, LOGIN["clientId"])
    )
    assert [
        (call["method"], call["url"], call["headers"]["VERSION"])
        for call in session.calls
    ] == [
        ("POST", GATEWAY + "/session", "2"),
        ("GET", GATEWAY + f"/markets/{EPIC}", "2"),
        ("GET", GATEWAY + f"/prices/{EPIC}", "3"),
        ("GET", GATEWAY + f"/prices/{EPIC}", "3"),
    ]
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert [call["params"] for call in session.calls[2:]] == [
        {"resolution": "HOUR", "max": 3, "pageSize": 3, "pageNumber": 1},
        {"resolution": "MINUTE", "max": 24, "pageSize": 24, "pageNumber": 1},
    ]


def test_unexpected_market_stops_before_history() -> None:
    other = {**MARKET, "instrument": {**MARKET["instrument"], "epic": "OTHER"}}
    with pytest.raises(IGMarketError, match="configured EUR/USD EPIC"):
        run(market=other)


def test_interpretation_and_deterministic_evidence() -> None:
    report = run(H1, M1)
    assert report == run(H1, M1)
    assert report["history"]["HOUR"]["allowance"]["remaining"] == 998
    assert report["history"]["MINUTE"]["prices"] == M1["prices"]
    assert report["timestamp_policy"] == "unresolved"
    assert report["broker_completion"] == "unknown"
    assert report["forming_candidates_by_h1_hypothesis"] == {
        "start": [BAR["snapshotTimeUTC"]],
        "end": [],
    }
    comparisons = {
        (row["h1_hypothesis"], row["m1_hypothesis"]): row
        for row in report["comparisons"]
    }
    start = comparisons["start", "start"]
    assert start["opening_matches_at_edge"] is True
    assert start["m1_extrema_within_h1"] is True
    assert start["close_matches_at_edge"] is None
    assert start["m1_count"] == 3
    assert start["last_m1_snapshotTimeUTC"] == "2026-11-17T10:02:00Z"
    end = comparisons["end", "end"]
    assert end["candidate_elapsed_at_request"] is True
    assert end["m1_count"] == 1
    assert end["close_matches_at_edge"] is True


def test_end_edge_and_mismatch() -> None:
    prices = {"prices": [{**BAR, "closePrice": {"bid": 102, "ask": 103}}]}
    minute = {
        "prices": [
            {
                **BAR,
                "snapshotTimeUTC": "2026-11-17T09:59:00",
                "highPrice": {"bid": 106, "ask": 107},
            }
        ]
    }
    report = run(prices, minute)
    end = next(
        row
        for row in report["comparisons"]
        if (row["h1_hypothesis"], row["m1_hypothesis"]) == ("end", "start")
    )
    assert end["m1_count"] == 1
    assert end["close_matches_at_edge"] is False
    assert end["m1_extrema_within_h1"] is False


def test_closed_and_empty_samples() -> None:
    report = run(
        {"prices": []},
        {"prices": []},
        market={**MARKET, "snapshot": {"marketStatus": "CLOSED"}},
    )
    assert report["history"]["HOUR"]["count"] == 0
    assert report["history"]["MINUTE"]["count"] == 0
    assert not report["comparisons"]
    assert "cannot settle" in report["current_candle_limit"]


@pytest.mark.parametrize(
    "bad_h1",
    [
        {"prices": [BAR] * 4},
        {"prices": [{"snapshotTimeUTC": PASSWORD}]},
        {"prices": [], "metadata": {"pageData": {"totalPages": 2}}},
    ],
)
def test_malformed_h1_fails_safely(bad_h1: Any) -> None:
    with pytest.raises(IGMarketError, match="malformed historical H1"):
        run(bad_h1)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (Reply({}, 429), "throttled"),
        (
            Reply(
                {
                    "errorCode": "error.public-api."
                    "exceeded-account-historical-data-allowance"
                },
                403,
            ),
            "historical-price allowance exhausted",
        ),
    ],
)
def test_quota_errors_do_not_retry(reply: Reply, message: str) -> None:
    with pytest.raises(IGQuotaError, match=message):
        run(reply)
    partial = run(H1, reply)
    assert partial["history"]["HOUR"]["count"] == 1
    assert partial["history"]["MINUTE"] is None
    assert message in partial["minute_error"]


def test_malformed_minute_data_is_reported() -> None:
    partial = run(H1, {"prices": [{"snapshotTimeUTC": TOKEN}]})
    assert partial["history"]["MINUTE"] is None
    assert partial["minute_error"] == "IG returned malformed historical M1 data"
    assert TOKEN not in json.dumps(partial)


def test_manual_comparison_of_the_same_label(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    first.write_text(json.dumps(run(H1, M1)), encoding="utf-8")
    prior = probe._prior_h1(first, EPIC)
    changed = {
        "prices": [
            {
                **BAR,
                "snapshotTimeUTC": f"{BAR['snapshotTimeUTC']}Z",
                "closePrice": {"bid": 104, "ask": 105},
            }
        ]
    }
    client, _ = client_for(login(), MARKET, changed, M1)
    with client:
        report = probe.collect(
            client,
            EPIC,
            previous=prior,
            now=lambda: datetime(2026, 11, 17, 11, 2, tzinfo=UTC),
        )
    (overlap,) = report["overlapping_h1_observations"]
    assert overlap["ohlc_changed"] is True
    assert overlap["candidate_elapsed_at_prior_retrieval"] == {
        "start": False,
        "end": True,
    }
    assert report["broker_completion"] == "unknown"
    with pytest.raises(ValueError, match="previous IG audit evidence"):
        probe._prior_h1(first, "SOME.OTHER.EPIC")
