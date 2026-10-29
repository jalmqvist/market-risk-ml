# MPML PhaseAware audit

**Original audit date:** 2026-10-16\
**Status updated:** 2026-10-29\
**Scope:** Canonical MPML PhaseAware path for MRML integration and
replay parity.

## Current status

The frozen EURUSD historical replay fixture has been exported and
committed in MRML at `PARITY/phaseaware_replay_fixture_eurusd/`. The
fixture contains 157 D1 input bars and frozen reference outputs for
signals, execution timeline, trades, accounting diagnostics,
reconciliation, and replay metrics.

The historical replay parity test has passed against the pinned MPML
implementation. This establishes reproducibility of the frozen
historical replay; it does **not** establish parity for a new MRML
current-decision runtime. That remains the primary objective of
Integration PR2.

H1-to-D1 aggregation parity remains a separate task for PR3. OANDA and
PostgreSQL integration are outside PR2.

## Baseline provenance

-   MPML run: `run_20261014T160427Z`
-   MPML source revision: `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`
-   Composition: `phaseaware_default` → `PhaseAware_TF4_MR42`
-   Data source: `broker_csv`, source root
    `../market-sentiment-ml/data/input/fx`
-   Timestamp convention: broker-naive timestamps interpreted as fixed
    UTC+1; MPML documents daily boundaries on fixed UTC+1 without DST
-   Walk-forward setup: 7-year training window, 6-month test window,
    6-month step; label horizon 20 bars
-   Backtester assumptions for this baseline: initial capital 10,000;
    spread 1 pip; slippage 0.5 pips per side; commission 0; EURUSD pip
    value 0.0001; legacy signal-magnitude sizing
    (`use_atr_sizing=False`)
-   DL and sentiment were disabled for this baseline.

These are historical parity conditions, not MRML's live risk policy.

## Canonical invocation and implementation boundary

In the audited baseline walk-forward path, `main.py` resolves the
PhaseAware composition, slices a test fold into `df_test`, constructs
the standalone `PhaseAwareStrategy` with the configured experts, calls
`generate_signals(df_test)`, and passes the returned
`(signals, sl_pct, tp_pct)` series to `Backtester.run()`. The standalone
result is stored in `base_res` and contributes to baseline metrics.

This standalone implementation is **not** equivalent to
`StrategySelector_Dynamic`'s PhaseAware-labelled routing mode. The
selector's PhaseAware-labelled branch routes between trend-following and
mean-reversion signals but does not call the standalone
`PhaseAwareStrategy.generate_signals()` or apply its signal multipliers.
A selector timeline is therefore not a valid substitute for standalone
PhaseAware expected outputs.

MPML remains the canonical owner of strategy behavior. MRML should
invoke or adapt that implementation rather than duplicate the strategy
logic.

## Strategy semantics observed in the pinned source

  Phase          Expert family           Signal multiplier
  -------------- --------------------- -------------------
  `HV_Trend`     TF4 trend following                   0.5
  `LV_Trend`     TF4 trend following                   1.5
  `HV_Ranging`   MR42 mean reversion                   0.5
  `LV_Ranging`   MR42 mean reversion                   1.0

-   **TF4:** LWMA(40) direction and Stochastic(5,3,1) extremes; its
    returned SL/TP series are zero.
-   **MR42:** Bollinger Bands(20,2) with an ADX(14) filter below 20; SL
    2.5%, TP 1.25%.
-   Unrecognized phases remain neutral in the standalone PhaseAware
    output.
-   Preserve the canonical implementation's parameters and quirks; do
    not silently correct behavior during integration.

The signal multipliers scale signal magnitude. Their effect on exposure
and PnL depends on downstream interpretation and should not be conflated
with MRML's risk sizing.

## Historical Backtester timing contract

The inspected MPML `Backtester.run()` behavior is:

-   The signal at bar `i-1` is consumed at bar `i`; the first bar is
    forced flat.
-   Execution is at the current bar's close.
-   Current-bar stop/target checks occur before signal-driven exits.
-   SL/TP series are read at the entry bar.
-   An open position is closed at the end of the supplied test fold.
-   PhaseAware expert indicators are calculated from the supplied
    `df_test` fold slice. Do not silently change the scope over which
    these indicators are computed.

This is the **historical MPML backtester contract**, not a specification
to copy MPML's backtester into MRML's daily runtime. PR2 must define the
current-decision timing contract explicitly and test it separately.

## Frozen replay fixture

Location: `PARITY/phaseaware_replay_fixture_eurusd/`

-   Input: 157 processed D1 rows, 2012-02-10 through 2012-08-10
-   Canonical path: `src.strategies.PhaseAwareStrategy("TF4", "MR42")`
    plus `src.strategies.Backtester`
-   Source revision: `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`
-   Input SHA-256:
    `ad5e491e1062fd67683f25646f33fc8d874917181ce580ecfa831bdddb0e6834`
-   Expected trades: 9
-   Initial capital: 10,000
-   Final equity: `10341.322204572449`
-   Entry costs: `7.104734293818441`
-   Final-equity reconciliation error: 0

Frozen expected artifacts and SHA-256 values:

  -----------------------------------------------------------------------------------------------------------------------
  Artifact                                           SHA-256
  -------------------------------------------------- --------------------------------------------------------------------
  `expected/phaseaware_signals.csv`                  `8e332ce64a045d5366a1289db04ee64db08900103784109bbd7dcbe0cc5cdb1a`

  `expected/phaseaware_execution_timeline.csv`       `17739cc67a3d6f91591427b323ecb1a0602fb1cb5dcba97620c2f02021169f13`

  `expected/phaseaware_trades.csv`                   `6fd889791f3d171b8ba3bf52029840b1dc79e633e173c6bfd018c07ff53d9342`

  `expected/phaseaware_accounting_diagnostics.csv`   `0195a6a09b3ec0511a6f81f0f4daec8623eff7cbad4a50403035bdfdda89fdba`

  `expected/replay_metrics.json`                     `0d258818f86e0862407ea906d0918e49907193e3a6bf725369a1d524c6ca1829`
  -----------------------------------------------------------------------------------------------------------------------

The fixture also includes `expected/accounting_reconciliation.json` and
`fixture_manifest.json`; consult the manifest for its complete
provenance and integrity information. Timestamps remain naive as
represented in MPML's processed D1 input; do not reinterpret them as
UTC-aware timestamps.

### Fixture test layers

1.  **Always-on MRML fixture checks** validate fixture presence, hashes,
    row counts, pinned revision metadata, and accounting reconciliation
    without installing MPML into MRML.
2.  **Opt-in canonical replay parity** uses `MPML_ROOT` to identify the
    external MPML checkout and runs the exporter in the pinned MPML
    environment. The generated expected artifacts must match the
    committed hashes byte-for-byte.

Example from the MRML repository root:

``` bash
MPML_ROOT=/path/to/market-phase-ml \
MPML_PYTHON=/path/to/market-phase-ml/.venv/bin/python \
pytest -m parity -q
```

The checkout must be at the exact pinned revision. Do not automatically
regenerate expected outputs in ordinary tests or update the frozen
baseline merely to make a failing test pass. Inspect artifact diffs and
record an intentional fixture-version change if the baseline ever needs
to change.

The canonical replay previously passed (`1 passed, 68 deselected`). That
result is evidence for this fixture and revision only; it is not
current-runtime parity evidence.

## H1-to-D1 boundary audit

The supplied audit compared processed EURUSD D1 data against independent
aggregation of the supplied H1 source. All 6,575 overlapping OHLCV rows
matched exactly; the independent aggregation had 34 additional dates and
no processed-only dates. An alternate UTC interpretation also matched
OHLCV in that comparison, so the audit did not establish a
timestamp-shift transformation.

Preserve MPML's documented fixed UTC+1 daily-boundary convention. PR3
should verify MRML's aggregation directly against the relevant MPML
input bars, including timestamps, OHLCV, missing-bar handling, and
session boundaries. Historical OHLCV agreement alone does not establish
strategy-decision parity.

## PR2 acceptance criteria

1.  The MRML scaffold baseline is recorded before implementation
    changes, including commands run and pre-existing failures.
2.  The adapter invokes the canonical MPML implementation at the pinned
    revision or clearly reports why that cannot yet be done.
3.  The fixed v0 composition remains PhaseAware TF4/MR42; MRML does not
    duplicate strategy, phase, SL/TP, or signal-carry-forward logic.
4.  The runtime accepts validated historical price bars and returns an
    explicit, deterministic current-decision result with timestamp and
    provenance.
5.  Prefix/cutoff tests establish that later bars and forward labels
    cannot affect earlier decisions; compare phase, signal, SL/TP, and
    timestamp with the canonical implementation where defined.
6.  Warm-up, carry-forward, phase transitions, exit/reset behavior,
    malformed input, insufficient history, and fail-closed behavior are
    tested where applicable.
7.  Historical replay parity and current-decision runtime parity are
    reported as separate claims.
8.  The daily MRML runtime does not run MPML training, walk-forward
    evaluation, strategy ranking, Recommendation generation, or the
    historical Backtester.
9.  Risk, sizing, execution, OANDA connectivity, PostgreSQL
    connectivity, and H1-to-D1 aggregation are not expanded as part of
    PR2.

## Related documents

-   `PARITY/phaseaware_replay_fixture_eurusd/README.md`
-   `PARITY/phaseaware_replay_fixture_eurusd/fixture_manifest.json`
-   `PHASEAWARE_REPLAY_PARITY.md`
-   `MPML_MRML_PhaseAware_Investigation_Notes_2026-10-15.md`
-   MRML `ROADMAP.md`, `ARCHITECTURE.md`, and `ADRs.md`
