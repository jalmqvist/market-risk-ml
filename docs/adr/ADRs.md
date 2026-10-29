# ADR-001: MPML artifact semantics

Status: RESOLVED (2026-10-08). Question: What exactly do
surface_id/state_id/strategy_id/evaluation_id mean for a recommendation,
and how is the pair dimension represented (column, metadata, or per-pair
evaluation_id)? Action: print schemas of a real bundle; write the answer
here; make the contracts match.

# ADR-002: Runtime dependency on MPML's selector

Status: RESOLVED for the original selector-dependency question;
superseded for v0 runtime scope by ADR-006 (2026-10-08). Question: When
the recommendation says "PhaseAware_TF4_MR2", does the MPML walk-forward
behaviour depend on the XGBoost selector (confidence gating, hysteresis,
minimum hold, ATR% guard) at inference? If yes: MRML must run the
selector, which means a model artifact and its feature schema become
part of the bundle. If no: MRML only needs the strategy logic and a
lookup. Default assumption until verified: yes, it depends. Do not
assume otherwise.

# ADR-003: Sentiment cadence parity

Status: DEFERRED (2026-10-08; model-serving stage). Question: The frozen
calibration was built on 3 sentiment points per day. Which hourly
timestamps correspond to those, and how is the state computed at the D1
decision time? Replace nothing silently: document the mapping and add a
test.

# ADR-004: Bar boundaries

Status: OPEN. Decide in PR4. MPML's broker_csv path aggregates on fixed
UTC+1 day boundaries. Build D1 from H1 candles with identical
boundaries. Verify OANDA candle parameters (alignment, timezone, price
component: bid/mid/ask) against OANDA's documentation. Do not use
OANDA's default daily candles unless proven equivalent.

# ADR-005: Hosting, secrets, backups

Status: PROPOSED. See ARCHITECTURE.md section 12.

# ADR-006: State resolution and recommendation semantics

Status: ACCEPTED (2026-10-08) Finding: In per-state MPML bundles,
baseline PhaseAware rows are identical across states and to the default
baseline; only the selector row varies. state_id labels the DL artifact
loaded, not a conditioning variable. Decision: - v0 does not resolve
live state to choose a strategy. - Strategy per pair is a fixed,
versioned config (frozen before first trade). - Bundles supply
provenance and candidate evidence only. - Current state may be logged as
shadow data. - State-conditioned selection requires new MPML evidence
(G6) and, for the selector, live MSML inference. Both are out of v0
scope. Supersedes: ADR-002/003 for v0 (both deferred to the
model-serving stage).

# ADR-007: Causal PhaseAware strategy-runtime interface

Status: PROPOSED (2026-10-29; implementation contract to be confirmed in
Integration PR2).

## Context

MRML v0 needs a current per-decision signal from MPML's canonical
PhaseAware strategy logic. The frozen EURUSD
`PhaseAwareStrategy("TF4", "MR42")` + `Backtester` fixture establishes
historical replay reproducibility, but does not establish that the batch
Backtester is a supported incremental runtime API.

## Decision direction

-   MPML remains the sole owner of phase detection and strategy rules;
    MRML must not port or independently reimplement them.
-   Integration PR2 must bind MRML to a pinned, explicitly supported
    MPML-owned causal decision interface. If MPML lacks one, define and
    test that interface as a separately reviewable MPML change before
    binding MRML.
-   The daily MRML loop must not call MPML walk-forward evaluation,
    training, ranking, Recommendation generation, or the historical
    Backtester.
-   Runtime inputs contain only information available at the decision
    timestamp; forward-looking target columns (`next_return`,
    `next_direction`, and `next_direction_binary`) are excluded.
-   The response records the signal bar timestamp, signal, phase, SL/TP
    values, strategy composition, MPML revision, and reproducibility
    provenance.
-   Preserve canonical PhaseAware/TF4/MR42 behavior and the agreed
    signal-to-execution timing. The historical Backtester consumes the
    previous bar's signal on the current bar; runtime timing must be
    documented and tested rather than assumed.
-   Begin with deterministic recomputation over the available historical
    prefix. A 252-bar volatility window alone does not prove a
    sufficient history length, because indicator warm-up and
    forward-filled signal state also matter. Optimize to bounded history
    or incremental state only after parity is demonstrated.

## Required validation

-   Prefix-based parity at multiple historical cutoffs for signal,
    phase, SL%, TP%, timestamp, and composition metadata.
-   Tests for carried-forward signals, exits/resets, phase transitions,
    and indicator warm-up.
-   Determinism, exclusion of future labels, explicit timing, and
    fail-closed handling of malformed or insufficient history.
-   Keep the frozen historical replay fixture as a separate regression
    oracle.

## Consequences and non-goals

This ADR does not authorize live state resolution or selector serving;
ADR-006 continues to govern those boundaries. It does not authorize MRML
to execute the historical Backtester in its daily loop, alter
risk/execution/journaling, or silently correct legacy strategy
semantics.
