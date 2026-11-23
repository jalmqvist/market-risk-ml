from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_ig_probe import BAR, EPIC, PRICES, Reply, client_for, login

from mrml.data.ig import DEMO_GATEWAY, IGMarketError, IGPricePage, IGQuotaError

START = datetime(2026, 11, 17, 10, tzinfo=UTC)
END = START + timedelta(hours=2)


class Stream(Reply):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        data = (
            self.payload
            if isinstance(self.payload, bytes)
            else json.dumps(self.payload).encode()
        )
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]


def authenticated(stream, *, delays=None):
    client, session = client_for(login(), stream, delays=delays)
    client.authenticate()
    return client, session


def response():
    value = deepcopy(PRICES)
    value["metadata"]["pageData"]["pageSize"] = 3
    value["metadata"]["size"] = 2
    return value


def test_exact_demo_v3_wire_request_and_terminal_edge():
    stream = Stream(response())
    client, session = authenticated(stream)
    result = client.get_h1_range(EPIC, START, END, point_limit=3)
    assert tuple(row.timestamp for row in result.observations) == (START, END)
    assert result.allowance.remaining == 998
    assert result.page == IGPricePage(page_number=1, page_size=3, total_pages=1)
    assert stream.closed
    assert len(session.calls) == 2
    request = session.calls[-1]
    assert request["url"] == f"{DEMO_GATEWAY}/prices/{EPIC}"
    assert request["headers"]["VERSION"] == "3"
    assert request["params"] == {
        "from": "2026-11-17T10:00:00",
        "to": "2026-11-17T12:00:00",
        "resolution": "HOUR",
        "pageSize": 3,
        "pageNumber": 1,
    }
    assert request["stream"] and not request["allow_redirects"]


@pytest.mark.parametrize(
    "patch",
    [
        {"pageNumber": 2},
        {"pageSize": 100},
        {"totalPages": 2},
        {"totalPages": 0},
        {"totalPages": True},
        {"pageSize": None},
    ],
)
def test_malformed_or_partial_pagination_refused(patch):
    payload = response()
    payload["metadata"]["pageData"].update(patch)
    client, session = authenticated(Stream(payload))
    with pytest.raises(IGMarketError, match="over-budget"):
        client.get_h1_range(EPIC, START, END, point_limit=3)
    assert len(session.calls) == 2


@pytest.mark.parametrize("bad_label", ["09:00:00", "12:00:01", "10:30:00", "10:00:00"])
def test_unexpected_labels_and_duplicates_rejected(bad_label):
    payload = response()
    payload["prices"][1] = {**BAR, "snapshotTimeUTC": f"2026-11-17T{bad_label}"}
    client, _ = authenticated(Stream(payload))
    reason = "duplicate or disordered" if bad_label == "10:00:00" else "out-of-window"
    with pytest.raises(IGMarketError, match=reason):
        client.get_h1_range(EPIC, START, END, point_limit=3)


@pytest.mark.parametrize("identity", [{"epic": "OTHER"}, {"resolution": "MINUTE"}])
def test_wrong_series_rejected(identity):
    client, _ = authenticated(Stream({**response(), **identity}))
    with pytest.raises(IGMarketError, match="H1"):
        client.get_h1_range(EPIC, START, END, point_limit=3)


def test_empty_range_and_missing_allowance():
    payload = {
        "prices": [],
        "metadata": {
            "pageData": {
                "pageNumber": 0,
                "pageSize": 0,
                "totalPages": 0,
            }
        },
    }
    client, _ = authenticated(Stream(payload))
    result = client.get_h1_range(EPIC, START, END, point_limit=3)
    assert result.observations == ()
    assert result.allowance is None


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_reported_without_http_retry(status):
    delays = []
    client, session = authenticated(
        Stream({}, status, **{"Retry-After": "30"}), delays=delays
    )
    before = datetime.now(UTC)
    with pytest.raises(IGQuotaError) as error:
        client.get_h1_range(EPIC, START, END, point_limit=3)
    assert error.value.retry_at >= before + timedelta(seconds=30)
    assert len(session.calls) == 2 and delays == []


def test_historical_exhaustion_distinct_from_throttle():
    client, session = authenticated(
        Stream(
            {
                "errorCode": (
                    "error.public-api.exceeded-account-historical-data-allowance"
                )
            },
            403,
        )
    )
    with pytest.raises(IGQuotaError) as error:
        client.get_h1_range(EPIC, START, END, point_limit=3)
    assert error.value.historical
    assert len(session.calls) == 2


def test_outbound_validation_and_response_bytes_budget():
    stream = Stream(b"x" * 262_145)
    client, session = authenticated(stream)
    with pytest.raises(ValueError, match="bounds"):
        client.get_h1_range(EPIC, START, END, point_limit=2)
    with pytest.raises(ValueError, match="bounds"):
        client.get_h1_range(EPIC, START, END + timedelta(days=1), point_limit=25)
    assert len(session.calls) == 1
    with pytest.raises(IGMarketError, match="byte budget"):
        client.get_h1_range(EPIC, START, END, point_limit=3)
    assert stream.closed
