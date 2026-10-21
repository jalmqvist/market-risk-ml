#!/usr/bin/env python3
"""Generate expected PhaseAware replay outputs inside the pinned MPML runtime.

Run from the MPML repository root so ``src.strategies`` resolves to the audited
implementation. The input CSV is the frozen, one-fold D1 fixture; this script
intentionally does not recalculate phases or precompute strategy indicators on
full history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd
from src.strategies import Backtester, PhaseAwareStrategy, trades_to_dataframe


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("phaseaware_replay_fixture_eurusd/input_bars.csv"),
        help="Frozen one-fold input bars exported during the audit.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("phaseaware_replay_fixture_eurusd/fixture_manifest.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("phaseaware_replay_fixture_eurusd/expected"),
    )
    args = parser.parse_args()
    input_path = args.input_csv.resolve()
    manifest_path = args.manifest.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path, parse_dates=["timestamp"]).set_index("timestamp")
    df.index.name = "timestamp"
    df = df.sort_index()
    if df.empty or not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise SystemExit("Input bars must be nonempty, unique, and sorted.")
    required = {"Open", "High", "Low", "Close", "adx", "atr", "phase", "stop_atr_mult"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise SystemExit(f"Input fixture is missing required columns: {missing}")

    strategy = PhaseAwareStrategy("TF4", "MR42")
    signals, sl_pct, tp_pct = strategy.generate_signals(df)
    backtester = Backtester(
        initial_capital=10_000.0,
        spread_pips=1.0,
        slippage_pips=0.5,
        pip_value=0.0001,
        commission_per_trade=0.0,
        use_atr_sizing=False,
    )
    result = backtester.run(
        df,
        signals,
        "PhaseAware_TF4_MR42_WF",
        sl_pct,
        tp_pct,
    )

    signal_df = pd.DataFrame(
        {
            "timestamp": df.index,
            "phase": df["phase"].astype(str).to_numpy(),
            "raw_signal": signals.to_numpy(),
            "executed_signal": signals.shift(1).fillna(0).to_numpy(),
            "sl_pct": sl_pct.to_numpy(),
            "tp_pct": tp_pct.to_numpy(),
        }
    )
    signal_df.to_csv(output_dir / "phaseaware_signals.csv", index=False)

    execution = result["execution_timeline"].copy()
    if "timestamp" not in execution.columns:
        execution = execution.reset_index()
        if "index" in execution.columns:
            execution = execution.rename(columns={"index": "timestamp"})
    execution.to_csv(output_dir / "phaseaware_execution_timeline.csv", index=False)

    trade_objects = result.get("trades", [])
    trades = trades_to_dataframe(trade_objects)
    trades.to_csv(output_dir / "phaseaware_trades.csv", index=False)

    # Reconstruct MPML's accounting without changing canonical strategy behavior.
    # Backtester deducts entry cost directly from capital, while TradeResult.pnl
    # records the exit-side PnL only. Therefore sum(trade.pnl) alone does not
    # equal final_equity - initial_capital.
    accounting_rows = []
    accounting_capital = float(backtester.initial_capital)
    for trade_index, trade in enumerate(trade_objects, start=1):
        entry_price = float(trade.entry_price)
        entry_cost_fraction = float(backtester._calculate_trade_cost(entry_price))
        capital_before_entry = accounting_capital
        entry_cost_amount = capital_before_entry * entry_cost_fraction
        capital_after_entry_cost = capital_before_entry - entry_cost_amount
        recorded_trade_pnl = float(trade.pnl)
        pnl_pct = float(trade.pnl_pct)
        size_multiplier = float(trade.size_multiplier)
        reconstructed_trade_pnl = (
            capital_after_entry_cost * pnl_pct * size_multiplier
        )
        pnl_reconciliation_error = reconstructed_trade_pnl - recorded_trade_pnl
        capital_after_exit = capital_after_entry_cost + recorded_trade_pnl
        accounting_rows.append({
            "trade_index": trade_index,
            "entry_date": trade.entry_date,
            "exit_date": trade.exit_date,
            "direction": trade.direction,
            "entry_price": entry_price,
            "entry_cost_fraction": entry_cost_fraction,
            "capital_before_entry": capital_before_entry,
            "entry_cost_amount": entry_cost_amount,
            "capital_after_entry_cost": capital_after_entry_cost,
            "pnl_pct": pnl_pct,
            "size_multiplier": size_multiplier,
            "recorded_trade_pnl": recorded_trade_pnl,
            "reconstructed_trade_pnl": reconstructed_trade_pnl,
            "pnl_reconciliation_error": pnl_reconciliation_error,
            "capital_after_exit": capital_after_exit,
            "exit_reason": trade.exit_reason,
        })
        accounting_capital = capital_after_exit

    accounting = pd.DataFrame(accounting_rows)
    accounting.to_csv(output_dir / "phaseaware_accounting_diagnostics.csv", index=False)
    sum_trade_pnl = float(sum(float(t.pnl) for t in trade_objects))
    sum_entry_costs = float(accounting["entry_cost_amount"].sum()) if not accounting.empty else 0.0
    equity_curve = result.get("equity_curve")
    final_equity = float(equity_curve.iloc[-1]) if equity_curve is not None and len(equity_curve) else float(backtester.initial_capital)
    reconstructed_final_equity = float(accounting_capital)
    accounting_error = reconstructed_final_equity - final_equity
    max_abs_trade_pnl_error = (
        float(accounting["pnl_reconciliation_error"].abs().max())
        if not accounting.empty else 0.0
    )
    accounting_summary = {
        "initial_capital": float(backtester.initial_capital),
        "n_trades": int(len(trade_objects)),
        "sum_recorded_trade_pnl": sum_trade_pnl,
        "sum_entry_costs_deducted_separately": sum_entry_costs,
        "equity_gain": final_equity - float(backtester.initial_capital),
        "reconstructed_final_equity": reconstructed_final_equity,
        "reported_final_equity": final_equity,
        "final_equity_reconciliation_error": accounting_error,
        "max_abs_trade_pnl_reconstruction_error": max_abs_trade_pnl_error,
        "identity": "initial_capital + sum(recorded_trade_pnl) - sum(entry_costs) = final_equity",
        "status": "pass" if abs(accounting_error) <= 1e-6 and max_abs_trade_pnl_error <= 1e-6 else "fail",
    }
    if accounting_summary["status"] != "pass":
        raise SystemExit(
            "Accounting reconciliation failed: "
            + json.dumps(accounting_summary, indent=2)
        )

    metrics = {
        "strategy": result.get("strategy"),
        "total_return": result.get("total_return"),
        "sharpe_ratio": result.get("sharpe_ratio"),
        "max_drawdown": result.get("max_drawdown"),
        "win_rate": result.get("win_rate"),
        "profit_factor": result.get("profit_factor"),
        "n_trades": result.get("n_trades"),
        "initial_capital": float(backtester.initial_capital),
        "final_equity": final_equity,
        "sum_recorded_trade_pnl": sum_trade_pnl,
        "sum_entry_costs_deducted_separately": sum_entry_costs,
        "equity_gain": final_equity - float(backtester.initial_capital),
        "accounting_reconciliation_error": accounting_error,
        "accounting_reconciliation_status": accounting_summary["status"],
        "accounting_diagnostics_file": "phaseaware_accounting_diagnostics.csv",
        "input_rows": int(len(df)),
        "input_start": str(df.index.min()),
        "input_end": str(df.index.max()),
        "input_sha256": sha256(input_path),
        "mpml_generation_git_sha": git_sha(),
        "source_run_id": "run_20261014T160427Z",
        "source_run_git_sha": "90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d",
        "strategy_path": "src.strategies.PhaseAwareStrategy + src.strategies.Backtester",
        "accounting_reconciliation": accounting_summary,
        "notes": [
            "Signals and trades were generated on this fold slice only, matching MPML's df_test strategy invocation.",
            "This is the standalone PhaseAware baseline path, not StrategySelector_Dynamic's PhaseAware-labelled routing mode.",
            "Fold-end liquidation is retained because Backtester.run closes any open position at the end of the supplied data.",
            "TradeResult.pnl excludes the entry cost deducted separately from capital; use the accounting diagnostics to reconcile final equity.",
        ],
    }
    (output_dir / "replay_metrics.json").write_text(json.dumps(metrics, indent=2, default=str) + "\n")

    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest["status"] = "expected_outputs_generated_with_accounting_reconciliation"
    manifest["expected_outputs"] = {
        "status": "generated_with_accounting_reconciliation",
        "generation_command": "python phaseaware_replay_fixture_eurusd/export_phaseaware_replay_fixture.py",
        "generation_git_sha": git_sha(),
        "input_sha256": sha256(input_path),
        "output_files": {
            name: sha256(output_dir / name)
            for name in (
                "phaseaware_signals.csv",
                "phaseaware_execution_timeline.csv",
                "phaseaware_trades.csv",
                "phaseaware_accounting_diagnostics.csv",
                "replay_metrics.json",
            )
        },
        "output_dir": str(output_dir),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Generated PhaseAware replay outputs in {output_dir}")
    print(f"rows={len(df)} trades={len(trades)} input_sha256={sha256(input_path)}")
    print(
        "accounting=" + accounting_summary["status"]
        + f" final_equity={final_equity:.6f}"
        + f" entry_costs={sum_entry_costs:.6f}"
        + f" reconciliation_error={accounting_error:.3e}"
    )


if __name__ == "__main__":
    main()
