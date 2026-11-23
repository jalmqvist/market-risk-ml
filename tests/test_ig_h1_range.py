from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_ig_probe import BAR, EPIC, PRICES, Reply, client_for, login

from mrml.data.ig import (
    DEMO_GATEWAY,
    IGError,
    IGMarketError,
    IGPricePage,
    IGQuotaError,
    IGRequestTimezone,
)

START = datetime(2026, 11, 17, 10, tzinfo=UTC)
END = START + timedelta(hours=2)
UTC_REQUEST = IGRequestTimezone("Etc/UTC", "Synthetic UTC approval")


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
    result = client.get_h1_range(
        EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
    )
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
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("bad_label", "reason", "invalid_allowance"),
    [
        ("2026-11-17T09:00:00Z", "before requested start", False),
        ("2026-11-17T13:00:00+00:00", "after requested end", False),
        ("2026-11-17T10:30:00", "not aligned to a UTC hour", False),
        ("2026-11-17T12:00:01", "after requested end", True),
    ],
)
def test_rejected_range_label_has_sanitized_evidence(
    bad_label, reason, invalid_allowance
):
    payload = response()
    payload["prices"][1] = {**BAR, "snapshotTimeUTC": bad_label}
    payload["prices"][1]["snapshotTime"] = "private-local-field"
    payload["metadata"]["pageData"]["private"] = "private-page-field"
    allowance = payload["metadata"]["allowance"]
    allowance["private"] = "private-allowance-field"
    if invalid_allowance:
        allowance["remainingAllowance"] = "private-invalid-value"
    client, session = authenticated(Stream(payload))

    with pytest.raises(IGMarketError, match=reason) as caught:
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )

    message = str(caught.value)
    assert "private" not in message and "openPrice" not in message
    assert json.loads(message.split("; ", 1)[1]) == {
        "snapshotTimeUTC": bad_label,
        "requested_start_utc": START.isoformat(),
        "requested_end_utc": END.isoformat(),
        "wire_from": START.isoformat(timespec="seconds")[:-6],
        "wire_to": END.isoformat(timespec="seconds")[:-6],
        "observation_count": 2,
        "returned_snapshot_times_utc": [BAR["snapshotTimeUTC"], bad_label],
        "login_timezone_offset_hours": None,
        "pagination": {"page_number": 1, "page_size": 3, "total_pages": 1},
        "allowance": None
        if invalid_allowance
        else {
            "remaining": allowance["remainingAllowance"],
            "total": allowance["totalAllowance"],
            "reset_seconds": allowance["allowanceExpiry"],
        },
    }
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("day", "wire_start", "wire_end"),
    [
        ("2026-01-19", "01:00:00", "04:00:00"),
        ("2026-07-20", "02:00:00", "05:00:00"),
    ],
)
def test_approved_timezone_converts_wire_and_preserves_inclusive_utc_labels(
    day, wire_start, wire_end
):
    start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    end = start + timedelta(hours=3)
    labels = [f"{day}T{hour:02d}:00:00" for hour in range(4)]
    payload = response()
    payload["prices"] = [{**BAR, "snapshotTimeUTC": label} for label in labels]
    payload["metadata"]["size"] = 4
    payload["metadata"]["pageData"]["pageSize"] = 4
    session_reply = login()
    session_reply.payload = {**session_reply.payload, "timezoneOffset": -5}
    client, session = client_for(session_reply, Stream(payload))
    client.authenticate()
    zone = IGRequestTimezone("Europe/Stockholm", "Synthetic account approval")

    result = client.get_h1_range(EPIC, start, end, point_limit=4, request_timezone=zone)

    assert [item.raw_timestamp for item in result.observations] == labels
    assert [item.timestamp for item in result.observations] == [
        start + timedelta(hours=hour) for hour in range(4)
    ]
    assert session.calls[-1]["params"]["from"] == f"{day}T{wire_start}"
    assert session.calls[-1]["params"]["to"] == f"{day}T{wire_end}"


@pytest.mark.parametrize(
    ("first", "last", "reason"),
    [
        ("2026-03-29T00:00:00", "2026-03-29T01:00:00", "offset transition"),
        ("2026-10-25T00:00:00", "2026-10-25T03:00:00", "ambiguous"),
        ("2026-10-25T01:00:00", "2026-10-25T03:00:00", "ambiguous"),
        ("2026-10-24T23:00:00", "2026-10-25T03:00:00", "offset transition"),
    ],
)
def test_dst_bounds_refused_before_price_request(first, last, reason):
    start, end = (datetime.fromisoformat(value + "+00:00") for value in (first, last))
    zone = IGRequestTimezone("Europe/Stockholm", "Synthetic account approval")
    client, session = authenticated(Stream(response()))
    with pytest.raises(IGError, match=reason):
        client.get_h1_range(EPIC, start, end, point_limit=5, request_timezone=zone)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("name", "approval", "reason"),
    [
        ("Europe/Stockholm", " ", "approval evidence"),
        ("localtime", "fixture", "IANA timezone"),
        ("+01:00", "fixture", "IANA timezone"),
    ],
)
def test_invalid_timezone_configuration_refused(name, approval, reason):
    with pytest.raises(IGError, match=reason):
        IGRequestTimezone(name, approval)


def test_range_refuses_missing_approval_even_when_login_supplies_offset():
    session_reply = login()
    session_reply.payload = {**session_reply.payload, "timezoneOffset": 1}
    client, session = client_for(session_reply, Stream(response()))
    client.authenticate()
    with pytest.raises(IGError, match="approved IANA timezone"):
        client.get_h1_range(EPIC, START, END, point_limit=3)
    assert len(session.calls) == 1


@pytest.mark.parametrize(("offset", "safe_offset"), [(1, 1), ("private", None)])
def test_observed_november_range_stays_rejected(offset, safe_offset):
    start = datetime(2026, 11, 23, 10, tzinfo=UTC)
    end = start + timedelta(hours=3)
    # Replay the observed shifted labels to confirm UTC validation stays strict.
    labels = [f"2026-11-23T{hour:02d}:00:00" for hour in range(9, 13)]
    payload = response()
    payload["prices"] = [{**BAR, "snapshotTimeUTC": label} for label in labels]
    payload["metadata"].update(
        size=4,
        pageData={"pageNumber": 1, "pageSize": 4, "totalPages": 1},
        allowance={
            "remainingAllowance": 9875,
            "totalAllowance": 10000,
            "allowanceExpiry": 169890,
        },
    )
    session_reply = login()
    session_reply.payload = {**session_reply.payload, "timezoneOffset": offset}
    client, session = client_for(session_reply, Stream(payload))
    client.authenticate()

    with pytest.raises(IGMarketError, match="before requested start") as caught:
        client.get_h1_range(
            EPIC, start, end, point_limit=4, request_timezone=UTC_REQUEST
        )

    diagnostic = json.loads(str(caught.value).split("; ", 1)[1])
    assert diagnostic["snapshotTimeUTC"] == labels[0]
    assert diagnostic["returned_snapshot_times_utc"] == labels
    assert diagnostic["login_timezone_offset_hours"] == safe_offset
    assert diagnostic["observation_count"] == 4
    assert diagnostic["pagination"] == {
        "page_number": 1,
        "page_size": 4,
        "total_pages": 1,
    }
    assert diagnostic["allowance"] == {
        "remaining": 9875,
        "total": 10000,
        "reset_seconds": 169890,
    }
    assert len(session.calls) == 2


def test_malformed_label_does_not_echo_untrusted_text():
    payload = response()
    payload["prices"][1] = {**BAR, "snapshotTimeUTC": "private-invalid-label"}
    client, _ = authenticated(Stream(payload))
    with pytest.raises(IGMarketError, match="malformed") as caught:
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )
    assert "private" not in str(caught.value)


def test_duplicate_range_labels_rejected():
    payload = response()
    payload["prices"][1] = payload["prices"][0]
    client, _ = authenticated(Stream(payload))
    with pytest.raises(IGMarketError, match="duplicate or disordered"):
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )


@pytest.mark.parametrize("identity", [{"epic": "OTHER"}, {"resolution": "MINUTE"}])
def test_wrong_series_rejected(identity):
    client, _ = authenticated(Stream({**response(), **identity}))
    with pytest.raises(IGMarketError, match="H1"):
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )


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
    result = client.get_h1_range(
        EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
    )
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
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )
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
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )
    assert error.value.historical
    assert len(session.calls) == 2


def test_outbound_validation_and_response_bytes_budget():
    stream = Stream(b"x" * 262_145)
    client, session = authenticated(stream)
    with pytest.raises(ValueError, match="bounds"):
        client.get_h1_range(
            EPIC, START, END, point_limit=2, request_timezone=UTC_REQUEST
        )
    with pytest.raises(ValueError, match="bounds"):
        client.get_h1_range(
            EPIC,
            START,
            END + timedelta(days=1),
            point_limit=25,
            request_timezone=UTC_REQUEST,
        )
    assert len(session.calls) == 1
    with pytest.raises(IGMarketError, match="byte budget"):
        client.get_h1_range(
            EPIC, START, END, point_limit=3, request_timezone=UTC_REQUEST
        )
    assert stream.closed
