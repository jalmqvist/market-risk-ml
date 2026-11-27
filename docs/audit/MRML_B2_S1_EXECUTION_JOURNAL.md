# MRML B2-S1 durable execution journal

B2-S1 implements PostgreSQL storage for the
[B1-S3 lifecycle](../design/IG_B1_S3_BROKER_LIFECYCLE.md). The legacy run,
order, fill, and market-data records retain their existing schemas.

## Schema and request identity

| Table | Durable record |
| --- | --- |
| `execution_accounts` | Unique provider, demo environment, and opaque account identity. The account row is the serialization point for mutations. |
| `execution_intents` | Immutable UUID, scoped caller idempotency key, immutable canonical request and SHA-256 fingerprint, optional scoped allocated reference, internal authorization ID, optional originating run, and creation time. |
| `execution_claims` | Irreversible claim UUID and timestamp, unique per intent and per scope. It references its own scoped dispatch evidence and can optionally reference exposure evidence already linked to the intent. Claim presence is the durable uncertainty hold. |
| `execution_evidence` | Append-only UUID, scoped intent, account-local deduplication key, evidence class, safe reason code, optional broker identifier with an explicit role, optional exact amounts, and separate observation and recording times. Administrative reviews carry an opaque internal operator ID. |

The version-1 request is a closed typed contract for recorded `OPEN` and
`CLOSE` actions using market or limit terms. It fingerprints every supported
field, including operation, instrument, expiry, side, quantity, order type,
price, time in force, currency, open/protection flags and protective
level or distance, exact closing target and allocated reference where
applicable. Unsupported shapes fail validation. Decimal values are encoded
as normalized plain strings in sorted canonical JSON and persisted in
`NUMERIC(38,18)` columns where applicable. The repository rejects floats,
nonfinite values and values requiring rounding or overflow. Legacy float
fields remain as recorded.

A scoped key with an identical canonical request returns the original intent
and preserves its original authorization and run links. A changed request
records a deduplicated conflict event and fails. A different key represents
a separate action even with a matching fingerprint. The database also
enforces scoped uniqueness of non-null allocated references.

## Transactions, holds, and recovery

- `create_intent` inserts or finds the scope, locks it, then resolves replay
  and inserts the intent in one transaction. The conflict path commits
  only its audit event before reporting a conflict.
- `claim_dispatch` locks the scoped account row with `SELECT FOR UPDATE`,
  verifies the intent and optional baseline, checks for an existing hold
  and inserts claim evidence and claim in one transaction. The claim FK
  requires evidence of type `DISPATCH_CLAIM` for the same scope and intent.
  The account uniqueness constraint is an independent concurrency guard.
- `append_evidence` uses the same scoped lock. Replay requires the same
  UUID, deduplication key, intent and canonical contents. Conflicting reuse
  fails. The global event UUID and scoped deduplication key also have
  independent database constraints.
- `account_hold`, `read_intent`, and `recover_unresolved` reconstruct
  state from committed rows. A claim survives a restart or reconnect.
  Missing acknowledgement, accepted or conflicting confirmation, empty
  exposure, and administrative review all leave the hold in effect.
  The storage primitive supplies no network authorization.
- PostgreSQL triggers reject updates, deletes and truncation of the four
  tables. Composite foreign keys enforce scope on evidence, claims and
  baseline associations. Repository errors and record representations
  omit sensitive parameters. Evidence has typed fields and no raw HTTP
  payload or credential fields. Database operators must treat account and
  broker identifiers as restricted data.

B1-S3 envisioned a richer account gate, complete exposure snapshots and
later validated release. This revision derives the hold from the single
claim and supports no release or expiry. Its optional baseline is an
evidence link, with completeness, freshness and approval verification left
to a later stage. The stored reference is caller allocated. An eventual IG
opening path must enforce reference allocation and authorization before it
uses this primitive. This stage does not implement dispatch, polling,
broker reads, automatic reconciliation, snapshots, or execution eligibility.

## Migration and verification

Revision `0005_execution_journal` adds the four tables after
`0004_ig_gap_revisit`. The PostgreSQL downgrade locks the execution tables
and refuses any populated journal. Empty execution tables can be removed
independently of legacy journal and market-data records. The offline
downgrade refuses because emptiness cannot be verified. SQLite supports the
existing migration smoke tests, and the execution repository requires
PostgreSQL.

The isolated tests exercise synchronized competing claims on the same and
separate accounts, database-level uniqueness and scope violations,
append-only guards, injected transaction failures, crash-equivalent
reconnect without acknowledgement, Decimal round trips, and migration
preservation and downgrade refusal. The tests use synthetic evidence and
no broker credentials.

- Default suite passed 523 tests. It skipped 27 isolated PostgreSQL cases
  run separately and seven external MPML cases.
- All 27 isolated cases passed on PostgreSQL 16.16. Fresh upgrade reached
  `0005_execution_journal` and `alembic check` found no schema drift.
- Mypy, Ruff lint and format, and `git diff --check` passed.
