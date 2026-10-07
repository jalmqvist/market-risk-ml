# MRML Roadmap (v0.2, 2026-10-07)

Capacity: 2 h/weekday = 10 h/week. Each PR targets 8-12 h unless noted.
Rule: one PR open at a time. Each PR ends with green CI and a short note in
docs/CHANGELOG.md.

## Week 0 (Thu Oct 8 - Fri Oct 9): unblock, don't build
- Launch MPML runs on the server (A1 first, then A3/A2). Decide Persistent
  candidate compositions.
- Collect inputs for PR 3: schema printouts of real strategy_evaluations.parquet,
  recommendations.parquet, experiment_manifest.json (see "Inputs needed").
- Create the empty mrml repo on GitHub, enable branch protection on main.

## Phase 1: Foundations (Weeks 1-2)
PR1 (Wk1)   Scaffold, config, CI, docker-compose Postgres, contracts + bundle
            loader against synthetic fixtures. [prompt below]
PR2 (Wk1-2) Journal: SQLAlchemy models, Alembic migrations, repositories,
            idempotency keys. Tests against a real Postgres in CI.
PR3 (Wk2)   Bundle loader v2: validate against REAL MPML artifacts; resolve
            ADR-001 (pair dimension, strategy_id semantics). Staleness checks.

## Phase 2: Data and strategy parity (Weeks 3-4)
PR4 (Wk3)   OANDA client (practice only, requests-based, retries, timeouts) +
            H1 -> D1 bar builder with MPML-identical boundaries. Parity test:
            rebuild a stretch of MPML's D1 bars from H1 and diff. Resolves ADR-004.
PR5 (Wk3-4) RiskController: pure functions, flat sizing, currency exposure caps,
            kill switch, daily loss limit. Property-style tests.
PR6 (Wk4)   TrendVolResolver + StrategyRunner wrapping MPML. Replay test: run
            2-3 years of history, diff entries/exits against MPML artifacts.
            Resolves ADR-002. If the replay won't match, STOP and fix here.

## Phase 3: Execution and burn-in (Weeks 5-6)
PR7 (Wk5)   Order manager (deterministic client_order_id), reconciler,
            fail-closed behaviour, dry-run mode.
PR8 (Wk5-6) Daily runner, locking, run summary, alerting (email or Telegram).
PR9 (Wk6)   Deployment: server setup, cron/systemd timer, backups, runbook.
            Start Stage A on OANDA demo (TrendVol only).

## Phase 4: Behavioral surfaces (Weeks 7-10)
PR10 (Wk7)  Sentiment ingest: Postgres schema for scraper output, data-quality
            checks (gaps, duplicates, broker tag). Resolves ADR-003 (cadence).
PR11 (Wk8)  BehavioralResolver for Reactive-JPY using frozen BSVE calibration.
            Golden test: reproduce BSVE state assignments with diff = 0.
PR12 (Wk9)  Wire Reactive-JPY recommendations into the runner. Stage B in
            dry-run first, then demo.
Buffer (Wk10) Overrun, bug fixes, documentation.

## Phase 5: After MPML ablations land (Week 11+)
- Persistent surface (Stage C) once A1/A3/A2 results decide composition and
  whether live ANN inference is needed.
- Shadow analysis: logged state transitions vs. realised outcomes, to test
  the state-transition exit offline.
- First review of the forward-test journal (see success criteria).
- Decide on sizing research, and on any commercial path.

## Weekly rhythm
- Mon: plan the week's PR slice. Fri: 20-minute review of what's merged,
  update this file.
- Keep a "parking lot" section for ideas. Nothing enters a phase without displacing something.
