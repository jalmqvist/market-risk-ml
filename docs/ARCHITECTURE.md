# MRML Architecture (v0.4 platform-first, status updated 2026-11-28)

This document describes the current IG demo platform-first architecture.
[The roadmap](ROADMAP.md) governs delivery status and acceptance; the
original MPML-first adapter and replay evidence below is retained as a
historical, reusable integration record, not the current critical path.

## 1. Purpose

MRML is a minimal, independent broker-connected trading, risk and operational
platform. The first operational objective is a controlled end-to-end **IG demo**
lifecycle: retrieve account and market data, explicitly authorize one small
opening, verify confirmation and exposure, close the known position and
reconcile immutable journal evidence. Live capital and live IG endpoints are
outside v0.

MRML is the eventual execution/risk layer of BSVE → MSML → MPML → MRML, but
does not require either research repository for its first platform proof of
concept. MPML supplies optional future recommendations and owns strategy
research; MRML independently owns execution decisions, sizing, broker data,
account state and risk. Forward-test performance and strategy parity are later
evaluation objectives, not prerequisites for a manual demo lifecycle.

## 2. Principles

1. **Platform first:** prove an operator-controlled IG demo opening, readback
   and closing before recommendation-driven operation.
2. **Fail closed:** missing, stale, conflicting or unverified account, price,
   broker or risk evidence blocks exposure-changing operations.
3. **Broker authority:** IG is authoritative for account state, fills and
   positions; MRML records observations and reconciles them independently.
4. **No ambiguous write retry:** durable claims precede broker mutation; a
   timeout or restart does not justify resubmission.
5. **Account gate is not authorization:** immutable historical claims,
   claim-specific uncertainty, present exposure, operational blockers,
   independent risk approval and operator authorization remain distinct.
6. **Provider boundaries:** preserve replaceable MarketDataProvider,
   BrokerExecutionProvider and RecommendationProvider contracts.
7. **Minimal interface:** explicit terminal commands and concise sanitized
   audit output before GUIs, automation or scheduling.
8. **Separate research:** MPML owns strategy selection, evaluation and its
   causal runtime; MSML owns sentiment research. Neither is required to
   operate the basic platform.

## 3. Operational stages and clocks

**Now — manual IG demo lifecycle:** an operator selects a narrowly bounded
action, MRML verifies an intended demo account and current exposure, applies
reviewed IG dealing/risk constraints, records immutable intent and an atomic
dispatch claim, performs at most one separately authorized broker mutation,
then records acknowledgement, confirmation, independent readback and
reconciliation. Broker writes are **not implemented or authorized** as of
2026-11-28.

**Later — recommendation-driven daily cycle:** MPML produces frozen research
recommendations (slow/offline) and the pinned causal strategy runtime can
supply current signals (at the relevant completed-bar cutoff). MRML validates
the proposed action against its own IG-derived market phase, broker state,
risk, causal timing and account gate. This integration is deferred until the
independent platform demonstration; MPML's historical Backtester must not
become an execution runtime.

The historical OANDA and MPML runtime integration below remains usable
research compatibility infrastructure; it is not the active IG demo
execution path.

## 4. Current components and persistence boundaries

- **IG demo data:** authenticated bounded, read-only broker and market-data
  adapters; provisional timestamp serialization policy and explicit source
  observations in PostgreSQL.
- **Canonical market data:** independent versioned instrument mappings,
  source revisions and H1 interpretation; no forced historical MPML broker
  parity.
- **Legacy journal and strategy scaffold:** run/config, signals, risk
  decisions, orders and floating-point fills; retain APIs without using
  legacy orders as the authoritative broker execution ledger.
- **Execution journal (B2-S1):** immutable scoped intent/fingerprint,
  irreversible dispatch claim, append-only evidence and per-account hold.
- **Broker observations (B2-S2):** immutable account-wide position and
  working-order collections; intent-specific transaction evidence and
  versioned opening correlation with exact replay after restart.
- **Account gates (B2-S3/S4A):** append-only gate transitions, offline claim
  uncertainty resolution, operational restrictions and conservative
  recovery. Revision `0008_successive_claims` supports ordered historical
  claim representation, but the database still refuses successor/CLOSE
  claims on the operational path.
- **Risk:** existing independent pure-risk scaffold; IG-specific order
  size, currency, dealing rules, available funds/margin and exact operator
  authorization remain to be integrated before an order.
- **Recommendations and runtime:** optional MPML bundle validation,
  PhaseAware strategy adapter and historical parity tests; not an
  execution prerequisite.
- **Interface:** small commands/status/audit reports; no trading GUI,
  background scheduler or automatic broker writes.

Milestone A's bounded source-only IG demo ingestion has passed operational
readback; nonempty confirmation/position payloads and a complete position
lifecycle remain unverified. The current schema head is
`0008_successive_claims`. See [B1-S3 lifecycle](design/IG_B1_S3_BROKER_LIFECYCLE.md),
[B2-S3 audit](audit/MRML_B2_S3_ACCOUNT_GATE_RECOVERY.md) and
[Milestone B](ROADMAP.md#milestone-b--position-and-order-lifecycle-on-ig-demo).

### Historical MPML strategy-runtime contract (Integration PR2; see [ADR-007](adr/ADRs.md#adr-007-causal-phaseaware-strategy-runtime-interface))



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

**Boundary status (2026-10-30, superseded by the binding below):** MRML contains a tested boundary
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

**Validation evidence (separate categories):**

-   Real git-verified runtime integration (MPML HEAD
    `ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`, clean tracked tree):
    `test_mpml_runtime_integration.py` 5 passed, 1 xfailed (the strict
    frozen-fixture prefix test); `test_mpml_binding.py` 22 passed.
-   Synthetic causal-prefix parity: 69 cutoffs, all four phases, against
    MPML's canonical detector and strategy. This is synthetic evidence
    only; it does not establish causal-prefix parity on historical data.
-   Frozen EURUSD fixture prefixes (126 to 157 bars): 10 of 32 matched,
    22 phase-only mismatches, because the fixture's phases come from a
    longer upstream history. The fixture is not a valid OHLCV-only oracle.
-   Historical replay, a distinct category: 3 passed against a separate
    checkout at MPML `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`. It
    establishes fixture reproducibility, not current-decision parity.

## 5. Durable storage model (current)

The SQLAlchemy/Alembic operational store contains legacy run, signal,
risk-decision, order, fill and config-snapshot records. IG market-data
storage maintains revision-aware source observations, interpretations,
ingestion attempts and retrieval provenance.

The separate **broker execution ledger** contains `execution_accounts`,
`execution_intents`, `execution_claims`, `execution_evidence`,
`broker_observations`, `broker_assessments`,
`broker_assessment_inputs` and `broker_gate_transitions`.
PostgreSQL constraints, account-row serialization and append-only triggers
protect identity, evidence and gate history. New broker amounts use exact
Decimal/NUMERIC-compatible storage. Full historical claim history remains
immutable; one claim per account currently blocks repeat dispatch.

Do not infer that a legacy run-scoped `client_order_id`, a matched
opening or a replayable assessment independently authorizes a broker
write. Broker reads collected separately do not form an atomic snapshot.

## 6. Near-term execution workflow (planned, not implemented)

1. Operator explicitly selects a single **demo-only** action and account.
2. Verify active session/account, complete and current account exposure,
   instrument/dealing rules, unit size, funds/margin, risk limits and
   action-specific authorization.
3. Persist immutable intent and an atomic durable dispatch claim before
   any network mutation; commit before the broker request.
4. Send one bounded, authorized mutation with no automatic write retry.
5. Preserve returned transaction reference separately from issued reference
   and affected deal IDs; independently retrieve confirmation and exposure.
6. Persist observed facts and versioned reconciliation; preserve holds and
   halt on unknown, mismatched or unreplayable outcomes.
7. Report status without credentials or raw sensitive account IDs.

Before this workflow can run, B2-S4 must implement authenticated operator
issuance, trusted account/session/risk evidence and atomic approval consumption
with claim admission. The current one-operational-claim restriction also needs
a safe, separately authorized full-close route. B2-S4C1–C3A have verified
read-only product evidence and offline contract arithmetic; D1 records the
operator-authorization design but does not implement an issuer. See B3/B4 in the
roadmap. No write operation has yet been implemented or exercised.

## 7. Risk boundary and staged acceptance

A limited but real execution-specific subset of Milestone C must gate the
first B3 demo opening: fresh verified broker identity, complete account
positions/orders, known currency and instrument dealing units, conservative
size/stop rules, available funds and margin, unambiguous risk refusal,
exact operator authorization and demo-only endpoint enforcement. Reject
rather than invent a protective stop or a missing quantity conversion.

Milestone C separately completes broader controls (currency concentration,
portfolio exposure, drawdown, daily loss, kill switch and reporting). No
MPML signal or research recommendation is necessary for B3/B4.

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

## 10. Delivery and rollout (current)

The first **pre-release** acceptance target is one manual IG demo OPEN,
independently verified and then separately authorized full CLOSE, with durable
journal reconciliation and restart-safe uncertainty handling. A's bounded
IG source-only ingestion is accepted; B0–B2 established read-only and offline
persistence/validation, and B2-S4C1–C3A plus D1 have advanced contract and
authorization design. Remaining critical work includes native product-unit
verification, authenticated approval, trusted admission/one-shot transport,
and the safe opening-to-closing transition. Minimum per-order safety checks
precede B3; broader C risk controls, MPML recommendations (E) and integrated
forward testing (F) are deferred. See
[pre-release readiness](audit/MRML_IG_DEMO_PRERELEASE_READINESS_20261128.md).

Old OANDA-first and MPML-first rollout descriptions in the archived
roadmap and earlier ADRs are historical, not operational instructions.

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

## 13. Acceptance criteria and later forward-test evidence

**First platform proof (B3/B4/D):** one explicitly approved small IG demo
position is opened, independently confirmed and read back, then closed
under a separately approved action, with journal/broker reconciliation,
bounded uncertainty handling, restart-safe audit and concise terminal
reporting. Live-capital trading remains impossible in scope.

**Later recommendation/forward-test validation (E/F):** MPML signal
provenance and causal behavior, phase conventions, IG price differences,
slippage and spread versus assumptions, skipped-action reasons, and
ongoing journal/broker parity must be measured independently. Historical
replay fixtures, synthetic strategy prefixes and broker-source ingestion
evidence do not substitute for operational IG demo lifecycle acceptance.
No trading-edge claim is warranted from the initial demonstration.
