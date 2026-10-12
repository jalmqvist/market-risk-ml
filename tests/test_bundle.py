"""Tests for fail-fast MPML bundle loading."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from mrml.contracts.bundle import load_bundle
from mrml.contracts.errors import (
    DuplicateIdentifierError,
    EvidenceEndDateError,
    ForeignKeyError,
    InvalidBundleError,
    InvalidDtypeError,
    InvalidRankError,
    ManifestCountError,
    MissingBundleFileError,
    MissingColumnError,
    SchemaVersionError,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """Return an isolated copy of the committed synthetic fixture bundle."""
    target = tmp_path / "bundle"
    shutil.copytree(FIXTURES, target)
    return target


def _read_frame(directory: Path, artifact: str) -> pd.DataFrame:
    return pd.read_parquet(directory / artifact)


def _write_frame(directory: Path, artifact: str, frame: pd.DataFrame) -> None:
    frame.to_parquet(directory / artifact, index=False)


def _read_manifest(directory: Path) -> dict[str, Any]:
    return json.loads((directory / "experiment_manifest.json").read_text())


def _write_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    (directory / "experiment_manifest.json").write_text(json.dumps(manifest))


def test_loads_provenance_and_lookups(bundle_dir: Path) -> None:
    bundle = load_bundle(bundle_dir, max_evidence_age_days=10)

    assert len(bundle.strategy_evaluations) == 3
    assert bundle.strategy_evaluations[0].confidence is None
    assert bundle.strategy_evaluations[1].win_rate is None
    assert bundle.strategy_evaluations[2].pair is None
    assert bundle.candidates("EUR_USD")[0].state_id == "state-1"
    assert bundle.evaluation_for("EUR_USD", "strategy-a").evaluation_id == "eval-eur"
    assert bundle.evaluation_for("USD_JPY", "missing") is None
    assert bundle.experiment_id == "synthetic-experiment"
    assert bundle.git_sha == "0123456789abcdef"
    assert bundle.seed == 42
    assert bundle.phaseaware_strategy == "synthetic-strategy"
    assert bundle.spread_pips == 1.2
    assert bundle.slippage_pips == 0.3
    assert bundle.strategy_ids == ("strategy-a", "strategy-b")
    assert bundle.evidence_end_known
    assert bundle.is_trade_eligible(date(2026, 10, 1))
    assert not bundle.is_trade_eligible(date(2026, 9, 30))
    assert not bundle.is_trade_eligible(date(2026, 10, 11))


def test_content_hash_is_stable_across_loads(bundle_dir: Path) -> None:
    assert load_bundle(bundle_dir).content_hash == load_bundle(bundle_dir).content_hash


def test_missing_file_fails(bundle_dir: Path) -> None:
    (bundle_dir / "recommendations.parquet").unlink()
    with pytest.raises(MissingBundleFileError):
        load_bundle(bundle_dir)


def test_missing_column_fails(bundle_dir: Path) -> None:
    frame = _read_frame(bundle_dir, "recommendations.parquet").drop(
        columns=["recommendation_policy"]
    )
    _write_frame(bundle_dir, "recommendations.parquet", frame)
    with pytest.raises(MissingColumnError):
        load_bundle(bundle_dir)


def test_wrong_dtype_fails(bundle_dir: Path) -> None:
    frame = _read_frame(bundle_dir, "strategy_evaluations.parquet")
    frame["expected_return"] = frame["expected_return"].astype(str)
    _write_frame(bundle_dir, "strategy_evaluations.parquet", frame)
    with pytest.raises(InvalidDtypeError):
        load_bundle(bundle_dir)


@pytest.mark.parametrize(
    ("artifact", "id_column"),
    [
        ("recommendations.parquet", "recommendation_id"),
        ("recommendations_per_pair.parquet", "recommendation_id"),
        ("strategy_evaluations.parquet", "evaluation_id"),
    ],
)
def test_duplicate_ids_fail(bundle_dir: Path, artifact: str, id_column: str) -> None:
    frame = _read_frame(bundle_dir, artifact)
    frame.loc[1, id_column] = frame.loc[0, id_column]
    _write_frame(bundle_dir, artifact, frame)
    with pytest.raises(DuplicateIdentifierError):
        load_bundle(bundle_dir)


@pytest.mark.parametrize(
    "artifact",
    ["recommendations.parquet", "recommendations_per_pair.parquet"],
)
def test_non_positive_rank_fails(bundle_dir: Path, artifact: str) -> None:
    frame = _read_frame(bundle_dir, artifact)
    frame.loc[0, "rank"] = 0
    _write_frame(bundle_dir, artifact, frame)
    with pytest.raises(InvalidRankError):
        load_bundle(bundle_dir)


def test_duplicate_pair_rank_fails(bundle_dir: Path) -> None:
    frame = _read_frame(bundle_dir, "recommendations_per_pair.parquet")
    frame.loc[1, ["pair", "surface_id", "state_id", "rank"]] = frame.loc[
        0, ["pair", "surface_id", "state_id", "rank"]
    ]
    frame.loc[1, "recommendation_id"] = "another-id"
    _write_frame(bundle_dir, "recommendations_per_pair.parquet", frame)
    with pytest.raises(InvalidRankError):
        load_bundle(bundle_dir)


@pytest.mark.parametrize(
    "artifact",
    ["recommendations.parquet", "recommendations_per_pair.parquet"],
)
def test_foreign_key_fails(bundle_dir: Path, artifact: str) -> None:
    frame = _read_frame(bundle_dir, artifact)
    frame.loc[0, "evaluation_id"] = "unknown"
    _write_frame(bundle_dir, artifact, frame)
    with pytest.raises(ForeignKeyError):
        load_bundle(bundle_dir)


@pytest.mark.parametrize("metadata_value", ['{"schema_version":"99.0"}', "{}"])
def test_missing_or_unknown_schema_version_fails(
    bundle_dir: Path, metadata_value: str
) -> None:
    frame = _read_frame(bundle_dir, "recommendations.parquet")
    frame.loc[0, "metadata"] = metadata_value
    _write_frame(bundle_dir, "recommendations.parquet", frame)
    with pytest.raises(SchemaVersionError):
        load_bundle(bundle_dir)


@pytest.mark.parametrize(
    ("manifest_key", "wrong_count"),
    [("recommendation_per_pair_count", 99), ("strategy_evaluation_count", 99)],
)
def test_manifest_count_mismatch_fails(
    bundle_dir: Path, manifest_key: str, wrong_count: int
) -> None:
    manifest = _read_manifest(bundle_dir)
    manifest[manifest_key] = wrong_count
    _write_manifest(bundle_dir, manifest)
    with pytest.raises(ManifestCountError):
        load_bundle(bundle_dir)


def test_evidence_date_is_optional_by_default(bundle_dir: Path) -> None:
    manifest = _read_manifest(bundle_dir)
    manifest.pop("evidence_end_date")
    _write_manifest(bundle_dir, manifest)
    bundle = load_bundle(bundle_dir)
    assert not bundle.evidence_end_known
    assert not bundle.is_trade_eligible(date(2026, 10, 1))
    with pytest.raises(EvidenceEndDateError):
        load_bundle(bundle_dir, require_evidence_end_date=True)


def test_invalid_evidence_date_fails(bundle_dir: Path) -> None:
    manifest = _read_manifest(bundle_dir)
    manifest["evidence_end_date"] = "not-a-date"
    _write_manifest(bundle_dir, manifest)
    with pytest.raises(EvidenceEndDateError):
        load_bundle(bundle_dir)


def test_aggregate_row_must_not_have_pair(bundle_dir: Path) -> None:
    frame = _read_frame(bundle_dir, "strategy_evaluations.parquet")
    frame.loc[2, "metadata"] = json.dumps({"pair_count": 2, "pair": "EUR_USD"})
    _write_frame(bundle_dir, "strategy_evaluations.parquet", frame)
    with pytest.raises(InvalidBundleError):
        load_bundle(bundle_dir)
