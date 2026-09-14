"""Phase 3E configuration, run lifecycle, resume, and evaluation tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.modeling.features import ModelingDataError
from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset
from howreliable.modeling.pytorch.pipeline import PyTorchPipelineBundle
from howreliable.modeling.pytorch.training_config import (
    TrainingConfig,
    config_checksum,
    derive_run_id,
)
from howreliable.modeling.pytorch.training_infrastructure import (
    atomic_write_json,
    compare_with_random_forest,
    create_training_run,
    evaluate_training_run,
    inspect_training_run,
    resume_training_run,
)

FIXED_TIME = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _dataset(count: int, seed: int, dimension: int = 8) -> HowReliableCohortDataset:
    generator = np.random.default_rng(seed)
    features = generator.normal(size=(count, dimension)).astype(np.float32)
    targets = (features[:, 0] + features[:, 1] > 0).astype(np.float32).reshape(-1, 1)
    metadata = [
        {
            "normalized_make": "make",
            "normalized_model": "model",
            "model_year": 2022 - (index % 25),
            "cohort_age_at_cutoff": index % 25,
            "historical_complaint_count": (1, 3, 7, 20, 60)[index % 5],
            "communication_observed_by_cutoff": bool(index % 2),
            "communication_asof_status": "OBSERVED_RECORDS",
            "recall_observed_by_cutoff": bool((index // 2) % 2),
            "recall_asof_status": "OBSERVED_RECORDS",
            "split": "fixture",
        }
        for index in range(count)
    ]
    return HowReliableCohortDataset(
        features, targets, [f"cohort-{seed}-{index}" for index in range(count)], metadata
    )


def _pipeline(*, invalid_test: bool = False, invalid_train: bool = False) -> PyTorchPipelineBundle:
    dimension = 8
    preprocessor = SimpleNamespace(
        transformed_features=tuple(f"feature_{index}" for index in range(dimension)),
        split_checksum="fixture-split",
    )
    return PyTorchPipelineBundle(
        preprocessor=cast(Any, preprocessor),
        datasets={
            "TRAIN": _dataset(48, 1, 7 if invalid_train else dimension),
            "VALIDATION": _dataset(24, 2, dimension),
            "TEST": _dataset(24, 3, 7 if invalid_test else dimension),
        },
        loaders=cast(Any, MagicMock()),
        metadata={
            "inputs": {
                "features": {"sha256": "fixture-features"},
                "target": {"sha256": "fixture-target"},
            }
        },
        artifact_paths={},
        artifact_checksums={
            "feature_manifest": "fixture-manifest",
            "preprocessor": "fixture-preprocessor",
            "dataset_metadata": "fixture-metadata",
        },
    )


def _lineage() -> dict[str, str]:
    return {
        "target_version": "future-complaint-activity-1.0",
        "target_checksum": "fixture-target",
        "feature_checksum": "fixture-features",
        "feature_manifest_checksum": "fixture-manifest",
        "preprocessor_checksum": "fixture-preprocessor",
        "dataset_metadata_checksum": "fixture-metadata",
        "split_checksum": "fixture-split",
    }


def _repository(path: Path) -> Path:
    destination = path / "artifacts/models"
    destination.mkdir(parents=True)
    metrics = {
        "roc_auc": 0.5,
        "pr_auc": 0.5,
        "f1": 0.5,
        "brier_score": 0.25,
    }
    (destination / "baseline-results.json").write_text(
        json.dumps(
            {"best_model": {"validation_metrics": metrics, "test_metrics": metrics}}
        ),
        encoding="utf-8",
    )
    return path


def _config() -> TrainingConfig:
    return TrainingConfig(
        architecture_name="fixture",
        hidden_dimensions=(6,),
        dropout=0.1,
        learning_rate=1e-3,
        batch_size=8,
        max_epochs=5,
        early_stopping_patience=10,
        seed=17,
    )


def test_config_validation_immutability_and_deterministic_run_identity() -> None:
    config = TrainingConfig()
    assert config.hidden_dimensions == (64, 32)
    with pytest.raises(FrozenInstanceError):
        config.seed = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="validation ROC-AUC"):
        TrainingConfig(selection_metric="test_roc_auc")
    first = derive_run_id(config, _lineage())
    second = derive_run_id(config, _lineage())
    assert first == second
    assert first != derive_run_id(TrainingConfig(dropout=0.2), _lineage())
    assert config_checksum(config) == config_checksum(TrainingConfig())


def test_continuous_and_resumed_training_are_identical(tmp_path: Path) -> None:
    continuous_root = _repository(tmp_path / "continuous")
    resumed_root = _repository(tmp_path / "resumed")
    pipeline = _pipeline()
    config = _config()
    continuous = create_training_run(
        continuous_root,
        tmp_path / "unused",
        continuous_root / "runs",
        config,
        pipeline_override=pipeline,
        clock=lambda: FIXED_TIME,
    )
    interrupted = create_training_run(
        resumed_root,
        tmp_path / "unused",
        resumed_root / "runs",
        config,
        pipeline_override=pipeline,
        clock=lambda: FIXED_TIME,
        stop_after_epoch=2,
    )
    assert interrupted["status"] == "INTERRUPTED"
    run_id = continuous["run_id"]
    resumed_directory = resumed_root / "runs" / run_id
    resumed = resume_training_run(
        resumed_root,
        tmp_path / "unused",
        resumed_directory,
        pipeline_override=pipeline,
        clock=lambda: FIXED_TIME,
    )
    assert resumed["status"] == "COMPLETED"
    continuous_directory = continuous_root / "runs" / run_id
    assert (continuous_directory / "history.json").read_bytes() == (
        resumed_directory / "history.json"
    ).read_bytes()
    first = torch.load(
        continuous_directory / "best-checkpoint.pt", map_location="cpu", weights_only=True
    )
    second = torch.load(
        resumed_directory / "best-checkpoint.pt", map_location="cpu", weights_only=True
    )
    assert first["epoch"] == second["epoch"]
    assert all(
        torch.equal(first["state_dict"][name], second["state_dict"][name])
        for name in first["state_dict"]
    )
    latest = torch.load(
        resumed_directory / "latest-state.pt", map_location="cpu", weights_only=True
    )
    assert latest["optimizer_state_dict"]["state"]
    assert latest["rng_state"]["torch"].dtype == torch.uint8
    assert latest["early_stopping"]["best_state_dict"]
    assert inspect_training_run(resumed_directory)["current_epoch"] == 5
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        create_training_run(
            resumed_root,
            tmp_path / "unused",
            resumed_root / "runs",
            config,
            pipeline_override=pipeline,
        )
    with pytest.raises(ArtifactExistsError, match="cannot be resumed"):
        resume_training_run(
            resumed_root,
            tmp_path / "unused",
            resumed_directory,
            pipeline_override=pipeline,
        )


def test_training_avoids_test_and_evaluation_is_explicit(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repo")
    config = _config()
    metadata = create_training_run(
        repository,
        tmp_path / "unused",
        repository / "runs",
        config,
        pipeline_override=_pipeline(invalid_test=True),
        clock=lambda: FIXED_TIME,
    )
    assert metadata["status"] == "COMPLETED"
    run_directory = repository / "runs" / metadata["run_id"]
    validation = evaluate_training_run(
        repository,
        tmp_path / "unused",
        run_directory,
        pipeline_override=_pipeline(invalid_test=True),
        clock=lambda: FIXED_TIME,
    )
    assert validation["split"] == "VALIDATION"
    with pytest.raises(ValueError, match="VALIDATION or TEST"):
        evaluate_training_run(
            repository,
            tmp_path / "unused",
            run_directory,
            split="TRAIN",
            pipeline_override=_pipeline(),
        )
    with pytest.raises(ValueError, match="expected"):
        evaluate_training_run(
            repository,
            tmp_path / "unused",
            run_directory,
            split="TEST",
            pipeline_override=_pipeline(invalid_test=True),
        )

    valid_repository = _repository(tmp_path / "valid")
    valid = create_training_run(
        valid_repository,
        tmp_path / "unused",
        valid_repository / "runs",
        config,
        pipeline_override=_pipeline(),
        clock=lambda: FIXED_TIME,
    )
    valid_directory = valid_repository / "runs" / valid["run_id"]
    test_result = evaluate_training_run(
        valid_repository,
        tmp_path / "unused",
        valid_directory,
        split="TEST",
        pipeline_override=_pipeline(),
        clock=lambda: FIXED_TIME,
    )
    assert test_result["split"] == "TEST"
    assert set(test_result["subgroups"]) == {"age", "historical_support", "source_coverage"}
    assert test_result["promotion_policy"]["preferred_model"] == "random_forest"
    assert test_result["random_forest_comparison"]["neural_minus_random_forest"]
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        evaluate_training_run(
            valid_repository,
            tmp_path / "unused",
            valid_directory,
            split="TEST",
            pipeline_override=_pipeline(),
        )


def test_failure_recording_atomic_write_and_incompatible_resume(tmp_path: Path) -> None:
    destination = tmp_path / "atomic.json"
    atomic_write_json(destination, {"complete": True})
    assert json.loads(destination.read_text()) == {"complete": True}
    assert not list(tmp_path.glob(".atomic.json.*"))
    deltas = compare_with_random_forest(
        {"roc_auc": 0.6, "pr_auc": 0.7, "f1": 0.8, "brier_score": 0.2},
        {"roc_auc": 0.5, "pr_auc": 0.5, "f1": 0.5, "brier_score": 0.25},
    )
    assert deltas["neural_minus_random_forest"]["roc_auc"] == pytest.approx(0.1)

    repository = _repository(tmp_path / "failure")
    with pytest.raises(ValueError, match="expected"):
        create_training_run(
            repository,
            tmp_path / "unused",
            repository / "runs",
            _config(),
            pipeline_override=_pipeline(invalid_train=True),
            clock=lambda: FIXED_TIME,
        )
    run_directory = next((repository / "runs").iterdir())
    failed = json.loads((run_directory / "run-metadata.json").read_text())
    assert failed["status"] == "FAILED"
    assert failed["failure"]["error_type"] == "ValueError"

    interrupted_root = _repository(tmp_path / "incompatible")
    interrupted = create_training_run(
        interrupted_root,
        tmp_path / "unused",
        interrupted_root / "runs",
        _config(),
        pipeline_override=_pipeline(),
        stop_after_epoch=1,
    )
    interrupted_directory = interrupted_root / "runs" / interrupted["run_id"]
    changed = TrainingConfig.from_dict(
        json.loads((interrupted_directory / "config.json").read_text())
    ).to_dict()
    changed["dropout"] = 0.2
    atomic_write_json(interrupted_directory / "config.json", changed)
    with pytest.raises(ModelingDataError, match="configuration checksum"):
        resume_training_run(
            interrupted_root,
            tmp_path / "unused",
            interrupted_directory,
            pipeline_override=_pipeline(),
        )
    assert sha256_file(destination)
