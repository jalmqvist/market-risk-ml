# IG demo H1 A3-S3 acceptance report

**Review state on 2026-11-23** — Bounded source-only operational verification passed. After three diagnostic rejections, the operator committed a corrected IG demo retrieval and read its four observations directly from PostgreSQL. This establishes the source-only Milestone A deliverable for the tested account, product and window. PR #10 remains open for review.

| Area | Status | Evidence | Remaining uncertainty |
|---|---|---|---|
| Demo authentication | PASS (operator demo attempt) | The demo authentication step completed and the client reached H1 response validation. | Continued access for a follow-up. |
| H1 date-range interpretation | PASS (corrected demo window) | The unconverted third request returned 09:00–12:00 UTC. The corrected run persisted the intended 06:00–09:00 UTC labels using provisional `Europe/Stockholm` request serialization. | Exact named account timezone and historical DST behavior remain unverified. |
| Boundary inclusion | OBSERVED (demo) | The shifted third response and the corrected stored sequence each contained both tested edges. | Scope is this account, observation date and windows. |
| Allowance consumption | PASS (offline); OBSERVED (second and third demo responses) | Remaining allowance decreased from 9,879 to 9,875 of 10,000 between comparable reports. | The net change of four matches the third response's count. Concurrent account usage was not independently excluded. |
| PostgreSQL persistence | PASS (demo and isolated PostgreSQL) | A direct repository read returned all four demo rows under one committed retrieval ID, with UTC source timestamps and bid/ask OHLC stored as decimals. | Additional broker wire precision beyond Python’s numeric parsing was not verified. |
| Committed readback and restart recovery | PASS (demo readback and isolated PostgreSQL recovery) | The operator read back the committed demo retrieval through the repository. Isolated tests verify reopen, staged recovery and fresh CLI status. | Demo database-server restart and fault injection were outside the verification. |
| Incremental planning | PASS (offline); NOT RUN (demo follow-up) | Tests exercise previously stored labels, overlap, gaps, bounded catch-up and explicit gap revisit. | Rerun the offline planner after a committed demo retrieval. |
| Provisional rechecks | PASS (offline); NOT RUN (A3-S3 demo) | Synthetic rechecks retain separate revisions and unknown completion. A3-S0 observed a real same-label revision. | An additional demo recheck is optional. Broker candle finality remains unknown. |
| Transaction recovery | PASS (offline and isolated PostgreSQL) | Fixtures cover interruption before staging, staged rollback, recovery with original identity, explicit replacement with new identity, as-of history, and absence of partial batches or false checkpoints. | Broker request outcome can remain uncertain before durable staging. |
| Canonical mapping/policy | PASS (offline and isolated PostgreSQL); NOT RUN (demo) | Synthetic source-only and approved canonical paths exercise explicit scope, provenance, interval assignment and unknown completion and availability. | A reviewed exact-product mapping and explicit timestamp approval are prerequisites for the demo canonical check. |
| Product identity and quote units | NOT RUN (exact-product confirmation) | A3-S0 retained currency type, contract size 100000, lot size 10.0 and values near 10990. IG documents market metadata fields. | Verified variant, economic sizing interpretation and quote conversion. Preserve source values. |
| Session evidence | NOT RUN (historical calendar verification) | A3-S0 observed empty hours and a point-in-time TRADEABLE status. Offline fixtures verify UNKNOWN gaps and an unset checkpoint in the absence of evidence. | Product-specific historical sessions and exceptions. |
| Operational journal | PASS (isolated PostgreSQL); NOT RUN (demo comparison) | A pre-existing populated run was unchanged across market-data migration, ingestion recovery and downgrade. | Compare all isolated journal table digests before and after the operator’s demo ingestion. |

## First credentialed attempt

The operator used the demo EPIC `CS.D.EURUSD.CEE.IP`, `resolution=HOUR`, `pageNumber=1`, and `pageSize=4`. The requested UTC range was `2026-11-23T10:00:00Z` through `2026-11-23T13:00:00Z`. The client serialized these bounds as `from=2026-11-23T10:00:00` and `to=2026-11-23T13:00:00`. It reported `IG returned an out-of-window or non-hourly H1 source label`. The journal recorded the failure and committed no source observations. The offending label, response pagination and allowance were not captured.

The operator supplied the literal MRML variant `VERIFIED_VARIANT` instead of `standard`. The variant keys local storage and policy scope. The IG client sends the EPIC, resolution, range and paging parameters, so the variant did not affect wire parameters or timestamp validation. Subsequent attempts used `standard`. The failed attempt retains its original journal scope.

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

## Third credentialed attempt

The operator used demo EPIC `CS.D.EURUSD.CEE.IP`, variant `standard`, H1, and the same `2026-11-23T10:00:00Z` through `2026-11-23T13:00:00Z` UTC window. The wire bounds were offset-free `2026-11-23T10:00:00` and `2026-11-23T13:00:00`. IG returned 09:00, 10:00, 11:00 and 12:00 UTC on one page and reported login `timezoneOffset=+1`. The client rejected the 09:00 UTC label before staging. IG reported 9,875 of 10,000 points remaining, down four from 9,879. Both corresponding edges under the +1 interpretation were present. This strongly supports account-local, inclusive bounds for this account and observation date. It leaves historical DST and other accounts unverified.

The standard-variant replacement has been used. Preserve both original failed job identities and the linked replacement attempt. The corrected run used a fresh, bounded window.

## Corrected source-only run and PostgreSQL readback

The operator reported that a direct `MarketDataRepository.source_revisions()` query returned exactly four committed observations for demo EPIC `CS.D.EURUSD.CEE.IP`, variant `standard`. The source labels were 06:00, 07:00, 08:00 and 09:00 UTC on 2026-11-23 and matched the committed ingestion outcome. All four rows shared retrieval ID `e764c91c-f9c4-4606-a7ad-e3cc4ae44bf8`. Each row held bid and ask OHLC as `Decimal`. Basic OHLC ordering and positive bid/ask spreads were consistent across all four rows. The readback establishes that the demo source observations were available from PostgreSQL after ingestion. The operator did not report a new comparable allowance reading for this run.

## Date-range timezone investigation

- The REST v3 prices reference specifies offset-free `yyyy-MM-dd'T'HH:mm:ss` for `from` and `to`. It separately lists `snapshotTime` as local time and `snapshotTimeUTC` as the UTC source field. IG's glossary says dates and times are relative to the account timezone unless otherwise specified. This supports an account-local hypothesis for the request parameters without documenting endpoint-specific parsing or boundary inclusion. The client preserves the exact UTC source field and parses it as an instant. A3-S0's interval-start inference concerns that field for this product. [IG prices reference](https://labs.ig.com/reference/prices-epic.html) · [IG glossary](https://labs.ig.com/glossary.html)
- IG documents a numeric `timezoneOffset` in the existing `POST /session` v2 login response and in `GET /session` v1 session details. The references describe hours relative to UTC. They do not document an IANA zone identifier or the offset's effective dates and DST rules. The documented accounts and account-preferences schemas provide no named timezone field. The login offset can corroborate a same-season observation but cannot resolve a historical request's timezone or DST behavior by itself. Check for an explicitly named timezone in the account configuration or obtain written IG confirmation that it applies to this endpoint. [IG session reference](https://labs.ig.com/reference/session.html) · [IG accounts reference](https://labs.ig.com/reference/accounts.html) · [IG account preferences reference](https://labs.ig.com/reference/accounts-preferences.html)
The correction converts the canonical UTC endpoints with an explicitly approved IANA timezone at the IG wire boundary. The approval reference must identify independently reviewed account timezone evidence. Neither the host timezone, login offset nor EPIC supplies a default. The CLI refuses missing approval, ambiguous wall-clock endpoints and windows spanning an offset transition. It preserves the internal UTC bounds, exact `snapshotTimeUTC` text, retrieval identities and existing persistence and quota rules.

The tests cover winter and summer conversion, spring and autumn transition refusal, both folds of an ambiguous hour, terminal-edge acceptance, missing approval, and strict rejection of the observed shifted sequence. The [runbook](IG_A3_S3_OPERATIONAL_VERIFICATION.md) records the source-only verification procedure.

## Test audit

- `test_ig_h1_range.py` exercises the REST v3 request parameters, the single-page and response limits, allowed edge labels, and malformed or out-of-range responses. It does not establish actual IG range semantics.
- `test_ig_ingestion.py` exercises planner coverage, quota holds, rechecks, session evidence, policy gating and ingestion recovery. Its PostgreSQL cases cover rollback, concurrent attempts, recovery and explicit replacement. The rejected-label CLI case confirms zero staged responses, retrievals and observations. Added checks cover pagination and source labels after staging and recovery, fresh CLI status and preservation of a populated journal row.
- `test_market_data_storage.py` covers immutable product versions, exact stored decimals, as-of revisions, provenance and transaction rollback. Its two PostgreSQL tests cover migrations, constraints and concurrent idempotency.

## Milestone A acceptance review

| Roadmap requirement | Evidence and disposition |
|---|---|
| A1-IG read-only demo connectivity | Credentialed authentication, account discovery, explicit EPIC verification, bounded H1 bid/ask retrieval and allowance metadata passed in the A1-IG diagnostic. A3-S3 confirmed demo connectivity again. |
| A2 canonical market-data contract | Deterministic tests cover explicit mapping and timestamp-policy inputs, source-label fidelity, continuity diagnostics, and fail-closed eligibility. The corrected demo run exercised the source path. No approved canonical mapping or timestamp policy was exercised against the demo retrieval. |
| A3-S0 source semantics | Real H1/M1 comparisons support start labels for the exact EPIC. The audit observed a real same-label revision. Broker finality, first availability and a general timestamp convention remain unresolved. [A3-S0 audit](IG_H1_TIMESTAMP_SEMANTICS.md) |
| A3-S1 source/revision persistence | Real demo observations were committed and read back with one identity, exact source labels, and bid/ask decimals. Isolated PostgreSQL verifies migration, atomicity, idempotency, concurrent writes, versioned revision storage and as-of reads. |
| A3-S2 bounded ingestion and operations | The real corrected request demonstrated bounded historical ingestion. Deterministic tests cover planning, allowance reserve, throttle/refusal paths, provisional rechecks, and normal and failed responses. Isolated PostgreSQL covers durable staging, recovery after interrupted persistence, replacement identity, fresh status and operational journal preservation. Real post-commit replanning and staged recovery were not reported. |
| A3-S3 source-only operational acceptance | Passed for the tested account, product and elapsed window. Credentialed evidence covers the shifted wire behavior, both tested edges, reported allowance and successful corrected persistence. Deterministic tests supplement this with current-label handling, DST conversion and refusals, demo/live separation and failure paths. |

`Europe/Stockholm` remains provisional for this demo account. The successful November window confirms the observed conversion for that window. It provides no independent account timezone identification or historical DST confirmation. Product quotation units and sizing semantics remain unresolved, so keep stored prices in source units. Canonical use requires a separately approved exact-product mapping and timestamp policy. Historical session evidence remains insufficient for verified closures; otherwise unexplained gaps stay `UNKNOWN` and the verified coverage checkpoint stays unset. Market-data acceptance covers source-only operation. Canonical use, inferred finality and historical coverage claims remain outside the demonstrated scope.

## Validation results

| Check | Result |
|---|---|
| Default offline suite | 391 passed, 14 skipped. The default run excludes opt-in PostgreSQL and external MPML checks. |
| Isolated PostgreSQL 16.16 | 7 passed. Alembic upgraded a fresh database to `0004_ig_gap_revisit` and reported no pending schema operations. |
| Pinned MPML runtime at `ef6b1b84` | 5 passed, 1 expected failure. |
| Pinned MPML replay at `90d8ea7` | 3 passed, including exact frozen output hashes. |
| Static checks | Mypy, Ruff lint and format, and `git diff --check` passed. |
| Credentialed IG demo | Operator evidence records three rejected diagnostic attempts followed by one successful corrected source-only ingestion and direct PostgreSQL readback. |
