# IG demo H1 A3-S3 acceptance report

**Review state on 2026-11-23** — The first operator-initiated demo request was rejected by the client’s H1 timestamp validation. Follow-up with the revised diagnostic is pending. A3-S0 provides prior credentialed evidence for the exact demo EPIC where noted. Milestone A remains open.

| Area | Status | Evidence | Remaining uncertainty |
|---|---|---|---|
| Demo authentication | PASS (operator demo attempt) | The demo authentication step completed and the client reached H1 response validation. | Continued access for a follow-up. |
| H1 date-range interpretation | FAIL (first demo attempt) | The client rejected an H1 label under its UTC bounds or alignment check. The original diagnostic did not capture the offending label. | Whether IG used a different timezone for the request, returned an out-of-range label for another reason, or returned a non-hourly label. |
| Boundary inclusion | INCONCLUSIVE (first demo attempt) | Mock transport accepts the terminal hour; CLI records validated pagination and observed labels. Duplicate, disordered and out-of-window responses are refused offline. | First and terminal inclusion for the chosen real window. Absent labels can be ambiguous. |
| Allowance consumption | PASS (offline); INCONCLUSIVE (first demo attempt) | Reserve, historical exhaustion, throttle holds and local conservative debits tested with synthetic evidence. The first attempt did not capture its response allowance. | Reported allowance and any actual debit from the rejected request. |
| PostgreSQL persistence | PASS (isolated PostgreSQL); NOT RUN (demo) | PostgreSQL 16 migrations and source storage passed with synthetic observations. The operator confirmed that the first demo failure was journaled without committing source observations. | Demo values, metadata and responses with wire precision beyond Python’s numeric parsing. |
| Restart durability | PASS (isolated PostgreSQL); NOT RUN (demo) | Fresh ingestion/repository instances read committed and recovered retrievals. Fresh CLI status returns the same committed evidence. | Reopen and inspect the real demo retrieval. |
| Incremental planning | PASS (offline); NOT RUN (demo follow-up) | Tests exercise previously stored labels, overlap, gaps, bounded catch-up and explicit gap revisit. | Rerun the offline planner after a committed demo retrieval. |
| Provisional rechecks | PASS (offline); NOT RUN (A3-S3 demo) | Synthetic rechecks retain separate revisions and unknown completion. A3-S0 observed a real same-label revision. | An additional demo recheck is optional. Broker candle finality remains unknown. |
| Transaction recovery | PASS (offline and isolated PostgreSQL) | Fixtures cover interruption before staging, staged rollback, recovery with original identity, explicit replacement with new identity, as-of history, and absence of partial batches or false checkpoints. | Broker request outcome can remain uncertain before durable staging. |
| Canonical mapping/policy | PASS (offline and isolated PostgreSQL); NOT RUN (demo) | Synthetic source-only and approved canonical paths exercise explicit scope, provenance, interval assignment and unknown completion and availability. | A reviewed exact-product mapping and explicit timestamp approval are prerequisites for the demo canonical check. |
| Product identity and quote units | NOT RUN (exact-product confirmation) | A3-S0 retained currency type, contract size 100000, lot size 10.0 and values near 10990. IG documents market metadata fields. | Verified variant, economic sizing interpretation and quote conversion. Preserve source values. |
| Session evidence | NOT RUN (historical calendar verification) | A3-S0 observed empty hours and a point-in-time TRADEABLE status. Offline fixtures verify UNKNOWN gaps and an unset checkpoint in the absence of evidence. | Product-specific historical sessions and exceptions. |
| Operational journal | PASS (isolated PostgreSQL); NOT RUN (demo comparison) | A pre-existing populated run was unchanged across market-data migration, ingestion recovery and downgrade. | Compare all isolated journal table digests before and after the operator’s demo ingestion. |

## First credentialed attempt

The operator used the demo EPIC `CS.D.EURUSD.CEE.IP`, `resolution=HOUR`, `pageNumber=1`, and `pageSize=4`. The requested UTC range was `2026-11-23T10:00:00Z` through `2026-11-23T13:00:00Z`. The client serialized these bounds as `from=2026-11-23T10:00:00` and `to=2026-11-23T13:00:00`. It reported `IG returned an out-of-window or non-hourly H1 source label`. The journal recorded the failure and committed no source observations. The offending label, response pagination and allowance were not captured.

The operator supplied the literal MRML variant `VERIFIED_VARIANT` instead of `standard`. The variant keys local storage and policy scope. The IG client sends the EPIC, resolution, range and paging parameters, so the variant did not affect wire parameters or timestamp validation. Use `standard` for the follow-up and leave the failed attempt in its original journal scope.

## Date-range timezone investigation

The REST v3 prices reference specifies `yyyy-MM-dd'T'HH:mm:ss` for `from` and `to` without identifying their timezone. It distinguishes local `snapshotTime` from `snapshotTimeUTC` in the response. IG's glossary in the market details reference says dates and times are account-relative unless otherwise specified. This makes account-local interpretation plausible, but does not establish the rule for the prices parameters. For example, a hypothetical UTC+1 interpretation would translate the requested 10:00–13:00 clock values to 09:00–12:00 UTC, where a returned 09:00 UTC label would explain a before-start rejection. The actual account timezone and rejected label are unavailable, so neither the direction nor the size of a shift can be inferred. A UTC-hour-aligned label outside the range and an in-range non-hourly label require different investigations. Compare the next diagnostic with the exact wire bounds and an independently verified account timezone. Keep the wire serialization and strict validation unchanged pending evidence. [IG prices REST reference](https://labs.ig.com/reference/prices-epic.html) · [IG market details REST reference](https://labs.ig.com/reference/markets-epic.html)

## Test audit

- `test_ig_h1_range.py` exercises the REST v3 request parameters, the single-page and response limits, allowed edge labels, and malformed or out-of-range responses. It does not establish actual IG range semantics.
- `test_ig_ingestion.py` exercises planner coverage, quota holds, rechecks, session evidence, policy gating and ingestion recovery. Its PostgreSQL cases cover rollback, concurrent attempts, recovery and explicit replacement. The rejected-label CLI case confirms zero staged responses, retrievals and observations. Added checks cover pagination and source labels after staging and recovery, fresh CLI status and preservation of a populated journal row.
- `test_market_data_storage.py` covers immutable product versions, exact stored decimals, as-of revisions, provenance and transaction rollback. Its two PostgreSQL tests cover migrations, constraints and concurrent idempotency.

Follow the [operational verification runbook](IG_A3_S3_OPERATIONAL_VERIFICATION.md) for the single bounded follow-up request and evidence capture. Preserve pending or ambiguous results in the next report revision.

## Validation results

| Check | Result |
|---|---|
| Default offline suite | 379 passed, 14 skipped. The default run excludes opt-in PostgreSQL and external MPML checks. |
| Isolated PostgreSQL 16.16 | 7 passed. Alembic upgraded a fresh database to `0004_ig_gap_revisit` and reported no pending schema operations. |
| Pinned MPML runtime at `ef6b1b84` | 5 passed, 1 expected failure. |
| Pinned MPML replay at `90d8ea7` | 3 passed, including exact frozen output hashes. |
| Static checks | Mypy, Ruff lint and format, and `git diff --check` passed. |
| Credentialed IG demo | First request authenticated and failed H1 label validation. No source observations were committed. Follow-up pending. |
