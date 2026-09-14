"""Training-only numeric preprocessing for the Phase 3C tensor pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

import numpy as np
from numpy.typing import NDArray

from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL
from howreliable.modeling.features import FEATURE_FAMILIES, FEATURE_SCHEMA, ModelingDataError

PIPELINE_VERSION: Final = "pytorch-cohort-pipeline-1.0"
BINARY_FEATURES: Final = (
    "communication_observed_by_cutoff",
    "recall_observed_by_cutoff",
)


def _feature_family(name: str) -> str:
    for family, features in FEATURE_FAMILIES.items():
        if name in features:
            return family
    raise ModelingDataError(f"feature has no family: {name}")


def _feature_type(name: str) -> str:
    if name in BINARY_FEATURES:
        return "binary"
    if name.endswith("_share"):
        return "share"
    if name.endswith("_count") or name.startswith("complaints_last_"):
        return "count"
    return "continuous"


def _median(values: list[float]) -> float:
    return float(np.median(np.asarray(values, dtype=np.float64)))


@dataclass(frozen=True)
class CohortPreprocessor:
    """Serializable preprocessing parameters learned from training rows only."""

    candidate_features: tuple[str, ...]
    input_features: tuple[str, ...]
    transformed_features: tuple[str, ...]
    feature_types: dict[str, str]
    feature_families: dict[str, str]
    medians: dict[str, float]
    means: dict[str, float]
    standard_deviations: dict[str, float]
    missing_indicators: tuple[str, ...]
    removed_features: tuple[dict[str, str], ...]
    cutoff: str
    source_feature_checksum: str
    split_checksum: str
    version: str = PIPELINE_VERSION

    @classmethod
    def fit(
        cls,
        rows: list[dict[str, Any]],
        *,
        cutoff: str,
        source_feature_checksum: str,
        split_checksum: str,
    ) -> CohortPreprocessor:
        """Fit medians and scaling statistics using training rows only."""
        if not rows:
            raise ModelingDataError("preprocessor requires training rows")
        candidate_features = tuple(ALL_EVIDENCE_MODEL)
        removed: list[dict[str, str]] = []
        selected: list[str] = []
        observed_vectors: dict[tuple[str, tuple[Any, ...]], str] = {}
        for name in candidate_features:
            if any(name not in row for row in rows):
                raise ModelingDataError(f"missing candidate feature: {name}")
            vector = tuple(row[name] for row in rows)
            present = [value for value in vector if value is not None]
            if not present:
                removed.append({"feature": name, "reason": "all_missing_in_training"})
                continue
            if len(present) == len(vector) and len(set(present)) == 1:
                removed.append({"feature": name, "reason": "constant_in_training"})
                continue
            feature_type = _feature_type(name)
            transform_group = "identity" if feature_type in {"binary", "share"} else feature_type
            duplicate_key = (transform_group, vector)
            duplicate_of = observed_vectors.get(duplicate_key)
            if duplicate_of is not None:
                removed.append(
                    {
                        "feature": name,
                        "reason": "exact_duplicate_in_training",
                        "duplicate_of": duplicate_of,
                    }
                )
                continue
            observed_vectors[duplicate_key] = name
            selected.append(name)

        types = {name: _feature_type(name) for name in selected}
        families = {name: _feature_family(name) for name in selected}
        medians: dict[str, float] = {}
        missing_indicators: list[str] = []
        columns: dict[str, NDArray[np.float64]] = {}
        for name in selected:
            values = [row[name] for row in rows]
            numeric = [float(value) for value in values if value is not None]
            if not numeric:
                raise ModelingDataError(f"selected feature is all missing: {name}")
            median = _median(numeric)
            medians[name] = median
            if len(numeric) != len(values):
                missing_indicators.append(name)
            filled = np.asarray(
                [median if value is None else float(value) for value in values],
                dtype=np.float64,
            )
            if types[name] == "count":
                if np.any(filled < 0):
                    raise ModelingDataError(f"count feature contains a negative value: {name}")
                filled = np.log1p(filled)
            columns[name] = filled

        means: dict[str, float] = {}
        deviations: dict[str, float] = {}
        for name in selected:
            if types[name] not in {"count", "continuous"}:
                continue
            mean = float(np.mean(columns[name]))
            deviation = float(np.std(columns[name], ddof=0))
            if deviation == 0 or not np.isfinite(deviation):
                raise ModelingDataError(f"feature cannot be standardized: {name}")
            means[name] = mean
            deviations[name] = deviation

        transformed: list[str] = []
        for name in selected:
            transformed.append(name)
            if name in missing_indicators:
                transformed.append(f"{name}__missing")
        return cls(
            candidate_features=candidate_features,
            input_features=tuple(selected),
            transformed_features=tuple(transformed),
            feature_types=types,
            feature_families=families,
            medians=medians,
            means=means,
            standard_deviations=deviations,
            missing_indicators=tuple(missing_indicators),
            removed_features=tuple(removed),
            cutoff=cutoff,
            source_feature_checksum=source_feature_checksum,
            split_checksum=split_checksum,
        )

    def transform(self, rows: list[dict[str, Any]]) -> NDArray[np.float32]:
        """Apply frozen parameters without learning from the supplied rows."""
        matrix = np.empty((len(rows), len(self.transformed_features)), dtype=np.float32)
        for row_index, row in enumerate(rows):
            tensor_index = 0
            for name in self.input_features:
                raw = row.get(name)
                missing = raw is None
                value = self.medians[name] if missing else float(cast(float | int | bool, raw))
                feature_type = self.feature_types[name]
                if feature_type == "count":
                    if value < 0:
                        raise ModelingDataError(f"count feature contains a negative value: {name}")
                    value = float(np.log1p(value))
                if feature_type in {"count", "continuous"}:
                    value = (value - self.means[name]) / self.standard_deviations[name]
                matrix[row_index, tensor_index] = value
                tensor_index += 1
                if name in self.missing_indicators:
                    matrix[row_index, tensor_index] = float(missing)
                    tensor_index += 1
        if not np.all(np.isfinite(matrix)):
            raise ModelingDataError("preprocessing produced a non-finite tensor value")
        return matrix

    def manifest(self) -> dict[str, Any]:
        """Return deterministic tensor index and transformation metadata."""
        entries: list[dict[str, Any]] = []
        index = 0
        for name in self.input_features:
            feature_type = self.feature_types[name]
            if feature_type == "count":
                transform = "log1p_then_train_standardize"
            elif feature_type == "continuous":
                transform = "train_standardize"
            else:
                transform = "identity"
            entries.append(
                {
                    "feature_name": name,
                    "source_feature": name,
                    "feature_family": self.feature_families[name],
                    "input_type": "numeric",
                    "numeric_type": feature_type,
                    "missingness_handling": (
                        "train_median_with_explicit_indicator"
                        if name in self.missing_indicators
                        else "none_required"
                    ),
                    "transform": transform,
                    "tensor_index": index,
                }
            )
            index += 1
            if name in self.missing_indicators:
                entries.append(
                    {
                        "feature_name": f"{name}__missing",
                        "source_feature": name,
                        "feature_family": self.feature_families[name],
                        "input_type": "numeric",
                        "numeric_type": "binary",
                        "missingness_handling": "not_applicable",
                        "transform": "is_missing",
                        "tensor_index": index,
                    }
                )
                index += 1
        return {
            "pipeline_version": self.version,
            "cutoff": self.cutoff,
            "candidate_input_feature_count": len(self.candidate_features),
            "selected_input_feature_count": len(self.input_features),
            "final_tensor_feature_count": len(self.transformed_features),
            "features": entries,
            "removed_features": list(self.removed_features),
            "excluded_canonical_columns": [
                name for name in FEATURE_SCHEMA if name not in self.input_features
            ],
            "additional_exclusions": ["all target columns", "all production columns"],
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the fitted preprocessing contract."""
        return {
            "pipeline_version": self.version,
            "cutoff": self.cutoff,
            "source_feature_checksum": self.source_feature_checksum,
            "split_checksum": self.split_checksum,
            "candidate_features": list(self.candidate_features),
            "input_features": list(self.input_features),
            "transformed_features": list(self.transformed_features),
            "feature_types": self.feature_types,
            "feature_families": self.feature_families,
            "medians": self.medians,
            "means_after_imputation_and_count_transform": self.means,
            "standard_deviations_after_imputation_and_count_transform": (self.standard_deviations),
            "missing_indicators": list(self.missing_indicators),
            "removed_features": list(self.removed_features),
            "fit_scope": "TRAIN split only",
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CohortPreprocessor:
        """Restore a fitted preprocessing contract from JSON-compatible data."""
        if value.get("pipeline_version") != PIPELINE_VERSION:
            raise ModelingDataError("incompatible PyTorch preprocessing version")
        return cls(
            candidate_features=tuple(value["candidate_features"]),
            input_features=tuple(value["input_features"]),
            transformed_features=tuple(value["transformed_features"]),
            feature_types=dict(value["feature_types"]),
            feature_families=dict(value["feature_families"]),
            medians={key: float(item) for key, item in value["medians"].items()},
            means={
                key: float(item)
                for key, item in value["means_after_imputation_and_count_transform"].items()
            },
            standard_deviations={
                key: float(item)
                for key, item in value[
                    "standard_deviations_after_imputation_and_count_transform"
                ].items()
            },
            missing_indicators=tuple(value["missing_indicators"]),
            removed_features=tuple(dict(item) for item in value["removed_features"]),
            cutoff=str(value["cutoff"]),
            source_feature_checksum=str(value["source_feature_checksum"]),
            split_checksum=str(value["split_checksum"]),
            version=str(value["pipeline_version"]),
        )
