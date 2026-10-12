"""Loading and fail-fast validation for MPML recommendation bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd
from pandas.api.types import is_integer_dtype, is_numeric_dtype

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
from mrml.contracts.schemas import (
    Recommendation,
    RecommendationPerPair,
    StrategyEvaluation,
    immutable_metadata,
)

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})
_RECOMMENDATION_COLUMNS = frozenset(
    {
        "recommendation_id",
        "evaluation_id",
        "rank",
        "recommendation_policy",
        "metadata",
    }
)
_PER_PAIR_COLUMNS = _RECOMMENDATION_COLUMNS | frozenset(
    {
        "pair",
        "surface_id",
        "state_id",
    }
)
_EVALUATION_COLUMNS = frozenset(
    {
        "evaluation_id",
        "surface_id",
        "surface_version",
        "state_id",
        "strategy_id",
        "expected_return",
        "expected_sharpe",
        "expected_drawdown",
        "win_rate",
        "confidence",
        "stability",
        "n_folds",
        "n_trades",
        "metadata",
    }
)
_STRING_COLUMNS = frozenset(
    {
        "recommendation_id",
        "evaluation_id",
        "recommendation_policy",
        "pair",
        "surface_id",
        "state_id",
        "surface_version",
        "strategy_id",
        "metadata",
    }
)
_NUMERIC_EVALUATION_COLUMNS = frozenset(
    {
        "expected_return",
        "expected_sharpe",
        "expected_drawdown",
        "win_rate",
        "confidence",
        "stability",
    }
)
_INTEGER_COLUMNS = frozenset({"rank", "n_folds", "n_trades"})


@dataclass(frozen=True)
class RecommendationBundle:
    """Immutable, validated MPML bundle and its source provenance."""

    recommendations: tuple[Recommendation, ...]
    recommendations_per_pair: tuple[RecommendationPerPair, ...]
    strategy_evaluations: tuple[StrategyEvaluation, ...]
    manifest: Mapping[str, Any]
    content_hash: str
    evidence_end_date: date | None
    max_evidence_age_days: int

    @property
    def evidence_end_known(self) -> bool:
        """Whether a valid evidence end date was present in the manifest."""
        return self.evidence_end_date is not None

    @property
    def experiment_id(self) -> Any:
        """Return experiment provenance from the manifest."""
        return self.manifest.get("experiment_id")

    @property
    def git_sha(self) -> Any:
        """Return source revision provenance from the manifest."""
        return self.manifest.get("git_sha")

    @property
    def timestamp_utc(self) -> Any:
        """Return generation timestamp provenance from the manifest."""
        return self.manifest.get("timestamp_utc")

    @property
    def seed(self) -> Any:
        """Return experiment seed provenance from the manifest."""
        return self.manifest.get("seed")

    @property
    def phaseaware_strategy(self) -> Any:
        """Return the phase-aware strategy provenance from the manifest."""
        return self.manifest.get("phaseaware_strategy")

    @property
    def spread_pips(self) -> Any:
        """Return spread cost provenance from the manifest."""
        costs = self.manifest.get("costs", {})
        return costs.get("spread_pips") if isinstance(costs, Mapping) else None

    @property
    def slippage_pips(self) -> Any:
        """Return slippage cost provenance from the manifest."""
        costs = self.manifest.get("costs", {})
        return costs.get("slippage_pips") if isinstance(costs, Mapping) else None

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        """Return evaluation-scope strategy identifiers from the manifest."""
        scope = self.manifest.get("evaluation_scope", {})
        if not isinstance(scope, Mapping):
            return ()
        strategy_ids = scope.get("strategy_ids", ())
        if not isinstance(strategy_ids, (list, tuple)):
            return ()
        return tuple(value for value in strategy_ids if isinstance(value, str))

    def is_trade_eligible(self, as_of: date | datetime) -> bool:
        """Check evidence timing and staleness; unknown dates are ineligible."""
        if self.evidence_end_date is None:
            return False
        check_date = as_of.date() if isinstance(as_of, datetime) else as_of
        if check_date <= self.evidence_end_date:
            return False
        return (check_date - self.evidence_end_date).days <= self.max_evidence_age_days

    def candidates(self, pair: str) -> tuple[RecommendationPerPair, ...]:
        """Return pair-specific candidate evidence; state remains provenance."""
        return tuple(
            sorted(
                (item for item in self.recommendations_per_pair if item.pair == pair),
                key=lambda item: (
                    item.surface_id,
                    item.state_id,
                    item.rank,
                    item.recommendation_id,
                ),
            )
        )

    def evaluation_for(self, pair: str, strategy_id: str) -> StrategyEvaluation | None:
        """Find an evaluation; raises InvalidBundleError if the match is ambiguous."""
        matches = tuple(
            item
            for item in self.strategy_evaluations
            if item.pair == pair and item.strategy_id == strategy_id
        )
        if len(matches) > 1:
            raise InvalidBundleError(
                f"Multiple evaluations match pair={pair!r}, strategy_id={strategy_id!r}"
            )
        return matches[0] if matches else None


def _decode_metadata(value: Any, *, context: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise InvalidBundleError(f"{context} metadata is not valid JSON") from error
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise InvalidDtypeError(f"{context} metadata must be a JSON object")
    if not isinstance(decoded, dict):
        raise InvalidBundleError(f"{context} metadata must decode to a JSON object")
    return decoded


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _check_columns(frame: pd.DataFrame, required: Set[str], *, artifact: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise MissingColumnError(
            f"{artifact} is missing required columns: {', '.join(sorted(missing))}"
        )


def _check_dtypes(
    frame: pd.DataFrame,
    *,
    artifact: str,
    extra_string_columns: Set[str] | None = None,
) -> None:
    string_columns = _STRING_COLUMNS | (extra_string_columns or set())
    for column in string_columns & set(frame.columns):
        values = frame[column].dropna()
        if not values.map(lambda value: isinstance(value, str)).all():
            raise InvalidDtypeError(f"{artifact}.{column} must contain strings")
        if column not in {"metadata"} and frame[column].isna().any():
            raise InvalidDtypeError(f"{artifact}.{column} must not contain null values")
    for column in _NUMERIC_EVALUATION_COLUMNS & set(frame.columns):
        if not is_numeric_dtype(frame[column].dtype):
            raise InvalidDtypeError(f"{artifact}.{column} must have a numeric dtype")
        if column not in {"win_rate", "confidence"} and frame[column].isna().any():
            raise InvalidDtypeError(f"{artifact}.{column} must not contain null values")
    for column in _INTEGER_COLUMNS & set(frame.columns):
        if not is_integer_dtype(frame[column].dtype):
            raise InvalidDtypeError(f"{artifact}.{column} must have an integer dtype")
        if frame[column].isna().any():
            raise InvalidDtypeError(f"{artifact}.{column} must not contain null values")


def _check_unique_ids(frame: pd.DataFrame, column: str, artifact: str) -> None:
    if frame[column].duplicated().any():
        raise DuplicateIdentifierError(f"{artifact}.{column} values must be unique")


def _validate_recommendation_metadata(
    frame: pd.DataFrame, artifact: str
) -> list[Mapping[str, Any]]:
    metadata = [
        _decode_metadata(value, context=f"{artifact} row {index}")
        for index, value in enumerate(frame["metadata"].tolist())
    ]
    for index, values in enumerate(metadata):
        version = values.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise SchemaVersionError(
                f"{artifact} row {index} has missing or unsupported "
                f"schema_version: {version!r}"
            )
    return metadata


def _check_recommendation_ranks(frame: pd.DataFrame, artifact: str) -> None:
    if (frame["rank"] < 1).any():
        raise InvalidRankError(f"{artifact}.rank values must be positive")
    groups = ["pair", "surface_id", "state_id"]
    if frame.duplicated(groups + ["rank"]).any():
        raise InvalidRankError(
            f"{artifact}.rank values must be unique within {', '.join(groups)}"
        )


def _manifest_date(
    manifest: Mapping[str, Any], field: str, required: bool
) -> date | None:
    raw_value = manifest.get(field)
    if raw_value is None or raw_value == "":
        if required:
            raise EvidenceEndDateError(f"Manifest is missing required field {field!r}")
        return None
    if not isinstance(raw_value, str):
        raise EvidenceEndDateError(
            f"Manifest field {field!r} must be an ISO date string"
        )
    try:
        return date.fromisoformat(raw_value)
    except ValueError as error:
        raise EvidenceEndDateError(
            f"Manifest field {field!r} must be an ISO date string"
        ) from error


def _read_manifest(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidBundleError(f"Could not read manifest {path.name}") from error
    if not isinstance(data, dict):
        raise InvalidBundleError("experiment_manifest.json must contain a JSON object")
    return data


def _read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception as error:
        raise InvalidBundleError(
            f"Could not read parquet artifact {path.name}"
        ) from error


def _manifest_count(manifest: Mapping[str, Any], key: str, actual: int) -> None:
    expected = manifest.get(key)
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ManifestCountError(f"Manifest field {key!r} must be an integer")
    if expected != actual:
        raise ManifestCountError(
            f"Manifest {key}={expected} does not match parquet row count {actual}"
        )


def _evaluation_rows(frame: pd.DataFrame) -> tuple[StrategyEvaluation, ...]:
    result = []
    for index, row in frame.iterrows():
        metadata = _decode_metadata(row["metadata"], context=f"evaluation row {index}")
        pair = metadata.get("pair")
        pair_count = metadata.get("pair_count")
        if pair is not None and not isinstance(pair, str):
            raise InvalidDtypeError(
                f"evaluation row {index} metadata pair must be a string"
            )
        if isinstance(pair_count, int) and pair_count > 1 and pair is not None:
            raise InvalidBundleError(
                f"aggregate evaluation row {index} must not declare a pair"
            )
        result.append(
            StrategyEvaluation(
                evaluation_id=row["evaluation_id"],
                surface_id=row["surface_id"],
                surface_version=row["surface_version"],
                state_id=row["state_id"],
                strategy_id=row["strategy_id"],
                expected_return=float(row["expected_return"]),
                expected_sharpe=float(row["expected_sharpe"]),
                expected_drawdown=float(row["expected_drawdown"]),
                win_rate=None if pd.isna(row["win_rate"]) else float(row["win_rate"]),
                confidence=(
                    None if pd.isna(row["confidence"]) else float(row["confidence"])
                ),
                stability=float(row["stability"]),
                n_folds=int(row["n_folds"]),
                n_trades=int(row["n_trades"]),
                metadata=immutable_metadata(metadata),
            )
        )
    return tuple(result)


def _recommendation_rows(
    frame: pd.DataFrame, metadata: list[Mapping[str, Any]]
) -> tuple[Recommendation, ...]:
    return tuple(
        Recommendation(
            recommendation_id=row["recommendation_id"],
            evaluation_id=row["evaluation_id"],
            rank=int(row["rank"]),
            recommendation_policy=row["recommendation_policy"],
            metadata=immutable_metadata(values),
        )
        for (_, row), values in zip(frame.iterrows(), metadata, strict=True)
    )


def _per_pair_rows(
    frame: pd.DataFrame, metadata: list[Mapping[str, Any]]
) -> tuple[RecommendationPerPair, ...]:
    return tuple(
        RecommendationPerPair(
            recommendation_id=row["recommendation_id"],
            evaluation_id=row["evaluation_id"],
            pair=row["pair"],
            surface_id=row["surface_id"],
            state_id=row["state_id"],
            rank=int(row["rank"]),
            recommendation_policy=row["recommendation_policy"],
            metadata=immutable_metadata(values),
        )
        for (_, row), values in zip(frame.iterrows(), metadata, strict=True)
    )


def load_bundle(
    directory: str | Path,
    *,
    max_evidence_age_days: int = 30,
    require_evidence_end_date: bool = False,
    evidence_end_date_field: str = "evidence_end_date",
) -> RecommendationBundle:
    """Read and validate all four MPML artifacts; raises typed bundle errors."""
    if max_evidence_age_days < 0:
        raise ValueError("max_evidence_age_days must be non-negative")
    root = Path(directory)
    names = (
        "recommendations.parquet",
        "recommendations_per_pair.parquet",
        "strategy_evaluations.parquet",
        "experiment_manifest.json",
    )
    missing = [name for name in names if not (root / name).is_file()]
    if missing:
        raise MissingBundleFileError(f"Missing bundle files: {', '.join(missing)}")

    recommendations_frame = _read_parquet(root / "recommendations.parquet")
    per_pair_frame = _read_parquet(root / "recommendations_per_pair.parquet")
    evaluations_frame = _read_parquet(root / "strategy_evaluations.parquet")
    manifest = _read_manifest(root / "experiment_manifest.json")

    _check_columns(
        recommendations_frame, _RECOMMENDATION_COLUMNS, artifact="recommendations"
    )
    _check_columns(
        per_pair_frame, _PER_PAIR_COLUMNS, artifact="recommendations_per_pair"
    )
    _check_columns(
        evaluations_frame, _EVALUATION_COLUMNS, artifact="strategy_evaluations"
    )
    _check_dtypes(recommendations_frame, artifact="recommendations")
    _check_dtypes(
        per_pair_frame,
        artifact="recommendations_per_pair",
        extra_string_columns={"pair"},
    )
    _check_dtypes(evaluations_frame, artifact="strategy_evaluations")

    _check_unique_ids(recommendations_frame, "recommendation_id", "recommendations")
    _check_unique_ids(per_pair_frame, "recommendation_id", "recommendations_per_pair")
    _check_unique_ids(evaluations_frame, "evaluation_id", "strategy_evaluations")
    if (recommendations_frame["rank"] < 1).any():
        raise InvalidRankError("recommendations.rank values must be positive")
    _check_recommendation_ranks(per_pair_frame, "recommendations_per_pair")

    evaluation_ids = set(evaluations_frame["evaluation_id"])
    for artifact, frame in (
        ("recommendations", recommendations_frame),
        ("recommendations_per_pair", per_pair_frame),
    ):
        unknown = set(frame["evaluation_id"]) - evaluation_ids
        if unknown:
            raise ForeignKeyError(
                f"{artifact} references unknown evaluation_id(s): "
                f"{', '.join(sorted(unknown))}"
            )

    recommendations_metadata = _validate_recommendation_metadata(
        recommendations_frame, "recommendations"
    )
    per_pair_metadata = _validate_recommendation_metadata(
        per_pair_frame, "recommendations_per_pair"
    )
    _manifest_count(
        manifest,
        "recommendation_per_pair_count",
        len(per_pair_frame),
    )
    _manifest_count(
        manifest,
        "strategy_evaluation_count",
        len(evaluations_frame),
    )

    evidence_end_date = _manifest_date(
        manifest, evidence_end_date_field, require_evidence_end_date
    )
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")

    return RecommendationBundle(
        recommendations=_recommendation_rows(
            recommendations_frame, recommendations_metadata
        ),
        recommendations_per_pair=_per_pair_rows(per_pair_frame, per_pair_metadata),
        strategy_evaluations=_evaluation_rows(evaluations_frame),
        manifest=_freeze(dict(manifest)),
        content_hash=digest.hexdigest(),
        evidence_end_date=evidence_end_date,
        max_evidence_age_days=max_evidence_age_days,
    )
