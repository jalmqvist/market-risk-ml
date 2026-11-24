"""Read-only IG demo REST access for a bounded diagnostic."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from http.client import HTTPConnection
from typing import Any, Self
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests import Response, Session
from requests.exceptions import RequestException
from requests.structures import CaseInsensitiveDict

from mrml.data.ig_broker_state import (
    IGAccountBalance,
    IGActivity,
    IGActivityAction,
    IGAffectedDeal,
    IGConfirmation,
    IGPosition,
    IGPositionObservation,
    IGWorkingOrder,
)

DEMO_GATEWAY = "https://demo-api.ig.com/gateway/deal"
SAMPLE_SIZE = 32
_SEARCH_PAGE_SIZE = 50
RESOLUTION = "HOUR"
MAX_RANGE_INTERVALS = 24
_MAX_RANGE_BYTES = 262_144
MAX_BROKER_STATE_ROWS = 100
MAX_ACTIVITY_WINDOW = timedelta(hours=24)
ACTIVITY_PAGE_SIZE = 50

_QUOTA_PREFIX = "error.public-api."
_QUOTA_ERRORS = {
    "exceeded-api-key-allowance": "API-key allowance",
    "exceeded-account-allowance": "account allowance",
    "exceeded-account-historical-data-allowance": "historical-price allowance",
    "exceeded-account-trading-allowance": "account trading allowance",
}
_SESSION_ERRORS = {
    "error.security.account-token-invalid",
    "error.security.account-token-missing",
    "error.security.client-token-invalid",
    "error.security.client-token-missing",
}


class IGError(RuntimeError):
    """IG diagnostic failure with a safe message."""


class IGLookupNotFound(IGError):
    """IG returned the documented lookup-specific not-found response."""


class IGEvidenceConflict(IGError):
    """Returned evidence conflicts with the requested identity."""


class IGTransportError(IGError):
    """IG transport failed before a usable response was available."""


class IGMalformedResponse(IGError):
    """The broker response failed validation."""


class IGAuthenticationError(IGError):
    """Login credentials, API key or login response failed validation."""


class IGSessionError(IGError):
    """No usable IG session remains."""


@dataclass(frozen=True)
class IGRequestTimezone:
    """Operator-approved account timezone for historical request bounds."""

    name: str
    approval: str = field(repr=False)
    _zone: ZoneInfo = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.approval.strip():
            raise IGError("IG historical request timezone requires approval evidence")
        if self.name in {"localtime", "posixrules"} or self.name.startswith(
            ("posix/", "right/")
        ):
            raise IGError("IG historical request timezone must be an IANA timezone")
        try:
            zone = ZoneInfo(self.name)
        except (ValueError, ZoneInfoNotFoundError):
            raise IGError(
                "IG historical request timezone must be an IANA timezone"
            ) from None
        object.__setattr__(self, "_zone", zone)

    def wire_bounds(self, start: datetime, end: datetime) -> tuple[str, str]:
        if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
            raise IGError("IG historical request bounds must be UTC")
        local_start, local_end = (
            bound.astimezone(self._zone) for bound in (start, end)
        )
        for bound in (local_start, local_end):
            if bound.replace(fold=0).utcoffset() != bound.replace(fold=1).utcoffset():
                raise IGError(
                    "IG historical request bound is ambiguous in the approved timezone"
                )
        # Check offsets throughout the bounded window, including a terminal
        # transition. The feed supports at most 24 UTC hours per request.
        cursor = start
        offset = local_start.utcoffset()
        while cursor < end:
            cursor = min(cursor + timedelta(hours=1), end)
            if cursor.astimezone(self._zone).utcoffset() != offset:
                raise IGError(
                    "IG historical request spans a timezone offset transition"
                )
        return (
            local_start.strftime("%Y-%m-%dT%H:%M:%S"),
            local_end.strftime("%Y-%m-%dT%H:%M:%S"),
        )


class IGQuotaError(IGError):
    """An IG allowance or request throttle was reached."""

    def __init__(
        self,
        message: str,
        *,
        historical: bool = False,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.historical = historical
        self.retry_at = retry_at


class IGMarketError(IGError):
    """Market discovery, availability or historical data failed validation."""


@dataclass(frozen=True)
class IGAccount:
    account_id: str = field(repr=False)
    currency: str | None
    account_type: str | None
    status: str | None
    balances: IGAccountBalance | None = field(default=None, repr=False)
    active: bool | None = None


@dataclass(frozen=True)
class IGMarket:
    epic: str = field(repr=False)
    status: str | None


@dataclass(frozen=True)
class IGQuote:
    bid: float
    ask: float


@dataclass(frozen=True)
class IGObservation:
    timestamp: datetime
    open: IGQuote
    high: IGQuote
    low: IGQuote
    close: IGQuote
    raw_timestamp: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class IGAllowance:
    remaining: int
    total: int
    reset_seconds: int


@dataclass(frozen=True)
class IGPricePage:
    page_number: int
    page_size: int
    total_pages: int


@dataclass(frozen=True)
class IGPriceSample:
    epic: str = field(repr=False)
    resolution: str
    observations: tuple[IGObservation, ...]
    allowance: IGAllowance | None
    gaps: int
    page: IGPricePage | None = None


def validate_epic(value: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", value) is None or (
        len(value) > 100
    ):
        raise ValueError("Invalid IG EPIC syntax")


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")
    return value


def _optional_text(value: Any) -> str | None:
    return None if value is None else _text(value)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Expected array")
    return value


def _header(value: str) -> bool:
    return bool(value) and all(33 <= ord(char) <= 126 for char in value)


def _is_eurusd(instrument_type: Any, name: str) -> bool:
    return instrument_type == "CURRENCIES" and bool(
        re.search(r"\bEUR\s*/\s*USD\b", name, flags=re.IGNORECASE)
    )


@contextmanager
def _private_http_logs() -> Iterator[None]:
    # Transport logs can include request URLs and exception details.
    def discard(_: logging.LogRecord) -> bool:
        return False

    loggers = [
        logging.getLogger(name)
        for name in (
            "urllib3.connectionpool",
            "urllib3.util.retry",
            "requests.sessions",
        )
    ]
    for logger in loggers:
        logger.addFilter(discard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(discard)


def _retry_delay(value: str | None) -> float | None:
    """Return a short delay, or decline to retry if IG asks for a longer wait."""
    delay = 0.25
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                deadline = parsedate_to_datetime(value)
                delay = (deadline - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                pass
    return max(0.0, delay) if math.isfinite(delay) and delay <= 2 else None


def _retry_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.isascii() and value.isdecimal():
            return datetime.now(UTC) + timedelta(seconds=int(value))
        deadline = parsedate_to_datetime(value)
        return deadline.astimezone(UTC) if deadline.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _bounded_json(
    response: Response, *, broker_state: bool = False
) -> dict[str, Any] | None:
    with response:
        data = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            data.extend(chunk)
            if len(data) > _MAX_RANGE_BYTES:
                if broker_state:
                    raise IGError("IG broker-state response exceeds the byte budget")
                raise IGMarketError("IG historical response exceeds the byte budget")
        try:
            return _object(
                json.loads(data, parse_float=Decimal if broker_state else None)
            )
        except (ValueError, RecursionError, InvalidOperation):
            return None


def _json(response: Response) -> dict[str, Any] | None:
    try:
        return _object(response.json())
    except ValueError:
        return None


class IGClient:
    """One explicit login, in-memory tokens, and demo-only GET operations."""

    def __init__(
        self,
        api_key: str,
        username: str,
        password: str,
        *,
        session: Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _header(api_key) or not username.strip() or not password.strip():
            raise ValueError("IG demo requires a valid API key, username and password")
        self._api_key = api_key
        self._login = {"identifier": username, "password": password}
        self._tokens: dict[str, str] = {}
        self._active_account_id: str | None = None
        self._login_timezone_offset_hours: int | float | None = None
        self._session = requests.Session() if session is None else session
        self._owns_session = session is None
        if self._owns_session:
            self._session.trust_env = False
        self._sleep = sleep

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self._tokens.clear()
        self._active_account_id = None
        if self._owns_session:
            self._session.cookies.clear()
            self._session.close()

    def authenticate(self, *, bounded: bool = False) -> None:
        self._tokens.clear()
        self._active_account_id = None
        self._login_timezone_offset_hours = None
        payload, response = self._request(
            "/session", 2, login=True, broker_state=bounded
        )
        headers = CaseInsensitiveDict(response.headers)
        tokens = {name: headers.get(name, "") for name in ("CST", "X-SECURITY-TOKEN")}
        if not all(_header(value) for value in tokens.values()):
            raise IGAuthenticationError(
                "IG login response is missing valid session tokens"
            )
        for name in ("currentAccountId", "clientId"):
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise IGAuthenticationError("IG returned malformed login data")
        self._tokens = tokens
        self._active_account_id = payload["currentAccountId"]
        offset = payload.get("timezoneOffset")
        if isinstance(offset, Decimal) and offset.is_finite():
            offset = float(offset)
        if (
            isinstance(offset, (int, float))
            and not isinstance(offset, bool)
            and -14 <= offset <= 14
        ):
            self._login_timezone_offset_hours = offset

    def get_accounts(self) -> tuple[IGAccount, ...]:
        """Return the account discovery fields used by the connectivity probe."""
        payload, _ = self._request("/accounts", 1)
        return self._parse_accounts(payload, broker_state=False)

    def get_broker_accounts(self) -> tuple[IGAccount, ...]:
        """Return bounded account state and identify the enabled login account."""
        payload = self._broker_request("/accounts", 1)
        accounts = self._parse_accounts(payload, broker_state=True)
        if not any(row.active and row.status == "ENABLED" for row in accounts):
            raise IGSessionError("IG login account is missing or is not enabled")
        return accounts

    def _parse_accounts(
        self, payload: dict[str, Any], *, broker_state: bool
    ) -> tuple[IGAccount, ...]:
        try:
            rows = (
                _broker_rows(payload, "accounts")
                if broker_state
                else _array(payload.get("accounts"))
            )
            accounts = []
            for row in rows:
                item = _object(row)
                currency = _optional_text(item.get("currency"))
                if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError("Invalid currency")
                account_id = _text(item.get("accountId"))
                enum = _optional_broker_enum if broker_state else _optional_text
                accounts.append(
                    IGAccount(
                        account_id,
                        currency,
                        enum(item.get("accountType")),
                        enum(item.get("status")),
                        _account_balances(item.get("balance"))
                        if broker_state
                        else None,
                        active=(account_id == self._active_account_id)
                        if broker_state
                        else None,
                    )
                )
            if not accounts or len({item.account_id for item in accounts}) != len(
                accounts
            ):
                raise ValueError("Expected distinct accounts")
            return tuple(accounts)
        except ValueError:
            raise IGError("IG returned empty or malformed account data") from None

    def _verify_lookup_account(self, account_id: str) -> None:
        _text(account_id)
        accounts = self.get_broker_accounts()
        if not any(row.active and row.account_id == account_id for row in accounts):
            raise IGSessionError("IG active account does not match the intended scope")

    def get_confirmation(
        self, deal_reference: str, *, account_id: str
    ) -> IGConfirmation:
        validate_deal_reference(deal_reference)
        self._verify_lookup_account(account_id)
        payload = self._broker_request(
            f"/confirms/{deal_reference}", 1, not_found="error.confirms.deal-not-found"
        )
        try:
            evidence = _confirmation(payload)
        except ValueError:
            raise IGMalformedResponse(
                "IG returned malformed confirmation data"
            ) from None
        if evidence.deal_reference != deal_reference:
            raise IGEvidenceConflict("IG confirmation reference conflicts with request")
        return evidence

    def get_position(self, deal_id: str, *, account_id: str) -> IGPositionObservation:
        validate_deal_id(deal_id)
        self._verify_lookup_account(account_id)
        payload = self._broker_request(
            f"/positions/{quote(deal_id, safe='')}",
            1,
            not_found="error.position.notfound",
        )
        observed_at = datetime.now(UTC)
        try:
            position = _position(payload, size_field="size", level_field="level")
            row = _object(payload.get("position"))
            observation = IGPositionObservation(
                position,
                observed_at,
                _optional_text(row.get("createdDateUTC")),
                _optional_broker_decimal(row.get("stopLevel")),
                _optional_broker_decimal(row.get("limitLevel")),
            )
        except ValueError:
            raise IGMalformedResponse(
                "IG returned malformed individual-position data"
            ) from None
        if position.deal_id != deal_id:
            raise IGEvidenceConflict("IG position identity conflicts with request")
        return observation

    def get_positions(self) -> tuple[IGPosition, ...]:
        self.get_broker_accounts()
        payload = self._broker_request("/positions", 1)
        try:
            return tuple(_position(row) for row in _broker_rows(payload, "positions"))
        except ValueError:
            raise IGError("IG returned malformed position data") from None

    def get_working_orders(self) -> tuple[IGWorkingOrder, ...]:
        self.get_broker_accounts()
        payload = self._broker_request("/workingorders", 2)
        try:
            return tuple(
                _working_order(row) for row in _broker_rows(payload, "workingOrders")
            )
        except ValueError:
            raise IGError("IG returned malformed working-order data") from None

    def get_activity(self, start: datetime, end: datetime) -> tuple[IGActivity, ...]:
        validate_activity_window(start, end)
        self.get_broker_accounts()
        payload = self._broker_request(
            "/history/activity",
            3,
            params={
                "from": start.isoformat(timespec="seconds").removesuffix("+00:00")
                + "Z",
                "to": end.isoformat(timespec="seconds").removesuffix("+00:00") + "Z",
                "detailed": "true",
                "pageSize": ACTIVITY_PAGE_SIZE,
            },
        )
        try:
            rows = _broker_rows(payload, "activities", limit=ACTIVITY_PAGE_SIZE)
            paging = _object(_object(payload.get("metadata")).get("paging"))
            size = _nonnegative_integer(paging.get("size"))
            next_page = paging["next"]
            if next_page is not None:
                _text(next_page)
                raise IGError("IG activity exceeds one page; narrow the UTC window")
            if not len(rows) <= size <= ACTIVITY_PAGE_SIZE:
                raise ValueError("Invalid activity page size")
            return tuple(_activity(row) for row in rows)
        except (ValueError, KeyError):
            raise IGError("IG returned malformed or oversized activity data") from None

    def _broker_request(
        self,
        path: str,
        version: int,
        *,
        params: dict[str, str | int] | None = None,
        not_found: str | None = None,
    ) -> dict[str, Any]:
        payload, _ = self._request(
            path,
            version,
            retry=False,
            broker_state=True,
            params=params,
            not_found=not_found,
        )
        return payload

    def find_market(self, epic: str | None = None) -> IGMarket:
        if epic is not None:
            market, _ = self._eurusd_market_details(epic)
            return market

        payload, _ = self._request(
            "/markets",
            2,
            params={
                "searchTerm": "EUR/USD",
                "pageNumber": 0,
                "pageSize": _SEARCH_PAGE_SIZE,
            },
        )
        try:
            rows = _array(payload.get("markets"))
            total_pages = payload.get("totalPages")
            total_results = payload.get("totalResults", len(rows))
            page_number = payload.get("pageNumber", 0)
            if (
                (
                    total_pages is not None
                    and (type(total_pages) is not int or total_pages < 0)
                )
                or type(total_results) is not int
                or total_results < len(rows)
                or type(page_number) is not int
                or page_number != 0
                or (rows and total_pages == 0)
            ):
                raise ValueError("Invalid market pagination")
            if (
                (total_pages is not None and total_pages > 1)
                or total_results > len(rows)
                or len(rows) > _SEARCH_PAGE_SIZE
                or (total_pages is None and len(rows) == _SEARCH_PAGE_SIZE)
            ):
                raise IGMarketError(
                    "IG market search exceeds one page; set MRML_IG_EPIC"
                )
            matches = {}
            for row in rows:
                item = _object(row)
                if not _is_eurusd(
                    item.get("instrumentType"), _text(item.get("instrumentName"))
                ):
                    continue
                candidate = _text(item.get("epic"))
                validate_epic(candidate)
                expiry = _optional_text(item.get("expiry"))
                status = _optional_text(item.get("marketStatus"))
                matches[(candidate, expiry)] = IGMarket(candidate, status)
        except ValueError:
            raise IGMarketError(
                "IG returned malformed EUR/USD search results"
            ) from None
        if len(matches) != 1:
            raise IGMarketError(
                "IG returned no EUR/USD market; check market access or set MRML_IG_EPIC"
                if not matches
                else "IG returned multiple EUR/USD markets; set MRML_IG_EPIC"
            )
        return next(iter(matches.values()))

    def _eurusd_market_details(self, epic: str) -> tuple[IGMarket, dict[str, Any]]:
        validate_epic(epic)
        payload, _ = self._request(f"/markets/{epic}", 2)
        try:
            instrument = _object(payload.get("instrument"))
            snapshot = _object(payload.get("snapshot"))
            name = _text(instrument.get("name"))
            if instrument.get("epic") != epic or not _is_eurusd(
                instrument.get("type"), name
            ):
                raise ValueError("Unexpected market")
            return IGMarket(
                epic, _optional_text(snapshot.get("marketStatus"))
            ), instrument
        except ValueError:
            raise IGMarketError(
                "IG market details did not validate the configured EUR/USD EPIC"
            ) from None

    def get_h1_sample(self, epic: str) -> IGPriceSample:
        sample = self._get_price_sample(epic, RESOLUTION, SAMPLE_SIZE)
        if not sample.observations:
            raise IGMarketError(
                "IG returned no H1 observations; history is unavailable"
            )
        return sample

    def get_h1_range(
        self,
        epic: str,
        start: datetime,
        end: datetime,
        *,
        point_limit: int,
        request_timezone: IGRequestTimezone | None = None,
    ) -> IGPriceSample:
        """Serialize approved account wall times and validate UTC response labels."""
        if request_timezone is None:
            raise IGError("IG historical request requires an approved IANA timezone")
        validate_epic(epic)
        hour = timedelta(hours=1)
        if (
            start.utcoffset() != timedelta(0)
            or end.utcoffset() != timedelta(0)
            or start != start.replace(minute=0, second=0, microsecond=0)
            or end.microsecond
            or not timedelta(0) < end - start <= MAX_RANGE_INTERVALS * hour
            or type(point_limit) is not int
            or not (end - start) // hour + 1 <= point_limit <= MAX_RANGE_INTERVALS + 1
        ):
            raise ValueError("Invalid IG H1 request bounds or point budget")
        wire_start, wire_end = request_timezone.wire_bounds(start, end)
        payload, _ = self._request(
            f"/prices/{epic}",
            3,
            params={
                "from": wire_start,
                "to": wire_end,
                "resolution": RESOLUTION,
                "pageSize": point_limit,
                "pageNumber": 1,
            },
            retry=False,
            bounded_body=True,
        )
        try:
            prices = _array(payload.get("prices"))
            metadata = _object(payload.get("metadata"))
            page = _object(metadata.get("pageData"))
            pages = _nonnegative_integer(page.get("totalPages"))
            number = _nonnegative_integer(page.get("pageNumber"))
            size = _nonnegative_integer(page.get("pageSize"))
            if (
                pages not in (0, 1)
                or number not in ((0, 1) if not prices and pages == 0 else (1,))
                or (prices and pages == 0)
                or not len(prices) <= size <= point_limit
                or len(prices) > point_limit
                or (
                    "size" in metadata
                    and _nonnegative_integer(metadata["size"]) != len(prices)
                )
            ):
                raise ValueError("Unexpected paging")
            for key, expected in (("epic", epic), ("resolution", RESOLUTION)):
                if any(
                    key in item and item[key] != expected
                    for item in (payload, metadata)
                ):
                    raise ValueError("Unexpected product or resolution")
            pagination = IGPricePage(number, size, pages)
            observations = tuple(_observation(_object(row)) for row in prices)
            for row in observations:
                if row.timestamp < start:
                    reason = "before requested start"
                elif row.timestamp > end:
                    reason = "after requested end"
                elif row.timestamp != row.timestamp.replace(minute=0, second=0):
                    reason = "not aligned to a UTC hour"
                else:
                    continue

                # Only include allowlisted fields. Invalid allowance cannot obscure
                # the label rejection or be used as quota evidence.
                allowance = None
                with suppress(ValueError):
                    allowance = _allowance(metadata.get("allowance"))
                diagnostic = {
                    "snapshotTimeUTC": row.raw_timestamp,
                    "requested_start_utc": start.isoformat(),
                    "requested_end_utc": end.isoformat(),
                    "wire_from": wire_start,
                    "wire_to": wire_end,
                    "observation_count": len(observations),
                    "returned_snapshot_times_utc": [
                        item.raw_timestamp for item in observations
                    ],
                    "login_timezone_offset_hours": self._login_timezone_offset_hours,
                    "pagination": asdict(pagination),
                    "allowance": asdict(allowance) if allowance is not None else None,
                }
                raise IGMarketError(
                    f"IG rejected H1 response: source label {reason}; "
                    f"{json.dumps(diagnostic)}"
                )
            gaps = 0
            for earlier, later in zip(observations, observations[1:], strict=False):
                if earlier.timestamp >= later.timestamp:
                    raise IGMarketError(
                        "IG returned duplicate or disordered H1 source labels"
                    )
                gaps += later.timestamp - earlier.timestamp > hour
            allowance = _allowance(metadata.get("allowance"))
        except (ValueError, OverflowError):
            raise IGMarketError(
                "IG returned malformed or over-budget H1 data"
            ) from None
        return IGPriceSample(
            epic,
            RESOLUTION,
            observations,
            allowance,
            gaps,
            pagination,
        )

    def _get_price_sample(
        self, epic: str, resolution: str, limit: int, *, retry: bool = True
    ) -> IGPriceSample:
        validate_epic(epic)
        payload, _ = self._request(
            f"/prices/{epic}",
            3,
            params={
                "resolution": resolution,
                "max": limit,
                "pageSize": limit,
                "pageNumber": 1,
            },
            retry=retry,
        )
        step = timedelta(minutes=1) if resolution == "MINUTE" else timedelta(hours=1)
        try:
            prices = _array(payload.get("prices"))
            if len(prices) > limit:
                raise ValueError("Too many observations")
            metadata = _object(payload.get("metadata", {}))
            paged = _object(metadata.get("pageData", {}))
            if (
                type(paged.get("totalPages", 1)) is not int
                or paged.get("totalPages", 1) not in (0, 1)
                or (prices and paged.get("pageNumber", 1) != 1)
                or (prices and paged.get("totalPages", 1) == 0)
            ):
                raise ValueError("Unexpected paging")
            observations = tuple(_observation(_object(row)) for row in prices)
            gaps = 0
            for previous, current in zip(observations, observations[1:], strict=False):
                interval = current.timestamp - previous.timestamp
                if interval < step or interval % step:
                    raise ValueError("Unexpected observation order or spacing")
                gaps += interval > step
            allowance = _allowance(metadata.get("allowance"))
        except (ValueError, OverflowError):
            label = "H1" if resolution == RESOLUTION else "M1"
            raise IGMarketError(
                f"IG returned malformed historical {label} data"
            ) from None
        return IGPriceSample(epic, resolution, observations, allowance, gaps)

    def _request(
        self,
        path: str,
        version: int,
        *,
        login: bool = False,
        retry: bool = True,
        bounded_body: bool = False,
        broker_state: bool = False,
        params: dict[str, str | int] | None = None,
        not_found: str | None = None,
    ) -> tuple[dict[str, Any], Response]:
        bounded_body = bounded_body or broker_state
        if not login and not self._tokens:
            raise IGSessionError("IG session is unavailable; authenticate explicitly")
        if HTTPConnection.debuglevel:
            raise IGError("Disable HTTP wire debugging before using the IG client")
        headers = {
            "Accept": "application/json",
            "VERSION": str(version),
            "X-IG-API-KEY": self._api_key,
            **self._tokens,
        }
        for attempt in range(2):
            response = None
            try:
                with _private_http_logs():
                    response = self._session.request(
                        "POST" if login else "GET",
                        DEMO_GATEWAY + path,
                        headers=headers,
                        params=params,
                        json=self._login if login else None,
                        timeout=(5, 10),
                        allow_redirects=False,
                        stream=bounded_body,
                    )
                    status = response.status_code
                    if 300 <= status < 400:
                        if bounded_body:
                            response.close()
                        raise IGError("IG returned an unexpected redirect")
                    payload = (
                        _bounded_json(response, broker_state=broker_state)
                        if bounded_body
                        else _json(response)
                    )
            except RequestException:
                response = None
            if response is None:
                raise IGTransportError("IG transport request failed")

            if status == 200 and payload is not None and "errorCode" not in payload:
                return payload, response

            code = (payload or {}).get("errorCode")
            code = code if isinstance(code, str) else ""
            if status == 404 and not_found is not None and code == not_found:
                raise IGLookupNotFound("IG lookup evidence is unavailable")
            quota = _QUOTA_ERRORS.get(code.removeprefix(_QUOTA_PREFIX))
            if code.startswith(_QUOTA_PREFIX) and quota is not None:
                raise IGQuotaError(
                    f"IG {quota} exhausted",
                    historical=quota == "historical-price allowance",
                    retry_at=_retry_at(response.headers.get("Retry-After")),
                )
            if code.startswith("error.security.api-key-"):
                raise IGAuthenticationError("IG rejected the demo API key")
            if not login and (code in _SESSION_ERRORS or status == 401):
                self._tokens.clear()
                self._active_account_id = None
                raise IGSessionError("IG rejected the session or it expired")
            if login and (
                status in (400, 401, 403)
                or status == 200
                or code.startswith(("error.security.", "error.public-api.failure."))
            ):
                raise IGAuthenticationError(
                    "IG login failed or returned malformed data"
                )

            if status in (429, 502, 503, 504) and not login and attempt == 0 and retry:
                delay = _retry_delay(response.headers.get("Retry-After"))
                if delay is not None:
                    self._sleep(delay)
                    continue
            retry_at = _retry_at(response.headers.get("Retry-After"))
            if status == 429 or (
                bounded_body and status in (502, 503, 504) and retry_at is not None
            ):
                raise IGQuotaError(
                    "IG temporarily throttled requests", retry_at=retry_at
                )
            if path.startswith(("/markets", "/prices")) and status in (400, 403, 404):
                raise IGMarketError(
                    "IG market or historical data unavailable or access denied"
                )
            if status == 200:
                if broker_state:
                    raise IGMalformedResponse(
                        "IG returned a malformed broker-state response"
                    )
                if bounded_body:
                    raise IGMarketError("IG returned a malformed historical response")
                raise IGError("IG returned an unexpected response")
            raise IGError(f"IG request failed (HTTP {status})")
        raise AssertionError("IG retry loop exhausted")


def validate_activity_window(start: datetime, end: datetime) -> None:
    if (
        any(
            value.utcoffset() != timedelta(0) or value.microsecond
            for value in (start, end)
        )
        or not timedelta(0) < end - start <= MAX_ACTIVITY_WINDOW
    ):
        raise ValueError("IG activity needs whole-second UTC bounds within 24 hours")


def _broker_rows(
    payload: dict[str, Any], key: str, *, limit: int = MAX_BROKER_STATE_ROWS
) -> list[Any]:
    rows = _array(payload.get(key))
    if len(rows) > limit:
        raise ValueError("Collection exceeds the row budget")
    return rows


def _broker_enum(value: Any) -> str:
    token = _text(value)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", token):
        raise ValueError("Invalid broker enum")
    return token


def _optional_broker_enum(value: Any) -> str | None:
    return None if value is None else _broker_enum(value)


def _broker_decimal(value: Any) -> Decimal:
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        number = Decimal(value)
    elif isinstance(value, str) and re.fullmatch(
        r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value
    ):
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("Invalid decimal") from None
    else:
        raise ValueError("Expected a decimal number")
    if not number.is_finite():
        raise ValueError("Expected a finite decimal number")
    return number


def _optional_broker_decimal(value: Any) -> Decimal | None:
    return None if value is None else _broker_decimal(value)


def _account_balances(value: Any) -> IGAccountBalance | None:
    if value is None:
        return None
    amounts = _object(value)
    return IGAccountBalance(
        balance=_optional_broker_decimal(amounts.get("balance")),
        available=_optional_broker_decimal(amounts.get("available")),
        deposit=_optional_broker_decimal(amounts.get("deposit")),
        profit_loss=_optional_broker_decimal(amounts.get("profitLoss")),
    )


def validate_deal_reference(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", value):
        raise ValueError("Invalid IG deal reference")


def validate_deal_id(value: str) -> None:
    # Conservative path segment policy; the IG identifier itself is opaque.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid IG deal ID")


def _confirmation(payload: dict[str, Any]) -> IGConfirmation:
    affected = None
    if payload.get("affectedDeals") is not None:
        affected = tuple(
            IGAffectedDeal(_text(item.get("dealId")), _broker_enum(item.get("status")))
            for item in map(_object, _broker_rows(payload, "affectedDeals"))
        )
    status = _broker_enum(payload.get("dealStatus"))
    if status not in {"ACCEPTED", "REJECTED"}:
        raise ValueError("Unknown confirmation outcome")
    return IGConfirmation(
        deal_reference=_text(payload.get("dealReference")),
        deal_status=status,
        affected_deals=affected,
        deal_id=_optional_text(payload.get("dealId")),
        status=_optional_broker_enum(payload.get("status")),
        reason=_optional_broker_enum(payload.get("reason")),
        epic=_optional_text(payload.get("epic")),
        direction=_optional_broker_enum(payload.get("direction")),
        size=_optional_broker_decimal(payload.get("size")),
        level=_optional_broker_decimal(payload.get("level")),
        stop_level=_optional_broker_decimal(payload.get("stopLevel")),
        limit_level=_optional_broker_decimal(payload.get("limitLevel")),
        raw_date=_optional_text(payload.get("date")),
    )


def _position(
    value: Any, *, size_field: str = "dealSize", level_field: str = "openLevel"
) -> IGPosition:
    row = _object(value)
    market = _object(row.get("market"))
    position = _object(row.get("position"))
    return IGPosition(
        deal_id=_text(position.get("dealId")),
        epic=_text(market.get("epic")),
        direction=_broker_enum(position.get("direction")),
        size=_broker_decimal(position.get(size_field)),
        deal_reference=_optional_text(position.get("dealReference")),
        status=_optional_broker_enum(position.get("status")),
        open_level=_optional_broker_decimal(position.get(level_field)),
        created_date=_optional_text(position.get("createdDate")),
    )


def _working_order(value: Any) -> IGWorkingOrder:
    row = _object(value)
    order = _object(row.get("workingOrderData"))
    epic = _text(order.get("epic"))
    if row.get("marketData") is not None:
        market_epic = _optional_text(_object(row["marketData"]).get("epic"))
        if market_epic is not None and market_epic != epic:
            raise ValueError("Conflicting instrument identifiers")
    return IGWorkingOrder(
        deal_id=_text(order.get("dealId")),
        epic=epic,
        direction=_broker_enum(order.get("direction")),
        size=_broker_decimal(order.get("orderSize")),
        deal_reference=_optional_text(order.get("dealReference")),
        status=_optional_broker_enum(order.get("status")),
        order_type=_optional_broker_enum(order.get("orderType")),
        time_in_force=_optional_broker_enum(order.get("timeInForce")),
        order_level=_optional_broker_decimal(order.get("orderLevel")),
        created_date=_optional_text(order.get("createdDate")),
        created_date_utc=_optional_text(order.get("createdDateUTC")),
        good_till_date=_optional_text(order.get("goodTillDate")),
    )


def _activity(value: Any) -> IGActivity:
    item = _object(value)
    details = {} if item.get("details") is None else _object(item["details"])
    activity_id = _optional_text(item.get("activityId"))
    history_id = _optional_text(item.get("activityHistoryId"))
    if activity_id is not None and history_id is not None and activity_id != history_id:
        raise ValueError("Conflicting activity identifiers")
    actions = None
    if details.get("actions") is not None:
        actions = tuple(
            IGActivityAction(
                affected_deal_id=_text(action.get("affectedDealId")),
                action_type=_broker_enum(action.get("actionType")),
            )
            for action in map(_object, _broker_rows(details, "actions"))
        )
    return IGActivity(
        raw_date=_text(item.get("date")),
        deal_id=_optional_text(item.get("dealId")),
        epic=_optional_text(item.get("epic")),
        activity_id=activity_id or history_id,
        deal_reference=_optional_text(details.get("dealReference")),
        direction=_optional_broker_enum(details.get("direction")),
        size=_optional_broker_decimal(details.get("size")),
        status=_optional_broker_enum(item.get("status")),
        activity_type=_optional_broker_enum(item.get("type")),
        channel=_optional_broker_enum(item.get("channel")),
        actions=actions,
    )


def _quote(item: Any) -> IGQuote:
    price = _object(item)
    bid, ask = price.get("bid"), price.get("ask")
    if (
        isinstance(bid, bool)
        or not isinstance(bid, (int, float))
        or isinstance(ask, bool)
        or not isinstance(ask, (int, float))
    ):
        raise ValueError("Missing or invalid bid/ask")
    if not math.isfinite(bid) or not math.isfinite(ask) or not 0 < bid <= ask:
        raise ValueError("Invalid bid/ask levels")
    return IGQuote(bid, ask)


def parse_snapshot_time_utc(raw: str) -> datetime:
    """Parse the REST UTC field without assigning it an interval meaning."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)?", raw):
        raise ValueError("Invalid IG UTC timestamp")
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


def _observation(row: dict[str, Any]) -> IGObservation:
    raw = _text(row.get("snapshotTimeUTC"))
    timestamp = parse_snapshot_time_utc(raw)
    opening, high, low, close = (
        _quote(row.get(name))
        for name in ("openPrice", "highPrice", "lowPrice", "closePrice")
    )
    if not (
        low.bid
        <= min(opening.bid, close.bid)
        <= max(opening.bid, close.bid)
        <= high.bid
        and low.ask
        <= min(opening.ask, close.ask)
        <= max(opening.ask, close.ask)
        <= high.ask
    ):
        raise ValueError("Invalid IG OHLC range")
    return IGObservation(timestamp, opening, high, low, close, raw_timestamp=raw)


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Expected a nonnegative integer")
    return value


def _allowance(value: Any) -> IGAllowance | None:
    if value is None:
        return None
    item = _object(value)
    remaining, total, reset = (
        _nonnegative_integer(item.get(key))
        for key in ("remainingAllowance", "totalAllowance", "allowanceExpiry")
    )
    if remaining > total:
        raise ValueError("Invalid IG historical allowance")
    return IGAllowance(remaining, total, reset)
