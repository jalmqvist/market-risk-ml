# Changelog

## Unreleased

- Added a practice-only OANDA H1 client with retries/timeouts, UTC+1 H1-to-D1
  aggregation, deterministic overlap diffs, and incomplete-bar rejection.
- Added fixed-composition PhaseAware strategy adaptation, pure flat-sizing risk
  limits, fail-closed daily orchestration, demo-only execution handoff, and
  SQLAlchemy journal models/Alembic schema.
- Added explicit frozen-universe, practice account, risk, evidence, and bar
  boundary configuration; live execution remains blocked.
- Added account-currency-aware risk sizing; missing quote-currency conversions
  reject new entries rather than assuming parity.
- Added offline runtime/risk/journal tests and a CI replay-parity stub; real
  MPML strategy binding, broker reconciliation/order adapter, and historical
  parity fixtures remain follow-ups.

## 0.1.0 - 2026-10-12

- Scaffolded the Python package, typed environment settings, CI, local Postgres,
  and synthetic fixtures.
- Added fail-fast validation and provenance access for the amended MPML
  recommendation bundle format.
- Aligned planning documents with ARCHITECTURE v0.3 / ROADMAP 2026-10-08
  direction (PhaseAware-first v0, no live state resolver in v0, ADR status sync).
- Rewrote ROADMAP.md as a single canonical v0.3 execution plan to remove
  superseded phase ambiguity.
- Rewrote ARCHITECTURE.md as a single canonical v0.3 document and removed
  transitional split-structure sections.
