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

## Persistence keys and A3 prerequisites

- Identify a source product by provider, source instrument ID, and variant.
  Version the mapping from that product to the canonical FX instrument.
- Identify a logical hourly interval by source product, resolution, and UTC
  start/end. Use the mapping version and transformation and timestamp policy
  versions when selecting or rebuilding a canonical representation.
- Identify the source observation stream by source product, source resolution,
  timestamp field and normalized source label. Preserve original timestamp text,
  side prices, retrieval time and any evidence on the individual revision.
  A3 needs a durable retrieval identifier or equivalent idempotency key because
  repeated revisions can share a source label and may even share a clock
  timestamp. A later revision can change prices and completion evidence.
- A3 needs verified IG boundary semantics, product-specific session evidence
  where available, and explicit rules for rechecking provisional bars. It
  must implement transactional PostgreSQL storage, idempotent retries and
  revision selection, bounded quota-aware retrieval, coverage checks,
  checkpoints and diagnostics.
