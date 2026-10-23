# MRML Roadmap (v0.3 canonical, 2026-10-23)

## Scope baseline (v0)
- Execution/risk/journaling harness for forward testing on OANDA demo.
- PhaseAware-first operation from MPML bundles.
- No live state resolution in v0; no selector model-serving in v0.

## Current implementation status and MPML integration sequence (2026-10-23)

MRML v0 is a forward-test harness for the BSVE → MSML → MPML → MRML pipeline.
Its purpose is to test whether a strategy configuration selected and evaluated
by MPML can be operated reliably under data-availability constraints, portfolio
risk controls, and broker execution. MRML is not a second research or
walk-forward evaluation system.

The canonical standalone MPML `PhaseAwareStrategy("TF4", "MR42")` +
`Backtester` path has been audited and frozen in a one-fold EURUSD D1 fixture.
The fixture includes hash-pinned signals, execution timeline, trades, accounting
diagnostics, metrics, and provenance. The opt-in parity test regenerates the
artifacts with the pinned MPML checkout and verifies exact output hashes.
This fixture establishes a reproducible historical reference; it does not by
itself prove that MRML's per-decision runner is equivalent to MPML's strategy
runtime.

### Repository responsibilities and interface boundary

**MPML owns research and evaluation:** walk-forward folds, historical strategy
evaluation, evaluation evidence, strategy ranking, Recommendation generation,
and research provenance. MPML also owns behavioral-prediction consumption and
H1→D1 behavioral-feature aggregation inside its research pipeline.

**MRML owns forward operation:** broker-facing H1 data ingestion, H1→D1
**price-bar construction**, current strategy decisions through a supported MPML
runtime interface, portfolio risk controls, broker interaction, operational
journaling, reconciliation, and fail-closed behavior.

MRML MUST NOT run MPML walk-forward evaluation, retrain models, rank strategies,
reconstruct evaluation evidence, or independently select a historically best
strategy. Walk-forward evaluation and Recommendation generation remain MPML
responsibilities.

Two interfaces must remain distinct:

1. **Research-artifact interface:** MRML validates and consumes MPML
   Recommendation and manifest/provenance artifacts as versioned research
   inputs. `StrategyEvaluation` remains MPML's canonical internal evidence.
2. **Strategy-runtime interface:** for the fixed v0 PhaseAware composition,
   MRML requires a pinned and explicitly supported way to obtain current
   strategy signals from MPML's canonical strategy logic. This interface must
   not invoke walk-forward evaluation, training, ranking, or the historical
   `Backtester` as part of the daily operating loop.

The existing `PhaseAwareStrategy` + `Backtester` fixture is a regression oracle
for the audited historical path. It must not be treated as proof that the batch
historical API can safely be called as a per-decision runtime. If MPML does not
expose a suitable causal runtime interface, the gap must be resolved explicitly
before the MRML adapter is implemented; MRML must not silently duplicate
strategy rules.

### Integration PR2 — Establish and bind the MPML strategy-runtime interface

**Motivation:** MRML needs current signals from the same canonical strategy
logic that MPML evaluates. The frozen replay proves the historical reference is
reproducible, but does not establish a safe integration between MPML's batch
interfaces and MRML's per-decision runner.

**Scope:**
- Inspect and document the canonical MPML strategy entry point, its inputs,
  state requirements, dependencies, and execution semantics.
- Decide and document the supported causal runtime interface for the fixed
  PhaseAware composition.
- Pin the MPML revision and runtime dependency contract used by MRML.
- Implement the smallest adapter required to obtain current signals without
  reimplementing strategy rules.
- Preserve MRML's existing risk, execution, and journaling policies.
- Retain the frozen historical replay test and add tests for the actual
  MRML-to-MPML runtime boundary.

**Acceptance criteria:**
- Repeated decisions over identical input history are deterministic.
- Runtime decisions use only information available at the decision timestamp.
- Runtime signals agree with canonical MPML strategy semantics on explicitly
  defined parity fixtures.
- Missing, stale, inconsistent, or insufficient inputs fail closed.
- MRML does not invoke walk-forward evaluation, train models, rank strategies,
  or run the historical `Backtester` during the daily loop.
- Strategy rules are not independently reimplemented in MRML.
- Risk and execution behavior are unchanged by the strategy-binding work.

If the existing MPML strategy API cannot meet these criteria, PR2 must first
establish the missing MPML-owned runtime contract rather than treating a batch
backtester as an incremental strategy API.

### Integration PR3 — Validate MRML H1-to-D1 market-price-bar parity

**Motivation:** MRML receives broker data at H1 resolution but operates the v0
PhaseAware strategy on D1 price bars. Different day boundaries, timestamp
handling, or missing-bar policies could change signals even when the strategy
implementation is identical.

**Scope:**
- Compare MRML's H1→D1 **price-bar construction** with MPML's canonical D1
  market-data preparation on overlapping historical data.
- Verify timezone and day-boundary conventions, OHLCV aggregation, incomplete
  bars, missing hours, weekends, and late data.
- Record exact matching and discrepancy results with reproducible fixtures.

**Acceptance criteria:**
- Matching input hours produce matching D1 bars under the agreed boundary
  convention.
- Missing, incomplete, or ambiguous data is handled deterministically and
  fails closed where required.
- Any tolerated differences are explicitly documented and tested.
- Behavioral-prediction H1→D1 aggregation remains an internal MPML
  responsibility; MRML does not recreate that layer or independently construct
  behavioral features.

### Non-goals for these integration PRs

- Moving walk-forward evaluation, training, or strategy ranking into MRML.
- Adding live behavioral-state resolution or selector model serving to v0.
- Reimplementing MPML strategy rules in MRML.
- Changing the risk controller, portfolio policy, broker execution, or order
  lifecycle.
- Treating historical replay parity alone as proof of live operational parity.

The existing numbered foundation phases below retain their original meaning.
This integration sequence does not renumber or redefine them.

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
PR6 (Wk4)   StrategyRunner integration, gated by the MPML runtime contract and
            parity criteria in Integration PR2. No state resolver in v0.

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

## Superseded plan note
The previous roadmap structure (including state-resolver-first sequencing in
older Phase 2-5 text) was superseded by the 2026-10-08 architecture/ADR
updates and consolidated here into a single canonical plan.
