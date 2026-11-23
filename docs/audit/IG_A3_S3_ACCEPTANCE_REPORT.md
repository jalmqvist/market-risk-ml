# IG demo H1 A3-S3 acceptance report

**Review state on 2026-11-23** — Both operator-initiated demo requests failed strict H1 label validation. The second captured an hourly UTC label one hour before the requested start. A3-S3 and Milestone A remain open pending independently reviewed timezone evidence and a successful credentialed ingestion.

| Area | Status | Evidence | Remaining uncertainty |
|---|---|---|---|
| Demo authentication | PASS (operator demo attempt) | The demo authentication step completed and the client reached H1 response validation. | Continued access for a follow-up. |
| H1 date-range interpretation | FAIL (both demo attempts) | The second response contained 09:00 UTC for the requested 10:00–13:00 UTC range. | Whether the endpoint interpreted offset-free bounds in the account timezone. |
| Boundary inclusion | INCONCLUSIVE (demo) | The second response had four observations and included 09:00 UTC. | The other three labels were not captured; a shifted inclusive range is consistent with the count. |
| Allowance consumption | PASS (offline); OBSERVED (second demo response) | IG reported 9,879 of 10,000 points remaining and 169,890 seconds until reset. Offline reserve and throttle tests passed. | No comparable baseline establishes the debit or concurrent quota usage. |
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

## Second credentialed attempt

| Field | Observed value |
|---|---|
| Demo EPIC and local variant | `CS.D.EURUSD.CEE.IP`, `standard` |
| Resolution and requested UTC bounds | `HOUR`, `2026-11-23T10:00:00Z` through `2026-11-23T13:00:00Z` |
| Wire bounds from the client serializer | `from=2026-11-23T10:00:00`, `to=2026-11-23T13:00:00` |
| Rejection | `before requested start`; exact `snapshotTimeUTC=2026-11-23T09:00:00` |
| Observations and pagination | 4; page 1 of 1, page size 4 |
| Historical allowance | 9,879 remaining of 10,000; 169,890 seconds until reset |

The attempt used the corrected local variant. The H1 client rejected the response before staging valid observations. Preserve both failed journal entries in their respective product scopes. The remaining three labels and session timezone offset were unavailable in this capture.

## Date-range timezone investigation

- The REST v3 prices reference specifies offset-free `yyyy-MM-dd'T'HH:mm:ss` for `from` and `to`. It separately lists `snapshotTime` as local time and `snapshotTimeUTC` as the UTC source field. IG's glossary says dates and times are relative to the account timezone unless otherwise specified. This supports an account-local hypothesis for the request parameters without documenting endpoint-specific parsing or boundary inclusion. The client preserves the exact UTC source field and parses it as an instant. A3-S0's interval-start inference concerns that field for this product. [IG prices reference](https://labs.ig.com/reference/prices-epic.html) · [IG glossary](https://labs.ig.com/glossary.html)
- IG documents a numeric `timezoneOffset` in the existing `POST /session` v2 login response and in `GET /session` v1 session details. The references describe hours relative to UTC. They do not document an IANA zone identifier or the offset's effective dates and DST rules. The documented accounts and account-preferences schemas provide no named timezone field. The login offset can corroborate a same-season observation but cannot resolve a historical request's timezone or DST behavior by itself. Check for an explicitly named timezone in the account configuration or obtain written IG confirmation that it applies to this endpoint. [IG session reference](https://labs.ig.com/reference/session.html) · [IG accounts reference](https://labs.ig.com/reference/accounts.html) · [IG account preferences reference](https://labs.ig.com/reference/accounts-preferences.html)
- If IG interpreted the 10:00 and 13:00 wire values in UTC+1, the corresponding UTC bounds would be 09:00 and 12:00. A continuous inclusive sequence would be 09:00, 10:00, 11:00 and 12:00. A terminal-exclusive sequence would have three labels. The observed 09:00 and count of four fit the inclusive hypothesis, but the complete sequence and the account timezone are unknown. The observed label rules out compliance with MRML's requested UTC bounds for this response. It does not establish the broker's general rule or actual quota debit.

Keep strict UTC validation and current serialization while the timezone convention is unverified. If IG confirms account-local parsing and the exact named zone can be independently established, convert each UTC request endpoint with `zoneinfo.ZoneInfo` at the wire boundary. Preserve the original UTC plan and validation bounds, the exact source timestamp text, and the existing retrieval identities. Never derive a timezone from a login offset. An offset-free wall time can be ambiguous during a fall-back fold. Refuse ambiguous bounds and transition-spanning ranges until IG's handling is established. An instant converted from UTC cannot produce a nonexistent local time. Test that invariant, winter and summer offsets, fold refusal, transition-spanning refusal and both boundary labels before deploying a conversion.

The [runbook](IG_A3_S3_OPERATIONAL_VERIFICATION.md) describes one optional operator-controlled experiment. The diagnostic captures all successfully parsed UTC label strings and any numeric login offset on rejection, without another metadata call or persistence changes. The test suite exercises current UTC serialization and validation across winter, summer and European DST transition dates. Those tests assert invariance to the login offset while the provider convention remains unverified.

## Test audit

- `test_ig_h1_range.py` exercises the REST v3 request parameters, the single-page and response limits, allowed edge labels, and malformed or out-of-range responses. It does not establish actual IG range semantics.
- `test_ig_ingestion.py` exercises planner coverage, quota holds, rechecks, session evidence, policy gating and ingestion recovery. Its PostgreSQL cases cover rollback, concurrent attempts, recovery and explicit replacement. The rejected-label CLI case confirms zero staged responses, retrievals and observations. Added checks cover pagination and source labels after staging and recovery, fresh CLI status and preservation of a populated journal row.
- `test_market_data_storage.py` covers immutable product versions, exact stored decimals, as-of revisions, provenance and transaction rollback. Its two PostgreSQL tests cover migrations, constraints and concurrent idempotency.

Follow the [operational verification runbook](IG_A3_S3_OPERATIONAL_VERIFICATION.md) for the single bounded follow-up request and evidence capture. Preserve pending or ambiguous results in the next report revision.

## Validation results

| Check | Result |
|---|---|
| Default offline suite | 385 passed, 14 skipped. The default run excludes opt-in PostgreSQL and external MPML checks. |
| Isolated PostgreSQL 16.16 | 7 passed. Alembic upgraded a fresh database to `0004_ig_gap_revisit` and reported no pending schema operations. |
| Pinned MPML runtime at `ef6b1b84` | 5 passed, 1 expected failure. |
| Pinned MPML replay at `90d8ea7` | 3 passed, including exact frozen output hashes. |
| Static checks | Mypy, Ruff lint and format, and `git diff --check` passed. |
| Credentialed IG demo | Both requests authenticated and failed H1 label validation. Successful credentialed persistence remains pending. |
