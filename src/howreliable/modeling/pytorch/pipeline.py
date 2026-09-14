"""Validated artifact-to-tensor orchestration for Phase 3C."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import torch

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL, TARGET_COLUMN
from howreliable.modeling.features import (
    FEATURE_CUTOFF,
    FEATURE_MATRIX_VERSION,
    FEATURE_SCHEMA,
    ModelingDataError,
    _json,
    _jsonl,
    _write_json,
)
from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset
from howreliable.modeling.pytorch.loaders import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LOADER_SEED,
    DEFAULT_NUM_WORKERS,
    CohortDataLoaders,
    create_dataloaders,
)
from howreliable.modeling.pytorch.preprocessing import PIPELINE_VERSION, CohortPreprocessor
from howreliable.modeling.splits import SPLIT_SCHEMA, SPLIT_SEED, SPLIT_VERSION

EXPECTED_INPUT_CHECKSUMS: Final = {
    "features": "1cffb203298af438639c102a924f205ffb8ec25e6ea87035e670cecc902dfdfe",
    "target": "56b04f3f842d4f857f39b050c310b6fee2a83b20b9afeefc82c5c5967c19864e",
    "split": "43211c19328054f21ed730da6b9e65b78fae6e4b0af5983411b2a6811b34297a",
    "baseline_results": "3fe3545d4890db16628102b6e3ad6489aa3e2443309535a894d56e33551481c2",
}
EXPECTED_SPLIT_COUNTS: Final = {"TRAIN": 5_891, "VALIDATION": 1_262, "TEST": 1_263}
EXPECTED_POSITIVE_COUNTS: Final = {"TRAIN": 3_078, "VALIDATION": 660, "TEST": 660}
BASELINE_COMPARISON: Final = {
    "validation": {"roc_auc": 0.905985, "pr_auc": 0.929263, "f1": 0.827751},
    "test": {"roc_auc": 0.892844, "pr_auc": 0.917283, "f1": 0.820673},
}


@dataclass(frozen=True)
class ValidatedInputs:
    """Canonical rows after checksum, schema, identity, and split validation."""

    features: list[dict[str, Any]]
    targets_by_id: dict[str, dict[str, Any]]
    split_by_id: dict[str, str]
    paths: dict[str, Path]
    checksums: dict[str, str]


@dataclass(frozen=True)
class PyTorchPipelineBundle:
    """In-memory Phase 3C datasets/loaders plus their persisted contracts."""

    preprocessor: CohortPreprocessor
    datasets: dict[str, HowReliableCohortDataset]
    loaders: CohortDataLoaders
    metadata: dict[str, Any]
    artifact_paths: dict[str, Path]
    artifact_checksums: dict[str, str]


def _artifact_paths(repository_root: Path) -> dict[str, Path]:
    return {
        "features": repository_root
        / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl",
        "target": repository_root
        / "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl",
        "split": repository_root / "data/processed/modeling/cohort-split-2022-12-31.jsonl",
        "baseline_results": repository_root / "artifacts/models/baseline-results.json",
    }


def _validate_inputs(
    repository_root: Path,
    *,
    expected_checksums: Mapping[str, str],
    expected_split_counts: Mapping[str, int],
    expected_positive_counts: Mapping[str, int],
) -> ValidatedInputs:
    paths = _artifact_paths(repository_root)
    checksums: dict[str, str] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise ModelingDataError(f"missing Phase 3C input artifact: {path}")
        checksums[name] = sha256_file(path)
        if checksums[name] != expected_checksums[name]:
            raise ModelingDataError(f"unexpected {name} checksum")

    feature_provenance = _json(
        paths["features"].with_name(f"{paths['features'].stem}.provenance.json")
    )
    target_provenance = _json(paths["target"].with_name(f"{paths['target'].stem}.provenance.json"))
    split_provenance = _json(paths["split"].with_name(f"{paths['split'].stem}.provenance.json"))
    if (
        feature_provenance.get("output_sha256") != checksums["features"]
        or feature_provenance.get("feature_matrix_version") != FEATURE_MATRIX_VERSION
        or feature_provenance.get("cutoff") != FEATURE_CUTOFF.isoformat()
    ):
        raise ModelingDataError("incompatible feature provenance")
    if (
        target_provenance.get("output_sha256") != checksums["target"]
        or target_provenance.get("target_definition_version") != TARGET_DEFINITION_VERSION
        or target_provenance.get("target_definition", {}).get("cutoff")
        != FEATURE_CUTOFF.isoformat()
    ):
        raise ModelingDataError("incompatible target provenance")
    if (
        split_provenance.get("output_sha256") != checksums["split"]
        or split_provenance.get("split_version") != SPLIT_VERSION
        or split_provenance.get("seed") != SPLIT_SEED
        or split_provenance.get("input_checksums", {}).get("features") != checksums["features"]
        or split_provenance.get("input_checksums", {}).get("target") != checksums["target"]
    ):
        raise ModelingDataError("incompatible split provenance")

    features = _jsonl(paths["features"], FEATURE_SCHEMA)
    targets = _jsonl(paths["target"], TARGET_SCHEMA)
    splits = _jsonl(paths["split"], SPLIT_SCHEMA)
    feature_ids = [cast(str, row["broad_vehicle_id"]) for row in features]
    targets_by_id = {cast(str, row["broad_vehicle_id"]): row for row in targets}
    split_by_id = {cast(str, row["broad_vehicle_id"]): cast(str, row["split"]) for row in splits}
    if (
        len(set(feature_ids)) != len(features)
        or len(targets_by_id) != len(targets)
        or len(split_by_id) != len(splits)
    ):
        raise ModelingDataError("duplicate Phase 3C input identity")
    if not (set(feature_ids) == set(targets_by_id) == set(split_by_id)):
        raise ModelingDataError("feature, target, and split identities do not align")
    for feature in features:
        identifier = cast(str, feature["broad_vehicle_id"])
        target = targets_by_id[identifier]
        for name in ("normalized_make", "normalized_model", "model_year"):
            if feature[name] != target[name]:
                raise ModelingDataError(f"cohort identity field mismatch: {identifier}/{name}")

    split_counts = Counter(split_by_id.values())
    if dict(split_counts) != dict(expected_split_counts):
        raise ModelingDataError(f"unexpected frozen split counts: {dict(split_counts)}")
    positive_counts = Counter(
        split_by_id[identifier]
        for identifier, target in targets_by_id.items()
        if target[TARGET_COLUMN] is True
    )
    if dict(positive_counts) != dict(expected_positive_counts):
        raise ModelingDataError(f"unexpected split positive counts: {dict(positive_counts)}")
    if any(name.startswith("future_") or "target" in name for name in ALL_EVIDENCE_MODEL):
        raise ModelingDataError("target-like field entered neural feature selection")
    if any(name in ALL_EVIDENCE_MODEL for name in ("normalized_make", "normalized_model")):
        raise ModelingDataError("raw make/model identity entered the neural tensor")

    baseline = json.loads(paths["baseline_results"].read_text(encoding="utf-8"))
    if baseline.get("best_model", {}).get("model") != "random_forest":
        raise ModelingDataError("Phase 3B comparison baseline is incompatible")
    return ValidatedInputs(features, targets_by_id, split_by_id, paths, checksums)


def _datasets(
    inputs: ValidatedInputs, preprocessor: CohortPreprocessor
) -> dict[str, HowReliableCohortDataset]:
    result: dict[str, HowReliableCohortDataset] = {}
    for split in ("TRAIN", "VALIDATION", "TEST"):
        rows = [
            row
            for row in inputs.features
            if inputs.split_by_id[cast(str, row["broad_vehicle_id"])] == split
        ]
        identifiers = [cast(str, row["broad_vehicle_id"]) for row in rows]
        targets = np.asarray(
            [
                [float(inputs.targets_by_id[identifier][TARGET_COLUMN])]
                for identifier in identifiers
            ],
            dtype=np.float32,
        )
        metadata = [
            {
                "normalized_make": str(row["normalized_make"]),
                "normalized_model": str(row["normalized_model"]),
                "model_year": int(row["model_year"]),
                "cohort_age_at_cutoff": int(row["cohort_age_at_cutoff"]),
                "historical_complaint_count": int(row["historical_complaint_count"]),
                "communication_observed_by_cutoff": bool(row["communication_observed_by_cutoff"]),
                "communication_asof_status": str(row["communication_asof_status"]),
                "recall_observed_by_cutoff": bool(row["recall_observed_by_cutoff"]),
                "recall_asof_status": str(row["recall_asof_status"]),
                "split": split,
            }
            for row in rows
        ]
        result[split] = HowReliableCohortDataset(
            preprocessor.transform(rows), targets, identifiers, metadata
        )
    return result


def _batch_diagnostic(batch: dict[str, Any]) -> dict[str, Any]:
    features = cast(torch.Tensor, batch["features"])
    targets = cast(torch.Tensor, batch["target"])
    return {
        "feature_shape": list(features.shape),
        "target_shape": list(targets.shape),
        "feature_dtype": str(features.dtype),
        "target_dtype": str(targets.dtype),
        "target_positives": int(targets.sum().item()),
    }


def generate_pytorch_pipeline(
    repository_root: Path,
    artifact_directory: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    loader_seed: int = DEFAULT_LOADER_SEED,
    expected_checksums: Mapping[str, str] = EXPECTED_INPUT_CHECKSUMS,
    expected_split_counts: Mapping[str, int] = EXPECTED_SPLIT_COUNTS,
    expected_positive_counts: Mapping[str, int] = EXPECTED_POSITIVE_COUNTS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PyTorchPipelineBundle:
    """Fit preprocessing, build datasets/loaders, and persist Phase 3C contracts."""
    if artifact_directory.exists():
        raise ArtifactExistsError(
            f"refusing to overwrite PyTorch pipeline output: {artifact_directory}"
        )
    inputs = _validate_inputs(
        repository_root,
        expected_checksums=expected_checksums,
        expected_split_counts=expected_split_counts,
        expected_positive_counts=expected_positive_counts,
    )
    train_rows = [
        row
        for row in inputs.features
        if inputs.split_by_id[cast(str, row["broad_vehicle_id"])] == "TRAIN"
    ]
    preprocessor = CohortPreprocessor.fit(
        train_rows,
        cutoff=FEATURE_CUTOFF.isoformat(),
        source_feature_checksum=inputs.checksums["features"],
        split_checksum=inputs.checksums["split"],
    )
    datasets = _datasets(inputs, preprocessor)
    diagnostic_loaders = create_dataloaders(
        datasets, batch_size=batch_size, seed=loader_seed, num_workers=DEFAULT_NUM_WORKERS
    )
    example_batches = {
        "TRAIN": _batch_diagnostic(next(iter(diagnostic_loaders.train))),
        "VALIDATION": _batch_diagnostic(next(iter(diagnostic_loaders.validation))),
        "TEST": _batch_diagnostic(next(iter(diagnostic_loaders.test))),
    }
    loaders = create_dataloaders(
        datasets, batch_size=batch_size, seed=loader_seed, num_workers=DEFAULT_NUM_WORKERS
    )
    manifest = preprocessor.manifest()
    family_counts = Counter(cast(str, item["feature_family"]) for item in manifest["features"])
    missing_before = {
        name: sum(row[name] is None for row in inputs.features)
        for name in preprocessor.input_features
        if any(row[name] is None for row in inputs.features)
    }
    split_counts = {name: len(datasets[name]) for name in ("TRAIN", "VALIDATION", "TEST")}
    positive_counts = {
        name: int(datasets[name].targets.sum().item()) for name in ("TRAIN", "VALIDATION", "TEST")
    }
    metadata: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "creation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "cutoff": FEATURE_CUTOFF.isoformat(),
        "inputs": {
            name: {
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": inputs.checksums[name],
            }
            for name, path in inputs.paths.items()
        },
        "row_alignment": "exact feature/target/split cohort identity and static identity match",
        "input_row_count": len(inputs.features),
        "canonical_feature_column_count": len(FEATURE_SCHEMA),
        "candidate_model_input_count": len(preprocessor.candidate_features),
        "selected_model_input_count": len(preprocessor.input_features),
        "final_tensor_dimension": len(preprocessor.transformed_features),
        "transformed_feature_family_counts": dict(sorted(family_counts.items())),
        "log1p_count_features": [
            name
            for name in preprocessor.input_features
            if preprocessor.feature_types[name] == "count"
        ],
        "standardized_features": list(preprocessor.means),
        "share_features": [
            name
            for name in preprocessor.input_features
            if preprocessor.feature_types[name] == "share"
        ],
        "binary_features": [
            name
            for name in preprocessor.input_features
            if preprocessor.feature_types[name] == "binary"
        ],
        "missing_indicators": list(preprocessor.missing_indicators),
        "missing_before_preprocessing": missing_before,
        "missing_before_preprocessing_total": sum(missing_before.values()),
        "missing_after_preprocessing": 0,
        "removed_features": list(preprocessor.removed_features),
        "excluded_canonical_columns": manifest["excluded_canonical_columns"],
        "split_counts": split_counts,
        "positive_counts": positive_counts,
        "positive_prevalence": {
            name: positive_counts[name] / split_counts[name] for name in split_counts
        },
        "tensor_contract": {
            "feature_dtype": "torch.float32",
            "target_dtype": "torch.float32",
            "item_feature_shape": [len(preprocessor.transformed_features)],
            "item_target_shape": [1],
            "cohort_identity_is_model_input": False,
        },
        "loader_configuration": {
            "batch_size": batch_size,
            "num_workers": DEFAULT_NUM_WORKERS,
            "train_shuffle": True,
            "validation_shuffle": False,
            "test_shuffle": False,
            "train_generator_seed": loader_seed,
            "drop_last": False,
            "sampling": "natural split membership; no over/undersampling or weighting",
        },
        "example_batches": example_batches,
        "subgroup_metadata": [
            "cohort_age_at_cutoff",
            "historical_complaint_count",
            "communication_observed_by_cutoff",
            "communication_asof_status",
            "recall_observed_by_cutoff",
            "recall_asof_status",
        ],
        "identity_metadata": [
            "broad_vehicle_id",
            "normalized_make",
            "normalized_model",
            "model_year",
        ],
        "phase_3d_baseline_comparison": BASELINE_COMPARISON,
        "phase_3d_test_policy": "test remains untouched until final Phase 3D evaluation",
        "model_training_performed": False,
    }
    artifact_paths = {
        "feature_manifest": artifact_directory / "feature-manifest.json",
        "preprocessor": artifact_directory / "preprocessor.json",
        "dataset_metadata": artifact_directory / "dataset-metadata.json",
    }
    try:
        _write_json(artifact_paths["feature_manifest"], manifest)
        _write_json(artifact_paths["preprocessor"], preprocessor.to_dict())
        metadata["supporting_artifacts"] = {
            name: {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in artifact_paths.items()
            if name != "dataset_metadata"
        }
        _write_json(artifact_paths["dataset_metadata"], metadata)
    except Exception:
        for path in artifact_paths.values():
            path.unlink(missing_ok=True)
        if artifact_directory.exists():
            artifact_directory.rmdir()
        raise
    artifact_checksums = {name: sha256_file(path) for name, path in artifact_paths.items()}
    return PyTorchPipelineBundle(
        preprocessor, datasets, loaders, metadata, artifact_paths, artifact_checksums
    )


def load_pytorch_pipeline(
    repository_root: Path,
    artifact_directory: Path,
    *,
    expected_checksums: Mapping[str, str] = EXPECTED_INPUT_CHECKSUMS,
    expected_split_counts: Mapping[str, int] = EXPECTED_SPLIT_COUNTS,
    expected_positive_counts: Mapping[str, int] = EXPECTED_POSITIVE_COUNTS,
) -> PyTorchPipelineBundle:
    """Reload persisted preprocessing and deterministically rebuild tensors from canonical rows."""
    manifest_path = artifact_directory / "feature-manifest.json"
    preprocessor_path = artifact_directory / "preprocessor.json"
    metadata_path = artifact_directory / "dataset-metadata.json"
    for path in (manifest_path, preprocessor_path, metadata_path):
        if not path.is_file():
            raise ModelingDataError(f"missing PyTorch pipeline artifact: {path}")
    metadata = _json(metadata_path)
    for name, path in (("feature_manifest", manifest_path), ("preprocessor", preprocessor_path)):
        expected = metadata.get("supporting_artifacts", {}).get(name, {}).get("sha256")
        if sha256_file(path) != expected:
            raise ModelingDataError(f"PyTorch {name} checksum mismatch")
    inputs = _validate_inputs(
        repository_root,
        expected_checksums=expected_checksums,
        expected_split_counts=expected_split_counts,
        expected_positive_counts=expected_positive_counts,
    )
    preprocessor = CohortPreprocessor.from_dict(_json(preprocessor_path))
    if (
        preprocessor.source_feature_checksum != inputs.checksums["features"]
        or preprocessor.split_checksum != inputs.checksums["split"]
    ):
        raise ModelingDataError("preprocessor input checksums are incompatible")
    if _json(manifest_path) != preprocessor.manifest():
        raise ModelingDataError("feature manifest does not match preprocessor")
    datasets = _datasets(inputs, preprocessor)
    loader_configuration = metadata.get("loader_configuration", {})
    loaders = create_dataloaders(
        datasets,
        batch_size=int(loader_configuration["batch_size"]),
        seed=int(loader_configuration["train_generator_seed"]),
        num_workers=int(loader_configuration["num_workers"]),
    )
    paths = {
        "feature_manifest": manifest_path,
        "preprocessor": preprocessor_path,
        "dataset_metadata": metadata_path,
    }
    return PyTorchPipelineBundle(
        preprocessor,
        datasets,
        loaders,
        metadata,
        paths,
        {name: sha256_file(path) for name, path in paths.items()},
    )
