# ADR-001: MPML artifact semantics
Status: OPEN. Decide in PR3.
Question: What exactly do surface_id/state_id/strategy_id/evaluation_id mean
for a recommendation, and how is the pair dimension represented (column,
metadata, or per-pair evaluation_id)?
Action: print schemas of a real bundle; write the answer here; make the
contracts match.

# ADR-002: Runtime dependency on MPML's selector
Status: OPEN. Decide in PR6.
Question: When the recommendation says "PhaseAware_TF4_MR2", does the MPML
walk-forward behaviour depend on the XGBoost selector (confidence gating,
hysteresis, minimum hold, ATR% guard) at inference?
If yes: MRML must run the selector, which means a model artifact and its
feature schema become part of the bundle.
If no: MRML only needs the strategy logic and a lookup.
Default assumption until verified: yes, it depends. Do not assume otherwise.

# ADR-003: Sentiment cadence parity
Status: OPEN. Decide in PR10.
Question: The frozen calibration was built on 3 sentiment points per day. Which
hourly timestamps correspond to those, and how is the state computed at the
D1 decision time? Replace nothing silently: document the mapping and add a test.

# ADR-004: Bar boundaries
Status: OPEN. Decide in PR4.
MPML's broker_csv path aggregates on fixed UTC+1 day boundaries. Build D1 from
H1 candles with identical boundaries. Verify OANDA candle parameters (alignment,
timezone, price component: bid/mid/ask) against OANDA's documentation. Do not
use OANDA's default daily candles unless proven equivalent.

# ADR-005: Hosting, secrets, backups
Status: PROPOSED. See ARCHITECTURE.md section 12.

# ADR-006: State resolution and recommendation semantics
Status: ACCEPTED (2026-10-08)
Finding: In per-state MPML bundles, baseline PhaseAware rows are identical
across states and to the default baseline; only the selector row varies.
state_id labels the DL artifact loaded, not a conditioning variable.
Decision:
- v0 does not resolve live state to choose a strategy.
- Strategy per pair is a fixed, versioned config (frozen before first trade).
- Bundles supply provenance and candidate evidence only.
- Current state may be logged as shadow data.
- State-conditioned selection requires new MPML evidence (G6) and, for the
  selector, live MSML inference. Both are out of v0 scope.
  Supersedes: ADR-002/003 for v0 (both deferred to the model-serving stage).
