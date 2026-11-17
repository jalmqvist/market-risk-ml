# MRML Roadmap (v0.4, 2026-11-12)

## Scope baseline and guiding principle (v0)

MRML is an independent, minimalistic trading, risk, and operational
platform. Its first objective is to establish a reliable end-to-end
connection to an OANDA practice account: retrieve market and account
data, open and manage positions, close positions, and reconcile broker
state with its own journal.

MRML must remain useful without MSML or MPML. Those repositories are
upstream research and recommendation providers, not authorities over
MRML's market-data, risk, execution, or broker contracts. Integration
with MPML is planned after the basic trading platform is operational.

The initial strategy/phase interpretation may reuse MPML's current
TF4/MR42 trend-volatility conventions as a practical starting point.
These definitions are provisional research choices, not ground truth
or permanent MRML architecture. MRML will calculate its own market phase
from its own price data. Differences between OANDA and the historical
broker/data used by MPML are acceptable for the proof of concept and are
not a reason to block platform development. Their effects should be
measured later through benchmarking and back-testing.

### Product and interface design principles

- Keep the platform minimalistic in both implementation and appearance.
- Prefer a small, clear operational interface and concise status/report
  views over a feature-rich graphical trading terminal.
- Do not imitate the layout, charting, or feature set of an existing
  retail or professional trading platform.
- Do not build a GUI as a prerequisite for the proof of concept. A
  command-line workflow, configuration files, structured logs, and
  compact operational summaries are sufficient initially.
- Prioritise correctness, observability, safety, and recoverability over
  visual polish or convenience features.

### Core architectural boundaries

Keep these interfaces independent and replaceable:

1. **MarketDataProvider:** supplies broker or other source market data;
   MRML normalises it into its own canonical price/bar representation.
2. **RecommendationProvider:** supplies strategy recommendations.
   MPML is the first planned provider, not a mandatory dependency of the
   platform.
3. **BrokerExecutionProvider:** submits and manages orders and retrieves
   account, position, and transaction state. OANDA practice is the first
   implementation.

OANDA may initially implement both market-data and execution interfaces.
That convenience must not couple the canonical MRML data contract to
OANDA-specific details. Likewise, recommendations must remain distinct
from executable orders: MRML independently applies risk limits, checks
freshness and validity, verifies broker constraints, and may refuse a
recommendation.

No live capital is in scope for v0. All broker-connected development
must explicitly use the OANDA practice environment until a separate
future decision changes that boundary.

## Existing MPML integration — implementation record (2026-11-10)

This section records the MPML runtime and historical-replay integration
work already completed. It is retained for provenance and future reuse;
it does not define the new MRML product objective or critical path. MRML's
primary purpose is now to provide an independent OANDA-connected trading
and risk platform. MPML is an optional upstream recommendation provider
that will be integrated after the core platform proof of concept.

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

**MRML owns platform operation:** broker-facing market-data ingestion,
canonical price-bar construction, independent phase interpretation,
risk controls, broker interaction, operational journaling,
reconciliation, and fail-closed behavior. Strategy recommendations
may be supplied by MPML or another provider through a replaceable
interface; MRML does not depend on MPML to operate its core platform.

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

**Historical evidence:** historical replay parity is established for the
frozen EURUSD fixture at the pinned MPML revision. The opt-in parity
test was run in a detached worktree at MPML commit
`90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d` and passed
(`1 passed, 68 deselected`). A separate baseline run on the newer
checkout was reported byte-identical, but the pinned-revision run
remains the canonical historical regression check. This proves
historical fixture reproducibility; it does **not** prove per-decision
runtime parity.

**MRML PR2 status at 2026-11-10:** the OHLCV-only boundary is bound to
MPML's merged causal runtime (`mpml.runtime.phaseaware.latest_decision`,
MPML `ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`). MRML supplies no phase
or other prepared field and calculates no MPML feature, indicator or
signal. Not yet wired into `DailyRunner`. Evidence, kept separate:

-   Fake-module binding tests run in CI (mapping, error handling,
    revision verification). They are not MPML parity.
-   Real-MPML tests need `MPML_ROOT` at the pinned revision and skip
    otherwise. They were run against a real git clone at
    `ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57` (HEAD verified, tracked
    tree clean, no bypass): `test_mpml_runtime_integration.py` 5 passed,
    1 xfailed (the strict fixture-prefix test); `test_mpml_binding.py`
    22 passed. Synthetic causal-prefix parity against MPML's canonical
    detector and strategy matched at 69 cutoffs (126 to 600 bars),
    covering all four phases. A fresh local rerun on 2026-11-10 used
    `/tmp/mpml/market-phase-ml`, verified at the pinned revision with a
    clean tracked tree; `MPML_ROOT=/tmp/mpml/market-phase-ml python -m
    pytest tests/test_mpml_runtime_integration.py -rs` completed with
    `5 passed, 1 xfailed` in 12.89 seconds. The xfail is the strict
    frozen-fixture full-prefix parity test, whose known phase-only
    divergence is characterized by a separate passing test. An initial
    attempt to run pytest from the MPML directory collected MPML's own
    suite and failed collection because `ta` and `matplotlib` were
    missing; that was not an MRML integration-test failure. The targeted
    MRML test command is the valid evidence.
-   Historical causal-prefix fixture: **not investigated.** No search
    for suitable historical D1 OHLCV data with sufficient prehistory was
    carried out, and no separate historical causal-prefix fixture was
    created. Causal-prefix evidence is therefore synthetic only, plus
    the frozen-fixture comparison above, which is not a valid oracle.
    This remains open work.
-   Historical replay (separate evidence): `test_replay_parity.py`
    passed (3 tests) against a separate git-verified checkout at the
    fixture generation revision
    `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`. Its pin is unchanged and
    it is not run against the runtime revision.
-   CI: a dedicated `mpml-runtime` job clones MPML at the runtime
    revision, verifies HEAD and clean tracked tree, and runs the real
    integration and binding tests; a skipped real-MPML test fails it.
-   Frozen EURUSD fixture prefixes (126 to 157 bars, 32 prefixes): 10
    match and 22 differ, all in `phase` only (signal direction and SL/TP
    agree). The fixture's `adx`/`atr`/`phase` were computed on a longer
    upstream history, so they cannot be reproduced from its 157 OHLCV
    bars. This is recorded as a strict `xfail`; the fixture is
    unchanged.
-   Open decision: TF4 has no fixed stop, so its non-flat signals are
    refused as unexecutable until a stop policy is agreed.

The following scaffold baseline is historical (taken before the MPML
binding above): `make test` passed (`68 passed, 1 skipped`); the skip is
canonical historical replay because `MPML_ROOT` was not set.
`pytest -m parity -q` reported `1 skipped, 68 deselected`. `mypy src`
passed. Baseline `ruff check .` reported 16 existing `E501` violations
in the replay exporter and replay parity test; `ruff format --check .`
reported those same two existing files unformatted. No application
startup command is declared; configuration requires `MRML_DATABASE_URL`,
and actual broker/database services were not exercised. Tests use
offline stubs and local SQLite only.

The exercised scaffold components are the contracts/configuration,
price-bar transformations, pure risk gates, injected daily/strategy
orchestration, and journal schema tests on temporary SQLite. OANDA
requests are mocked; no credentialed service is called. The application
has no declared CLI/startup entry point, and the daily runner requires
injected market-data, reconciliation, risk-context, journal, bundle, and
strategy implementations.

After adding the boundary, `make test` passed (`92 passed, 1 skipped`);
`tests/test_strategy_runtime.py` passed all 24 interface tests and the
fixture test module reported `2 passed, 1 skipped`.
`pytest -m parity -q` reported `1 skipped, 92 deselected`, so canonical
historical replay was not run. The new and directly affected Python
files pass Ruff lint and format checks, and `mypy src` passes.
Full-repository Ruff continues to report only the same 16 baseline
`E501` violations.

The source-dependent evidence listed here at the time of the baseline
(preparation path, required fields, 126-bar warm-up, signal semantics,
and a causal per-decision entry point independent of the `Backtester`)
is now supplied by the MPML runtime at `ef6b1b8`. Remaining limits: the
frozen fixture is not a valid OHLCV-only oracle (22 of 32 prefix
mismatches, phase only); TF4 can emit non-flat signals with
`sl_pct = 0`, which MRML refuses as `UnexecutableDecision` because no
stop-loss policy is approved; `DailyRunner` is not wired to the
MPML-backed strategy by default; PR3 H1-to-D1 parity is separate.

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

#### Out of scope for the completed PR2 integration

-   Rewriting TF4/MR42 or correcting unrelated legacy indicator/parameter
    semantics as part of the adapter.
-   Adding live state resolution, selector model-serving, or
    behavioral-prediction aggregation to MRML.
-   Moving risk, sizing, broker execution, order lifecycle, or journaling
    into MPML.
-   Treating this adapter as a required dependency for OANDA platform
    connectivity or execution.

#### Historical implementation prompt note

The original PR2 implementation prompt treated this section as the
source of truth for the MPML adapter. That instruction applied to PR2
only. Future platform work must follow the platform-first delivery plan
below and must not expand MPML coupling or make MPML parity a prerequisite
for OANDA data and execution milestones.

## Delivery plan: platform first, recommendations second (2026-11-12)

The previous strategy-integration-first sequence is superseded. The
critical path is now to establish a minimal but real OANDA practice
trading platform, then connect recommendations through a replaceable
provider interface. Historical MPML compatibility and strategy
benchmarking are not prerequisites for the platform proof of concept.

### Existing implementation and reusable work

The repository already contains configuration/contracts, a journal
schema and persistence layer, risk components, injected orchestration,
price-bar transformations, and a tested MPML causal-runtime adapter.
The MPML adapter and its tests are retained as reusable integration
work, but they are not the next critical-path task and must not dictate
MRML's canonical data or execution contracts.

The MPML runtime binding targets
`mpml.runtime.phaseaware.latest_decision` at pinned revision
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`. The real-runtime test run
recorded 5 passed and 1 expected failure; synthetic prefix tests matched
at 69 cutoffs. The frozen EURUSD fixture's known phase-only prefix
divergence remains documented. TF4 can emit non-flat decisions with
`sl_pct = 0`, which MRML currently refuses as `UnexecutableDecision`;
no stop policy has been approved. `DailyRunner` is not yet wired to this
provider by default. These facts describe the existing integration,
not a blocker to OANDA connectivity or order-lifecycle work.

The previous historical EURUSD H1-to-D1 comparison also remains as
historical compatibility evidence: 175,790 H1 rows, 6,609 bars from the
MRML aggregation path, 6,575 processed MPML D1 rows, 6,575 common dates
matching on the compared fields, 34 aggregation-only dates, and no
processed-only dates. This comparison is not a requirement that OANDA
must reproduce MPML's historical broker conventions. The 34-date
coverage question and historical timestamp-label/cutoff question may be
revisited if they become relevant to a specific provider or evaluation.

### Milestone A — OANDA connectivity and market-data ingestion

**Purpose:** establish a working, observable connection to an OANDA
practice account and make broker data available through MRML's own
market-data interface.

**A1 implementation record (2026-11-17):** public read-only account and
instrument methods and `python -m mrml.data.oanda_probe` are implemented.
Offline tests verify practice-only requests, response validation, retries,
CLI outcomes, and redaction. No credentialed practice request has been
performed; real connectivity remains a separate manual acceptance step
([procedure](../README.md#a1-read-only-oanda-practice-diagnostic)). Canonical
bar semantics, provenance, and availability remain A2 work; Milestone A
is not complete.

Scope:

- Confirm practice-account authentication and environment selection.
- Retrieve account summary, account details, and instrument metadata.
- Retrieve historical candles and current/latest available market data.
- Define the smallest useful `MarketDataProvider` contract and an
  OANDA implementation.
- Normalise timestamps, instrument identifiers, candle completeness,
  bid/ask or mid-price fields, and price precision into MRML's canonical
  representation. Preserve source timestamps and provenance.
- Establish explicit handling for stale, missing, incomplete, duplicated,
  or out-of-order data.
- Build and maintain the price-bar resolutions needed by the initial
  platform workflow. Do not impose MPML's fixed UTC+1 historical day
  boundary as a universal MRML convention.
- Add concise CLI diagnostics or status output for connectivity and
  data freshness; no elaborate GUI is required.

Acceptance evidence:

- A documented command can authenticate against the practice endpoint
  and retrieve account and instrument information.
- Historical and current market data can be retrieved reproducibly.
- Canonical bars have explicit interval, timestamp, completion, and
  availability semantics.
- Tests cover normalisation, missing/stale data, malformed candles,
  retries, and practice/live endpoint separation.
- No order submission is needed to complete this milestone.

**Deliverable:** a tested OANDA practice connectivity and market-data
path behind MRML's provider boundary.

### Milestone B — Position and order lifecycle on OANDA practice

**Purpose:** prove that MRML can operate the complete broker position
lifecycle independently of any strategy or recommendation system.

Scope:

- Retrieve open positions, pending orders, trades, and transaction
  history.
- Submit a deliberately small, controlled practice order from an
  explicit test command or approved test workflow.
- Confirm order acceptance, fills, resulting trade/position state, and
  broker transaction identifiers.
- Close positions and verify the resulting broker state.
- Support the required order amendments/protective-order operations
  exposed by the selected OANDA account/instrument workflow.
- Use deterministic client identifiers where supported and protect
  against duplicate submissions.
- Handle rejections, partial fills, timeouts, ambiguous responses,
  retries, process restarts, and reconciliation.
- Keep a durable journal of requests, broker responses, fills, position
  changes, and reconciliation outcomes, without storing credentials.

Acceptance evidence:

- A practice-account test can open and close a position and verify the
  resulting state through a fresh broker read.
- Repeated or ambiguous submissions cannot silently create duplicate
  exposure.
- Restart/reconciliation tests recover broker state and identify
  journal discrepancies.
- Practice endpoint and credentials are enforced; live trading is
  explicitly unavailable in v0.

**Deliverable:** a minimal, auditable OANDA practice order and position
lifecycle.

### Milestone C — Independent risk and operational safeguards

**Purpose:** ensure MRML controls what it is willing and able to execute,
regardless of the source of a trading instruction.

Scope:

- Establish conservative position sizing and instrument-aware units.
- Enforce configured exposure and portfolio limits, daily loss limits,
  and a kill switch.
- Validate margin/available funds and broker trading constraints before
  submission where the API exposes the required information.
- Require protective-order parameters or an explicitly approved
  alternative risk policy before opening exposure. Do not invent a stop
  policy to make a strategy executable.
- Reject stale data, invalid instruments, malformed recommendations,
  duplicate intents, and unsafe account states.
- Make all refusals and their reasons auditable.
- Ensure broker state is authoritative for actual positions and fills;
  the MRML journal is reconciled against it.

Acceptance evidence:

- Unit and integration tests cover risk boundaries, sizing, kill switch,
  and refusal paths.
- Broker/journal reconciliation detects missing, duplicated, and
  mismatched state.
- Failures default to no new exposure.

**Deliverable:** a broker-connected platform with independent risk
controls and recoverable operational state.

### Milestone D — Minimal end-to-end platform proof of concept

This is the first milestone at which MRML should be considered a
working trading platform, even though it is not yet strategy-driven.

Provide a small documented workflow that can:

- connect to OANDA practice;
- retrieve and validate market and account data;
- accept an explicit test instruction (or a deliberately simple
  temporary test trigger);
- apply MRML risk and execution checks;
- open, monitor, and close a practice position;
- reconcile broker state with the journal; and
- emit a concise run summary with timestamps, instrument, order/trade
  identifiers, fills, costs where available, risk decisions, and final
  position state.

The workflow should be usable from a terminal and should not depend on
MPML, MSML, a research bundle, or a GUI. The test trigger is scaffolding
for proving the platform, not a production strategy.

**Acceptance evidence:** a repeatable practice-account lifecycle test,
including restart and reconciliation, with no live endpoint or live
capital access.

**Deliverable:** a minimal OANDA practice-account trading platform.

### Milestone E — Generic recommendation interface and initial MPML provider

Only after Milestone D is operational, introduce strategy
recommendations.

Scope:

- Define a provider-neutral `RecommendationProvider` contract. A
  recommendation should identify instrument, direction/intent,
  decision time, earliest eligibility time, strategy/provider identity
  and version, validity/freshness, and any required protective levels or
  sizing constraints.
- Keep recommendations separate from orders and broker commands.
- Implement MPML as the first provider using the existing supported
  causal PhaseAware runtime where appropriate.
- MRML constructs its own price history and independently determines
  the current phase. Initially, it may use MPML's current TF4/MR42
  trend-volatility definitions as a provisional convention.
- Allow MPML's strategy recommendation to be used even where OANDA's
  price feed and broker conventions differ from MPML's historical
  source. Record the source and differences; do not make exact historical
  parity a go/no-go criterion for the platform.
- Keep MRML's risk engine authoritative. An MPML recommendation is not
  an instruction to submit an order and can be rejected by MRML.
- Preserve causal timing and provenance, including the input-data source,
  bar interval, phase definition/version, MPML revision, and recommendation
  timestamp.

The initial phase partition is a practical starting point, not a claim
that TF4/MR42 is the uniquely correct division of trend-volatility
space. MSML behavioural research may reveal alternative or evolving
structures. Those findings should inform later experiments, not block
this first operational integration.

Acceptance evidence:

- Provider contract tests do not depend on MPML internals.
- MPML adapter tests verify mapping, provenance, causal timing, and
  refusal of malformed/stale outputs.
- MRML risk and execution tests run independently of the provider.
- A recommendation can be accepted, rejected, or ignored with a clear
  journal reason.

**Deliverable:** MPML-backed recommendations operating through a
replaceable provider interface.

### Milestone F — Integrated practice forward test and research refinement

Scope:

- Run OANDA market data → MRML phase interpretation → MPML
  recommendation → MRML risk → OANDA practice execution.
- Maintain complete decision, order, fill, position, and reconciliation
  records.
- Compare recommendation outcomes against simple baselines and
  alternative phase definitions through separate back-testing and
  benchmarking work.
- Evaluate the effects of historical MPML broker data versus OANDA
  market data; document differences rather than forcing artificial
  parity.
- Incorporate MSML findings as research evidence. Permit alternative
  phase definitions and recommendation providers without changing the
  core execution platform.
- Add scheduling and a compact operational status/reporting workflow
  only after the manual/command-driven lifecycle is reliable.

**Deliverable:** a stable OANDA practice forward-test using MPML as the
initial recommendation provider, with an evidence trail suitable for
subsequent benchmarking and refinement. This is not authorization for
live-capital trading.

### Critical path and explicit non-goals

The new critical path is **A → B → C → D → E → F**. OANDA connectivity,
data ingestion, order lifecycle, risk, and reconciliation come before
strategy integration. MPML historical parity, full strategy benchmarking,
and new MSML phase research are not prerequisites for the platform PoC.

Keep the following outside the initial platform build:

- a feature-rich GUI, advanced charting, or imitation of an existing
  trading terminal;
- live-capital trading;
- selector model-serving or live behavioural-state resolution;
- behavioural-feature aggregation or research/training pipelines inside
  MRML;
- walk-forward evaluation, strategy ranking, or model training inside
  MRML;
- premature runtime optimisation or elaborate deployment machinery
  before the manual practice workflow is proven.

The historical replay fixture, synthetic causal-prefix tests, real MPML
runtime tests, and historical H1-to-D1 comparison remain distinct
compatibility evidence. None substitutes for the OANDA platform
acceptance tests.

### Parked compatibility work — historical MPML data conventions

The former H1-to-D1 parity milestone is retained as optional
compatibility research, not a platform gate. Revisit only when needed to
explain a recommendation discrepancy or support a controlled historical
benchmark. Any such work should establish the relevant source's actual
bar intervals, timestamps, availability, and missing-data policy. Do not
force MRML's OANDA canonical data model to reproduce a different broker's
historical conventions merely to obtain nominal parity.

## Original foundation schedule (historical; superseded)

The dated Week 0 / Phase 1--4 schedule below is retained only as a
record of the original foundation plan. Its ordering and dependencies
are superseded by the platform-first delivery plan above. In particular,
MPML-identical bar boundaries and strategy integration are no longer
prerequisites for OANDA connectivity or the platform proof of concept.

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

PR4 (Wk3) OANDA practice client (historical and current data, retries,
timeouts) + MRML-owned canonical price-bar normalisation. PR5 (Wk3-4) RiskController: flat sizing, currency exposure
caps, kill switch, daily loss limit; property-style tests. PR6 (Wk4)
Keep strategy integration deferred until the OANDA practice platform
and position lifecycle are proven. No state resolver in v0.

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

## Later (after the platform proof of concept)

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
