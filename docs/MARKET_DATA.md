# Canonical H1 market data

## Instrument and price identity

`FXInstrument` identifies an ordered pair such as `EUR_USD` from MRML's
supported currency codes. Pairs are composed from the code set. An explicit,
versioned `InstrumentMapping` associates the pair with a `SourceProduct` whose
identity is provider, source instrument identifier, and variant. IG mappings use
the exact EPIC and a supplied variant such as standard, mini, weekend, or
forward. Mapping validation rejects missing EPICs, invalid syntax, other
providers, and competing assignments for an EPIC in the active set. It verifies
the declared mapping structurally. Product selection and verification of the
underlying pair depend on the caller's instrument evidence.

A `SourceBar` contains its mapping, H1 resolution, UTC start and exclusive end,
bid and ask OHLC, optional tick volume, source timestamp evidence, UTC retrieval
time, completion and availability metadata, and a transformation version.
Prices retain the numeric values parsed by the A1 client with no additional
conversion or quantization. IG volume is unknown. Each side has its own
high/low range. Corresponding bid levels must be less than or equal to ask
levels, consistent with the A1 source validation. Independently observed
extrema can occur at different times.

`componentwise_midpoint()` explicitly derives a labeled OHLC series from the
bid and ask components. Its averaged extrema need not equal extrema of an
observed midpoint path.

## Time, finality, and availability

The [IG historical prices REST v3 reference](https://labs.ig.com/reference/prices-epic.html)
describes `snapshotTimeUTC` as “Snapshot time.” Neither that description nor
the A1 sample establishes which H1 boundary it labels. The adapter requires a
named policy with an explicit start or end meaning and an evidence reference.
There is no default IG boundary interpretation. The adapter retains the source
field name, parsed instant, original field text when supplied by the client, and
the policy. A1 recognizes unqualified timestamps in that specific UTC field as
UTC. Already-parsed adapter inputs can carry explicit offsets and are
normalized to UTC before interval arithmetic. Naive parsed inputs are invalid.

Completion has three states, `COMPLETE`, `INCOMPLETE`, and `UNKNOWN`. A
definite state requires an evidence reference. A complete candle must have
been retrieved at or after its end. An elapsed interval or successful REST
response alone leaves the state unknown. First availability means independently
established initial availability of the interval and can precede its end if a
provisional candle was published. It must fall between interval start and
retrieval and requires its own evidence reference. IG observations default to
unknown completion and unknown first availability. A retrieval records when
MRML observed the particular values.

## History and eligibility

`diagnose_history` reports order, duplicates, overlaps and mixed source
series separately from interior calendar gaps. A session lookup can classify
a gap only with evidence for the exact product and entire missing UTC range.
Mixed or inadequately documented ranges remain unknown. A weekend contributes
no evidence by itself. Gap classification and candle finality are independent.

`evaluate_history` uses the caller's selected validated mapping and UTC
evaluation instant. It reports unknown metadata even when an explicit
consumer policy permits it. Incomplete candles, structural sequence defects
and confirmed missing trading intervals make a history ineligible. The
policy controls minimum sample size, strictness for unknown completion,
availability and session coverage, and maximum age measured from the latest
interval end. A revision retrieved after evaluation is ineligible even if
the interval had already been published. These checks cover the supplied
series and its interior gaps. Coverage of a requested window, including its
edges, is a provider responsibility.

`MarketDataProvider.read_h1` accepts a canonical FX instrument and UTC
half-open range. Implementations return one ordered source series and raise
`InsufficientHistoryError` when coverage cannot be established. The A1
client retrieves a bounded recent sample. Arbitrary window access is an A3
responsibility. Legacy `H1Bar` is a single OHLC stream with default volume
and completion values. Legacy `build_d1_bars` can require 24 consecutive hours
and defaults to a fixed UTC+1 boundary. Canonical H1 carries observed quote
sides and evidence directly and has no implicit D1 or strategy conversion.

## PostgreSQL persistence

A3-S1 uses the existing database engine and Alembic configuration. The
`MarketDataRepository` accepts that engine. Its metadata is separate from
the operational journal metadata, and Alembic tracks both.

| Table | Contents and identity |
| --- | --- |
| `market_data_products` | Unique provider, exact EPIC and variant. Standard, mini, weekend and forward products have independent identities. |
| `market_data_mappings` | Immutable mapping from a product and mapping version to an explicitly supplied canonical FX instrument, with an evidence or approval reference. |
| `market_data_retrievals` | One caller-identified IG response with its product, source resolution, timestamp field, retrieval time, ordered response fingerprint and returned allowance counters if present. |
| `market_data_observations` | One revision per retrieval ID and zero-based response ordinal. Stores the original timestamp text, parsed UTC label, exact bid/ask OHLC and a content fingerprint independent of the retrieval ID. |
| `market_data_timestamp_policies` | Named policy scoped to a registered mapping version. Stores the boundary meaning, evidence and explicit approval reference. |
| `market_data_h1_interpretations` | Links a revision to a policy and transformation version. Stores the UTC interval, interpretation time and completion and availability evidence. The link to the immutable revision supplies the unchanged bid/ask values. |

For each successful bounded response, the caller assigns a durable, globally
unique retrieval ID such as a UUID and records the UTC time the response was
received. Replay the same ID, timestamp, ordered observations and allowance
metadata on a persistence retry. A separate broker request uses a fresh ID
even if the response's labels and prices match. The observation fingerprint
covers the original timestamp text, UTC label and decimal price strings. The
retrieval fingerprint covers those fingerprints in response order. Fingerprints
check replay content. The database's unique keys and conflict-targeted inserts
arbitrate concurrent writes. A conflicting reuse of an ID, mapping, policy or
interpretation raises `PersistenceConflict`. A failed batch rolls back source
and canonical changes together. Stored allowance metadata consists only of the
returned remaining, total and reset counters.

Source OHLC prices use PostgreSQL `NUMERIC` without a precision or scale
modifier. The writer converts the existing IG object's numeric values using
`Decimal(str(value))`. This preserves integers and the shortest decimal spelling
that round-trips each previously parsed float. The repository receives parsed
objects, so the wire number spelling and digits already lost in JSON float
parsing are outside this contract. Source and canonical revision queries expose
the exact stored decimals through `SourceRevision`. The reconstructed A2
`SourceBar` uses its existing Python int/float OHLC contract. SQLite stores the
decimal strings as text to avoid binary floating-point conversion in its numeric
affinity rules. Price values retain their source quotation units.

PostgreSQL stores parsed, retrieval, interpretation, interval, and availability
instants as `timestamptz`. PostgreSQL displays these in the session time zone,
and the repository normalizes them to UTC when reading. SQLite drops time zone
information, so the storage type validates UTC on write and restores UTC on
read. Original IG timestamp text is stored separately with the source
`snapshotTimeUTC` field name.

### Interpretation and historical reads

Register the exact product mapping with its provenance before using an
`ApprovedTimestampPolicy` for that mapping. Source-only writes can happen
before mapping or policy approval. Replay the identical response with its
retrieval ID to attach an approved interpretation later. Distinct policy names
or transformation versions preserve distinct interpretations. The A2 adapter
supplies the implemented H1 transformation. Both start and end policies are
supported with explicit approval. The A3-S0 audit documents evidence scoped
to the audited demo product and has no effect on runtime policy selection.

IG source volume stays unknown. Completion and first availability are unknown
unless the caller supplies supporting evidence through the A2 contract.
A caller can specify when that interpretation and its evidence became known.
Otherwise the repository records the current UTC time for a new interpretation
batch and preserves that time on retries. Supply an earlier interpretation time
only when records substantiate it.

`source_revisions` returns every revision for the product and resolution with a
source label in the requested half-open window. `canonical_history` selects one
stored interpretation per interval start within a half-open window, scoped to
the exact mapping, policy name, and transformation version. The latest
eligible retrieval wins. Equal retrieval timestamps are ordered by greatest
retrieval ID lexicographically, then greatest ordinal. All OHLC components
come from the selected revision. If supplied, the as-of cutoff is inclusive
for retrieval time and also for interpretation time in canonical queries.
Source-only data remains visible in the source query. Without an as-of cutoff,
queries use the currently stored records.

`StoredHistory` reports whether interpretations exist. Its independent
`coverage_verified` flag stays false for these repository reads. The ingestion
journal carries separate, scoped gap evidence. `require_observations()` raises
`InsufficientHistoryError` for an empty result.

### Migration and tests

Use the existing PostgreSQL 16 Compose service and a configured
`MRML_DATABASE_URL`. Apply the additive migration using `alembic upgrade head`.
Revision `0002_market_data` owns only the market-data tables. Downgrading to
`0001_operational_journal` drops those tables and their stored evidence.

The default `python -m pytest -q` suite exercises offline SQLite behavior.
Run the isolated PostgreSQL tests with the following command, using a local
PostgreSQL URL with permission to create a test schema.

```sh
MRML_TEST_POSTGRES_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/DATABASE' \
  python -m pytest -q tests/test_market_data_storage.py -k postgres
```

The tests create and drop a unique schema per test and exercise Alembic upgrade
and downgrade, exact numeric values, a non-UTC SQL session, uniqueness,
concurrent retries and history reconstruction.

## Bounded IG demo H1 ingestion

A3-S2 starts with a single configured demo account and explicitly selected
product. It uses the A3-S1 source store and the additive
`0003_ig_ingestion` attempt journal. A plan reads existing source observations
and historical request windows before choosing the oldest unattempted label
and its contiguous run. An empty response leaves durable evidence of the
attempt and any unidentified gaps. The next incremental plan skips those
attempted labels. An explicit replacement is available for a failed or
uncertain attempt within the catch-up horizon.

### Request bounds and historical allowance

IG REST v3 `GET /prices/{epic}` documents `from` and `to` in
`yyyy-MM-dd'T'HH:mm:ss` form. MRML takes UTC instants, sends those UTC clock
values without a zone suffix, and checks the returned `snapshotTimeUTC` labels
against both sent bounds. The reference specifies the format. The demo
interpretation of the wire bounds remains an A3-S3 verification item. Requests
use `HOUR`, `pageNumber=1` and an explicit `pageSize`. The documented `max`
parameter does not apply to date ranges. MRML budgets for a label on the
terminal edge when it sends an hour-aligned `to`. It preserves such rows as
source evidence and excludes that extra label from the intended half-open
label window. When the next label has already been observed or attempted, the
planner ends one second before it. A recent request ends no later than the
invocation's current UTC second. Returned labels must be aligned, distinct
and strictly ordered. An unexpected page, inconsistent metadata, out-of-range
label or oversized response rejects the entire retrieval. The client caps the
decoded body at 256 KiB. It follows zero additional pages and performs zero
implicit historical retries. [IG historical prices reference](https://labs.ig.com/reference/prices-epic.html)

| Limit | Default | Maximum |
| --- | --- | --- |
| Candidate range on a normal run | Current label and three earlier labels | Set an explicit UTC `--start` within the catch-up horizon |
| Target H1 labels per request | 6 | 24 |
| Historical points per invocation, including any terminal edge | 7 | 25 |
| Historical requests and response pages per invocation | 1 each | 1 each |
| Catch-up horizon, including the current source label | 48 H1 labels | 168 H1 labels |
| Provisional rechecks per invocation | 1 when explicitly selected | 1 |
| Minimum historical points to keep in reserve | 20 | Configurable |

An actual request authenticates once using IG demo credentials. Historical
points and the reserve apply to prices. Authentication and IG's general API
throttles have separate semantics. A returned historical allowance is
preserved with the retrieval. While a report is current, the planner debits the
worst-case budget of subsequent local attempts, including uncertain attempts
and responses without counters, before checking the reserve. Without a
current report status shows unknown visibility and applies local caps.
This is a local estimate for a single demo account per database. Other
clients can consume the same allowance. Supported `Retry-After` values
enforce a hold across invocations. A general throttle without a usable delay
has a one-minute local cooldown. Historical exhaustion with no usable reset
evidence requires explicit `--quota-reset-confirmed` after the operator
verifies the reset. The flag leaves an active retry delay and known reserve
in force.

### Attempts, transactions and restart

A job identifies the intended label window. Each broker attempt has its own
UUID, with a maximum of two attempts per job. The committed A3-S1 retrieval
uses the UUID of the attempt that received that response. A fresh API call
always gets a fresh UUID, even when prices are identical. PostgreSQL serializes
ingestion workers in a database using an advisory lock across the short
transactions and the broker call. The ingestion table belongs to the
market-data schema alongside source tables. The operational journal is
independent.

1. Commit the attempt and exact sent bounds before calling IG. An interrupted
   `requesting` row means in flight or uncertain. A network request can
   consume quota before the database receives any recoverable response.
2. Stage the validated, ordered observations, counters and receipt time in a
   short transaction. Staging stores parsed response data and strips HTTP
   headers, session information and unrelated payload fields.
3. In one transaction, persist the A3-S1 retrieval, observations and any
   approved interpretations, record the ingestion outcome and clear the
   staged payload. An error rolls back the entire commit and leaves the
   immutable staged response available for recovery.
4. On restart, `recover` replays the durable response without authentication
   or an IG call. `ingest` also prioritizes a staged response. To replace an
   uncertain or failed request explicitly, use its `job_id` with
   `--retry-job`. Replacement checks committed observations first and narrows
   a partially satisfied job to a remaining run. The original attempt remains
   auditable. The workflow provides idempotent persistence. External API
   execution can occur more than once across attempts.

### Coverage, policy approval and provisional rechecks

Status distinguishes the last attempted range, last successful commit, last
known failure, latest source label, gap evidence and the latest outstanding
provisional label. Reports list gaps, recheck candidates and outstanding
attempts within the configured horizon. The last success, failure and source
label can predate the horizon. An observed row or successful HTTP response
establishes observation evidence. Canonical window coverage requires
additional evidence. A missing label without evidence stays `UNKNOWN`,
including at a weekend. Programmatic callers can supply a versioned
`SessionPolicy` for the exact product and UTC interval, using A2's
`SessionEvidence` and `GapKind`. A confirmed trading gap breaks coverage.
An evidenced elapsed expected closure can contribute to a contiguous
verified prefix scoped to the timestamp and session policies. The CLI
installs no session policy, so it leaves the verified checkpoint unset.
The storage read contract and as-of cutoffs remain unchanged.

Ingestion defaults to source-only capture. To enable canonical interpretation,
register an `InstrumentMapping` with provenance using
`MarketDataRepository.register_mapping`, then register a named
`ApprovedTimestampPolicy` with `register_timestamp_policy`. Select both at
invocation. The product variant must be supplied and verified independently.
The A3-S0 start-label audit applies to the documented demo EUR/USD EPIC
`CS.D.EURUSD.CEE.IP` and its audited product. Other products require their
own evidence. Source-only runs cannot make authoritative interval or
provisional claims. A recovered interpretation receives its actual new
interpretation time while the retrieval retains its original receipt time.

An approved policy can identify an observation received before its interval
ended as a provisional candidate. `--recheck` requests one eligible label
after the expected end, records changed, unchanged or missing prices, and
preserves both retrieval revisions. The same label and policy are not
automatically requested again. Even after an elapsed interval and a recheck,
completion stays `UNKNOWN` in the absence of broker evidence.

### CLI and PostgreSQL verification

Set `MRML_DATABASE_URL` for the PostgreSQL 16 Compose database, then apply
`alembic upgrade head`. Substitute the exact verified source product for
`EPIC` and `VARIANT`.

```sh
python -m mrml.data.ig_ingest plan --epic EPIC --variant VARIANT
python -m mrml.data.ig_ingest status --epic EPIC --variant VARIANT
python -m mrml.data.ig_ingest recover --epic EPIC --variant VARIANT
python -m mrml.data.ig_ingest ingest --epic EPIC --variant VARIANT
python -m mrml.data.ig_ingest ingest --epic EPIC --variant VARIANT \
  --mapping-version MAPPING --timestamp-policy POLICY --recheck
```

`plan` is an offline dry run against the database. `status` reads diagnostics
and `recover` commits only an already staged response. These commands do not
load credentials or call IG. `ingest` requires PostgreSQL and explicit
`MRML_IG_ENVIRONMENT=demo`, `MRML_IG_API_KEY`, `MRML_IG_USERNAME` and
`MRML_IG_PASSWORD`. If set, `MRML_IG_EPIC` must match the CLI EPIC. To plan
catch-up, use `--start` with a UTC ISO 8601 hour including `Z` or `+00:00`.
The CLI outputs bounded request metadata and redacted diagnostics.

```sh
python -m pytest -q tests/test_ig_h1_range.py tests/test_ig_ingestion.py
MRML_TEST_POSTGRES_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/DATABASE' \
  python -m pytest -q tests/test_ig_ingestion.py tests/test_market_data_storage.py -k postgres
```

The isolated PostgreSQL tests use temporary schemas and a non-UTC SQL session.
Downgrading to `0002_market_data` removes the A3-S2 journal and staged data
while retaining A3-S1 committed observations. Verify credentialed range
behavior and quota consumption in A3-S3 with explicitly bounded demo runs.

## Future data-source extensions

### IG retail client sentiment (deferred)

IG documents REST v1 `GET /client-sentiment`. The optional comma-separated
`marketIds` selects markets. Responses contain `marketId`,
`longPositionPercentage` and `shortPositionPercentage`. A future extension
could periodically store broker-specific snapshots in separate PostgreSQL
tables with retrieval time, the exact IG market identifier, raw percentages
and source/schema provenance. Verify how each `marketId` maps to a price EPIC
and MRML FX instrument before joining. The endpoint documentation gives no
historical observation timestamp, historical query capability or source
finality semantics. [IG client sentiment reference](https://labs.ig.com/reference/client-sentiment.html)

Broker population, coverage, calculation and update cadence may differ from
MSML's scraped source. Potential uses include cross-broker comparisons,
independent behavioral data collection, MSML research enrichment and later
MPML recommendation inputs, each subject to separate validation. This
research opportunity is deferred beyond A3-S2 and Milestone A acceptance.
