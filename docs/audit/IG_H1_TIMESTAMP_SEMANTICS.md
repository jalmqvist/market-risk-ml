# IG demo H1 timestamp and source semantics

A3-S0 collects evidence for the exact EUR/USD EPIC configured by the operator.
The H1 timestamp policy, forming-candle behavior, and revision behavior remain
unresolved. The automated audit tests use synthetic data. This audit has yet
to record a credentialed IG demo observation.

## What the REST reference establishes

- [Prices v3](https://labs.ig.com/reference/prices-epic.html) calls
  `snapshotTimeUTC` “Snapshot time” and describes bid and ask OHLC. It lists
  `HOUR` and `MINUTE` resolutions, a point cap for requests without a date
  range, and optional historical allowance metadata. It specifies neither
  resolution's timestamp boundary, the inclusion or exclusion of forming
  candles, nor a completion flag or immutability guarantee.
- [Market details v2](https://labs.ig.com/reference/markets-epic.html) returns
  the instrument EPIC, name, category, expiry and sizing fields, a snapshot
  market status, and instrument opening/closing times when provided. The
  probe validates the returned EPIC, a EUR/USD name and `CURRENCIES`.
  Check the name for explicit weekend or mini qualifiers and reconcile
  contract and lot sizes with an IG product specification for that EPIC.
  Expiry is published verbatim. Interpret any dash or missing value using
  product-specific documentation. Standard, mini and weekend classifications
  remain unverified without supporting evidence.
- Opening hours are reproduced as published. The reference describes times
  as account-relative unless otherwise specified. Resolve the time zone,
  weekdays, daylight saving, holidays and exceptions for the exact product
  before deriving a UTC calendar. Snapshot status describes a moment and
  leaves historical session coverage unknown.

## Protocol

Use the A1 environment variables or the working directory's local `.env`.
Set `MRML_IG_ENVIRONMENT=demo`, `MRML_IG_API_KEY`, `MRML_IG_USERNAME`,
`MRML_IG_PASSWORD`, and the exact `MRML_IG_EPIC`. Exported variables take
precedence. Run once during a known active session:

```sh
python -m mrml.data.ig_semantics_probe
```

One run logs in to the demo gateway, reads market details once logically,
then requests one page each of up to three recent H1 and 24 recent M1
points. Historical requests use no date range, retries, or additional
pages. The reused client may retry a transient market-details GET once.
The request budget is 27 historical points. An M1 request failure keeps the
H1 report and produces a nonzero exit status. Empty samples leave the
affected comparisons inconclusive.

To preserve the source labels and bid/ask OHLC for manual comparison, opt
into JSON and redirect stdout to a path outside the checkout:

```sh
python -m mrml.data.ig_semantics_probe --json > /path/outside/repo/ig-first.json
# Run once more at a deliberately chosen later observation time.
python -m mrml.data.ig_semantics_probe --previous /path/outside/repo/ig-first.json
```

The JSON structure uses sorted keys and contains the same evidence for the
same inputs and retrieval times. It includes only selected market fields,
historical prices, allowance counts, UTC observation times and derived
comparisons. Synthetic evidence and reviewed findings with no credentials,
account data, session tokens, raw responses, or restricted market data are
suitable to commit. Keep raw price captures local unless authorized to
redistribute them under the applicable IG terms.

## Interpreting observations

1. Compare each raw H1 label under both start and end hypotheses. Compare
   the M1 points under both minute-label hypotheses as well. The report
   counts candidate constituent minutes whose hypothetical end preceded or
   equaled the H1 request time, checks whether their bid/ask extrema fit
   inside the H1 extrema, and compares open or close when a constituent
   touches the corresponding edge. An edge match supports the hypotheses
   together and may occur by chance. A mismatch can reflect different
   aggregation inputs or revisions between the two requests. Gaps,
   unchanged prices, missing edges and non-overlapping samples weaken
   discrimination. The cap deliberately precludes a full 60-minute
   reconstruction in one run.
2. During an active session, capture near an hour edge to observe overlapping
   H1/M1 windows. An H1 label whose hypothesized hour spans the entire
   request/retrieval time is a forming-candle candidate under that hypothesis.
   A `TRADEABLE` status alone leaves presence unknown. Closed-market or
   stale observations cannot answer the current-candle question.
3. Compare separately initiated runs with overlapping H1 labels. Capture
   during an active hour and shortly after its boundary to look for changes
   to the same label. To investigate later revisions, compare runs after
   the candidate hour has elapsed. Changed bid/ask OHLC on the same label
   establishes a change between observations. An unchanged or missing
   overlap gives no guarantee of immutability. Elapsed status depends on the
   proposed boundary; broker-confirmed completion and first availability
   require independent evidence.

Before canonical storage, corroborate the H1 boundary with repeated,
discriminating observations and independent timestamp evidence, such as
explicit provider confirmation or a reference with verified interval
boundaries. Any proposed named policy should scope its evidence to the
specific demo product, resolution and REST version. A3 also needs verified
product identity, session evidence for classifying gaps, and explicit
treatment of provisional or revised observations, unknown finality and
retrieval provenance. This probe creates no canonical intervals or policy.
