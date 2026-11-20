"""Collect bounded, read-only evidence about IG demo H1 source semantics."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mrml.data.ig import (
    RESOLUTION,
    IGClient,
    IGError,
    IGObservation,
    IGPriceSample,
    IGQuote,
    _array,
    _object,
    _observation,
    _optional_text,
)
from mrml.data.ig_probe import ProbeConfig, load_probe_values

VERSION = "ig-h1-semantics/v1"
H1_LIMIT = 3
M1_LIMIT = 24
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
BOUNDARIES = ("start", "end")


def _market_metadata(instrument: dict[str, Any], status: str | None) -> dict[str, Any]:
    hours = _object(instrument.get("openingHours") or {})
    size = instrument.get("lotSize")
    if (
        not isinstance(size, (int, float))
        or isinstance(size, bool)
        or not math.isfinite(size)
    ):
        size = None
    ranges = []
    for value in _array(hours.get("marketTimes") or []):
        row = _object(value)
        ranges.append(
            {key: _optional_text(row.get(key)) for key in ("openTime", "closeTime")}
        )
    return {
        **{
            key: _optional_text(instrument.get(key))
            for key in ("name", "type", "expiry", "contractSize")
        },
        "lotSize": size,
        "marketStatus": status,
        "openingHours": ranges,
        "variant": "unverified; review name, expiry and sizing for this EPIC",
    }


def _price_row(row: IGObservation) -> dict[str, Any]:
    return {
        "snapshotTimeUTC": row.raw_timestamp,
        "openPrice": asdict(row.open),
        "highPrice": asdict(row.high),
        "lowPrice": asdict(row.low),
        "closePrice": asdict(row.close),
    }


def _latest_sample(
    client: IGClient,
    epic: str,
    resolution: str,
    limit: int,
    now: Callable[[], datetime],
) -> tuple[IGPriceSample, dict[str, Any]]:
    requested_at = now()
    sample = client._get_price_sample(epic, resolution, limit, retry=False)
    retrieved_at = now()
    return sample, {
        "requested_at": requested_at.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "count": len(sample.observations),
        "allowance": asdict(sample.allowance) if sample.allowance else None,
        "prices": [_price_row(row) for row in sample.observations],
    }


def _aggregate(minutes: list[IGObservation]) -> tuple[IGQuote, ...]:
    return (
        minutes[0].open,
        IGQuote(
            max(row.high.bid for row in minutes), max(row.high.ask for row in minutes)
        ),
        IGQuote(
            min(row.low.bid for row in minutes), min(row.low.ask for row in minutes)
        ),
        minutes[-1].close,
    )


def _compare_windows(
    hourly: tuple[IGObservation, ...],
    minute_rows: tuple[IGObservation, ...],
    h1_requested: datetime,
    h1_retrieved: datetime,
) -> list[dict[str, Any]]:
    comparisons = []
    for hour in hourly:
        for h1_label in BOUNDARIES:
            start = hour.timestamp if h1_label == "start" else hour.timestamp - HOUR
            end = start + HOUR
            for m1_label in BOUNDARIES:
                offset = MINUTE if m1_label == "end" else timedelta()
                # Use only minutes whose hypothetical ends precede the H1 request.
                # These are diagnostic windows, never canonical intervals.
                points = [
                    row
                    for row in minute_rows
                    if start <= row.timestamp - offset < end
                    and row.timestamp - offset + MINUTE <= h1_requested
                ]
                opening_match = close_match = within_range = None
                if points:
                    opening, high, low, close = _aggregate(points)
                    if points[0].timestamp - offset == start:
                        opening_match = opening == hour.open
                    if points[-1].timestamp - offset + MINUTE == end:
                        close_match = close == hour.close
                    within_range = (
                        hour.low.bid <= low.bid
                        and hour.low.ask <= low.ask
                        and high.bid <= hour.high.bid
                        and high.ask <= hour.high.ask
                    )
                comparisons.append(
                    {
                        "h1_snapshotTimeUTC": hour.raw_timestamp,
                        "h1_hypothesis": h1_label,
                        "m1_hypothesis": m1_label,
                        "candidate_elapsed_at_request": end <= h1_requested,
                        "candidate_in_progress_through_retrieval": (
                            start <= h1_requested <= h1_retrieved < end
                        ),
                        "m1_count": len(points),
                        "first_m1_snapshotTimeUTC": points[0].raw_timestamp
                        if points
                        else None,
                        "last_m1_snapshotTimeUTC": points[-1].raw_timestamp
                        if points
                        else None,
                        "opening_matches_at_edge": opening_match,
                        "close_matches_at_edge": close_match,
                        "m1_extrema_within_h1": within_range,
                    }
                )
    return comparisons


def _prior_h1(path: Path, epic: str) -> tuple[datetime, dict[datetime, IGObservation]]:
    try:
        with path.open("rb") as stream:
            data = stream.read(65_537)
        if len(data) > 65_536:
            raise ValueError
        prior = _object(json.loads(data))
        if (
            prior.get("version") != VERSION
            or prior.get("provider") != "IG"
            or (prior.get("environment") != "demo" or prior.get("epic") != epic)
        ):
            raise ValueError
        h1 = _object(_object(prior.get("history")).get("HOUR"))
        retrieved_at = datetime.fromisoformat(h1["retrieved_at"])
        if retrieved_at.utcoffset() != timedelta():
            raise ValueError
        rows = _array(h1.get("prices"))
        if len(rows) > H1_LIMIT:
            raise ValueError
        parsed = [_observation(_object(row)) for row in rows]
        observations = {row.timestamp: row for row in parsed}
        if len(observations) != len(rows):
            raise ValueError
        return retrieved_at, observations
    except (OSError, ValueError, OverflowError, TypeError, KeyError, RecursionError):
        raise ValueError(
            "Invalid previous IG audit evidence for this demo EPIC"
        ) from None


def collect(
    client: IGClient,
    epic: str,
    *,
    previous: tuple[datetime, dict[datetime, IGObservation]] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    started = now()
    if previous and previous[0] >= started:
        raise ValueError("Previous evidence must predate this execution")
    client.authenticate()
    market, instrument = client._eurusd_market_details(epic)
    try:
        metadata = _market_metadata(instrument, market.status)
    except (ValueError, OverflowError):
        raise ValueError("IG returned malformed product or session metadata") from None
    metadata["retrieved_at"] = now().isoformat()
    hourly, h1 = _latest_sample(client, epic, RESOLUTION, H1_LIMIT, now)
    minute_error = None
    minute_rows: tuple[IGObservation, ...] = ()
    m1 = None
    try:
        minute_sample, m1 = _latest_sample(client, epic, "MINUTE", M1_LIMIT, now)
        minute_rows = minute_sample.observations
    except IGError as error:
        minute_error = str(error)

    overlaps = []
    if previous:
        for row in hourly.observations:
            old = previous[1].get(row.timestamp)
            if old is not None:
                overlaps.append(
                    {
                        "snapshotTimeUTC": row.raw_timestamp,
                        "ohlc_changed": row != old,
                        "prior_h1_retrieved_at": previous[0].isoformat(),
                        "prior_bid_ask_ohlc": _price_row(old),
                        "candidate_elapsed_at_prior_retrieval": {
                            "start": row.timestamp + HOUR <= previous[0],
                            "end": row.timestamp <= previous[0],
                        },
                    }
                )
    comparisons = _compare_windows(
        hourly.observations,
        minute_rows,
        datetime.fromisoformat(h1["requested_at"]),
        datetime.fromisoformat(h1["retrieved_at"]),
    )
    forming_candidates = {
        label: sorted(
            {
                row["h1_snapshotTimeUTC"]
                for row in comparisons
                if row["h1_hypothesis"] == label
                and row["candidate_in_progress_through_retrieval"]
            }
        )
        for label in BOUNDARIES
    }
    return {
        "version": VERSION,
        "executed_at": started.isoformat(),
        "provider": "IG",
        "environment": "demo",
        "epic": epic,
        "market": metadata,
        "history": {"HOUR": h1, "MINUTE": m1},
        "minute_error": minute_error,
        "comparisons": comparisons,
        "forming_candidates_by_h1_hypothesis": forming_candidates,
        "previous_evidence_supplied": previous is not None,
        "overlapping_h1_observations": overlaps,
        "timestamp_policy": "unresolved",
        "broker_completion": "unknown",
        "current_candle_limit": (
            "Market status was TRADEABLE. Check returned labels under both hypotheses."
            if market.status == "TRADEABLE"
            else "Market was not reported TRADEABLE. This run cannot settle "
            "forming-candle availability."
        ),
        "comparison_limit": (
            "M1 windows are partial and M1 labels are hypothetical. Edge matches "
            "provide supporting observations. Mismatches weigh against the paired "
            "hypotheses if aggregation and prices agree across retrievals."
        ),
        "next_verification": (
            "Repeat manually during an active session with saved evidence. Compare "
            "across an hour boundary and seek independent confirmation of M1/H1 labels."
        ),
    }


def _print_summary(report: dict[str, Any]) -> None:
    print(f"{report['version']} | {report['executed_at']} | IG demo | {report['epic']}")
    print("Market " + json.dumps(report["market"], sort_keys=True, allow_nan=False))
    for resolution, sample in report["history"].items():
        if sample is not None:
            print(
                f"{resolution} | {sample['count']} points"
                f" | requested {sample['requested_at']}"
                f" | retrieved {sample['retrieved_at']}"
            )
            quota = sample["allowance"]
            print(f"  Historical allowance {quota if quota else 'unreported'}")
        else:
            print(f"{resolution} | {report['minute_error']}")
    for row in report["history"]["HOUR"]["prices"]:
        print("H1 " + json.dumps(row, sort_keys=True))
    for row in report["comparisons"]:
        if row["m1_count"] or row["candidate_in_progress_through_retrieval"]:
            print("Candidate " + json.dumps(row, sort_keys=True))
    print(
        "Forming H1 candidates by hypothesis "
        + json.dumps(report["forming_candidates_by_h1_hypothesis"], sort_keys=True)
    )
    overlaps = report["overlapping_h1_observations"]
    if overlaps:
        for row in overlaps:
            print(
                f"H1 {row['snapshotTimeUTC']} | OHLC changed {row['ohlc_changed']}"
                f" | prior retrieval {row['prior_h1_retrieved_at']}"
            )
    else:
        print(
            "H1 revisions "
            + (
                "no overlapping labels"
                if report["previous_evidence_supplied"]
                else "no prior observation supplied"
            )
        )
    print("Timestamp policy unresolved. Broker completion unknown.")
    print(report["current_candle_limit"])
    print(report["comparison_limit"])
    print(report["next_verification"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit detailed evidence to stdout"
    )
    parser.add_argument(
        "--previous", type=Path, help="compare against a prior --json run"
    )
    args = parser.parse_args()
    try:
        config = ProbeConfig.from_environ(load_probe_values())
        if config.epic is None:
            raise ValueError("Set MRML_IG_EPIC explicitly for this audit")
        previous = _prior_h1(args.previous, config.epic) if args.previous else None
        with IGClient(config.api_key, config.username, config.password) as client:
            report = collect(client, config.epic, previous=previous)
        if args.json:
            print(json.dumps(report, allow_nan=False, indent=2, sort_keys=True))
        else:
            _print_summary(report)
    except (IGError, ValueError) as error:
        print(f"IG semantics audit failed ({error})", file=sys.stderr)
        return 1
    return int(report["minute_error"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
