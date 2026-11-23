# IG demo H1 A3-S3 acceptance report

**Review state on 2026-11-23** — Operator execution pending. No IG requests were sent for this verification. The workspace had no configured demo credentials or reviewed product approval. A3-S0 provides prior credentialed evidence for the exact demo EPIC where noted. Milestone A remains open.

| Area | Status | Evidence | Remaining uncertainty |
|---|---|---|---|
| Demo authentication | NOT RUN (A3-S3 demo) | Demo-only guard and authentication path exercised with a fake client. A3-S0 authenticated to demo. | Current access for the operator and selected EPIC. |
| H1 date-range interpretation | NOT RUN (demo) | Mock transport asserts exact offset-free UTC parameters. Offline CLI planned three hours with a four-point cap. A3-S0 supports start labels for the audited EPIC through H1/M1 comparisons. | IG interpretation of the bounded request’s offset-free strings. |
| Boundary inclusion | NOT RUN (demo) | Mock transport accepts the terminal hour; CLI records validated pagination and observed labels. Duplicate, disordered and out-of-window responses are refused offline. | First and terminal inclusion for the chosen real window. Absent labels can be ambiguous. |
| Allowance consumption | PASS (offline); NOT RUN (demo) | Reserve, historical exhaustion, throttle holds and local conservative debits tested with synthetic evidence. | Reported allowance for the bounded request, measured debit, resets and concurrent usage. |
| PostgreSQL persistence | PASS (isolated PostgreSQL); NOT RUN (demo) | PostgreSQL 16 migrations, retrieval and source storage, decimals, metadata and CLI attempt outcomes exercised with synthetic observations. | Demo values, metadata and responses with wire precision beyond Python’s numeric parsing. |
| Restart durability | PASS (isolated PostgreSQL); NOT RUN (demo) | Fresh ingestion/repository instances read committed and recovered retrievals. Fresh CLI status returns the same committed evidence. | Reopen and inspect the real demo retrieval. |
| Incremental planning | PASS (offline); NOT RUN (demo follow-up) | Tests exercise previously stored labels, overlap, gaps, bounded catch-up and explicit gap revisit. | Rerun the offline planner after a committed demo retrieval. |
| Provisional rechecks | PASS (offline); NOT RUN (A3-S3 demo) | Synthetic rechecks retain separate revisions and unknown completion. A3-S0 observed a real same-label revision. | An additional demo recheck is optional. Broker candle finality remains unknown. |
| Transaction recovery | PASS (offline and isolated PostgreSQL) | Fixtures cover interruption before staging, staged rollback, recovery with original identity, explicit replacement with new identity, as-of history, and absence of partial batches or false checkpoints. | Broker request outcome can remain uncertain before durable staging. |
| Canonical mapping/policy | PASS (offline and isolated PostgreSQL); NOT RUN (demo) | Synthetic source-only and approved canonical paths exercise explicit scope, provenance, interval assignment and unknown completion and availability. | A reviewed exact-product mapping and explicit timestamp approval are prerequisites for the demo canonical check. |
| Product identity and quote units | NOT RUN (exact-product confirmation) | A3-S0 retained currency type, contract size 100000, lot size 10.0 and values near 10990. IG documents market metadata fields. | Verified variant, economic sizing interpretation and quote conversion. Preserve source values. |
| Session evidence | NOT RUN (historical calendar verification) | A3-S0 observed empty hours and a point-in-time TRADEABLE status. Offline fixtures verify UNKNOWN gaps and an unset checkpoint in the absence of evidence. | Product-specific historical sessions and exceptions. |
| Operational journal | PASS (isolated PostgreSQL); NOT RUN (demo comparison) | A pre-existing populated run was unchanged across market-data migration, ingestion recovery and downgrade. | Compare all isolated journal table digests before and after the operator’s demo ingestion. |

## Test audit

- `test_ig_h1_range.py` exercises the REST v3 request parameters, the single-page and response limits, allowed edge labels, and malformed or out-of-range responses. It does not establish actual IG range semantics.
- `test_ig_ingestion.py` exercises planner coverage, quota holds, rechecks, session evidence, policy gating and ingestion recovery. Its PostgreSQL cases cover rollback, concurrent attempts, recovery and explicit replacement. Added checks cover pagination and source labels after staging and recovery, fresh CLI status and preservation of a populated journal row.
- `test_market_data_storage.py` covers immutable product versions, exact stored decimals, as-of revisions, provenance and transaction rollback. Its two PostgreSQL tests cover migrations, constraints and concurrent idempotency.

Follow the [operational verification runbook](IG_A3_S3_OPERATIONAL_VERIFICATION.md) for the single bounded demo request and evidence capture. Preserve pending or ambiguous results in the next report revision.

## Validation results

| Check | Result |
|---|---|
| Default offline suite | 377 passed, 13 skipped. The default run excludes opt-in PostgreSQL and external MPML checks. |
| Isolated PostgreSQL 16.16 | 6 passed. Alembic upgraded a fresh database to `0004_ig_gap_revisit` and reported no pending schema operations. |
| Pinned MPML runtime at `ef6b1b84` | 5 passed, 1 expected failure. |
| Pinned MPML replay at `90d8ea7` | 3 passed, including exact frozen output hashes. |
| Static checks | Mypy, Ruff lint and format, and `git diff --check` passed. |
| Credentialed IG demo | NOT RUN. |
