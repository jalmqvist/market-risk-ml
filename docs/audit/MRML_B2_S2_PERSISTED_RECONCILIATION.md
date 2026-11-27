# MRML B2-S2 persisted reconciliation

B2-S2 saves presupplied broker observations and results of the existing pure
opening correlation. The B1-S4 rules remain authoritative and the B2-S1
account hold still derives from the irreversible dispatch claim.

## Observation contract

`broker_observations` stores a stable UUID and an account-local original
evidence ID with the source, observation and recording times, schema version
and a content digest. The PostgreSQL account row identifies the provider,
demo environment and opaque account. A caller supplies normalized evidence
for an already verified scope. Storage performs no authentication or reads.

- **Account collections** have no intent owner. Position and working-order
  records preserve their independent collection status, coverage, optional
  start/end interval and entire normalized row set. Unmatched positions
  remain retrievable through the account and observation ID. An account
  collection can be complete and empty. A failed read has no members, an
  incomplete read can preserve available members, and a missing read has
  no record. A documented individual-position not-found covers only its
  target. That coverage remains explicit when reconstructed for correlation.
- **Transaction evidence** belongs to a scoped intent. Typed input variants
  preserve acknowledgement and confirmation references, transaction
  deal ID, affected deal IDs and statuses, Decimal terms, outcome, reason
  and raw broker date labels. Activity-opening evidence and a documented
  confirmation not-found with its requested reference are also supported.
  The issued reference stays on the immutable B2-S1 intent.
- **Exact values** are stored as PostgreSQL JSONB numeric tokens, which use
  PostgreSQL `numeric` internally. Serialization and readback bypass binary
  floats. The repository enforces the existing `NUMERIC(38,18)` value
  range and preserves broker text and member order. Schema-v1 documents
  contain normalized typed fields only. They omit raw HTTP payloads,
  credentials and tokens. Ordinary errors and record representations omit
  broker identifiers.

Recording a collection writes its status, member count and complete document
in one transaction. A PostgreSQL constraint checks the document kind and
array count. Repeated evidence requires the same UUID, original ID and
digest. Changing content under an existing identity conflicts. Fresh
observations of the same deal can use fresh identities. Intent-scoped
evidence and account collection writes use the existing scoped row lock,
and PostgreSQL enforces scoped foreign keys and unique identities.

## Assessment and replay

`ObservationJournal` provides `record_collection`, `record_transaction`,
`read_observation`, `assess_opening` and `replay`. An assessment selects two
explicit collection UUIDs plus an ordered list of transaction UUIDs. It
loads the original immutable intent, rebuilds `OpeningEvidence`, and calls
`correlate_opening()` with the supplied timezone-aware evaluation timestamp
and positive maximum evidence age. It stores the result, exact reason,
source evidence IDs and algorithm identifier `opening-v1`.

The two collection foreign keys pin account and collection kind. Transaction
associations pin both intent and account. The assessment and all associations
commit together. Deferred PostgreSQL checks enforce the declared number and
ordinals of transaction inputs and reject later additions. All new records
reject updates, deletes and truncation. A database failure rolls back the
failed write without altering prior evidence or the account hold.

Replay reconstructs the same inputs with the saved clock and age from
PostgreSQL. It returns the stored and recomputed results and whether they
match. Missing inputs, corrupt observations and unsupported versions are
reported explicitly. A recent observation cannot replace a stored older one.
The algorithm ID must change when correlation semantics change. This stage
can replay `opening-v1` and refuses other versions.

Freshness and post-confirmation ordering follow B1-S4 using recorded,
comparable observation timestamps. A broker timestamp label cannot prove
execution time. Independent position and working-order reads have separate
timestamps and no atomic broker snapshot semantics. A matched assessment
describes its intent only and leaves the durable account hold in place.

## Migration and limits

Revision `0006_broker_observations` follows `0005_execution_journal` and
adds three tables. It preserves the four B2-S1 execution tables, legacy
journal, and market-data records. An empty downgrade is supported online.
A populated or offline downgrade is refused. SQLite continues to support
the existing migration smoke test. Runtime storage requires PostgreSQL.

Synthetic PostgreSQL tests cover collection coverage, multiple reads of a
deal, exact decimals, ordering, conflicting confirmations, account/intent
scope, reference integrity, manifest sealing, append-only guards, concurrent
idempotent insertion, rollback and replay after reconnect. No broker calls
were made.

The only implemented assessment is opening correlation. Snapshot metadata
and normalized evidence are durable; implicit discovery, continuous
monitoring, new broker reads, hold release, dispatch and account eligibility
remain outside B2-S2. Hold release will require a separate reviewed design.

## Verification results

- Default suite passed 523 tests. It skipped 59 PostgreSQL cases run
  separately and seven external MPML cases.
- Targeted offline broker suite passed 121 tests. All 59 isolated cases
  passed on PostgreSQL 16.16, including 32 new observation cases.
- A fresh database reached `0006_broker_observations` with no Alembic
  schema drift. Upgrade and downgrade checks preserved existing records
  and refused a populated downgrade.
- Mypy, Ruff lint and format, and `git diff --check` passed.
