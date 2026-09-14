"""Phase 3D first-network model, training, evaluation, and artifact tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.modeling.features import ModelingDataError
from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset
from howreliable.modeling.pytorch.evaluation import (
    calibration_table,
    evaluate_model,
    subgroup_metrics,
)
from howreliable.modeling.pytorch.model import HowReliableMLP, MLPConfig
from howreliable.modeling.pytorch.pipeline import PyTorchPipelineBundle
from howreliable.modeling.pytorch.training import (
    EarlyStopping,
    run_first_mlp_experiment,
    select_candidate,
    set_deterministic_seed,
    train_candidate,
    validate_checkpoint,
)

FIXED_TIME = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _dataset(start: int, count: int, seed: int) -> HowReliableCohortDataset:
    generator = np.random.default_rng(seed)
    features = generator.normal(size=(count, 90)).astype(np.float32)
    logits = features[:, 0] * 1.5 + features[:, 1] * 0.5
    targets = (logits > np.median(logits)).astype(np.float32).reshape(-1, 1)
    identifiers = [f"cohort-{start + index:03d}" for index in range(count)]
    metadata = []
    ages = (1, 4, 8, 15, 25)
    supports = (1, 3, 7, 20, 60)
    for index in range(count):
        metadata.append(
            {
                "normalized_make": "make",
                "normalized_model": "model",
                "model_year": 2022 - ages[index % len(ages)],
                "cohort_age_at_cutoff": ages[index % len(ages)],
                "historical_complaint_count": supports[index % len(supports)],
                "communication_observed_by_cutoff": bool(index % 2),
                "communication_asof_status": "OBSERVED_RECORDS"
                if index % 2
                else "NO_MATCHED_RECORD",
                "recall_observed_by_cutoff": bool((index // 2) % 2),
                "recall_asof_status": "OBSERVED_RECORDS"
                if (index // 2) % 2
                else "NO_MATCHED_RECORD",
                "split": "TRAIN",
            }
        )
    return HowReliableCohortDataset(features, targets, identifiers, metadata)


def _datasets() -> dict[str, HowReliableCohortDataset]:
    return {
        "TRAIN": _dataset(0, 64, 1),
        "VALIDATION": _dataset(100, 30, 2),
        "TEST": _dataset(200, 30, 3),
    }


def _loader(dataset: HowReliableCohortDataset) -> DataLoader[dict[str, Any]]:
    return DataLoader(dataset, batch_size=8, shuffle=False, num_workers=0)


def test_mlp_raw_logits_shapes_loss_and_deterministic_initialization() -> None:
    set_deterministic_seed(7)
    first = HowReliableMLP(90, (64, 32), 0.1)
    set_deterministic_seed(7)
    second = HowReliableMLP(90, (64, 32), 0.1)
    assert all(
        torch.equal(left, right)
        for left, right in zip(
            first.state_dict().values(), second.state_dict().values(), strict=True
        )
    )
    features = torch.randn(5, 90)
    logits = first(features)
    assert logits.shape == (5, 1)
    assert first.parameter_count == 7_937
    loss = nn.BCEWithLogitsLoss()(logits, torch.ones(5, 1))
    assert torch.isfinite(loss)
    for parameter in first.parameters():
        parameter.data.zero_()
    final_linear = next(layer for layer in reversed(first.network) if isinstance(layer, nn.Linear))
    final_linear.bias.data.fill_(2)
    assert torch.all(first(features) == 2)
    assert torch.all((torch.sigmoid(first(features)) >= 0) & (torch.sigmoid(first(features)) <= 1))


def test_optimizer_changes_parameters_and_evaluation_does_not() -> None:
    datasets = _datasets()
    model = HowReliableMLP(90, (8,), 0.0)
    optimizer = AdamW(model.parameters(), lr=1e-3)
    criterion = nn.BCEWithLogitsLoss()
    batch = next(iter(_loader(datasets["TRAIN"])))
    before = {name: value.clone() for name, value in model.state_dict().items()}
    loss = criterion(model(batch["features"]), batch["target"])
    loss.backward()
    optimizer.step()
    assert any(not torch.equal(before[name], value) for name, value in model.state_dict().items())
    before_evaluation = {name: value.clone() for name, value in model.state_dict().items()}
    output = evaluate_model(
        model, _loader(datasets["VALIDATION"]), criterion, device=torch.device("cpu")
    )
    assert all(
        torch.equal(before_evaluation[name], value) for name, value in model.state_dict().items()
    )
    assert np.all((output.probabilities >= 0) & (output.probabilities <= 1))
    assert sum(sum(row) for row in output.metrics["confusion_matrix"]) == len(
        datasets["VALIDATION"]
    )
    assert 0 <= output.metrics["brier_score"] <= 1


def test_early_stopping_restoration_and_deterministic_training() -> None:
    model = HowReliableMLP(90, (8,), 0.0)
    stopper = EarlyStopping(patience=2, min_delta=0.01)
    assert stopper.update(0.7, 1, model) is False
    retained = {name: value.clone() for name, value in stopper.best_state_dict.items()}  # type: ignore[union-attr]
    for parameter in model.parameters():
        parameter.data.add_(1)
    assert stopper.update(0.705, 2, model) is False
    assert stopper.update(0.704, 3, model) is True
    model.load_state_dict(stopper.best_state_dict)  # type: ignore[arg-type]
    assert all(torch.equal(retained[name], value) for name, value in model.state_dict().items())

    config = MLPConfig("tiny", (8,), 0.1, 1e-3)
    first_datasets = _datasets()
    second_datasets = _datasets()
    second_datasets["TEST"] = _dataset(900, 52, 99)
    first = train_candidate(first_datasets, config, seed=11, max_epochs=8, patience=3)
    second = train_candidate(second_datasets, config, seed=11, max_epochs=8, patience=3)
    assert first.history == second.history
    assert first.best_epoch == second.best_epoch
    assert all(
        torch.equal(first.state_dict[name], second.state_dict[name]) for name in first.state_dict
    )
    restored = HowReliableMLP(90, (8,), 0.1)
    restored.load_state_dict(first.state_dict)
    evaluation = evaluate_model(
        restored,
        _loader(_datasets()["VALIDATION"]),
        nn.BCEWithLogitsLoss(),
        device=torch.device("cpu"),
    )
    for name, value in evaluation.metrics.items():
        if name == "loss":
            assert value == pytest.approx(first.final_validation.metrics[name], abs=1e-7)
        else:
            assert value == first.final_validation.metrics[name]


def test_validation_selection_calibration_and_subgroups() -> None:
    simple = train_candidate(
        _datasets(), MLPConfig("simple", (8,), 0.0, 1e-3), seed=2, max_epochs=4, patience=2
    )
    wider = train_candidate(
        _datasets(), MLPConfig("wider", (16, 8), 0.1, 1e-3), seed=2, max_epochs=4, patience=2
    )
    simple.final_validation.metrics.update(
        {"roc_auc": 0.9, "pr_auc": 0.9, "f1": 0.85, "brier_score": 0.12}
    )
    wider.final_validation.metrics.update(
        {"roc_auc": 0.90005, "pr_auc": 0.90005, "f1": 0.80, "brier_score": 0.12}
    )
    selected = select_candidate([simple, wider])
    assert selected is simple
    assert not hasattr(selected, "final_test")
    table = calibration_table(selected.final_validation)
    assert len(table) == 10
    assert sum(item["row_count"] for item in table) == len(_datasets()["VALIDATION"])
    groups = subgroup_metrics(selected.final_validation)
    assert set(groups) == {"age", "historical_support", "source_coverage"}
    for family in groups.values():
        for metrics in family.values():
            assert metrics["positive_count"] >= 0
            assert sum(sum(row) for row in metrics["confusion_matrix"]) == metrics["row_count"]


def _synthetic_pipeline() -> PyTorchPipelineBundle:
    feature_names = tuple(f"feature_{index}" for index in range(90))
    preprocessor = SimpleNamespace(
        transformed_features=feature_names,
        split_checksum="synthetic-split-checksum",
        manifest=lambda: {
            "features": [
                {
                    "feature_name": name,
                    "feature_family": "static_vehicle",
                    "tensor_index": index,
                }
                for index, name in enumerate(feature_names)
            ]
        },
    )
    return PyTorchPipelineBundle(
        preprocessor=cast(Any, preprocessor),
        datasets=_datasets(),
        loaders=cast(Any, MagicMock()),
        metadata={
            "pipeline_version": "synthetic-pipeline-1.0",
            "inputs": {"target": {"sha256": "synthetic-target-checksum"}},
        },
        artifact_paths={},
        artifact_checksums={
            "feature_manifest": "synthetic-feature-manifest-checksum",
            "preprocessor": "synthetic-preprocessor-checksum",
        },
    )


def _synthetic_baseline_results() -> dict[str, Any]:
    metrics = {
        "accuracy": 0.5,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "roc_auc": 0.5,
        "pr_auc": 0.5,
        "brier_score": 0.25,
        "confusion_matrix": [[5, 5], [5, 5]],
        "predicted_positive_rate": 0.5,
    }
    return {"best_model": {"validation_metrics": metrics, "test_metrics": metrics}}


def test_synthetic_artifacts_checkpoint_and_no_phase_3e(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[1]
    pipeline = _synthetic_pipeline()
    output = tmp_path / "models"
    result = run_first_mlp_experiment(
        repository_root,
        repository_root / "artifacts/modeling/pytorch",
        output,
        candidates=(MLPConfig("fixture", (8,), 0.0, 1e-3),),
        robustness_seeds=(20220913,),
        max_epochs=2,
        patience=1,
        clock=lambda: FIXED_TIME,
        pipeline_override=pipeline,
        baseline_results_override=_synthetic_baseline_results(),
    )
    assert result["test_evaluation_count"] == 1
    assert result["phase_3e_started"] is False
    assert result["threshold_policy"].startswith("fixed 0.5")
    for name in ("first-mlp.pt", "first-mlp-history.json", "first-mlp-results.json"):
        assert (output / name).is_file()
    saved = torch.load(output / "first-mlp.pt", map_location="cpu", weights_only=True)
    assert isinstance(saved["state_dict"], dict)
    assert not any(isinstance(value, nn.Module) for value in saved.values())
    persisted_result = json.loads((output / "first-mlp-results.json").read_text())
    assert persisted_result["artifacts"]["checkpoint"]["sha256"] == sha256_file(
        output / "first-mlp.pt"
    )
    assert persisted_result["artifacts"]["history"]["sha256"] == sha256_file(
        output / "first-mlp-history.json"
    )
    reloaded = validate_checkpoint(output / "first-mlp.pt", pipeline)
    assert reloaded(torch.zeros(2, 90)).shape == (2, 1)
    incompatible_path = tmp_path / "incompatible.pt"
    saved["split_checksum"] = "wrong-split"
    torch.save(saved, incompatible_path)
    with pytest.raises(ModelingDataError, match="input contract mismatch"):
        validate_checkpoint(incompatible_path, pipeline)
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        run_first_mlp_experiment(
            repository_root,
            repository_root / "artifacts/modeling/pytorch",
            output,
            candidates=(MLPConfig("fixture", (8,), 0.0, 1e-3),),
            robustness_seeds=(20220913,),
            max_epochs=1,
            pipeline_override=pipeline,
            baseline_results_override=_synthetic_baseline_results(),
        )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repository_root / "src/howreliable/modeling/pytorch").glob("*.py")
    )
    assert "pytorch_lightning" not in source
    assert "torch.distributed" not in source
