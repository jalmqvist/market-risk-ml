# B0 operational journal audit

On 2026-11-24, the new-run regression reproduced the reported foreign-key
failure on PostgreSQL 16.16 with SQLAlchemy 2.1.9 and Alembic head
`0004_ig_gap_revisit`. PostgreSQL rejected the first
`INSERT INTO config_snapshots` with SQLSTATE `23503` on
`config_snapshots_run_id_fkey`. The statement log showed no preceding insert
into `runs` for that transaction. The traceback located the insert in
SQLAlchemy's commit-time flush. The two ORM objects were queued together and
linked only through a scalar foreign-key value, so the ORM flush did not order
the run before the snapshot.

`JournalRepository.create_run()` now flushes the run before adding the
snapshot within the existing transaction. The corrected statement order was
observed in PostgreSQL. The transaction still commits both rows together and
rolls back the run if the snapshot insert fails. The run ID, unique
`(run_date, mode)` key, duplicate behavior, and database schema are preserved.

- The new PostgreSQL regression tests confirm that a fresh repository finds
  the committed run on a repeated call, and SQL readback confirms exactly one
  snapshot linked to that run with its original data. A test-only check
  constraint rejects the snapshot after the run insert and leaves both
  tables empty after rollback.
- Isolated PostgreSQL suite: 9 passed. The existing migration round trip
  preserves populated journal records across market-data upgrade and
  downgrade. A fresh database upgraded to `0004_ig_gap_revisit` and
  `alembic check` reported no new upgrade operations.
- Default suite: 391 passed, including SQLite journal migration and daily
  runner tests. It skipped 9 PostgreSQL cases exercised separately and 7
  cases that require external MPML checkouts.
- `mypy src`, `ruff check .`, `ruff format --check .`, and
  `git diff --check` passed.
