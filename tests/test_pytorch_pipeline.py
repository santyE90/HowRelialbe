"""Phase 3C preprocessing, Dataset, DataLoader, and artifact tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from torch.utils.data import RandomSampler, SequentialSampler

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL
from howreliable.modeling.features import FEATURE_MATRIX_VERSION, FEATURE_SCHEMA, ModelingDataError
from howreliable.modeling.pytorch import (
    create_dataloaders,
    generate_pytorch_pipeline,
    load_pytorch_pipeline,
)
from howreliable.modeling.pytorch.pipeline import (
    EXPECTED_INPUT_CHECKSUMS,
    EXPECTED_POSITIVE_COUNTS,
    EXPECTED_SPLIT_COUNTS,
)
from howreliable.modeling.pytorch.preprocessing import _feature_type
from howreliable.modeling.splits import SPLIT_SCHEMA, SPLIT_SEED, SPLIT_VERSION

FIXED_TIME = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
SMALL_COUNTS = {"TRAIN": 12, "VALIDATION": 4, "TEST": 4}
SMALL_POSITIVES = {"TRAIN": 6, "VALIDATION": 2, "TEST": 2}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _schema_row(schema: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    return {name: values.get(name) for name in schema}


def _feature_row(index: int) -> dict[str, Any]:
    values: dict[str, Any] = {
        "broad_vehicle_id": f"cohort-{index:02d}",
        "normalized_make": f"make-{index % 3}",
        "normalized_model": f"model-{index % 7}",
        "model_year": 2000 + index,
        "cohort_age_at_cutoff": 22 - index,
        "feature_cutoff_date": "2022-12-31",
    }
    for feature_index, name in enumerate(ALL_EVIDENCE_MODEL, start=1):
        feature_type = _feature_type(name)
        if feature_type == "binary":
            values[name] = bool(index % 2) if feature_index % 2 else bool((index // 2) % 2)
        elif feature_type == "share":
            values[name] = ((index + 1) * feature_index % 101) / 100
        elif feature_type == "count":
            values[name] = (index + 1) * feature_index
        else:
            values[name] = float((index + 1) * feature_index)
    values["model_year"] = 2000 + index
    values["cohort_age_at_cutoff"] = 22 - index
    values["historical_mileage_median"] = None if index in {0, 12, 16} else 10_000 + index
    values["days_since_last_communication"] = None if index in {1, 13, 17} else index + 2
    values["days_since_last_recall"] = None if index in {2, 14, 18} else index + 3
    values["communication_asof_status"] = (
        "OBSERVED_RECORDS" if values["communication_observed_by_cutoff"] else "NO_MATCHED_RECORD"
    )
    values["recall_asof_status"] = (
        "OBSERVED_RECORDS" if values["recall_observed_by_cutoff"] else "NO_MATCHED_RECORD"
    )
    return _schema_row(FEATURE_SCHEMA, values)


def _target_row(index: int, positive: bool) -> dict[str, Any]:
    values: dict[str, Any] = {
        "broad_vehicle_id": f"cohort-{index:02d}",
        "normalized_make": f"make-{index % 3}",
        "normalized_model": f"model-{index % 7}",
        "model_year": 2000 + index,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "cutoff_date": "2022-12-31",
        "horizon_months": 12,
        "future_window_start": "2023-01-01",
        "future_window_end": "2023-12-31",
        "target_eligible": True,
        "eligibility_reason": "ELIGIBLE",
        "cohort_age_at_cutoff": 22 - index,
        "historical_complaint_count": index + 1,
        "future_complaint_count": int(positive),
        "future_any_complaint": positive,
    }
    for name in TARGET_SCHEMA:
        if name.startswith("future_") and name not in values:
            values[name] = False if name.endswith("activity") else 0
    return _schema_row(TARGET_SCHEMA, values)


def _small_repository(root: Path, *, extreme_holdout: bool = False) -> dict[str, str]:
    features = [_feature_row(index) for index in range(20)]
    if extreme_holdout:
        for index in range(12, 20):
            for name in ALL_EVIDENCE_MODEL:
                if name not in {"model_year", "cohort_age_at_cutoff"} and _feature_type(name) in {
                    "count",
                    "continuous",
                }:
                    features[index][name] = 1_000_000_000
    split_names = ["TRAIN"] * 12 + ["VALIDATION"] * 4 + ["TEST"] * 4
    positive_by_split = {"TRAIN": 0, "VALIDATION": 0, "TEST": 0}
    targets = []
    splits = []
    for index, split in enumerate(split_names):
        positive = positive_by_split[split] < SMALL_POSITIVES[split]
        positive_by_split[split] += int(positive)
        targets.append(_target_row(index, positive))
        splits.append(
            _schema_row(SPLIT_SCHEMA, {"broad_vehicle_id": f"cohort-{index:02d}", "split": split})
        )

    feature_path = root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl"
    target_path = root / "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl"
    split_path = root / "data/processed/modeling/cohort-split-2022-12-31.jsonl"
    baseline_path = root / "artifacts/models/baseline-results.json"
    _write_jsonl(feature_path, features)
    _write_jsonl(target_path, targets)
    _write_jsonl(split_path, splits)
    _write_json(baseline_path, {"best_model": {"model": "random_forest"}})
    checksums = {
        "features": sha256_file(feature_path),
        "target": sha256_file(target_path),
        "split": sha256_file(split_path),
        "baseline_results": sha256_file(baseline_path),
    }
    _write_json(
        feature_path.with_name(f"{feature_path.stem}.provenance.json"),
        {
            "output_sha256": checksums["features"],
            "feature_matrix_version": FEATURE_MATRIX_VERSION,
            "cutoff": "2022-12-31",
        },
    )
    _write_json(
        target_path.with_name(f"{target_path.stem}.provenance.json"),
        {
            "output_sha256": checksums["target"],
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "target_definition": {"cutoff": "2022-12-31"},
        },
    )
    _write_json(
        split_path.with_name(f"{split_path.stem}.provenance.json"),
        {
            "output_sha256": checksums["split"],
            "split_version": SPLIT_VERSION,
            "seed": SPLIT_SEED,
            "input_checksums": {
                "features": checksums["features"],
                "target": checksums["target"],
            },
        },
    )
    return checksums


def _generate_small(root: Path, output: Path, *, extreme_holdout: bool = False) -> Any:
    checksums = _small_repository(root, extreme_holdout=extreme_holdout)
    return generate_pytorch_pipeline(
        root,
        output,
        expected_checksums=checksums,
        expected_split_counts=SMALL_COUNTS,
        expected_positive_counts=SMALL_POSITIVES,
        clock=lambda: FIXED_TIME,
    )


def _loader_ids(loader: Any) -> list[str]:
    return [identifier for batch in loader for identifier in batch["cohort_id"]]


def test_preprocessor_tensor_dataset_and_manifest_contract(tmp_path: Path) -> None:
    bundle = _generate_small(tmp_path, tmp_path / "output")
    preprocessor = bundle.preprocessor
    assert len(bundle.datasets["TRAIN"]) == 12
    assert len(bundle.datasets["VALIDATION"]) == 4
    assert len(bundle.datasets["TEST"]) == 4
    assert "normalized_make" not in preprocessor.input_features
    assert "normalized_model" not in preprocessor.input_features
    assert not any(name.startswith("future_") for name in preprocessor.input_features)
    assert preprocessor.missing_indicators == (
        "historical_mileage_median",
        "days_since_last_communication",
        "days_since_last_recall",
    )
    item = bundle.datasets["TRAIN"][0]
    assert item["features"].dtype == torch.float32
    assert item["target"].dtype == torch.float32
    assert item["features"].shape == (len(preprocessor.transformed_features),)
    assert item["target"].shape == (1,)
    assert torch.isfinite(item["features"]).all()
    assert item["cohort_id"] == "cohort-00"
    assert set(item["metadata"]) == {
        "normalized_make",
        "normalized_model",
        "model_year",
        "cohort_age_at_cutoff",
        "historical_complaint_count",
        "communication_observed_by_cutoff",
        "communication_asof_status",
        "recall_observed_by_cutoff",
        "recall_asof_status",
        "split",
    }

    row = _feature_row(0)
    count_name = "historical_complaint_count"
    count_index = preprocessor.transformed_features.index(count_name)
    expected_count = (
        np.log1p(float(row[count_name])) - preprocessor.means[count_name]
    ) / preprocessor.standard_deviations[count_name]
    assert item["features"][count_index].item() == pytest.approx(expected_count)
    share_name = "historical_complaint_component_engine_share"
    assert item["features"][
        preprocessor.transformed_features.index(share_name)
    ].item() == pytest.approx(row[share_name])
    binary_name = "communication_observed_by_cutoff"
    assert item["features"][preprocessor.transformed_features.index(binary_name)].item() in {0, 1}
    missing_index = preprocessor.transformed_features.index("historical_mileage_median__missing")
    assert item["features"][missing_index].item() == 1

    manifest = preprocessor.manifest()
    assert [item["tensor_index"] for item in manifest["features"]] == list(
        range(len(preprocessor.transformed_features))
    )
    assert [item["feature_name"] for item in manifest["features"]] == list(
        preprocessor.transformed_features
    )
    assert {item["input_type"] for item in manifest["features"]} == {"numeric"}
    assert not torch.isnan(bundle.datasets["TRAIN"].features).any()
    assert bundle.metadata["missing_after_preprocessing"] == 0
    assert bundle.metadata["model_training_performed"] is False


def test_training_only_statistics_ignore_validation_and_test_extremes(tmp_path: Path) -> None:
    ordinary = _generate_small(tmp_path / "ordinary", tmp_path / "ordinary-output")
    extreme = _generate_small(
        tmp_path / "extreme", tmp_path / "extreme-output", extreme_holdout=True
    )
    assert ordinary.preprocessor.medians == extreme.preprocessor.medians
    assert ordinary.preprocessor.means == extreme.preprocessor.means
    assert ordinary.preprocessor.standard_deviations == extreme.preprocessor.standard_deviations
    assert ordinary.preprocessor.input_features == extreme.preprocessor.input_features


def test_dataloader_shapes_order_shuffle_and_membership(tmp_path: Path) -> None:
    bundle = _generate_small(tmp_path, tmp_path / "output")
    first = create_dataloaders(bundle.datasets, batch_size=5, seed=7)
    second = create_dataloaders(bundle.datasets, batch_size=5, seed=7)
    different = create_dataloaders(bundle.datasets, batch_size=5, seed=8)
    first_train = _loader_ids(first.train)
    assert first_train == _loader_ids(second.train)
    assert first_train != _loader_ids(different.train)
    assert set(first_train) == set(bundle.datasets["TRAIN"].cohort_ids)
    assert len(first_train) == len(set(first_train)) == len(bundle.datasets["TRAIN"])
    assert _loader_ids(first.validation) == list(bundle.datasets["VALIDATION"].cohort_ids)
    assert _loader_ids(first.test) == list(bundle.datasets["TEST"].cohort_ids)
    assert isinstance(first.train.sampler, RandomSampler)
    assert isinstance(first.validation.sampler, SequentialSampler)
    assert isinstance(first.test.sampler, SequentialSampler)
    train_batch = next(iter(create_dataloaders(bundle.datasets, batch_size=5, seed=7).train))
    assert train_batch["features"].shape == (5, len(bundle.preprocessor.transformed_features))
    assert train_batch["target"].shape == (5, 1)
    assert train_batch["features"].dtype == train_batch["target"].dtype == torch.float32
    with pytest.raises(ValueError, match="num_workers=0"):
        create_dataloaders(bundle.datasets, num_workers=1)


def test_artifacts_reload_determinism_overwrite_and_checksum_guards(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = _generate_small(first_root, first_root / "output")
    second = _generate_small(second_root, second_root / "output")
    for name in ("feature_manifest", "preprocessor", "dataset_metadata"):
        assert first.artifact_paths[name].read_bytes() == second.artifact_paths[name].read_bytes()
        assert first.artifact_checksums[name] == sha256_file(first.artifact_paths[name])
    loaded = load_pytorch_pipeline(
        first_root,
        first_root / "output",
        expected_checksums={
            name: value["sha256"] for name, value in first.metadata["inputs"].items()
        },
        expected_split_counts=SMALL_COUNTS,
        expected_positive_counts=SMALL_POSITIVES,
    )
    assert torch.equal(loaded.datasets["TRAIN"].features, first.datasets["TRAIN"].features)
    assert loaded.datasets["VALIDATION"].cohort_ids == first.datasets["VALIDATION"].cohort_ids
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        generate_pytorch_pipeline(
            first_root,
            first_root / "output",
            expected_checksums=_small_repository(first_root),
            expected_split_counts=SMALL_COUNTS,
            expected_positive_counts=SMALL_POSITIVES,
        )
    feature_path = second_root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl"
    feature_path.write_text(feature_path.read_text() + "{}\n", encoding="utf-8")
    with pytest.raises(ModelingDataError, match="features checksum"):
        generate_pytorch_pipeline(
            second_root,
            second_root / "bad-output",
            expected_checksums={
                **_small_repository(tmp_path / "checksum-reference"),
                "target": second.metadata["inputs"]["target"]["sha256"],
                "split": second.metadata["inputs"]["split"]["sha256"],
                "baseline_results": second.metadata["inputs"]["baseline_results"]["sha256"],
            },
            expected_split_counts=SMALL_COUNTS,
            expected_positive_counts=SMALL_POSITIVES,
        )


def test_full_corpus_contract_and_no_phase_3d_code(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[1]
    if not all(
        (repository_root / path).is_file()
        for path in (
            "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl",
            "data/processed/modeling/cohort-split-2022-12-31.jsonl",
            "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl",
            "artifacts/models/baseline-results.json",
        )
    ):
        pytest.skip("validated full-corpus Phase 3B artifacts are not present")
    bundle = generate_pytorch_pipeline(repository_root, tmp_path / "full", clock=lambda: FIXED_TIME)
    assert {name: len(value) for name, value in bundle.datasets.items()} == EXPECTED_SPLIT_COUNTS
    assert {
        name: int(value.targets.sum().item()) for name, value in bundle.datasets.items()
    } == EXPECTED_POSITIVE_COUNTS
    assert bundle.metadata["input_row_count"] == 8_416
    assert bundle.metadata["canonical_feature_column_count"] == 114
    assert bundle.metadata["candidate_model_input_count"] == 102
    assert bundle.metadata["selected_model_input_count"] == 87
    assert bundle.metadata["final_tensor_dimension"] == 90
    assert bundle.metadata["missing_before_preprocessing_total"] == 4_643
    assert bundle.metadata["missing_after_preprocessing"] == 0
    assert bundle.metadata["inputs"]["features"]["sha256"] == EXPECTED_INPUT_CHECKSUMS["features"]
    package = repository_root / "src/howreliable/modeling/pytorch"
    forbidden = ("nn.Module", "BCEWithLogitsLoss", "torch.optim", "optimizer.step")
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert not any(token in source for token in forbidden)
