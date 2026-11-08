# MRML Architecture (v0.3 canonical, 2026-10-23)

Supersedes prior planning drafts and the transitional v0.2 + amendment
layout.

## 1. Purpose

MRML is the execution and risk layer of the pipeline BSVE -\> MSML -\>
MPML -\> MRML.

-   MPML answers: which strategy has historically worked best in this
    state?
-   MRML answers: given today's executable context and risk limits,
    should we act, and how much?

MRML v0 is a forward-test harness. Its first job is to establish whether
the walk-forward system survives live execution (parity, slippage,
operational failure modes). It is not a proof of edge.

## 2. Principles

1.  Fail closed. Any missing, stale, or inconsistent input means no new
    trades.
2.  Trade what was tested. No execution path is introduced ad hoc in
    MRML.
3.  One source of strategy logic: MPML, through a pinned and explicitly
    supported runtime interface. Do not assume that a batch historical
    backtester is a per-decision runtime API.
4.  Research artifacts and runtime code are distinct interfaces:
    Recommendations and provenance are versioned research inputs; the
    strategy runtime supplies current signals. Neither interface
    transfers walk-forward responsibility to MRML.
5.  Everything is journaled: signals, risk decisions, orders, fills,
    state/shadow, artifact versions, and config hash.
6.  Research datasets stay immutable. MRML never writes to them.
7.  Deterministic, replayable daily run. Same inputs give same
    decisions.

## 3. Two clocks

SLOW (weeks/offline): MPML performs research and walk-forward
evaluation, then exports Recommendation and manifest/provenance
artifacts.

FAST (daily, after D1 close): MRML validates the frozen
bundle/configuration, constructs broker-derived D1 **price bars**,
obtains a current signal through the supported pinned MPML
strategy-runtime interface, applies MRML risk controls, executes if
approved, and journals the result.

A Recommendation bundle is a versioned research input and slow prior.
MRML never runs walk-forward evaluation, training, strategy ranking, or
Recommendation generation. The daily runtime must not invoke MPML's
historical Backtester.

## 4. Components

-   contracts: typed MPML artifact schemas, bundle loader, validation.
-   data: OANDA client (thin, requests-based), bar builder (H1-\>D1 with
    MPML-identical boundaries), optional research feeds.
-   strategy: StrategyRunner adapter for a pinned, supported MPML causal
    strategy-runtime interface (PhaseAware-first in v0). Its contract
    must be distinguished from MPML's batch historical `Backtester`; if
    MPML does not expose a suitable runtime entry point, define that
    contract before binding it.
-   risk: RiskController (pure functions, no I/O).
-   execution: order manager (idempotent), reconciler.
-   journal: Postgres repositories + migrations.
-   runner: daily orchestration, dry-run mode, alerting.

### Strategy-runtime contract (Integration PR2; see [ADR-007](adr/ADRs.md#adr-007-causal-phaseaware-strategy-runtime-interface))

The v0 runtime composition is the pinned canonical MPML
`PhaseAwareStrategy("TF4", "MR42")`; MRML must not maintain a second
implementation of its phase or strategy rules. The adapter must call an
explicitly supported MPML-owned causal decision interface. The
historical `Backtester` is a batch execution/evaluation component and
must not be called from the daily operating loop.

The decision input is an ordered, validated history of completed D1 OHLC
bars plus only the non-price fields that the canonical MPML decision
path requires. Forward-looking research labels such as `next_return`,
`next_direction`, and `next_direction_binary` are not runtime inputs.
The response must identify the bar timestamp that generated the decision
and include the canonical signal, phase, and SL/TP percentages, along
with the pinned MPML revision and strategy configuration needed for
provenance.

Preserve canonical semantics exactly, including phase-specific size
multipliers, TF4 stochastic/LWMA behavior, MR42 Bollinger/ADX behavior,
and forward-filled signal state. In particular, do not silently change
TF4's current parameter wiring while integrating it. Signal generation
and execution timing are separate: the historical Backtester consumes
the previous bar's signal on the current bar, and MRML must preserve the
agreed causal timing without same-bar look-ahead.

Do not assume that a 252-bar window is sufficient merely because the
volatility classifier uses a 252-bar rolling median. Indicator warm-up
and forward-filled signal state can require earlier history. Start with
recomputation from the available historical prefix; only introduce
bounded-window or incremental-state optimization after parity tests
demonstrate equivalence. Invalid, stale, unordered, duplicated, or
insufficient history must fail closed.

**Implementation status (2026-10-30):** MRML contains a tested boundary
for validating explicitly attested completed OHLCV bars, cutoff, and
typed runtime output. MRML supplies no phase or other prepared field;
those belong to the MPML-owned runtime.

**MPML binding (2026-11-08):** `MpmlPhaseAwareRuntime` calls
`mpml.runtime.phaseaware.latest_decision` at MPML
`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`, verifying the checkout's git
HEAD and clean tree (not MPML's `MPML_REVISION` override). Call path:
`StrategyRunner` -> `MpmlPhaseAwareStrategy.generate_signal` ->
`PhaseAwareDecisionAdapter` -> `MpmlPhaseAwareRuntime` -> MPML runtime
(causal preparation, `MarketPhaseDetector`, `PhaseAwareStrategy("TF4",
"MR42")`). The decision is for the latest completed bar and is available
at that bar's close (`timestamp + 1 day` for a D1 open timestamp); it is
not shifted. Warm-up is 126 bars, derived by MPML (not the 252-bar
volatility window); fewer bars raise `InsufficientStrategyHistory`.

Mapping to `SignalIntent`: sign is direction, `abs(signal)` is recorded as
`size_multiplier` (the MPML phase multiplier; MRML does not apply it), and
`sl_pct`/`tp_pct` (fractions of entry price) and timestamps are carried
through. `stop_distance` is `sl_pct` times the latest completed close, a
unit conversion rather than an entry price. TF4 supplies `sl_pct = 0`, so a
non-flat TF4 decision raises `UnexecutableDecision`, which halts the daily
run as `STRATEGY_DECISION_INVALID`; MRML does not invent a stop. History
must start at a stable date because the signals are forward-filled state.
`DailyRunner` is not wired to this strategy by default.

## 5. Data model (Postgres operational store)

Tables: artifact_bundles, runs, bars_h1, bars_d1 (derived),
state_observations (optional/shadow), signals, risk_decisions, orders,
fills, positions_snapshot, pnl_snapshots, shadow_events,
config_snapshots.

Rules: - every row carries run_id - signals reference bundle_id and
evaluation_id where applicable - orders use deterministic
client_order_id = hash(run_date, pair, strategy_id, direction) so a
re-run cannot double-submit

## 6. Daily run (idempotent)

1.  Lock; create run row (config hash, bundle hash, code version).
2.  Reconcile broker positions/orders with the journal. Mismatch -\>
    halt, alert.
3.  Seed/append D1 price bars from approved historical data and broker
    H1→D1 aggregation using the agreed MPML-compatible day boundaries.
    Gaps, incomplete bars, or late data that violate policy -\> halt.
4.  Validate bundle and configuration: schema, counts, referential
    integrity, provenance, and any configured evidence-end rule.
5.  For each configured pair, obtain the current signal from the
    supported MPML PhaseAware runtime interface using only data
    available at the decision time. Do not run walk-forward evaluation
    or the historical Backtester.
6.  RiskController approves/rejects; journal every decision with reason.
7.  Execute (demo only until explicitly changed). Journal fills and
    slippage vs manifest costs.
8.  Snapshot equity, drawdown, exposure; send run summary.

## 7. Risk controller (v0)

Single profile, configurable, flat sizing: - risk per trade: default
0.5% equity (MPML backtests may assume 1%; difference logged) - max
concurrent positions (global and per pair) - max net exposure per
currency (risk units) - equity drawdown kill switch (halts new entries,
does not force liquidation) - daily loss limit - stale-bundle block

Deferred: state-conditional sizing, multi-profile, multi-user.

## 8. State and recommendation semantics (ADR-006)

No live state resolution in v0.

In current MPML per-state bundles, state_id labels which DL artifact fed
the selector path; it does not condition baseline PhaseAware metrics in
a way MRML needs for v0 runtime routing.

Therefore in v0: - pair set and composition are versioned config frozen
before first trade - bundles supply provenance, parity targets, and
assumed costs - state may be logged as shadow/diagnostic data only -
behavioral/selector runtime use requires new MPML evidence and
model-serving design

## 9. Bundle consumption

Research bundle artifacts are loaded and validated by contracts. The
public research-artifact interface is MPML's Recommendation plus the
associated manifest/provenance and bundle contents; `StrategyEvaluation`
remains MPML's canonical historical evidence layer.

MRML enforces guarantees including: - unique IDs - rank and referential
integrity constraints - known schema version - count/provenance
consistency

Rank-1 is not, by itself, a trading decision. It is interpreted in
context of selection methodology and parity evidence. The
research-artifact interface is separate from the pinned strategy-runtime
interface used to obtain a current signal. Neither permits MRML to rerun
evaluation or independently rank strategies.

## 10. Rollout

-   Stage A: PhaseAware TF4/MR42 baseline set, dry-run then OANDA demo.
-   Stage B: alternative compositions from new bundles (configuration
    change only).
-   Stage C: selector + behavioral features only after model-serving
    design and evidence updates.
-   Live capital: separate decision, outside v0 scope.

## 11. Non-goals (v0)

MRML does not run MPML walk-forward evaluation, train models, rank
strategies, generate Recommendations, or execute the historical
Backtester in the daily loop. MRML does not reproduce MPML's H1→D1
behavioral-prediction aggregation. Other non-goals include dashboards,
multi-user, Java service, intraday execution, live ANN inference,
state-conditional sizing, broker-2-dependent runtime logic, and non-FX
instruments.

## 12. ADR status map

-   ADR-001: RESOLVED (artifact semantics, including pair dimension)
-   ADR-002: RESOLVED for v0 path (no selector runtime dependency in
    PhaseAware-first flow)
-   ADR-003: DEFERRED (sentiment cadence/model-serving stage)
-   ADR-004: OPEN (bar boundary convention and broker/OANDA equivalence)
-   ADR-005: PROPOSED (hosting, secrets, backup policy)
-   ADR-006: ACCEPTED (state and recommendation semantics)

## 13. Success criteria for v0 forward test

-   Zero unreconciled broker/journal mismatches.
-   Historical replay baseline: the frozen canonical MPML fixture
    reproduces its hash-pinned signals, execution timeline, trades,
    diagnostics, and metrics.
-   Runtime parity: after the runtime contract is established, MRML's
    per-decision signals match the agreed canonical MPML signal fixtures
    on identical available history; historical batch replay alone does
    not satisfy this criterion.
-   Market-bar parity: after H1→D1 validation, MRML price bars match
    MPML's agreed D1 boundary and aggregation convention on overlapping
    data.
-   Live demo signals on day N match dry-run recompute on the same bars.
-   Slippage and spread per fill are measured and compared with MPML
    assumed costs.
-   Every skipped trade has a logged reason.
-   No conclusions about edge until sufficient sample size; report trade
    counts and quality diagnostics, not only P&L.
