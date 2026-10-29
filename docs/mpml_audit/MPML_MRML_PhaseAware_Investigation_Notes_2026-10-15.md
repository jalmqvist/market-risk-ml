# MPML → MRML PhaseAware Investigation Notes

**Investigation started:** 2026-10-15\
**Status updated:** 2026-10-29\
**Status:** Canonical historical replay fixture completed; MRML PR2
runtime integration is the next task.

## Purpose and scope

This document records the investigation into how MRML should invoke
MPML's canonical PhaseAware strategy without changing MRML's risk or
execution path. It separates architectural intent from observed source
behavior and run artifacts.

The initial investigation and frozen EURUSD replay export are complete.
The remaining work is to establish a causal current-decision runtime
interface in PR2, then validate MRML's H1-to-D1 aggregation against
MPML's input convention in PR3.

## Current plan and status

1.  **Inspect the canonical MPML PhaseAware path --- complete.** The
    standalone strategy invocation, expert composition, phase routing,
    and historical Backtester timing were identified in the pinned
    source.
2.  **Export and freeze an EURUSD replay fixture --- complete.** The
    fixture and expected outputs are committed under
    `PARITY/phaseaware_replay_fixture_eurusd/`.
3.  **Implement PR2 runtime integration --- pending.** Establish a thin
    adapter around the pinned MPML implementation and validate causal
    current-decision behavior. Do not alter MRML's risk or execution
    path.
4.  **Validate H1-to-D1 aggregation in PR3 --- pending.** Compare MRML's
    constructed D1 bars with the MPML convention and fixture inputs.

## Architectural boundary

MPML owns strategy research, strategy evaluation, and the canonical
PhaseAware implementation. MRML owns the forward-test execution and risk
layer. For v0, the strategy composition is fixed to PhaseAware TF4/MR42;
do not introduce a live behavioral-state resolver, selector serving
path, or state-conditioned selection.

Keep these questions separate:

-   Can MRML invoke the same PhaseAware decision logic that MPML uses?
-   Can the canonical implementation reproduce a frozen historical
    replay?
-   Does a current-decision runtime use only information available at
    its decision cutoff?
-   Does MRML's H1-to-D1 aggregation reproduce the D1 bars and
    boundaries used by MPML?
-   Are risk, sizing, order handling, broker interaction, and execution
    behavior equivalent? These are not PR2 goals.

The frozen replay proves reproducibility of a historical path. It does
not, by itself, prove that a new MRML current-decision adapter is causal
or equivalent to MPML.

## Canonical MPML path and provenance

-   MPML revision: `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`
-   Reference run: `run_20261014T160427Z`
-   Run experiment: `Gen1_A`, `factorial_v1`, variant `A`
-   PhaseAware policy: `phaseaware_default`
-   Composition: `PhaseAware_TF4_MR42` (TF4 + MR42)
-   Data source: broker CSV; fixed UTC+1 convention as documented by
    MPML
-   Walk-forward configuration: 7-year training, 6-month test, 6-month
    step; label horizon 20 bars
-   DL and sentiment disabled in the reference baseline

For the audited baseline path, `main.py` resolves the PhaseAware
configuration, slices the test fold into `df_test`, constructs the
standalone `PhaseAwareStrategy`, calls `generate_signals(df_test)` to
obtain `(signals, sl_pct, tp_pct)`, and passes those outputs to
`Backtester.run()`. The standalone result is stored as `base_res` and
contributes to baseline metrics.

### Standalone PhaseAware versus selector routing

`StrategySelector_Dynamic` is a separate routing layer. Its
PhaseAware-labelled branch routes between trend-following and
mean-reversion signals, but does not call
`PhaseAwareStrategy.generate_signals()` and does not apply the
standalone PhaseAware multipliers. The selector timeline is not a valid
substitute for standalone PhaseAware expected outputs.

## Strategy semantics observed in the pinned source

  Phase          Expert family             Signal multiplier
  -------------- ----------------------- -------------------
  `HV_Trend`     Trend following (TF4)                   0.5
  `LV_Trend`     Trend following (TF4)                   1.5
  `HV_Ranging`   Mean reversion (MR42)                   0.5
  `LV_Ranging`   Mean reversion (MR42)                   1.0

-   **TF4:** LWMA(40) direction and Stochastic(5,3,1) extremes; its
    returned SL/TP series are zero.
-   **MR42:** Bollinger Bands(20,2), ADX(14) filter below 20; stop-loss
    2.5%, take-profit 1.25%.
-   Unrecognized phases remain neutral in the standalone PhaseAware
    output.
-   Preserve exact canonical parameters and quirks. In particular, do
    not silently substitute selector behavior for standalone PhaseAware
    behavior.

The strategy input frame includes a `phase` column and the expert
strategies' required fields. The complete input-preparation path must
remain tied to the pinned implementation; do not assume that raw OHLC
alone is sufficient without verifying the phase and feature-generation
requirements.

## Historical Backtester timing contract

The inspected MPML `Backtester.run()` behavior:

-   Uses the signal at bar `i-1` at bar `i`; the first bar is forced
    flat.
-   Executes at the current bar's close.
-   Checks current-bar stop/target conditions before signal-driven
    exits.
-   Reads SL/TP values at the entry bar.
-   Closes an open position at the end of the supplied fold.
-   Receives PhaseAware expert outputs computed on the supplied
    `df_test` fold slice.

These semantics describe the historical backtester. PR2 must not call
the historical Backtester as a substitute for a current-decision runtime
and must not copy its execution or risk logic into MRML's daily loop.
The runtime's decision timestamp and completed-bar/actionability
convention must be explicit and tested.

## Frozen EURUSD replay fixture

The fixture is committed at `PARITY/phaseaware_replay_fixture_eurusd/`.

-   Input: 157 processed D1 rows, 2012-02-10 through 2012-08-10
-   Source MPML revision: `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`
-   Input SHA-256:
    `ad5e491e1062fd67683f25646f33fc8d874917181ce580ecfa831bdddb0e6834`
-   Expected trades: 9
-   Initial capital: 10,000
-   Final equity: `10341.322204572449`
-   Entry costs: `7.104734293818441`
-   Final-equity reconciliation error: 0

The `expected/` directory contains frozen signals, execution timeline,
trades, accounting diagnostics, and replay metrics, plus an accounting
reconciliation JSON. Artifact hashes and full provenance are recorded in
`fixture_manifest.json`. See the fixture README for the exact
integration-test command and environment requirements.

Two test layers must remain distinct:

1.  Always-on MRML tests validate fixture presence, hashes, metadata,
    and accounting without installing MPML.
2.  The opt-in parity test uses `MPML_ROOT` and `MPML_PYTHON` to execute
    the exporter in the pinned MPML environment and compare generated
    artifact hashes byte-for-byte.

The canonical historical replay previously passed
(`1 passed, 68 deselected`). Do not describe this as proof of
current-decision runtime parity. Do not regenerate expected outputs
automatically in ordinary tests; a mismatch requires investigation and
an intentional, reviewed fixture-version update.

## H1-to-D1 boundary audit

The prior audit compared processed EURUSD D1 data with independent
aggregation of the supplied H1 source. All 6,575 overlapping OHLCV rows
matched exactly; independent aggregation contained 34 additional dates
and no processed-only dates. An alternate UTC interpretation also
matched OHLCV in the comparison, so the audit did not establish a
timestamp-shift transformation.

MPML documents broker-naive timestamps as fixed UTC+1 and daily
aggregation on fixed UTC+1 boundaries without DST. Preserve that
convention. PR3 should directly compare MRML's H1-to-D1 bars against
MPML inputs, including timestamps, OHLCV, missing-bar handling, and
session boundaries. Do not conflate OHLCV parity with strategy-decision
parity.

## Additional historical artifact audits

The source/run audit also examined the dynamic-selector portfolio
timeline and recommendation/evaluation exports. These checks found
internal accounting consistency in the inspected timeline, no duplicate
`(pair, timestamp, fold)` keys, and `dl_available=false` throughout the
DL-disabled baseline. Pair/fold equity segments reset independently and
should not be concatenated into a single continuous portfolio history
without an explicit aggregation method.

The audited exports contained 30 strategy evaluations and 28 per-pair
recommendations. Their evaluation references resolved,
pair/surface/state fields agreed, and pair-specific ranks were
structurally consistent with the recorded ranking metric.
`state_id="unknown"` was expected for this baseline because no
behavioral state was attached. These findings describe those artifacts;
they do not define MRML's runtime contract or prove candidate
eligibility.

## PR2 implementation requirements

1.  Start by auditing the existing MRML scaffold and recording
    offline-safe baseline test results before making changes.
2.  Use the pinned MPML source revision. If it is unavailable in the
    agent workspace, state which assumptions are supported by this
    document and which require direct source verification.
3.  Implement a thin adapter around canonical MPML behavior; do not
    vendor or duplicate `src/strategies.py`, `src/phases.py`, or other
    strategy logic into MRML.
4.  Preserve fixed v0 PhaseAware TF4/MR42 composition and explicit
    provenance.
5.  Recompute deterministically over the available historical prefix
    initially; do not use a bounded window unless parity tests prove
    equivalence.
6.  Test multiple historical cutoffs to establish causality and compare
    phase, signal, SL/TP, and timestamp against canonical MPML wherever
    defined.
7.  Include warm-up, signal carry-forward, phase transition, exit/reset,
    malformed input, insufficient history, deterministic output, and
    fail-closed behavior where applicable.
8.  Keep historical replay parity separate from current-decision runtime
    parity.
9.  Do not add OANDA or PostgreSQL integration, order placement, live
    state resolution, MPML research workflows, historical Backtester
    calls in the daily loop, or H1-to-D1 aggregation changes to PR2.

If a source-dependent detail cannot be verified, report the specific gap
rather than inventing behavior or weakening parity assertions.

## PR3 scope

After PR2 establishes the runtime interface, validate MRML's H1-to-D1
aggregation against the MPML price-bar convention. Compare timestamps,
OHLC values, session boundaries, missing-bar behavior, and downstream
phase/feature inputs. Do not change risk or execution behavior before
the relevant parity evidence exists.

## Guardrails

-   Treat inspected source and run artifacts as evidence; treat
    architectural documents as intended design unless verified.
-   Do not equate standalone PhaseAware with selector
    PhaseAware-labelled routing.
-   Do not alter risk sizing, portfolio allocation, or execution
    semantics as part of PR2.
-   Do not introduce a live state resolver or state-conditioned strategy
    selection in v0.
-   Do not infer DL participation from the
    `dl_conditional_portfolio_timeline.csv` filename; the reference
    baseline has DL disabled.
-   Keep pair/fold equity histories separate unless an explicit
    portfolio aggregation method is established.

## Related documents

-   `PHASEAWARE_AUDIT_2026-10-16.md` --- concise implementation and
    parity audit
-   `PHASEAWARE_REPLAY_PARITY.md` --- fixture/test contract
-   `PARITY/phaseaware_replay_fixture_eurusd/README.md`
-   `PARITY/phaseaware_replay_fixture_eurusd/fixture_manifest.json`
-   MRML `ROADMAP.md`, `ARCHITECTURE.md`, and `ADRs.md`
