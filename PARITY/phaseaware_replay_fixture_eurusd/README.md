# EURUSD PhaseAware replay fixture

This directory contains a frozen one-fold D1 input fixture and reference outputs
from the canonical MPML standalone PhaseAware path.

- `input_bars.csv`: 157 processed D1 rows, 2012-02-10 through 2012-08-10.
- `fixture_manifest.json`: provenance, fold boundaries, strategy/execution
  contract, source revision, and input/output hashes.
- `expected/`: frozen signals, execution timeline, trades, accounting
  diagnostics, reconciliation summary, and metrics.
- `export_phaseaware_replay_fixture.py`: reruns the canonical MPML strategy and
  backtester to regenerate the expected artifacts.

The reference is for `src.strategies.PhaseAwareStrategy("TF4", "MR42")` plus
`src.strategies.Backtester`, not `StrategySelector_Dynamic`'s PhaseAware-labelled
routing mode. Strategy indicators are calculated on the fold slice only, matching
the audited MPML path. The source revision is pinned to
`90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d`; the fixture input SHA-256 is
`ad5e491e1062fd67683f25646f33fc8d874917181ce580ecfa831bdddb0e6834`.

## Checks

The normal MRML test suite validates that the fixture and its expected outputs
are present, hash-pinned, and accounting-reconciled. To also regenerate outputs
through MPML, activate the dependency environment belonging to the pinned MPML
checkout and run from the MRML repository root:

```bash
MPML_ROOT=/path/to/market-phase-ml \
MPML_PYTHON=/path/to/market-phase-ml/.venv/bin/python \
pytest -m parity -q
```

The integration test requires `MPML_ROOT` to point to a Git checkout at the exact
pinned revision. Set `MPML_PYTHON` to the Python executable in that checkout's
virtual environment so the exporter uses MPML's installed dependencies. The test
adds the MPML root to `PYTHONPATH`, executes the exporter from that checkout, and
requires byte-for-byte hashes to match the frozen artifacts. MRML does not vendor
MPML's strategy implementation or its `ta` dependency.

## Accounting note

MPML deducts entry costs from capital separately; `TradeResult.pnl` records the
exit-side PnL only. The accounting identity is therefore:

`initial_capital + sum(recorded_trade_pnl) - sum(entry_costs) = final_equity`.

The reference has 9 trades, final equity `10341.322204572449`, entry costs
`7.104734293818441`, and zero reported final-equity reconciliation error.
Timestamps remain naive exactly as represented in MPML's processed D1 input;
do not reinterpret them as UTC-aware values.
