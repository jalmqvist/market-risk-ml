# MRML Roadmap (v0.6, 2026-11-19)

> **Current status (2026-11-20):** A1-IG is merged and operationally verified within its read-only scope: the IG demo diagnostic completed authentication, account discovery, explicit EUR/USD instrument validation, and retrieval of 32 H1 bid/ask observations. A2 defines the offline canonical H1 contract and IG adapter. A3-S0 audited IG demo H1 timestamp semantics. A3-S1 adds verified PostgreSQL storage for source revisions and canonical H1 interpretations. Incremental ingestion remains open. No execution operations were exercised.

## Scope baseline and guiding principle (v0)

MRML is an independent, minimalistic trading, risk, and operational
platform. Its first objective is to establish a reliable end-to-end
connection to an IG Markets demo account: retrieve market and account
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
from its own price data. Differences between IG and the historical
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
   account, position, and transaction state. IG demo is the first
   operational implementation.

IG may initially implement both market-data and execution interfaces.
That convenience must not couple the canonical MRML data contract to
IG-specific details. Likewise, recommendations must remain distinct
from executable orders: MRML independently applies risk limits, checks
freshness and validity, verifies broker constraints, and may refuse a
recommendation.

No live capital is in scope for v0. All broker-connected development
must explicitly use the IG demo environment until a separate
future decision changes that boundary.

### Durable persistence architecture — PostgreSQL (decision 2026-11-19)

**Decision:** PostgreSQL is the preferred primary durable store for MRML's
operational records **and** broker market data. This extends the existing
SQLAlchemy/Alembic journal and database infrastructure; it does not replace
IG as the authority for actual account balances, orders, fills, and positions.
MRML's stored broker state must be reconciled with fresh broker reads.

The initial implementation may use one PostgreSQL instance with logically
separated market-data and operational tables. Separate databases, specialised
time-series extensions, and distributed services are not prerequisites. The
canonical `MarketDataProvider` and price models must remain usable without
PostgreSQL or SQLAlchemy, including in deterministic offline tests.

**Planned persistence responsibilities:**

- **Source observations:** retain original IG bid/ask OHLC values, source EPIC,
  timeframe, source timestamp, response metadata where useful, retrieval time,
  and provenance sufficient to reproduce canonical transformations. Do not
  invent volume or broker-confirmed completeness fields absent from the source.
- **Canonical data:** store explicitly versioned MRML instrument mappings,
  interval boundaries, price-component and derivation policies, completion and
  availability evidence, and validation results. Permit deterministic rebuilding
  when interpretation or normalization rules change.
- **Ingestion state:** track bounded request windows, successfully committed
  intervals, gaps and their evidential classification, provisional observations,
  failures, and available quota information. Checkpoints must advance only
  after durable, validated writes; retries and restarts must be idempotent.
- **Operational state:** retain independent risk decisions, order intents,
  broker confirmations, fills, reconciliation outcomes, and audit history in
  the existing journal architecture. These records must not be conflated with
  market-data observations.

Use stable source keys (provider, source instrument ID, resolution, interval)
and explicitly defined update/revision semantics. Repeated retrievals of a
provisional candle must not silently destroy earlier evidence. Avoid writing
credentials, session tokens, private account details, or unredacted sensitive
responses to the database or logs.

**Sequencing:** A2 defines persistence-compatible canonical structures,
identity, temporal semantics, and validation with no database migrations.
A3 implements PostgreSQL market-data tables, incremental ingestion, recovery,
and quota-aware reuse of stored history. Later milestones reuse the same
durable infrastructure for execution and reconciliation. A database is not a
prerequisite for the existing A1 diagnostic.

**Quota rationale:** IG's default historical-data allowance is 10,000 price
data points per week (subject to account/API policy). Routine H1 ingestion for
a small FX universe should be comparatively modest when each completed
interval is fetched once; repeated overlapping history downloads and bulk
backfills can exhaust the allowance. Prefer cached/local history and bounded
catch-up requests, with explicit accounting for re-fetches, provisional bars,
and historical-data quotas. Do not equate the historical-price allowance with
all REST or streaming operations.

## Existing MPML integration — implementation record (2026-11-10)

This section records the MPML runtime and historical-replay integration
work already completed. It is retained for provenance and future reuse;
it does not define the new MRML product objective or critical path. MRML's
primary purpose is now to provide an independent broker-connected trading
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
for broker data and execution milestones.

## Broker selection decision — IG demo (2026-11-17)

**Decision:** IG Markets demo replaces OANDA practice as the initial operational
broker for the platform-first roadmap. The platform remains broker-independent;
this is a change of adapter and deployment target, not of MRML's responsibilities
or the A → B → C → D → E → F delivery sequence.

**Reason and evidence:** API availability for the intended OANDA practice
integration is constrained by regional/account arrangements, so credentialed
OANDA connectivity was not established. This is an access limitation, not a
failure of the existing client or its offline tests. IG Markets provides a
viable demo API integration path. A preliminary IG demo `POST /session`
authentication check on 2026-11-17 returned HTTP `200` with both `CST` and
`X-SECURITY-TOKEN` response headers present.

The preliminary result was superseded by the **A1-IG read-only operational
verification on 2026-11-18**, after PR #5 was merged: the diagnostic completed
authentication, account discovery, explicit EUR/USD EPIC validation, and a
bounded 32-observation H1 bid/ask history request. This does not verify
current-price ingestion, candle finality, execution, or reconciliation. Do not
record secrets, session tokens, or account-specific identifying information in
repository files or logs.

**OANDA retention:** GitHub PR #4 (`feat: add OANDA practice connectivity
diagnostic (A1)`) was merged on 2026-11-17. The read-only
`src/mrml/data/oanda.py` client, `python -m mrml.data.oanda_probe`, offline
tests, and documentation are preserved as implemented evidence. A1-OANDA passed
its reported offline checks and GitHub CI; authenticated API connectivity
was not verified. Its operational adoption is parked, not deleted and not represented
as completed broker connectivity. A separate documented decision would be
required to resume it as an operational provider.

**Broker-specific design constraints:** IG uses API-key/session-token
credentials and instrument EPIC identifiers, rather than OANDA's account and
instrument conventions. Keep these at the IG adapter boundary. The first IG
client should be independent of `OandaClient`; do not subclass it or reuse
OANDA-specific response models. The future canonical MRML representation must
handle bid/ask price components, timestamp/availability and completion
semantics, broker instrument metadata, and provenance without assuming a
universal mid-price convention. Respect applicable IG historical-price quotas
and rate limits; prefer bounded requests and eventual incremental local
persistence rather than bulk reconstruction by default.

**Configuration and secrets:** Supply IG demo credentials through environment
variables (`MRML_IG_API_KEY`, `MRML_IG_USERNAME`, `MRML_IG_PASSWORD`, with
`MRML_IG_ENVIRONMENT=demo`). A local Git-ignored `.env` file may supply these
values at a command-line entry point. Commit only an empty `.env.example`;
never commit `.env`, credentials, CST, or X-SECURITY-TOKEN. Session credentials
are ephemeral and must be managed in memory. There is no live endpoint option
in v0. A1-IG loads local `.env` values at its CLI boundary, with exported
environment variables taking precedence.

## Delivery plan: platform first, recommendations second (updated 2026-11-19)

The previous strategy-integration-first sequence remains superseded. The
critical path is to establish a minimal real **IG demo trading platform**, then
connect recommendations through a replaceable provider interface. Historical
MPML compatibility and strategy benchmarking are not prerequisites.

### Existing implementation and reusable work

The repository already contains configuration/contracts, journal schema and
persistence, risk components, injected orchestration, price-bar transformations,
a tested MPML causal-runtime adapter, and merged read-only OANDA and IG demo
adapters. Preserve these. Neither the OANDA adapter nor the MPML runtime is an
operational dependency of A2/A3. Existing database scaffolding is reusable,
but production PostgreSQL market-data ingestion has not yet been implemented.

The MPML runtime binding targets
`mpml.runtime.phaseaware.latest_decision` at pinned revision
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`. The real-runtime test run
recorded 5 passed and 1 expected failure; synthetic prefix tests matched
at 69 cutoffs. The frozen EURUSD fixture's known phase-only prefix
divergence remains documented. TF4 can emit non-flat decisions with
`sl_pct = 0`, which MRML currently refuses as `UnexecutableDecision`;
no stop policy has been approved. `DailyRunner` is not yet wired to this
provider by default. These facts do not block IG connectivity.

The earlier historical EURUSD H1-to-D1 comparison remains compatibility
evidence: 175,790 H1 rows, 6,609 bars from the MRML aggregation path,
6,575 processed MPML D1 rows, 6,575 matching common dates, 34
aggregation-only dates, and no processed-only dates. Neither IG nor any
future provider is required to reproduce that historical broker's bar
conventions. Investigate coverage or cutoff differences only when relevant.

### Milestone A — IG demo connectivity and market-data ingestion

**Purpose:** make IG demo account and market data available through MRML's
independent market-data boundary, with observable, verified behavior.

**A1-IG — read-only connectivity:**

The standalone `python -m mrml.data.ig_probe` diagnostic implements demo
session authentication, a sanitized account list, explicit EUR/USD EPIC
selection, and a bounded H1 bid/ask sample. The implementation was merged in
PR #5. Offline tests cover the workflow with simulated IG responses. A
credentialed, read-only run on 2026-11-18 completed all stages and returned
`Result: PASS`, with 32 H1 observations and historical allowance metadata.
A1-IG is complete within this bounded read-only scope; canonical data,
operational ingestion, and execution remain separate milestones.

**A2-IG — canonical market-data contract:**

The offline contract defines immutable FX and H1 models, an explicit IG product
mapping, a pure adapter, and sequence and eligibility diagnostics. The
provider-neutral read protocol describes bounded H1 windows. IG timestamp
boundary semantics still require source evidence before operational ingestion.
See [Canonical H1 market data](MARKET_DATA.md) for the schema, policy choices,
revision keys and A3 prerequisites.

**A3-IG — incremental ingestion and diagnostics:**

- **A3-S0 complete.** The demo audit documents the empirical H1 start-label
  evidence and same-label revisions for the audited EPIC. Product-wide
  semantics, finality, and first availability remain unresolved. See the
  [audit](audit/IG_H1_TIMESTAMP_SEMANTICS.md).
- **A3-S1 implemented and verified.** The additive SQLAlchemy/Alembic schema
  stores revision-aware source observations and versioned H1 interpretations
  with transactional retries and deterministic as-of reads. Isolated
  PostgreSQL 16 tests verify migrations, exact numeric and UTC timestamp
  round trips, concurrency, and reconstruction. See the
  [storage contract](MARKET_DATA.md#postgresql-persistence).
- **A3-S2 pending.** Retrieve bounded missing or provisional H1 intervals, rather than repeatedly
  downloading overlapping history. Validate and commit data transactionally;
  advance checkpoints only after successful commits. Make duplicate requests,
  retries and restarts idempotent, with explicit revision handling.
- Support bounded historical and latest-price retrieval, quota-aware retries,
  local reuse/caching, and conservative catch-up behavior. Do not undertake
  unbounded backfills to populate the initial database.
- Track quota metadata, observed gaps, retrieval failures, session health,
  freshness, and continuity through concise read-only CLI/status output.
- Ensure valid market closures do not trigger spurious gap-repair loops;
  unresolved trading-session gaps remain visible and fail closed when relevant.
- Verify recovery and refusal of stale or insufficiently supported data.
  Establish product-specific quote units before any normalization. Use
  offline and isolated database tests in CI.

**Milestone A acceptance evidence:** A1-IG's credentialed read-only diagnostic
is complete. A2 has offline coverage for deterministic, source-faithful canonical
bars and explicit eligibility and continuity semantics. A3 must demonstrate
bounded historical/current ingestion with PostgreSQL persistence, quota-aware requests,
idempotency, restart recovery and fresh-status diagnostics. Tests must cover
normal and failure paths and demo/live separation. No order submission is
needed to complete Milestone A.

**Deliverable:** tested, PostgreSQL-backed IG demo market-data ingestion behind
MRML's provider boundary. A1-IG is complete; Milestone A remains open until
A2 and A3 acceptance evidence is established.

### Milestone B — Position and order lifecycle on IG demo

**Purpose:** demonstrate the complete demo position lifecycle independently
of any strategy or recommendation provider.

Scope:

- Retrieve account positions, working orders, activity/deal history and
  available broker identifiers through IG's documented read APIs.
- Submit a deliberately small, explicitly authorized **demo** test order;
  do not enable autonomous order submission at this stage.
- Confirm acceptance/rejection, fills, deal confirmation and resulting
  broker exposure using fresh reads; distinguish requests from confirmed fills.
- Close and, where needed, modify positions and protective instructions,
  consistent with IG's instrument, account and order semantics.
- Reconcile IG deal references/identifiers with MRML's internal journal and
  establish duplicate-submission protection under ambiguous responses.
- Test rejections, timeouts, partial/uncertain outcomes, retries, restarts,
  and broker-state reconciliation; no blind retry of ambiguous writes.
- Use the existing PostgreSQL operational journal for durable requests,
  responses, fills and reconciliation outcomes, without storing credentials,
  session tokens, or unredacted sensitive account data.

Acceptance evidence:

- An authorized IG demo test opens and closes a small position and verifies
  the outcome through an independent fresh broker read.
- Duplicate and ambiguous requests cannot silently create extra exposure.
- Restart/reconciliation tests detect journal/broker mismatches.
- Demo endpoints and account checks are enforced; live trading is unavailable.

**Deliverable:** a minimal, auditable IG demo order and position lifecycle.

### Milestone C — Independent risk and operational safeguards

**Purpose:** ensure MRML controls execution eligibility regardless of the
provider of a trading instruction.

Scope:

- Establish conservative sizing with IG-specific units and contract semantics
  translated at the broker boundary.
- Enforce configured exposure and portfolio limits, daily loss limits,
  and a kill switch.
- Validate margin/available funds, minimum deal size, precision, dealing
  rules and broker constraints before submission where supported.
- Require protective levels or an explicitly approved alternative policy;
  do not invent a stop policy to make TF4 executable.
- Reject stale data, invalid instruments, malformed recommendations,
  duplicate intents, and unsafe account or session state.
- Journal every refusal with its reason; fail closed on uncertainty.
- Treat broker state as authoritative for actual positions and fills and
  reconcile it against MRML's journal.

Acceptance evidence:

- Unit/integration tests cover risk limits, size, kill switch and refusals.
- Reconciliation detects missing, duplicated and mismatched state.
- Failures default to no new exposure.

**Deliverable:** an IG demo-connected platform with independent risk
controls and recoverable operational state.

### Milestone D — Minimal end-to-end platform proof of concept

This is the first milestone at which MRML should be considered a working
trading platform, even though it is not yet strategy-driven.

Provide a small documented terminal workflow that:

- connects to IG demo and validates market/account data;
- accepts a deliberately explicit test instruction;
- applies MRML risk and eligibility checks;
- opens, monitors, and closes a demo position;
- reconciles broker state against the journal; and
- emits a concise report with timestamps, EPIC/instrument, deal identifiers,
  fills/costs where available, risk decisions and final position state.

It must not depend on MPML, MSML, a research bundle or a GUI. The manual test
trigger is scaffolding, not an approved production strategy.

**Acceptance evidence:** a repeatable IG demo lifecycle test including restart
and reconciliation, with no live endpoint or capital access.

**Deliverable:** a minimal IG demo trading platform.

### Milestone E — Generic recommendation interface and initial MPML provider

Only after Milestone D is operational, introduce recommendations.

Scope:

- Define provider-neutral `RecommendationProvider` objects carrying
  instrument, direction/intent, decision time, earliest eligibility,
  provenance/version, expiry/freshness and protective/sizing constraints.
- Keep recommendations separate from orders or broker instructions.
- Implement MPML as the first provider using the existing supported
  causal PhaseAware runtime where appropriate.
- MRML builds its own IG-derived price history and market-phase context.
  MPML's existing TF4/MR42 conventions may be a provisional starting point,
  not a permanent or uniquely correct partition of trend-volatility space.
- Record and later evaluate IG-versus-historical-broker price differences
  rather than forcing artificial MPML price parity.
- Keep MRML risk authoritative: any recommendation may be rejected or ignored.
- Preserve causal timing, phase-definition/version, instrument mapping,
  source/granularity and MPML revision in the audit trail.

Acceptance evidence:

- Provider contract tests are independent of MPML internals.
- MPML mapping/provenance/timing and invalid/stale-output refusals are tested.
- Risk and execution tests run independently of recommendation providers.
- Accepted, rejected and ignored recommendations have journal reasons.

**Deliverable:** MPML-backed recommendations behind a replaceable interface.

### Milestone F — Integrated demo forward test and research refinement

Scope:

- Run IG market data → MRML market-phase interpretation → MPML
  recommendation → MRML risk → IG demo execution.
- Maintain complete decision, order, fill, position and reconciliation logs.
- Benchmark outcomes against simple baselines and alternative phase
  definitions in separately controlled research and evaluation work.
- Evaluate differences between IG price data and historical MPML broker data;
  document, rather than force, parity.
- Incorporate MSML evidence without moving training/research into MRML.
- Add scheduling and concise operational reporting only once the manual
  lifecycle is reliable.

**Deliverable:** a stable IG demo forward test with MPML as the initial
recommendation provider. This is not authorization for live-capital trading.

### Critical path and explicit non-goals

The critical path remains **A → B → C → D → E → F**, now on IG demo.
Broker data and operations, risk, and reconciliation precede strategy
integration. MPML historical parity, full strategy benchmarking and new
MSML phase research are not prerequisites for the platform PoC.

Outside initial scope:

- live-capital trading and live IG endpoints;
- feature-rich GUIs or retail-terminal imitation;
- selector serving or behavioural-state resolution;
- behavioural aggregation, model training, walk-forward evaluation or
  strategy ranking inside MRML;
- bulk IG historical backfills that risk consuming broker quotas;
- specialized time-series databases, external message brokers, or distributed
  storage infrastructure before the ordinary PostgreSQL implementation is
  demonstrated;
- premature optimization, deployment and scheduling before manual proof.

Historical replay, synthetic prefix tests, real MPML runtime tests and the
historical H1-to-D1 comparison remain distinct compatibility evidence. None
substitutes for IG demo platform acceptance tests.

### Parked compatibility work — historical MPML data conventions

The former H1-to-D1 parity milestone remains optional compatibility research,
not a platform gate. Revisit only when explaining recommendation differences
or supporting a controlled historical benchmark, using the relevant source's
actual trading calendar, intervals, timestamps, availability and missing-data
policy. The existing MRML 24-hour D1 assembly and calendar-consecutive
`DailyRunner` checks were built for a constrained compatibility workflow; they
are not evidence that MPML requires weekend bars or that IG must provide one
bar for every calendar hour. Audit actual MPML D1 and source-broker conventions
before changing either interface. Do not force IG-derived MRML canonical bars
to reproduce another broker's historical conventions solely for nominal parity.

## Original foundation schedule (historical; superseded)

**Archive note (2026-11-17):** OANDA references below are retained verbatim
as historical planning records. They are not current implementation targets.

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
