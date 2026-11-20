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

`StoredHistory` reports whether interpretations exist and carries the
independent `coverage_verified` flag, which is false in A3-S1.
`require_observations()` raises `InsufficientHistoryError` for an empty result.
Even populated history has unverified window coverage. Gaps and empty windows
carry no market-session classification.

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

### A3-S2 prerequisites

A3-S2 still needs product-specific mapping and timestamp-policy approval,
quote-unit and session evidence where applicable, a durable retrieval ID
lifecycle, bounded request windows, quota-aware retries, provisional-bar
rechecks, recovery checkpoints and explicit window coverage. The storage
repository performs database operations only.
