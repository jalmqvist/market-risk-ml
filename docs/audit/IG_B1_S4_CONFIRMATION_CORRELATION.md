# B1-S4 read-only confirmation and evidence correlation

## Documented wire contracts

| Lookup | Version and response | Inconclusive response |
| --- | --- | --- |
| `GET /confirms/{dealReference}` | v1. Requires exact echoed reference and recognized `dealStatus`. Preserves transaction deal ID, object status, reason, EPIC, direction, Decimal size and levels, raw transaction date and each affected deal ID/status when supplied. [IG reference](https://labs.ig.com/reference/confirms-deal-reference.html) | HTTP 404 with `error.confirms.deal-not-found`. |
| `GET /positions/{dealId}` | v1. Requires matching `position.dealId`, `market.epic`, direction and `position.size`. Reads `position.level` and optional stop/limit levels. The list parser continues to use `dealSize` and `openLevel`. Opening-date strings, including `createdDateUTC` when supplied, are kept separately from the local UTC observation time. [IG reference](https://labs.ig.com/reference/positions-deal-id.html) | HTTP 404 with `error.position.notfound`. |

These schemas and error codes were checked against the public reference
and exercised offline. Both lookups require an authenticated demo session
and a fresh accounts check matching the caller's intended account ID.
The reference uses IG's documented 1–30 ASCII alphanumeric, underscore
and hyphen constraint. For deal IDs the adapter uses a conservative local
1–128 ASCII path-segment allowlist of the same characters and URL encodes
the segment. IG deal IDs remain opaque. Other shapes require review before
the lookup accepts them.

The transport bounds decoded response bodies to 256 KiB, disables redirects
and automatic retries, and uses the demo gateway. Authentication/session,
transport, malformed response and identity conflict fail separately from
the documented endpoint-specific not-found cases. Other HTTP errors have
no not-found meaning. The CLI prints only classification and counts.
Acceptance confirms the disposition of a request. It does not establish
a fill, an open position, or current account eligibility.

## Pure opening assessment

The evidence types carry the provider/environment/account scope, opaque
identity and provenance. They separate returned references, a transaction's
deal ID and affected deal roles. Callers retain the evidence and its raw
sources; results return a status, reason code and sorted evidence IDs.
Replaying the same facts yields the same result. Duplicate confirmation
facts can agree; contradictions in provided fields or affected legs
remain visible as a mismatch.

A match on an opening requires accepted confirmation for the exact issued
reference, one explicitly opened affected deal, and exact current position
ID, EPIC, direction and Decimal quantity. When present, an acknowledgement,
confirmation terms and the position reference must agree. Related working
orders report contingent exposure. Activity with an explicit opening action
can corroborate or contradict the link and cannot establish a match alone.
Unsupported affected-deal sets remain unresolved.

Exposure assessment requires complete position and working-order reads
within the caller's positive maximum age and from the intended scope.
Each read has its own aware timestamp; future, stale and incomplete
exposure reads cannot establish a match. A complete individual lookup
covers only its requested ID. Its absence after an accepted opening
produces a discrepancy and says nothing about historical execution.

A validated rejection records the request outcome independently of exposure.
The IG adapter currently permits a no-attribution reconciliation assessment
for explicit `MARKET_CLOSED`, `MARKET_OFFLINE` and `INSTRUMENT_NOT_FOUND`
rejections. Missing, unreviewed, duplicate and conflicting reasons stay
unresolved for exposure. Even definitive rejection requires full scoped
position and working-order collections without an exact attributable link.
The assessment never clears an account hold or authorizes a new request.

## Proposed bounded operator verification

After separate authorization, use existing known IDs from the intended
demo account. Supply `MRML_IG_LOOKUP_ACCOUNT_ID` and
`MRML_IG_LOOKUP_IDENTIFIER` through protected local configuration alongside
the existing demo settings. Run `python -m mrml.data.ig_broker_probe
confirmation` or `python -m mrml.data.ig_broker_probe position` as appropriate.
Each invocation performs one authentication, one scoped account check and
one lookup. Record only classified outcomes and counts. Exit status 2
reports an inconclusive documented not-found result. The diagnostic does
not discover IDs or persist a reconciliation.

Nonempty broker payloads, confirmation visibility and retention, returned
reference behavior, deal ID alphabet, multi-deal and partial-close semantics,
and the relationship between activity and confirmation remain operationally
unverified. No credentialed IG requests were made in this stage.
