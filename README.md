# MRML

MRML is the execution and risk layer in the BSVE → MSML → MPML → MRML
research pipeline. The v0 scaffold provides a practice-only OANDA
market-data client, H1-to-D1 aggregation, a strategy-runner scaffold,
pure risk gates, daily orchestration, and an operational journal. Its
OHLCV-only PhaseAware decision boundary is bound to the MPML causal
runtime (`mpml.runtime.phaseaware.latest_decision`) at
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`; `DailyRunner` is not wired
to it by default. The runtime is a forward-test harness; it has no live-capital
support and no live state resolver.

## Setup

Requires Python 3.11 or newer and pip.

``` sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Review `.env` and replace development credentials before starting
services; set `MRML_DATABASE_URL` to the configured Postgres username,
password, and database. The `MRML_DATABASE_URL` setting is required when
calling `load_settings()`. Settings use the `MRML_` prefix; environment
defaults to `dev`. `live` remains guarded by the explicit
`MRML_ALLOW_LIVE=true` override, but the daily runtime blocks `live`
mode regardless. Account mode and OANDA API endpoint must be `practice`.
Set `MRML_PAIR_UNIVERSE` and provide exactly one
`MRML_PAIR_COMPOSITIONS` entry per pair before running the baseline.
Risk limits, evidence policy, and the UTC+1 default bar boundary are
also configurable.

## Tests and checks

``` sh
make test
ruff check .
ruff format --check .
mypy src
```

Synthetic, non-production parquet and JSON fixtures are committed under
`tests/fixtures/`. Regenerate them with `python tests/make_fixtures.py`.
Tests need no network or database access.

## Local Postgres

Postgres is only for local development; no application code connects to
it in this release. Configure `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` in `.env`, then start and inspect the service:

``` sh
make db-up
docker compose ps
docker compose down
```

The service uses Postgres 16, a named persistent volume, and a readiness
healthcheck. Apply the operational journal schema after starting the
database:

``` sh
alembic upgrade head
```

`JournalRepository.create_schema()` exists for isolated tests and local
experiments; deployed schemas should use Alembic.
`docker compose down -v` also deletes the local database volume.

## Recommendation bundle

`mrml.contracts.load_bundle(directory)` validates
`strategy_evaluations.parquet`, `recommendations.parquet`,
`recommendations_per_pair.parquet`, and `experiment_manifest.json`. It
raises typed contract errors for missing artifacts or columns, invalid
dtypes, duplicate IDs, invalid ranks, broken evaluation references,
unsupported recommendation metadata schema versions, count mismatches,
and invalid required evidence dates.

## Runtime baseline

`mrml.data.OandaClient` always uses OANDA's practice URL, requests H1
mid candles, sets a timeout, and retries transient failures. Pass the
practice token through a secret manager/environment at construction
time; never commit it. `build_d1_bars()` uses fixed UTC+1 boundaries by
default and rejects incomplete days/candles. `compare_bars()` returns
deterministic diffs for overlapping timestamps.

`StrategyRunner` invokes an injected PhaseAware strategy protocol and
validates its bundle evidence. Candidate ranks are retained for
provenance but are not entry signals. `DailyRunner` is not wired to an
MPML-backed strategy by default. The separate
`PhaseAwareDecisionAdapter` validates explicitly attested completed-bar
OHLCV-only history, cutoff, and typed MPML result provenance. MRML supplies
no phase, indicator, or other prepared field.
`MpmlPhaseAwareRuntime` binds that boundary to
`mpml.runtime.phaseaware.latest_decision` at MPML
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`; it reads the checkout's git
HEAD (never `MPML_REVISION`) and requires a clean tree.
`MpmlPhaseAwareStrategy` is the `StrategyRunner` seam: sign is direction,
`abs(signal)` is recorded as `size_multiplier`, and `sl_pct`/`tp_pct` and
the signal-bar timestamp are carried through unchanged. Nothing is applied
to risk sizing. A non-flat TF4 decision has `sl_pct = 0` (no fixed stop);
MRML raises `UnexecutableDecision` rather than inventing one.
Real-MPML tests run only with `MPML_ROOT` set to a checkout at the pinned
revision (`MPML_ROOT=... pytest -m parity -rs`); otherwise they skip, and a
skip is not parity evidence. No selector or live state resolver is part of
this runtime.

`DailyRunner` requires reconciliation, complete current H1 data, a fresh
historical D1 series (normally seeded from MPML CSV history), a current
evidence bundle, strategy decisions, and risk decisions before any
execution handoff. The strategy receives the validated historical series
plus the newly built D1 bar; stale, empty, unsorted, duplicated, or
malformed history halts the run. Risk sizing converts quote-currency
stop distance into account-currency units; missing conversion rates
block non-account-currency quote pairs. The default mode is `dry_run`;
`demo` requires an injected practice-account executor. No live order
adapter is provided. Runs, config snapshots, signals, risk decisions,
and order/fill placeholders are persisted with SQLAlchemy. Execution
errors and missing or inconsistent inputs halt the run.

The frozen EURUSD historical replay fixture is hash-pinned and can be
checked against the pinned MPML checkout with the opt-in parity test.
This establishes historical replay reproducibility, not per-decision
runtime parity. Current-decision and prefix evidence is separate: the
OHLCV-only boundary is bound to the MPML runtime at
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57` (the replay fixture was
generated at `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`), and a dedicated
`mpml-runtime` CI job clones that revision, verifies its git HEAD and
clean tracked tree, and runs the real-MPML tests. Prefix parity on
synthetic bars matched MPML's canonical detector and strategy at 69
cutoffs; the frozen-fixture prefix comparison matched 10 of 32 prefixes
(22 phase-only mismatches, a strict `xfail`). PR3 separately validates H1-to-D1 price-bar construction. See [the PR2
implementation brief in the
roadmap](docs/ROADMAP.md#integration-pr2--establish-and-bind-the-mpml-strategy-runtime-interface).

Recommendation metadata currently supports schema version `1.0`. The
loader exposes the bundle content hash, manifest provenance, per-pair
candidates, and evidence timing. Evidence dates are optional by default;
a bundle with no evidence end date is not trade-eligible. Callers can
configure the evidence field name and maximum evidence age through the
loader arguments.

Assumptions: required `MRML_DATABASE_URL` has no default; bundle schema
version `1.0` is the initial supported version; an ambiguous
`evaluation_for(pair, strategy_id)` result raises a validation error
instead of selecting an arbitrary evaluation. Pair and strategy
identifier semantics are documented in
[ADR-001](docs/adr/ADRs.md#adr-001-mpml-artifact-semantics) (resolved
2026-10-08). The amendment's state provenance behavior follows
[ADR-006](docs/adr/ADRs.md#adr-006-state-resolution-and-recommendation-semantics):
state is not a lookup key in v0. A working broker order adapter remains
outstanding, `DailyRunner` is not wired to the MPML-backed strategy by
default, and no stop-loss policy has been approved for TF4 signals with
`sl_pct = 0` (MRML refuses them via `UnexecutableDecision`). The frozen historical
replay fixture exists, but does not replace runtime-prefix parity or the
separate H1-to-D1 market-bar parity work.

## Documentation

-   [Architecture](docs/ARCHITECTURE.md)
-   [Roadmap](docs/ROADMAP.md)
-   [ADRs](docs/adr/ADRs.md)
-   [Changelog](docs/CHANGELOG.md)
