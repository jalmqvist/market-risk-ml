# MRML Architecture (v0.3 canonical, 2026-10-12)

Supersedes prior planning drafts and the transitional v0.2 + amendment layout.

## 1. Purpose

MRML is the execution and risk layer of the pipeline BSVE -> MSML -> MPML -> MRML.

- MPML answers: which strategy has historically worked best in this state?
- MRML answers: given today's executable context and risk limits, should we act, and how much?

MRML v0 is a forward-test harness. Its first job is to establish whether the
walk-forward system survives live execution (parity, slippage, operational
failure modes). It is not a proof of edge.

## 2. Principles

1. Fail closed. Any missing, stale, or inconsistent input means no new trades.
2. Trade what was tested. No execution path is introduced ad hoc in MRML.
3. One source of strategy logic: MPML, imported as a pinned dependency.
4. Everything is journaled: signals, risk decisions, orders, fills, state/shadow,
   artifact versions, and config hash.
5. Research datasets stay immutable. MRML never writes to them.
6. Deterministic, replayable daily run. Same inputs give same decisions.

## 3. Two clocks

SLOW (weeks): MPML walk-forward -> recommendations artifacts + manifest
(versioned bundle with provenance and parity targets).

FAST (daily, after D1 close): MRML runner
fetch/construct bars -> bundle checks -> strategy decision -> risk -> execute -> journal.

A recommendation bundle is a slow prior. MRML never re-runs walk-forward.

## 4. Components

- contracts: typed MPML artifact schemas, bundle loader, validation.
- data: OANDA client (thin, requests-based), bar builder (H1->D1 with
  MPML-identical boundaries), optional research feeds.
- strategy: StrategyRunner interface wrapping MPML strategy classes
  (PhaseAware-first in v0).
- risk: RiskController (pure functions, no I/O).
- execution: order manager (idempotent), reconciler.
- journal: Postgres repositories + migrations.
- runner: daily orchestration, dry-run mode, alerting.

## 5. Data model (Postgres operational store)

Tables: artifact_bundles, runs, bars_h1, bars_d1 (derived),
state_observations (optional/shadow), signals, risk_decisions, orders, fills,
positions_snapshot, pnl_snapshots, shadow_events, config_snapshots.

Rules:
- every row carries run_id
- signals reference bundle_id and evaluation_id where applicable
- orders use deterministic client_order_id = hash(run_date, pair, strategy_id, direction)
  so a re-run cannot double-submit

## 6. Daily run (idempotent)

1. Lock; create run row (config hash, bundle hash, code version).
2. Reconcile broker positions/orders with the journal. Mismatch -> halt, alert.
3. Seed/append D1 bars (MPML CSV history + OANDA H1->D1, MPML boundaries).
   Gap or late data -> halt.
4. Bundle checks: schema, counts, FK integrity, provenance, evidence-end rule if configured.
5. For each configured pair: run the configured PhaseAware composition via MPML code
   (price-only; any volatility guard taken from the trailing training-window definition).
6. RiskController approves/rejects; journal every decision with reason.
7. Execute (demo only until explicitly changed). Journal fills and slippage vs manifest costs.
8. Snapshot equity, drawdown, exposure; send run summary.

## 7. Risk controller (v0)

Single profile, configurable, flat sizing:
- risk per trade: default 0.5% equity (MPML backtests may assume 1%; difference logged)
- max concurrent positions (global and per pair)
- max net exposure per currency (risk units)
- equity drawdown kill switch (halts new entries, does not force liquidation)
- daily loss limit
- stale-bundle block

Deferred: state-conditional sizing, multi-profile, multi-user.

## 8. State and recommendation semantics (ADR-006)

No live state resolution in v0.

In current MPML per-state bundles, state_id labels which DL artifact fed the
selector path; it does not condition baseline PhaseAware metrics in a way MRML
needs for v0 runtime routing.

Therefore in v0:
- pair set and composition are versioned config frozen before first trade
- bundles supply provenance, parity targets, and assumed costs
- state may be logged as shadow/diagnostic data only
- behavioral/selector runtime use requires new MPML evidence and model-serving design

## 9. Bundle consumption

Bundle artifacts are loaded and validated by contracts.

MRML enforces guarantees including:
- unique IDs
- rank and referential integrity constraints
- known schema version
- count/provenance consistency

Rank-1 is not, by itself, a trading decision. It is interpreted in context of
selection methodology and parity evidence.

## 10. Rollout

- Stage A: PhaseAware TF4/MR42 baseline set, dry-run then OANDA demo.
- Stage B: alternative compositions from new bundles (configuration change only).
- Stage C: selector + behavioral features only after model-serving design and evidence updates.
- Live capital: separate decision, outside v0 scope.

## 11. Non-goals (v0)

Dashboards, multi-user, Java service, intraday execution, live ANN inference,
state-conditional sizing, broker-2-dependent runtime logic, non-FX instruments.

## 12. ADR status map

- ADR-001: RESOLVED (artifact semantics, including pair dimension)
- ADR-002: RESOLVED for v0 path (no selector runtime dependency in PhaseAware-first flow)
- ADR-003: DEFERRED (sentiment cadence/model-serving stage)
- ADR-004: OPEN (bar boundary convention and broker/OANDA equivalence)
- ADR-005: PROPOSED (hosting, secrets, backup policy)
- ADR-006: ACCEPTED (state and recommendation semantics)

## 13. Success criteria for v0 forward test

- Zero unreconciled broker/journal mismatches.
- Replay parity: MRML signals reproduce MPML trade artifacts on identical historical bars (diff = 0).
- Live demo signals on day N match dry-run recompute on same bars.
- Slippage and spread per fill are measured and compared with MPML assumed costs.
- Every skipped trade has a logged reason.
- No conclusions about edge until sufficient sample size; report trade counts and quality diagnostics,
  not only P&L.
