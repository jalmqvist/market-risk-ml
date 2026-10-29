# MRML Roadmap (v0.3 canonical, 2026-10-23)

## Scope baseline (v0)

-   Execution/risk/journaling harness for forward testing on OANDA demo.
-   PhaseAware-first operation from MPML bundles.
-   No live state resolution in v0; no selector model-serving in v0.

## Current implementation status and MPML integration sequence (2026-10-23)

MRML v0 is a forward-test harness for the BSVE → MSML → MPML → MRML
pipeline. Its purpose is to test whether a strategy configuration
selected and evaluated by MPML can be operated reliably under
data-availability constraints, portfolio risk controls, and broker
execution. MRML is not a second research or walk-forward evaluation
system.

The canonical standalone MPML `PhaseAwareStrategy("TF4", "MR42")` +
`Backtester` path has been audited and frozen in a one-fold EURUSD D1
fixture. The fixture includes hash-pinned signals, execution timeline,
trades, accounting diagnostics, metrics, and provenance. The opt-in
parity test regenerates the artifacts with the pinned MPML checkout and
verifies exact output hashes. This fixture establishes a reproducible
historical reference; it does not by itself prove that MRML's
per-decision runner is equivalent to MPML's strategy runtime.

### Repository responsibilities and interface boundary

**MPML owns research and evaluation:** walk-forward folds, historical
strategy evaluation, evaluation evidence, strategy ranking,
Recommendation generation, and research provenance. MPML also owns
behavioral-prediction consumption and H1→D1 behavioral-feature
aggregation inside its research pipeline.

**MRML owns forward operation:** broker-facing H1 data ingestion, H1→D1
**price-bar construction**, current strategy decisions through a
supported MPML runtime interface, portfolio risk controls, broker
interaction, operational journaling, reconciliation, and fail-closed
behavior.

MRML MUST NOT run MPML walk-forward evaluation, retrain models, rank
strategies, reconstruct evaluation evidence, or independently select a
historically best strategy. Walk-forward evaluation and Recommendation
generation remain MPML responsibilities.

Two interfaces must remain distinct:

1.  **Research-artifact interface:** MRML validates and consumes MPML
    Recommendation and manifest/provenance artifacts as versioned
    research inputs. `StrategyEvaluation` remains MPML's canonical
    internal evidence.
2.  **Strategy-runtime interface:** for the fixed v0 PhaseAware
    composition, MRML requires a pinned and explicitly supported way to
    obtain current strategy signals from MPML's canonical strategy
    logic. This interface must not invoke walk-forward evaluation,
    training, ranking, or the historical `Backtester` as part of the
    daily operating loop.

The existing `PhaseAwareStrategy` + `Backtester` fixture is a regression
oracle for the audited historical path. It must not be treated as proof
that the batch historical API can safely be called as a per-decision
runtime. If MPML does not expose a suitable causal runtime interface,
the gap must be resolved explicitly before the MRML adapter is
implemented; MRML must not silently duplicate strategy rules.

### Integration PR2 --- Establish and bind the MPML strategy-runtime interface

**Status at 2026-10-29:** historical replay parity is established for
the frozen EURUSD fixture at the pinned MPML revision. The opt-in parity
test was run in a detached worktree at MPML commit
`90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d` and passed
(`1 passed, 68 deselected`). A separate baseline run on the newer
checkout was reported byte-identical, but the pinned-revision run
remains the canonical historical regression check. This proves
historical fixture reproducibility; it does **not** prove per-decision
runtime parity.

**Motivation:** MRML needs current signals from the same canonical
strategy logic that MPML evaluates. Source inspection identified the
canonical PhaseAware entry point and the TF4/MR42 behavior, but a batch
historical API must not be presumed to be a supported incremental
runtime API.

#### Required design

This section implements the proposed boundary in
[ADR-007](adr/ADRs.md#adr-007-causal-phaseaware-strategy-runtime-interface);
ADR-006 remains authoritative for no live state resolution or selector
serving in v0.

-   Keep strategy logic owned by MPML. MRML must not port or
    independently reimplement the phase detector, TF4, MR42, or
    PhaseAware routing rules.
-   Bind MRML to an explicitly supported, pinned MPML-owned causal
    decision interface. If no suitable interface exists, define and test
    that interface in MPML before binding `StrategyRunner`; do not route
    the daily loop through the historical `Backtester`.
-   Use the frozen v0 composition `PhaseAwareStrategy("TF4", "MR42")`,
    unless the existing configuration contract explicitly resolves
    another approved composition. The strategy ID/configuration and MPML
    revision must be recorded with each decision.
-   Start with deterministic recomputation over the available historical
    prefix. Do not assume a 252-bar window is sufficient: the rolling
    volatility median is one dependency, while indicator warm-up and
    forward-filled signal state are additional dependencies. Consider
    bounded-window or incremental-state optimization only after tests
    demonstrate exact parity.
-   Keep signal generation distinct from order execution. The historical
    Backtester consumes the previous bar's signal on the current bar;
    preserve the agreed causal timing and do not execute a
    just-generated completed-bar signal on that same bar unless the
    documented runtime contract explicitly defines an equivalent
    next-bar execution convention.
-   The decision response should include, at minimum: pair,
    decision/signal bar timestamp, canonical signal, phase, SL
    percentage, TP percentage, strategy composition, MPML revision, and
    enough provenance to reproduce the decision.
-   Runtime inputs must contain only information available at the
    decision timestamp. Exclude forward-looking research labels such as
    `next_return`, `next_direction`, and `next_direction_binary`.
    `DataLoader.prepare()` creates these labels for ML targets; the
    audited PhaseAware strategy path does not need them.
-   Preserve existing MPML semantics exactly. The inspected phase
    detector uses ADX(14), threshold 25, ATR(14), and a 252-bar rolling
    median of ATR% by default. PhaseAware routes trend phases to TF4 and
    ranging phases to MR42, with existing phase size multipliers. TF4
    uses LWMA direction and stochastic extremes, and forward-fills
    signals; its current `stoch_d`/`smooth_window` wiring must not be
    silently changed. MR42 uses Bollinger Bands plus an ADX filter and
    forward-fills entry direction; the Backtester separately handles
    SL/TP execution. Preserve implementation behavior rather than fixing
    unrelated legacy semantics during integration.
-   Reject malformed history: unordered or duplicate timestamps,
    incomplete current bars, non-finite or missing required OHLC values,
    and insufficient history must fail closed according to the existing
    MRML policy.

#### Scope

1.  Inspect the current `StrategyRunner` protocol and its callers/tests
    before editing.
2.  Confirm the supported MPML entry point, input requirements,
    dependency versions, strategy parameters, causal timing, and output
    semantics.
3.  Pin the MPML source revision and runtime dependency contract used by
    MRML.
4.  Implement the smallest adapter/interface binding needed to return
    the latest canonical decision. Keep MRML risk, execution, order
    lifecycle, and journaling behavior unchanged.
5.  Add runtime-boundary tests, including prefix parity against
    canonical MPML outputs.
6.  Retain the frozen historical EURUSD replay/hash/accounting test
    unchanged as a separate regression test.

#### Required validation

-   **Determinism:** repeated evaluation over identical input history
    yields identical decision output and provenance.
-   **Causality:** for multiple historical cutoffs, the adapter only
    sees data available at each cutoff and its latest decision matches
    the canonical MPML result computed from that prefix.
-   **Prefix parity:** compare signal, phase, SL%, TP%, signal
    timestamp, and composition metadata. Include a carried-forward
    signal, signal reset/exit, phase transition, and indicator warm-up
    case.
-   **No label leakage:** tests demonstrate that future-label columns
    are neither required nor consumed by the runtime adapter.
-   **Timing:** explicitly test the relationship between the signal bar
    and the earliest permitted execution bar; no accidental same-bar
    execution.
-   **Fail-closed behavior:** malformed, stale, duplicated, unordered,
    incomplete, or insufficient inputs result in no actionable decision
    and a clear reason.
-   **Architecture boundary:** daily operation does not invoke MPML
    walk-forward evaluation, model training, strategy ranking,
    Recommendation generation, or the historical `Backtester`.
-   **Regression:** frozen EURUSD historical replay fixture still passes
    exactly; risk, execution, and journaling tests remain unchanged and
    pass.

#### Out of scope

-   Rewriting TF4/MR42 or correcting unrelated legacy
    indicator/parameter semantics.
-   Changing portfolio risk, sizing, broker execution, order lifecycle,
    or journal schema.
-   Adding live state resolution, selector model-serving, or
    behavioral-prediction aggregation to MRML.
-   Optimizing into a bounded window or persistent incremental strategy
    state before equivalence is demonstrated.
-   Combining PR2 with H1-to-D1 aggregation changes; that is Integration
    PR3.

#### Copilot PR prompt reference

When preparing the implementation prompt, instruct Copilot to treat this
PR2 section as the source of truth, inspect the existing
`StrategyRunner` and test architecture before editing, and implement
only the scope above. Require it to report the exact MPML
revision/interface used, list changed files, explain timing and
causality, and show test results. Copilot must not invent an MPML API or
silently duplicate strategy rules in MRML. If the MPML side needs a new
supported interface, have it propose that as a separately reviewable
MPML change rather than hiding it inside the MRML adapter.

### Integration PR3 --- Validate MRML H1-to-D1 market-price-bar parity

**Motivation:** MRML receives broker data at H1 resolution but operates
the v0 PhaseAware strategy on D1 price bars. Different day boundaries,
timestamp handling, or missing-bar policies could change signals even
when the strategy implementation is identical.

**Scope:** - Compare MRML's H1→D1 **price-bar construction** with MPML's
canonical D1 market-data preparation on overlapping historical data. -
Verify timezone and day-boundary conventions, OHLCV aggregation,
incomplete bars, missing hours, weekends, and late data. - Record exact
matching and discrepancy results with reproducible fixtures.

**Acceptance criteria:** - Matching input hours produce matching D1 bars
under the agreed boundary convention. - Missing, incomplete, or
ambiguous data is handled deterministically and fails closed where
required. - Any tolerated differences are explicitly documented and
tested. - Behavioral-prediction H1→D1 aggregation remains an internal
MPML responsibility; MRML does not recreate that layer or independently
construct behavioral features.

### Non-goals for these integration PRs

-   Moving walk-forward evaluation, training, or strategy ranking into
    MRML.
-   Adding live behavioral-state resolution or selector model serving to
    v0.
-   Reimplementing MPML strategy rules in MRML.
-   Changing the risk controller, portfolio policy, broker execution, or
    order lifecycle.
-   Treating historical replay parity alone as proof of live operational
    parity.

The existing numbered foundation phases below retain their original
meaning. This integration sequence does not renumber or redefine them.

## Week 0 (Thu Oct 8 - Fri Oct 9): unblock, don't build

-   Launch MPML runs on the server (A1 first, then A3/A2).
-   Decide initial candidate compositions for v0 bundle consumption.
-   Collect real artifact schema printouts for contracts hardening.
-   Create the MRML repo and enable branch protection on `main`.

## Phase 1: Foundations (Weeks 1-2)

PR1 (Wk1) Scaffold, config, CI, docker-compose Postgres, contracts +
bundle loader against synthetic fixtures. PR2 (Wk1-2) Journal:
SQLAlchemy models, Alembic migrations, repositories, idempotency keys.
**Implemented in current baseline; real-Postgres CI coverage should be
confirmed separately if not already enabled.** PR3 (Wk2) Bundle loader
v2 against real MPML artifacts; harden semantics and staleness/evidence
checks.

## Phase 2: Data, parity, and risk (Weeks 3-4)

PR4 (Wk3) OANDA client (practice only, requests-based, retries,
timeouts) + H1-\>D1 bar builder with MPML-identical boundaries; diff on
overlap. PR5 (Wk3-4) RiskController: flat sizing, currency exposure
caps, kill switch, daily loss limit; property-style tests. PR6 (Wk4)
StrategyRunner integration, gated by the MPML runtime contract and
parity criteria in Integration PR2. No state resolver in v0.

## Phase 3: Execution and burn-in (Weeks 5-6)

PR7 (Wk5) Order manager (deterministic client_order_id), reconciler,
fail-closed behavior, dry-run mode. PR8 (Wk5-6) Daily runner, locking,
run summary, alerting. PR9 (Wk6) Deployment: server setup, scheduler
(cron/systemd), backups, runbook. Start Stage A on OANDA demo.

## Phase 4: Monitoring and operating cadence (Weeks 7-8)

PR10 (Wk7) Monitoring/reporting: slippage vs assumed costs, daily parity
checks, per-pair outcome report. PR11 (Wk8) Operational hardening:
reconciliation diagnostics, alert quality, runbook drills, restore-test
cadence.

## Later (post-A1/A3 evidence)

-   Alternative-composition bundles (config changes only where
    possible).
-   Sentiment ingest and shadow state logging for research.
-   Model-serving decision for selector path (out of v0).
-   Parallel MPML provenance improvements (dataset_version, data
    start/end, fingerprint).

## Superseded plan note

The previous roadmap structure (including state-resolver-first
sequencing in older Phase 2-5 text) was superseded by the 2026-10-08
architecture/ADR updates and consolidated here into a single canonical
plan.
