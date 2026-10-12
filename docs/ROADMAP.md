# MRML Roadmap (v0.3 canonical, 2026-10-12)

Capacity: 2 h/weekday = 10 h/week. Each PR targets 8-12 h unless noted.
Rule: one PR open at a time. Each PR ends with green CI and a short note in
docs/CHANGELOG.md.

## Scope baseline (v0)
- Execution/risk/journaling harness for forward testing on OANDA demo.
- PhaseAware-first operation from MPML bundles.
- No live state resolution in v0; no selector model-serving in v0.

## Week 0 (Thu Oct 8 - Fri Oct 9): unblock, don't build
- Launch MPML runs on the server (A1 first, then A3/A2).
- Decide initial candidate compositions for v0 bundle consumption.
- Collect real artifact schema printouts for contracts hardening.
- Create the MRML repo and enable branch protection on `main`.

## Phase 1: Foundations (Weeks 1-2)
PR1 (Wk1)   Scaffold, config, CI, docker-compose Postgres, contracts + bundle
            loader against synthetic fixtures.
PR2 (Wk1-2) Journal: SQLAlchemy models, Alembic migrations, repositories,
            idempotency keys. Tests against real Postgres in CI.
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
