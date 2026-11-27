"""Run one bounded, redacted IG demo broker-state diagnostic."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from mrml.broker.admission import MAX_COLLECTION_SPAN
from mrml.broker.gate import MAX_AGE
from mrml.data.ig import (
    IGClient,
    IGError,
    IGExecutionCapture,
    IGLookupNotFound,
    validate_activity_window,
    validate_deal_id,
    validate_deal_reference,
)
from mrml.data.ig_probe import ProbeConfig, load_probe_values


def _timestamp(value: str) -> datetime:
    try:
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
        ):
            raise ValueError
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DDTHH:MM:SSZ") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("accounts", "positions", "working-orders", "confirmation", "position"):
        commands.add_parser(name)
    commands.add_parser("execution-evidence")
    activity = commands.add_parser("activity")
    activity.add_argument("--from", dest="start", required=True, type=_timestamp)
    activity.add_argument("--to", dest="end", required=True, type=_timestamp)
    args = parser.parse_args(argv)

    try:
        if args.command == "activity":
            validate_activity_window(args.start, args.end)
        values = load_probe_values()
        execution = args.command == "execution-evidence"
        config = ProbeConfig.from_environ(values, read_epic=execution)
        if execution:
            args.epic = config.epic
            args.account_id = values.get("MRML_IG_EXECUTION_ACCOUNT_ID")
            if not args.epic:
                raise ValueError("Set MRML_IG_EPIC")
            if not args.account_id or not args.account_id.strip():
                raise ValueError("Set MRML_IG_EXECUTION_ACCOUNT_ID")
        if args.command in {"confirmation", "position"}:
            args.account_id = values.get("MRML_IG_LOOKUP_ACCOUNT_ID")
            args.identifier = values.get("MRML_IG_LOOKUP_IDENTIFIER") or ""
            if not args.account_id or not args.account_id.strip():
                raise ValueError("Set MRML_IG_LOOKUP_ACCOUNT_ID")
            validator = (
                validate_deal_reference
                if args.command == "confirmation"
                else validate_deal_id
            )
            validator(args.identifier)
        with IGClient(config.api_key, config.username, config.password) as client:
            client.authenticate(bounded=True)
            if execution:
                capture = client.discover_execution_evidence(
                    args.epic, account_id=args.account_id
                )
                result, summary = _execution_summary(capture)
                print(
                    json.dumps(
                        {"command": args.command, "result": result, **summary},
                        default=_evidence_json,
                    )
                )
                return 0 if result == "observed" else 2

            summary = _summary(client, args)
    except IGLookupNotFound:
        print(
            json.dumps(
                {
                    "command": args.command,
                    "result": "inconclusive",
                    "reason": "not_found",
                }
            )
        )
        return 2
    except (ValueError, IGError) as error:
        print(
            json.dumps({"command": args.command, "result": "fail", "error": str(error)})
        )
        return 1

    print(json.dumps({"command": args.command, "result": "pass", **summary}))
    return 0


def _evidence_json(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError("Unexpected execution evidence type")


def _execution_summary(capture: IGExecutionCapture) -> tuple[str, dict[str, Any]]:
    session = capture.session
    account = capture.account
    balances = account.balances
    clock_ordered = (
        session.authenticated_at
        <= capture.market_observed_at
        <= capture.positions.started_at
        <= capture.positions.observed_at
        <= capture.working_orders.started_at
        <= capture.working_orders.observed_at
        <= capture.finished_at
    )
    span = max(
        capture.exposure_span_seconds,
        (
            capture.working_orders.observed_at - capture.positions.started_at
        ).total_seconds(),
    )
    age = max(
        capture.exposure_age_seconds,
        (capture.finished_at - capture.positions.started_at).total_seconds(),
    )
    session_age = max(
        capture.session_age_seconds,
        (capture.finished_at - session.authenticated_at).total_seconds(),
    )
    fresh = (
        clock_ordered
        and span <= MAX_COLLECTION_SPAN.total_seconds()
        and age <= MAX_AGE.total_seconds()
        and session_age <= MAX_AGE.total_seconds()
    )
    dealing_enabled = "missing"
    if session.dealing_enabled_malformed:
        dealing_enabled = "malformed"
    elif session.dealing_enabled is not None:
        dealing_enabled = "enabled" if session.dealing_enabled else "disabled"

    result = "observed" if fresh else "inconclusive"
    return result, {
        "schema": "ig-demo-execution-evidence-v1",
        "environment": "demo",
        "session": {
            "local_generation": str(session.generation),
            "authenticated_at": session.authenticated_at,
            "active_account_matched_before_and_after": True,
            "dealing_enabled": dealing_enabled,
            "broker_expiry": "unknown",
            "age_seconds": session_age,
        },
        "account": {
            "status": "ENABLED",
            "currency": account.currency,
            "type": (
                account.account_type
                if account.account_type in {"CFD", "SPREADBET", "PHYSICAL"}
                else ("missing" if account.account_type is None else "unsupported")
            ),
            "balance_fields": {
                name: "available"
                if balances is not None and getattr(balances, name) is not None
                else "missing"
                for name in ("balance", "available", "deposit", "profit_loss")
            },
        },
        "exposure": {
            "positions": {
                **asdict(capture.positions),
                "coverage": "bounded_account_wide",
            },
            "working_orders": {
                **asdict(capture.working_orders),
                "coverage": "bounded_account_wide",
            },
            "finished_at": capture.finished_at,
            "span_seconds": span,
            "age_seconds": age,
            "freshness": "within_window" if fresh else "outside_window",
        },
        "market": {**asdict(capture.market), "observed_at": capture.market_observed_at},
    }


def _summary(client: IGClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "confirmation":
        evidence = client.get_confirmation(args.identifier, account_id=args.account_id)
        return {
            "deal_status": evidence.deal_status,
            "affected_deal_count": (
                len(evidence.affected_deals)
                if evidence.affected_deals is not None
                else None
            ),
        }
    if args.command == "position":
        client.get_position(args.identifier, account_id=args.account_id)
        return {"count": 1}
    if args.command == "accounts":
        accounts = client.get_broker_accounts()
        return {
            "count": len(accounts),
            "active_account_verified": True,
            "accounts_with_balances": sum(row.balances is not None for row in accounts),
        }
    if args.command == "positions":
        positions = client.get_positions()
        return {
            "count": len(positions),
            "unique_deal_ids": len({row.deal_id for row in positions}),
            "unique_epics": len({row.epic for row in positions}),
            "with_deal_reference": sum(
                row.deal_reference is not None for row in positions
            ),
        }
    if args.command == "working-orders":
        orders = client.get_working_orders()
        return {
            "count": len(orders),
            "unique_deal_ids": len({row.deal_id for row in orders}),
            "unique_epics": len({row.epic for row in orders}),
            "with_deal_reference": sum(
                row.deal_reference is not None for row in orders
            ),
        }
    rows = client.get_activity(args.start, args.end)
    return {
        "count": len(rows),
        "with_activity_id": sum(row.activity_id is not None for row in rows),
        "with_deal_id": sum(row.deal_id is not None for row in rows),
        "with_deal_reference": sum(row.deal_reference is not None for row in rows),
        "affected_deals_matching_activity_deal": sum(
            row.deal_id is not None
            and any(
                action.affected_deal_id == row.deal_id for action in row.actions or ()
            )
            for row in rows
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
