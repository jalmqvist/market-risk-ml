"""Explicit command-line access to bounded IG demo H1 ingestion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from mrml.data.canonical import SourceProduct
from mrml.data.ig import IGClient, IGError, IGPriceSample
from mrml.data.ig_ingestion import IGIngestion, IngestionBudget, IngestionRefused, Plan
from mrml.data.ig_probe import ProbeConfig, load_probe_values
from mrml.data.storage import MarketDataRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "status", "ingest", "recover"))
    parser.add_argument("--epic", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--mapping-version")
    parser.add_argument("--timestamp-policy")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", type=datetime.fromisoformat)
    mode.add_argument("--recheck", action="store_true")
    mode.add_argument("--retry-job")
    parser.add_argument("--quota-reset-confirmed", action="store_true")
    parser.add_argument("--max-intervals", type=int, default=6)
    parser.add_argument("--max-points", type=int, default=7)
    parser.add_argument("--catch-up-hours", type=int, default=48)
    parser.add_argument("--minimum-reserve", type=int, default=20)
    args = parser.parse_args(argv)
    if bool(args.mapping_version) != bool(args.timestamp_policy):
        parser.error("Use --mapping-version and --timestamp-policy together")
    if args.command in {"status", "recover"} and (
        args.start or args.recheck or args.retry_job or args.quota_reset_confirmed
    ):
        parser.error("Request planning options apply to plan and ingest")
    url = os.environ.get("MRML_DATABASE_URL")
    if not url:
        parser.error("Set MRML_DATABASE_URL")
    try:
        engine = create_engine(url, hide_parameters=True)
    except (SQLAlchemyError, ValueError, ImportError):
        print("Database configuration is invalid", file=sys.stderr)
        return 1
    try:
        product = SourceProduct("IG", args.epic, args.variant)
        repo = MarketDataRepository(engine)
        approval = (
            repo.resolve_timestamp_policy(
                product, args.mapping_version, args.timestamp_policy
            )
            if args.timestamp_policy
            else None
        )
        ingestion = IGIngestion(
            engine,
            product,
            approval=approval,
            budget=IngestionBudget(
                max_intervals=args.max_intervals,
                max_points=args.max_points,
                catch_up_hours=args.catch_up_hours,
                minimum_reserve=args.minimum_reserve,
            ),
        )
        options = dict(
            start=args.start,
            recheck=args.recheck,
            retry_job=args.retry_job,
            reset_confirmed=args.quota_reset_confirmed,
        )
        exit_code = 0
        if args.command == "status":
            output = ingestion.status()
        elif args.command == "plan":
            output = _describe_plan(ingestion.plan(**options))
            output["budget"] = asdict(ingestion.budget)
        elif args.command == "recover":
            output = {"attempt": ingestion.describe_attempt(ingestion.recover())}
        else:
            if engine.dialect.name != "postgresql":
                raise IngestionRefused("Network ingestion requires PostgreSQL")
            config = ProbeConfig.from_environ(load_probe_values())
            if config.epic and config.epic != args.epic:
                raise IngestionRefused("Configured demo EPIC differs from --epic")

            def fetch(
                epic: str, start: datetime, end: datetime, points: int
            ) -> IGPriceSample:
                with IGClient(
                    config.api_key, config.username, config.password
                ) as client:
                    client.authenticate()
                    return client.get_h1_range(epic, start, end, point_limit=points)

            plan, row = ingestion.run(fetch, **options)
            output = {
                "plan": _describe_plan(plan),
                "attempt": ingestion.describe_attempt(row),
            }
            exit_code = int(plan.refused)
        print(json.dumps(output, default=_json_time, indent=2))
        return exit_code
    except IGError as error:
        print(str(error), file=sys.stderr)
        return 1
    except IngestionRefused as error:
        print(str(error), file=sys.stderr)
        return 1
    except (SQLAlchemyError, ValueError, OverflowError):
        print(
            "Ingestion configuration or persistence failed. "
            "Check the database, registered policy, and durable status.",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()


def _describe_plan(plan: Plan) -> dict[str, object]:
    return {
        **asdict(plan),
        "quota_visibility": "unknown" if plan.quota_remaining is None else "known",
    }


def _json_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unexpected status value")


if __name__ == "__main__":
    raise SystemExit(main())
