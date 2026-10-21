# PhaseAware replay parity contract

## Purpose

Replace the synthetic replay-parity stub with a real, frozen EURUSD fold from
MPML. The fixture is a historical research harness; it does not change MRML's
forward-test `StrategyRunner`, risk limits, or execution behavior.

## Canonical reference

- MPML revision: `90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`
- Source path: `src.strategies.PhaseAwareStrategy` + `src.strategies.Backtester`
- Composition: TF4 + MR42, default PhaseAware policy
- Fixture: `PARITY/phaseaware_replay_fixture_eurusd/`
- Input: 157 D1 rows, 2012-02-10 through 2012-08-10
- Expected trades: 9
- Final equity: `10341.322204572449` from `10000.0` initial capital
- Accounting status: pass; final-equity reconciliation error is zero

This is the standalone PhaseAware baseline, not `StrategySelector_Dynamic`'s
PhaseAware-labelled routing mode. The test intentionally does not duplicate
MPML strategy logic in MRML.

## Test layers

1. **Always-on fixture checks** verify input/output hashes, output row counts,
   pinned source revision metadata, and accounting reconciliation. These tests
   run without installing MPML into MRML.
2. **Opt-in canonical replay** sets `MPML_ROOT` to a checkout at the pinned
   revision and reruns the fixture exporter inside that checkout. All five
   generated artifact hashes must exactly match the frozen expected artifacts.

Run the opt-in integration check from the MRML repository root, with the pinned
MPML checkout's normal dependency environment active:

```bash
MPML_ROOT=/path/to/market-phase-ml pytest -m parity -q
```

The MPML environment supplies `ta==0.11.0` and MPML's transitive source imports.
MRML does not add these strategy-runtime dependencies to its production
requirements, because the forward-test service should not silently inherit the
historical backtest runtime.

## Accounting semantics

The backtester deducts entry costs from capital separately, while each
`TradeResult.pnl` captures exit-side PnL. Reconciliation uses:

`initial_capital + sum(recorded_trade_pnl) - sum(entry_costs) = final_equity`.

No strategy or accounting code is reimplemented in the test. If canonical replay
hashes change, inspect the artifact diffs and record an intentional fixture
version update rather than rewriting expected outputs automatically.
