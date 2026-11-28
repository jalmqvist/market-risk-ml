# MRML

MRML is a minimal, independent trading, risk and operational platform,
intended to become the execution layer of the BSVE → MSML → MPML → MRML
research stack. **The current priority is an end-to-end IG Markets demo
platform that works without MPML or MSML.** First prove one explicit,
operator-authorized demo opening, independent readback, and separately
authorized closing. Then complete independent risk controls, the minimal
terminal workflow and, later, recommendations and forward testing.

**Status (2026-11-28):** bounded IG demo account/market and exposure reads
and PostgreSQL-backed H1 source ingestion have been operationally verified.
B0–B2 provide the tested offline execution-journal, observation, gate,
reconciliation and pre-execution fixture foundation. B2-S4C1–C3A record
credentialed EUR/USD product evidence and offline contract/margin arithmetic;
B2-S4D1 specifies, but does not implement, authenticated operator approval.
Nonempty broker transaction responses, an IG order-writing adapter, trusted
admission and actual opening/closing remain **unverified/unimplemented**.
The first pre-release target is one manual, separately authorized **demo
OPEN and full CLOSE**, with independent confirmation/readback and durable
reconciliation. Full portfolio risk, MPML recommendations and DL integration
are later milestones; minimum broker safety checks remain prerequisites.
See [prototype readiness](docs/audit/MRML_IG_DEMO_PRERELEASE_READINESS_20261128.md).

[Roadmap](docs/ROADMAP.md) · [Current architecture](docs/ARCHITECTURE.md) ·
[B2-S3 account-gate audit](docs/audit/MRML_B2_S3_ACCOUNT_GATE_RECOVERY.md)

## Setup

Requires Python 3.11 or newer and pip.

``` sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Review `.env` without committing or displaying credentials. Set
`MRML_DATABASE_URL` for PostgreSQL-backed ingestion and execution-journal
operations. The application settings still contain historical OANDA
practice-mode and MPML runner configuration; they are not prerequisites
for the standalone IG demo read-only diagnostics. For IG operations use
`MRML_IG_ENVIRONMENT=demo` with protected IG credentials. No live IG
account, broker write or automatic strategy execution is supported.
The historical `DailyRunner` has its own `MRML_PAIR_UNIVERSE`,
`MRML_PAIR_COMPOSITIONS`, risk and fixed-boundary settings; see the
[archived integration record](docs/ROADMAP.md#existing-mpml-integration--implementation-record-2026-11-10)
when working on that separate path.

## Tests and checks

``` sh
make test
ruff check .
ruff format --check .
mypy src
```

Synthetic, non-production parquet and JSON fixtures are committed under
`tests/fixtures/`. Regenerate them with `python tests/make_fixtures.py`.
The default offline suite makes no credentialed broker requests. Isolated
PostgreSQL integration tests exercise Alembic migrations, durable execution
claims, observation replay, gate recovery and concurrency against a real
PostgreSQL instance; they must be run separately as configured by the tests.
Do not treat skipped external MPML or PostgreSQL tests as passing evidence.

## Local Postgres

PostgreSQL 16 is the active durable store for IG market-data ingestion,
execution-journal records, broker observations and account-gate history.
The first IG demo execution has not yet been implemented. Configure
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env`, then
start and inspect the local service:

``` sh
make db-up
docker compose ps
docker compose down
```

The service uses Postgres 16, a named persistent volume, and a readiness
healthcheck. Apply the current Alembic schema (through `0008_successive_claims` as of
2026-11-28) after starting the database:

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

## Historical OANDA/MPML runtime scaffold (not the current IG demo critical path)

The following implementation record explains reusable earlier code and
compatibility evidence. The active platform-first delivery sequence is
in the [roadmap](docs/ROADMAP.md). No MPML recommendation or OANDA
connection is required for B2-S4, B3 or B4.

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

## Historical A1: read-only OANDA practice diagnostic

A1 checks practice authentication, identifies or validates an account, retrieves
its summary and available instrument metadata, and validates a small H1 mid-price
candle sample. It runs independently of PostgreSQL, bundles, the journal, strategies,
and MPML. It only issues GET requests to
`https://api-fxpractice.oanda.com/v3`; redirects are rejected and no order or
position mutation is supported. There is no live-endpoint option.

Create an OANDA **practice** account and generate a personal API access token in
OANDA's account management UI. Install MRML using the setup instructions above.
Provide `MRML_OANDA_TOKEN` through your local environment or secret manager;
do not paste credentials into commands, shell history, logs, or source control.
For example, in Bash, enter the token silently when prompted:

```sh
read -r -s -p 'OANDA practice token: ' MRML_OANDA_TOKEN; echo
export MRML_OANDA_TOKEN
# Optional when exactly one account is accessible; otherwise set the practice ID:
# export MRML_OANDA_ACCOUNT_ID='<your practice account ID>'
export MRML_OANDA_INSTRUMENT=EUR_USD  # optional; this is the default
python -m mrml.data.oanda_probe
unset MRML_OANDA_TOKEN
```

`MRML_OANDA_TOKEN` is required and must contain no whitespace.
`MRML_OANDA_ACCOUNT_ID` must use OANDA's `NNN-NNN-NNNNNNN-NNN` format when set;
an empty value is invalid. Without it, discovery must return exactly one account.
`MRML_OANDA_INSTRUMENT` must name an instrument available to that account.
The probe does not load `.env` or other credential files, or global application
settings; `MRML_DATABASE_URL` is unnecessary. `MRML_ALLOW_LIVE` and application
endpoint settings cannot redirect this command.

Example sanitized output (counts and dates vary):

```text
PASS OANDA practice (read-only): account validated; currency=USD; instruments=68
EUR_USD H1 mid: candles=48 complete=48 incomplete=0; range=2026-11-13T12:00:00+00:00..2026-11-17T11:00:00+00:00
```

The window covers the 96 UTC hours before the current UTC hour, with at most 96
candles and no pagination. Market closures can reduce the count. Empty, malformed,
unsorted, duplicated, out-of-window, or invalid OHLCV data fails validation.
Incomplete candles are counted separately. Timestamps identify candle opens.
The command returns 0 on PASS and 1 on FAIL. Output omits account IDs, tokens,
balances, and raw responses; transport debug logs are suppressed during requests.

- HTTP 401/403: check the practice token's validity and account access.
- Account discovery failure: explicitly configure the intended practice account ID.
- HTTP 404: verify the configured account and its practice environment.
- Unavailable instrument: select an instrument offered by that account.
- HTTP 429/5xx or timeouts: bounded retries were exhausted; check connectivity or
  service availability and retry later. Redirects and other HTTP errors fail directly.
- Malformed response: the returned data did not satisfy the expected v20 contract.

Offline tests verify the workflow with fake sessions and never call OANDA.
**A real credentialed practice smoke test remains a separate manual acceptance
step.** A1 does not establish execution readiness or change daily aggregation.
A2 should address canonical bar contracts, price components, provenance, and
availability before broader market-data ingestion.

## Current IG demo read-only and persistence checkpoints

- `python -m mrml.data.ig_probe`: bounded demo account, market and H1
  bid/ask diagnostics (historical-price allowance applies).
- `python -m mrml.data.ig_broker_probe`: bounded, read-only broker-state
  diagnostics and scoped confirmation/individual-position lookups; its
  `execution-evidence` subcommand has been run credentialed against the demo
  account (Nov 27 and 28). The Nov 28 EUR/USD capture returned a native pip
  label and `EDITS_ONLY` market status. Nonempty confirmation and position
  payloads remain unverified against IG.
- Bounded A3-S3 PostgreSQL IG source ingestion and readback passed in the
  [acceptance report](docs/audit/IG_A3_S3_ACCEPTANCE_REPORT.md).
- B2-S1/S2/S3 broker ledger, replay and account-gate tests are offline
  evidence only; they do not enable writing orders.

No credentialed probe should be run unnecessarily. Before the first demo
mutation, the B2-S4 safety review must establish complete current exposure,
dealing rules, risk/margin and operator authorization; B3 and B4 need
separate explicit approvals.

## A1-IG demo connectivity diagnostic

The standalone probe signs in to IG Markets demo, lists accessible accounts,
validates an EUR/USD market, and checks up to 32 hourly bid and ask OHLC
observations. It uses the IG demo gateway and requires no database or strategy
runtime.

For a local smoke test, install MRML using the setup commands above. If using
the Git-ignored `.env` copied from `.env.example`, fill in the three IG demo
credentials. The template already sets `MRML_IG_ENVIRONMENT=demo`. These values
can also be exported in the shell, where they take precedence over `.env`.

| Variable | Value |
| --- | --- |
| `MRML_IG_ENVIRONMENT` | Required, set to `demo` |
| `MRML_IG_API_KEY` | Required, IG demo API key |
| `MRML_IG_USERNAME` | Required, IG demo username |
| `MRML_IG_PASSWORD` | Required, IG demo password |
| `MRML_IG_EPIC` | Optional EUR/USD EPIC accessible to the demo account |

Run from the repository root:

```sh
python -m mrml.data.ig_probe
```

Expect `Result: PASS` (exit 0) after authentication, accounts, market selection
and historical validation each succeed. On `Result: FAIL` (exit 1), check the
reported stage. If search is ambiguous or exceeds one page, set the EUR/USD EPIC
for the intended instrument and rerun. An expired session requires rerunning
the probe.

The probe uses session V2, accounts V1, market search and details V2, and
historical prices V3. Historical requests consume IG data-point allowance.
Avoid polling the probe and use the reported remaining points and reset interval
when available. A closed market can still have usable history. The probe reports
hourly gaps and leaves observation completeness unknown. It preserves bid and
ask separately, without defining canonical MRML bars.
