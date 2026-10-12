"""Generate small synthetic recommendation bundle artifacts for tests."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def make_fixtures(directory: Path = FIXTURE_DIR) -> None:
    """Write deterministic synthetic parquet and manifest files; raises on I/O errors."""
    directory.mkdir(parents=True, exist_ok=True)
    versioned_metadata = json.dumps({"schema_version": "1.0"}, sort_keys=True)
    recommendations = pd.DataFrame(
        [
            {
                "recommendation_id": "rec-eur",
                "evaluation_id": "eval-eur",
                "rank": 1,
                "recommendation_policy": "synthetic-policy",
                "metadata": versioned_metadata,
            },
            {
                "recommendation_id": "rec-gbp",
                "evaluation_id": "eval-gbp",
                "rank": 1,
                "recommendation_policy": "synthetic-policy",
                "metadata": versioned_metadata,
            },
        ]
    )
    per_pair = pd.DataFrame(
        [
            {
                "recommendation_id": "rec-pair-eur",
                "evaluation_id": "eval-eur",
                "pair": "EUR_USD",
                "surface_id": "surface-a",
                "state_id": "state-1",
                "rank": 1,
                "recommendation_policy": "synthetic-policy",
                "metadata": versioned_metadata,
            },
            {
                "recommendation_id": "rec-pair-gbp",
                "evaluation_id": "eval-gbp",
                "pair": "GBP_USD",
                "surface_id": "surface-a",
                "state_id": "state-1",
                "rank": 1,
                "recommendation_policy": "synthetic-policy",
                "metadata": versioned_metadata,
            },
        ]
    )
    evaluations = pd.DataFrame(
        [
            {
                "evaluation_id": "eval-eur",
                "surface_id": "surface-a",
                "surface_version": "v1",
                "state_id": "state-1",
                "strategy_id": "strategy-a",
                "expected_return": 0.012,
                "expected_sharpe": 1.2,
                "expected_drawdown": -0.08,
                "win_rate": 0.55,
                "confidence": None,
                "stability": 0.8,
                "n_folds": 4,
                "n_trades": 120,
                "metadata": json.dumps({"pair": "EUR_USD"}, sort_keys=True),
            },
            {
                "evaluation_id": "eval-gbp",
                "surface_id": "surface-a",
                "surface_version": "v1",
                "state_id": "state-1",
                "strategy_id": "strategy-b",
                "expected_return": 0.009,
                "expected_sharpe": 1.0,
                "expected_drawdown": -0.1,
                "win_rate": None,
                "confidence": 0.7,
                "stability": 0.75,
                "n_folds": 4,
                "n_trades": 98,
                "metadata": json.dumps({"pair": "GBP_USD"}, sort_keys=True),
            },
            {
                "evaluation_id": "eval-aggregate",
                "surface_id": "surface-a",
                "surface_version": "v1",
                "state_id": "state-aggregate",
                "strategy_id": "strategy-aggregate",
                "expected_return": 0.01,
                "expected_sharpe": 1.1,
                "expected_drawdown": -0.09,
                "win_rate": None,
                "confidence": None,
                "stability": 0.77,
                "n_folds": 4,
                "n_trades": 218,
                "metadata": json.dumps({"pair_count": 2}, sort_keys=True),
            },
        ]
    )
    recommendations.to_parquet(directory / "recommendations.parquet", index=False)
    per_pair.to_parquet(directory / "recommendations_per_pair.parquet", index=False)
    evaluations.to_parquet(directory / "strategy_evaluations.parquet", index=False)
    manifest = {
        "experiment_id": "synthetic-experiment",
        "git_sha": "0123456789abcdef",
        "timestamp_utc": "2026-10-01T00:00:00Z",
        "seed": 42,
        "phaseaware_strategy": "synthetic-strategy",
        "costs": {"spread_pips": 1.2, "slippage_pips": 0.3},
        "evaluation_scope": {"strategy_ids": ["strategy-a", "strategy-b"]},
        "evidence_end_date": "2026-09-30",
        "recommendation_per_pair_count": len(per_pair),
        "strategy_evaluation_count": len(evaluations),
    }
    (directory / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    make_fixtures()
