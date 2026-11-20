# IG demo H1 timestamp and source semantics

**Study:** MRML A3-S0  
**Audit date:** 2026-11-20  
**Status:** Credentialed demo observations completed; H1 start-label interpretation strongly supported empirically. Broker-confirmed finality, historical immutability, first availability, and full product/session semantics remain unresolved.

## Purpose and evidence boundary

A3-S0 investigates the source semantics of IG Markets REST v3 historical prices before PostgreSQL-backed incremental ingestion. Its subject is the configured **IG demo EUR/USD EPIC `CS.D.EURUSD.CEE.IP`**, not IG instruments generally.

This document distinguishes:

- **Documented:** statements explicitly supported by IG REST references.
- **Observed:** measurements returned by the credentialed demo probe.
- **Inferred:** interpretations supported by those measurements, but not independently guaranteed by IG.
- **Unresolved:** properties for which the available evidence is insufficient.

The audit preserves the conservative A2 contract. An elapsed H1 interval is **not** equivalent to broker-confirmed candle completion. Retrieval time is **not** first-availability time. No production default timestamp policy or canonical ingestion rule is established merely by this document.

## What the REST reference establishes

- [Prices v3](https://labs.ig.com/reference/prices-epic.html) labels `snapshotTimeUTC` as “Snapshot time” and provides bid and ask OHLC at `HOUR` and `MINUTE` resolutions. The reference does not explicitly specify the interval boundary represented by either timestamp, a completion flag, first publication time, or an immutability guarantee.
- [Market details v2](https://labs.ig.com/reference/markets-epic.html) provides EPIC, instrument name/type, expiry and sizing fields, snapshot market status, and opening/closing times where supplied. Market status at retrieval does not establish historical session coverage.
- Any published opening hours must be interpreted with the exact product, relevant time zone, weekdays, daylight-saving transitions, holidays and exceptions. An empty opening-hours response is not evidence of continuous trading.

## Credentialed demo observations — 2026-11-20

The operator ran `python -m mrml.data.ig_semantics_probe` during an active EUR/USD demo session, then saved a JSON capture and manually compared it against a subsequent run using `--previous`. The original evidence files remain local rather than committed as raw price datasets.

All times below are **UTC**. H1 labels are reproduced from the source field; proposed intervals are explicitly hypothetical until a timestamp policy is approved.

### Source product and request metadata

| Field | Observed value |
|---|---|
| Environment | IG demo |
| EPIC | `CS.D.EURUSD.CEE.IP` |
| Instrument name / type | `EUR/USD ` / `CURRENCIES` |
| Contract size / lot size | `100000` / `10.0` (verbatim metadata; economic units not yet independently verified) |
| Expiry | `-` (not independently interpreted) |
| Snapshot market status | `TRADEABLE` at each capture |
| `openingHours` returned | Empty list |
| Requests per run | Up to 3 H1 and 24 M1 historical points; no automatic historical retries |

The instrument metadata supports an exact EPIC and EUR/USD currency category, but **does not independently establish the complete trading product specification**, market-session calendar, or quote-unit conversion.

### Capture chronology

| Capture | Probe/request timing, UTC | H1 labels returned | M1 sample | Role |
|---|---|---|---|---|
| Initial human-readable diagnostic | ~13:19:54 | 11:00, 12:00, 13:00 | 24 points | Independent start-label indication |
| First retained JSON capture | H1 retrieved 13:25:13.370 | 11:00, 12:00, 13:00 | 24 points | Baseline for revision comparison |
| Second comparative run | H1 retrieved 14:02:24.710 | 12:00, 13:00, 14:00 | 24 points | Cross-hour overlap and forming-candle check |

The initial diagnostic included M1 labels around the 13:00 boundary. Under the joint **H1-start / M1-start** hypotheses, the 13:00 H1 open matched the 13:00 M1 open, and 19 eligible M1 observations from 13:00–13:18 had extrema within the H1 range. The 12:00 H1 close matched the 12:59 M1 close under the same hypotheses. Competing end-label interpretations showed edge or range mismatches.

The retained first JSON capture at ~13:25 provided another forming-candle candidate labeled 13:00. Its 24 recent M1 observations were partial and did not reconstruct a full H1 interval.

The second run at ~14:02 returned a new H1 label of **14:00**, even though the hypothesized 14:00–15:00 interval had barely begun. Two M1 observations labeled 14:00 and 14:01 supported the start/start hypothesis: the opening prices matched at the corresponding edge and the M1 extrema lay within the H1 range. For the elapsed 13:00 hour, the 13:59 M1 close matched the 13:00-labeled H1 close under start/start assumptions; competing end-label hypotheses produced mismatches.

**Inference:** Repeated, mutually consistent H1/M1 comparisons across the 13:00 and 14:00 boundaries strongly support treating IG `snapshotTimeUTC` as the **start label** of H1 and M1 intervals for this demo EPIC and REST response format. The REST reference itself does not guarantee that interpretation.

### Same-label revision evidence

The first and second retained captures overlap on the H1 labels `12:00` and `13:00`.

| H1 source label | First retrieval (13:25 UTC) | Second retrieval (14:02 UTC) | Observation |
|---|---|---|---|
| `2026-11-20T12:00:00` | Bid/ask OHLC | Same bid/ask OHLC | No change detected |
| `2026-11-20T13:00:00` | Bid close `10998.1`, ask close `10999.4` | Bid close `10993.4`, ask close `10994.4` | **Changed** |

For the 13:00-labeled H1 candle, the bid/ask **open, high and low stayed the same** across the two captures. Its close changed by **−4.7 bid units** and **−5.0 ask units**, in the IG response's original numeric quotation units. These are source-unit differences, not normalized FX pip or price differences.

The first retrieval occurred inside the proposed **13:00–14:00** H1 interval; the second occurred after that interval had elapsed.

**Direct observation:** IG returned distinct OHLC values for the same H1 source label across two manually initiated requests.

**Supported inference:** The earlier capture was a provisional, updating H1 observation under the strongly supported start-label convention. The endpoint can expose such forming H1 candles.

**Not established:** That the 14:02 observation is broker-confirmed final, that subsequently elapsed H1 observations never change, or that all other IG products behave identically.

### Historical allowance

The retained first capture reported 9,938 remaining points after its H1 request and 9,914 after M1; the second reported 9,911 after H1 and 9,887 after M1, against a reported 10,000-point weekly allowance. This is consistent with the bounded request design, not proof of how all IG operations are charged. A3 must track returned allowance data and avoid redundant downloads.

## Interpretation and confidence

| Question | Finding | Evidence status |
|---|---|---|
| H1 `snapshotTimeUTC` is an interval-start label for this EPIC | Strongly supported | Empirical inference, not documented guarantee |
| M1 labels are interval starts | Supported by corresponding edge comparisons | Empirical inference |
| Forming H1 candles can appear in historical responses | Supported by 13:00 and 14:00 observations before proposed interval end | Empirical inference, high confidence |
| H1 values can change between retrievals | Confirmed on the 13:00 label | Direct observation |
| H1 values are immutable after interval end | Not established | Unresolved |
| Elapsed H1 is broker-confirmed complete | Not established; no completion flag observed | Unresolved |
| Source first-availability time | Not supplied | Unknown |
| Exact product variant, dealing units and source price scale | Not fully substantiated | Requires product-specific evidence |
| Historical market-closure calendar | `openingHours` was empty; market status is a snapshot | Unknown |

The reported EUR/USD levels were around `10990` in source quotation units. These are not conventional decimal EUR/USD prices; **do not introduce a guessed normalization factor**. Preserve raw source values and verify the product-specific quote-scale definition before deriving conventional FX units or downstream indicators.

## Recommended timestamp policy (proposal, not automatic activation)

A3 may propose a named policy along these lines:

- **Provider / environment:** IG / demo.
- **Product:** `CS.D.EURUSD.CEE.IP`.
- **Source:** REST v3 historical `/prices/{epic}`.
- **Resolution:** `HOUR`.
- **Meaning:** `INTERVAL_START`.
- **Proposed policy identifier:** `ig-demo-eurusd-h1-start-empirical-2026-11-20/v1`.
- **Evidence:** A3-S0 credentialed 2026-11-20 cross-hour H1/M1 comparisons and same-label revision observation; reference this audit and a sanitized evidence manifest.
- **Status:** Empirically supported; not broker-confirmed.

The A2 `TimestampPolicy` requires a named, evidence-backed interpretation. This document offers such a candidate, **not** a repository-wide IG timestamp default or proof of broker publication/completion semantics. Before production canonical ingestion, corroborate the interpretation with independent timestamp evidence or explicit provider confirmation when feasible. Until an approved interpretation is configured, preserve source timestamps and observations without assigning authoritative canonical interval keys.

## Consequences for A3 PostgreSQL ingestion

1. **Separate logical intervals and retrieval revisions.** The source product, resolution and resolved interval identify a logical H1 candle; each retrieval can carry distinct OHLC, metadata and retrieval provenance. A3 must not overwrite the first 13:00 retrieval with the later values without preserving sufficient revision history.
2. **Preserve raw observations before or independently of normalization.** Store the original source label/text, bid/ask OHLC, request/retrieval provenance and policy/version used to interpret the timestamp. Avoid assigning unverified canonical intervals irreversibly.
3. **Use explicit as-of selection.** Reproduction of a past decision requires knowing which observation had been retrieved at the decision time. Retrieval is evidence of MRML possession, not the first time IG published the candle.
4. **Recheck provisional intervals deliberately.** The latest forming H1 may need another bounded retrieval after its proposed end. Do not infer broker-confirmed completion merely from that elapsed boundary. Record observed changes and classify canonical eligibility separately.
5. **Advance checkpoints transactionally.** No checkpoint should assert successfully ingested/validated coverage until the required observations and provenance are committed. Unknown session gaps must not be silently treated as either missing trading data or verified closures.
6. **Keep raw quote units.** Source numeric values, sizing fields and product identity require independent interpretation before operational phase calculations, monetary sizing, or any price-component conversion.

A3-S1 can implement source-revision persistence independently of a universal timestamp convention, provided canonical interval assignment remains explicit and provenance-preserving. A3-S2/S3 should validate the approved policy, product session evidence and ingestion/recovery behavior on the demo connection.

## Repeatable A3-S0 audit protocol

Use the A1 environment variables or a Git-ignored local `.env`:

- `MRML_IG_ENVIRONMENT=demo`
- `MRML_IG_API_KEY`, `MRML_IG_USERNAME`, `MRML_IG_PASSWORD`
- Exact `MRML_IG_EPIC`

Exported variables take precedence. During an active session:

```sh
python -m mrml.data.ig_semantics_probe
```

Each run authenticates, retrieves validated market details, and requests one page each of up to three H1 and 24 M1 recent observations, using no historical retries, date-range backfills or automatic polling. The reused client may retry a transient market-details GET once. An M1 failure retains the H1 report but causes a nonzero exit status; empty samples leave comparisons inconclusive.

To compare revisions, save JSON outside the repository and manually rerun across an H1 boundary:

```sh
python -m mrml.data.ig_semantics_probe --json > /path/outside/repo/ig-first.json
python -m mrml.data.ig_semantics_probe --previous /path/outside/repo/ig-first.json
```

The comparison tests all four H1/M1 start/end hypotheses using partial minute windows. Edge matches support a joint hypothesis but may occur by chance; mismatches can result from different aggregation inputs or revisions between the sequential H1 and M1 requests. The 24-point cap prevents reconstructing all 60 minutes in a run. A market `TRADEABLE` snapshot alone is insufficient to infer market-calendar coverage.

JSON reports include price observations; retain them locally and do not commit raw price captures unless redistribution is explicitly authorized under applicable IG terms. Review any sanitized documentation or fixture for account data, credentials, CST, security tokens, or restricted content.

## Outstanding evidence and closure status

**A3-S0 diagnostic implementation:** Completed and exercised against IG demo, with repeated cross-hour observations.

**Supported source finding:** H1 interval-start labeling and provisional updates are strongly supported for the audited EPIC.

**Remaining before operational ingestion decisions:** independent boundary confirmation where feasible; explicit and approved timestamp policy; product-specific quote scale and instrument identity; non-trading-session evidence; definitive treatment of elapsed-but-unconfirmed H1 observations; revision selection and persistence behavior.

This audit does **not** mark all of A3 complete and does not authorize trading or source-to-legacy D1 conversion.
