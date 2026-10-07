# MRML Architecture (v0.2, 2026-10-07)

Supersedes planning_doc.md (July/Sept 2026).

## 1. Purpose

MRML is the execution and risk layer of the pipeline BSVE → MSML → MPML → MRML.

- MPML answers: which strategy has historically worked best in this state?
- MRML answers: given today's state and my risk limits, should I act, and how much?

MRML v0 is a forward-test harness. Its first job is to establish whether the
walk-forward system survives live execution (parity, slippage, operational
failure modes). It is not a proof of edge.

## 2. Principles

1. Fail closed. Any missing, stale, or inconsistent input means no new trades.
2. Trade what was tested. No rule is executed that was not in MPML's
   walk-forward evaluation. Untested ideas are logged (shadow), not traded.
3. One source of strategy logic: MPML, imported as a pinned dependency.
4. Everything is journaled: signals, risk decisions, orders, fills, state,
   artifact versions, config hash.
5. Research datasets stay immutable. MRML never writes to them.
6. Deterministic, replayable daily run. Same inputs give same decisions.

## 3. Two clocks

SLOW (weeks): MPML walk-forward -> recommendations.parquet +
strategy_evaluations.parquet + experiment_manifest.json (versioned bundle).

FAST (daily, after the D1 close): MRML runner
fetch bars -> resolve state -> lookup -> signal -> risk -> execute -> journal.

A recommendation bundle is a slow prior. MRML never re-runs walk-forward.

## 4. Components

- contracts: typed MPML artifact schemas, bundle loader, validation.
- data: OANDA client (thin, requests-based), bar builder (H1 -> D1 with
  MPML-identical boundaries), sentiment reader (Postgres).
- state: StateResolver interface. Implementations: TrendVolResolver (price
  only), BehavioralResolver (frozen BSVE calibration + sentiment).
- strategy: StrategyRunner interface wrapping MPML strategy classes.
- risk: RiskController (pure functions, no I/O).
- execution: order manager (idempotent), reconciler.
- journal: Postgres repositories + migrations.
- runner: daily orchestration, dry-run mode, alerting.

## 5. Data model (Postgres, operational store)

Tables: artifact_bundles, runs, bars_h1, bars_d1 (derived), sentiment_snapshots,
state_observations, signals, risk_decisions, orders, fills, positions_snapshot,
pnl_snapshots, shadow_events, config_snapshots.

Rules: every row carries run_id; signals reference bundle_id and evaluation_id;
orders use a deterministic client_order_id = hash(run_date, pair, strategy_id,
direction) so a re-run cannot double-submit.

## 6. Daily run (idempotent)

1. Acquire lock; create run row (config hash, bundle id, code version).
2. Reconcile broker positions/orders with the journal. Mismatch -> halt, alert.
3. Fetch H1 bars, build D1 with MPML boundaries. Gap or late data -> halt.
4. Check bundle: schema version known, age <= staleness ceiling, eligible_from
   reached. Otherwise no new entries (existing exits still managed).
5. Resolve states per pair/surface. Log every state, including transitions
   (shadow_events: state_transition when state differs from entry state).
6. Lookup top-ranked recommendation for (surface, state, pair).
7. Ask StrategyRunner for entry/exit action using MPML logic.
8. RiskController approves/rejects; every decision journaled with reason.
9. Execute (demo account only until explicitly changed). Journal fills,
   slippage, spread.
10. Snapshot equity, drawdown, exposure. Send run summary.

## 7. Risk controller (v0)

Single profile, configurable, flat sizing:
- risk per trade: default 0.5% of equity (MPML backtests use 1%; the difference
  is logged and is a deliberate demo choice)
- max concurrent positions: total and per pair
- max net exposure per currency (JPY, USD, EUR, GBP, ...) in risk units
- equity drawdown kill switch (halts entries, does not liquidate)
- daily loss limit
- stale-bundle and state-drift blocks (state-drift = state at entry no longer
  matches; v0 logs it only, it does not exit)

Deferred: state-conditional sizing, multiple profiles, multi-user.

## 8. State resolution

TrendVol: computed from D1 bars with the MPML feature code (ATR% median etc.).
Behavioral surfaces: use the frozen BSVE calibration file and the BSVE state
assignment function. Requirements:
- sentiment cadence parity: sample live hourly data at the same timestamps the
  calibration was built on (3 per day), decision recorded in an ADR
- golden test: replaying historical inputs must reproduce BSVE state
  assignments exactly (diff = 0)
- same broker/construction as training data (broker 1). Broker 2 is not used
  for state resolution in v0.

## 9. Recommendation consumption

Bundle = recommendations.parquet + strategy_evaluations.parquet +
experiment_manifest.json. MRML joins on evaluation_id and enforces the MPML
guarantees (unique ids, unique positive ranks, FK integrity, known
schema_version). The pair dimension and exact strategy_id semantics are
provisional until confirmed against MPML (see ADR-001).

Baseline composition (e.g. TF4/MR42 vs TF4/MR2) is chosen by which bundle is
loaded, never hard-coded.

## 10. Rollout

- Stage A: TrendVol only, dry-run then OANDA demo (infrastructure burn-in).
- Stage B: add Reactive-JPY once sentiment ingest and golden tests pass.
- Stage C: add Persistent only after MPML ablations/baseline runs conclude.
- Live capital: separate decision, not part of this plan.

## 11. Non-goals (v0)

Dashboards, multi-user, Java service, intraday execution, live ANN inference
(revisit after the MPML ablation ladder), state-conditional sizing, broker 2
data, non-FX instruments.

## 12. Open decisions (ADRs)

- ADR-001: Recommendation/StrategyEvaluation field semantics and pair dimension.
- ADR-002: Do recommended strategies require MPML's XGBoost selector at runtime?
- ADR-003: Sentiment cadence mapping to training timestamps.
- ADR-004: Bar boundary convention and OANDA candle handling.
- ADR-005: Hosting, secrets, backup policy.
  Default proposal: own server; Postgres via docker-compose; secrets in an
  env file outside the repo (chmod 600), never committed; nightly pg_dump to a
  second location; restore test once a month. The OANDA token for demo and any
  future live account are separate credentials.

## 13. Success criteria for v0 (forward test)

- Zero unreconciled broker/journal mismatches.
- Replay parity: MRML signals reproduce MPML's timeline artifacts on the same
  historical bars (diff = 0 on entries/exits).
- Live demo signals on day N match a dry-run recompute on the same bars.
- Slippage and spread per fill are measured and compared with MPML's assumed costs.
- Every skipped trade has a logged reason.
- No conclusions about edge until there are enough trades. Report trade counts, not just P&L.
