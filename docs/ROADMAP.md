# MRML Roadmap (v0.3 canonical, 2026-10-12)

Capacity: 2 h/weekday = 10 h/week. Each PR targets 8-12 h unless noted.
Rule: one PR open at a time. Each PR ends with green CI and a short note in
docs/CHANGELOG.md.

## Scope baseline (v0)
- Execution/risk/journaling harness for forward testing on OANDA demo.
- PhaseAware-first operation from MPML bundles.
- No live state resolution in v0; no selector model-serving in v0.

## Current implementation status and integration sequence (2026-10-16)

The changelog records the initial journal models/migrations, OANDA H1 client and
bar builder, fixed-composition adapter protocol, and offline runtime tests as
implemented. The EURUSD fold-0 PhaseAware fixture and its frozen reference
outputs are now complete, with an opt-in canonical MPML replay test. The remaining
integration gap is binding MRML to MPML without collapsing the batch-backtest and
per-decision forward-test interfaces.

The numbered PRs below retain their original **foundation-phase** meaning. The
current MPML integration work uses a separate short sequence, documented in
`MPML_MRML_PhaseAware_Investigation_Notes_2026-10-15.md`:

1. **Audit — complete (2026-10-16):** trace the canonical standalone PhaseAware
   path and record strategy/execution semantics.
2. **Replay fixture — complete:** freeze one EURUSD fold from the canonical MPML
   `PhaseAwareStrategy` + `Backtester` path, including signals, SL/TP, execution
   timeline, trades, metrics, and accounting reconciliation. Hashes and provenance
   are checked by `tests/test_replay_parity.py`; do not substitute the selector
   portfolio timeline.
3. **Integration PR2 — next:** bind MRML to the pinned MPML strategy implementation
   and prove replay reproducibility without changing MRML risk/execution policy.
4. **Integration PR3:** validate MRML H1→D1 aggregation against MPML on
   overlapping historical bars and confirm boundary/missing-bar behavior.

This sequence does not renumber or redefine the foundation PRs below.

## Week 0 (Thu Oct 8 - Fri Oct 9): unblock, don't build
- Launch MPML runs on the server (A1 first, then A3/A2).
- Decide initial candidate compositions for v0 bundle consumption.
- Collect real artifact schema printouts for contracts hardening.
- Create the MRML repo and enable branch protection on `main`.

## Phase 1: Foundations (Weeks 1-2)
PR1 (Wk1)   Scaffold, config, CI, docker-compose Postgres, contracts + bundle
            loader against synthetic fixtures.
PR2 (Wk1-2) Journal: SQLAlchemy models, Alembic migrations, repositories,
            idempotency keys. **Implemented in current baseline; real-Postgres CI
            coverage should be confirmed separately if not already enabled.**
PR3 (Wk2)   Bundle loader v2 against real MPML artifacts; harden semantics and
            staleness/evidence checks.

## Phase 2: Data, parity, and risk (Weeks 3-4)
PR4 (Wk3)   OANDA client (practice only, requests-based, retries, timeouts) +
            H1->D1 bar builder with MPML-identical boundaries; diff on overlap.
PR5 (Wk3-4) RiskController: flat sizing, currency exposure caps, kill switch,
            daily loss limit; property-style tests.
PR6 (Wk4)   StrategyRunner wrapping PhaseAware via MPML; replay parity vs MPML
            trade artifacts (diff = 0). No state resolver in v0.

## Phase 3: Execution and burn-in (Weeks 5-6)
PR7 (Wk5)   Order manager (deterministic client_order_id), reconciler,
            fail-closed behavior, dry-run mode.
PR8 (Wk5-6) Daily runner, locking, run summary, alerting.
PR9 (Wk6)   Deployment: server setup, scheduler (cron/systemd), backups,
            runbook. Start Stage A on OANDA demo.

## Phase 4: Monitoring and operating cadence (Weeks 7-8)
PR10 (Wk7)  Monitoring/reporting: slippage vs assumed costs, daily parity
            checks, per-pair outcome report.
PR11 (Wk8)  Operational hardening: reconciliation diagnostics, alert quality,
            runbook drills, restore-test cadence.

## Later (post-A1/A3 evidence)
- Alternative-composition bundles (config changes only where possible).
- Sentiment ingest and shadow state logging for research.
- Model-serving decision for selector path (out of v0).
- Parallel MPML provenance improvements (dataset_version, data start/end,
  fingerprint).

## Weekly rhythm
- Mon: plan the week's PR slice.
- Fri: 20-minute review of merged work; update this file.
- Keep a parking lot section for ideas. Nothing enters a phase without
  displacing something.

## Superseded plan note
The previous roadmap structure (including state-resolver-first sequencing in
older Phase 2-5 text) was superseded by the 2026-10-08 architecture/ADR
updates and consolidated here into a single canonical plan.
