"""Minimal deterministic Phase 3D MLP training and selection experiment."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import torch
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION
from howreliable.modeling.features import FEATURE_CUTOFF, ModelingDataError, _write_json
from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset
from howreliable.modeling.pytorch.evaluation import (
    EvaluationOutput,
    calibration_table,
    evaluate_model,
    permutation_family_importance,
    subgroup_metrics,
)
from howreliable.modeling.pytorch.loaders import DEFAULT_BATCH_SIZE, create_dataloaders
from howreliable.modeling.pytorch.model import HowReliableMLP, MLPConfig
from howreliable.modeling.pytorch.pipeline import (
    BASELINE_COMPARISON,
    PyTorchPipelineBundle,
    load_pytorch_pipeline,
)

EXPERIMENT_VERSION: Final = "first-mlp-1.0"
PRIMARY_SEED: Final = 20220913
ROBUSTNESS_SEEDS: Final = (20220913, 20220914, 20220915, 20220916, 20220917)
MAX_EPOCHS: Final = 100
EARLY_STOPPING_PATIENCE: Final = 10
EARLY_STOPPING_MIN_DELTA: Final = 1e-4
CLASSIFICATION_THRESHOLD: Final = 0.5
SELECTION_NEAR_TIE_TOLERANCE: Final = 1e-4
CPU_DEVICE: Final = torch.device("cpu")
CANDIDATES: Final = (
    MLPConfig("mlp_64_32", (64, 32), 0.1, 1e-3),
    MLPConfig("mlp_64", (64,), 0.1, 1e-3),
    MLPConfig("mlp_128_64", (128, 64), 0.2, 3e-4),
)


def set_deterministic_seed(seed: int) -> None:
    """Seed Python, NumPy, and torch and request deterministic torch operations."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)


def _clone_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    return {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}


@dataclass
class EarlyStopping:
    """Track and retain the state with the best validation ROC-AUC."""

    patience: int
    min_delta: float
    best_metric: float = float("-inf")
    best_epoch: int = 0
    epochs_without_improvement: int = 0
    best_state_dict: dict[str, torch.Tensor] | None = None

    def update(self, metric: float, epoch: int, model: nn.Module) -> bool:
        if metric > self.best_metric + self.min_delta:
            self.best_metric = metric
            self.best_epoch = epoch
            self.epochs_without_improvement = 0
            self.best_state_dict = _clone_state_dict(model)
        else:
            self.epochs_without_improvement += 1
        return self.epochs_without_improvement >= self.patience


@dataclass(frozen=True)
class TrainedCandidate:
    """One validation-selected checkpoint and its training evidence."""

    config: MLPConfig
    seed: int
    parameter_count: int
    best_epoch: int
    stopping_epoch: int
    early_stopping_activated: bool
    history: list[dict[str, Any]]
    final_train: EvaluationOutput
    final_validation: EvaluationOutput
    state_dict: dict[str, torch.Tensor]


def _stable_loader(dataset: HowReliableCohortDataset) -> DataLoader[dict[str, Any]]:
    return DataLoader(
        dataset,
        batch_size=DEFAULT_BATCH_SIZE,
        shuffle=False,
        num_workers=0,
        drop_last=False,
    )


def train_candidate(
    datasets: dict[str, HowReliableCohortDataset],
    config: MLPConfig,
    *,
    seed: int,
    max_epochs: int = MAX_EPOCHS,
    patience: int = EARLY_STOPPING_PATIENCE,
    min_delta: float = EARLY_STOPPING_MIN_DELTA,
    device: torch.device = CPU_DEVICE,
) -> TrainedCandidate:
    """Train one candidate using only TRAIN and VALIDATION datasets."""
    if set(datasets) != {"TRAIN", "VALIDATION", "TEST"}:
        raise ModelingDataError("candidate training requires the frozen three-way datasets")
    set_deterministic_seed(seed)
    input_dimension = datasets["TRAIN"].features.shape[1]
    model = HowReliableMLP(input_dimension, config.hidden_dimensions, config.dropout).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    loaders = create_dataloaders(datasets, batch_size=DEFAULT_BATCH_SIZE, seed=seed)
    validation_loader = _stable_loader(datasets["VALIDATION"])
    stopper = EarlyStopping(patience, min_delta)
    history: list[dict[str, Any]] = []
    stopping_epoch = max_epochs
    activated = False
    for epoch in range(1, max_epochs + 1):
        model.train()
        loss_sum = 0.0
        row_count = 0
        for batch in loaders.train:
            features = cast(torch.Tensor, batch["features"]).to(device)
            targets = cast(torch.Tensor, batch["target"]).to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(features)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            loss_sum += float(loss.item()) * len(targets)
            row_count += len(targets)
        validation = evaluate_model(
            model,
            validation_loader,
            criterion,
            device=device,
            threshold=CLASSIFICATION_THRESHOLD,
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": loss_sum / row_count,
                "validation_loss": validation.metrics["loss"],
                "validation_accuracy": validation.metrics["accuracy"],
                "validation_precision": validation.metrics["precision"],
                "validation_recall": validation.metrics["recall"],
                "validation_f1": validation.metrics["f1"],
                "validation_roc_auc": validation.metrics["roc_auc"],
                "validation_pr_auc": validation.metrics["pr_auc"],
                "validation_brier_score": validation.metrics["brier_score"],
            }
        )
        if stopper.update(cast(float, validation.metrics["roc_auc"]), epoch, model):
            stopping_epoch = epoch
            activated = True
            break
    if stopper.best_state_dict is None:
        raise AssertionError("early stopping did not retain an initial checkpoint")
    model.load_state_dict(stopper.best_state_dict)
    final_train = evaluate_model(
        model,
        _stable_loader(datasets["TRAIN"]),
        criterion,
        device=device,
        threshold=CLASSIFICATION_THRESHOLD,
    )
    final_validation = evaluate_model(
        model,
        validation_loader,
        criterion,
        device=device,
        threshold=CLASSIFICATION_THRESHOLD,
    )
    return TrainedCandidate(
        config=config,
        seed=seed,
        parameter_count=model.parameter_count,
        best_epoch=stopper.best_epoch,
        stopping_epoch=stopping_epoch,
        early_stopping_activated=activated,
        history=history,
        final_train=final_train,
        final_validation=final_validation,
        state_dict=_clone_state_dict(model),
    )


def select_candidate(candidates: list[TrainedCandidate]) -> TrainedCandidate:
    """Select by validation metrics only, with simplicity as the final tie-breaker."""
    if not candidates:
        raise ValueError("candidate selection requires at least one result")
    remaining = list(candidates)
    for metric, maximize in (
        ("roc_auc", True),
        ("pr_auc", True),
        ("f1", True),
        ("brier_score", False),
    ):
        values = [float(item.final_validation.metrics[metric]) for item in remaining]
        best = max(values) if maximize else min(values)
        remaining = [
            item
            for item in remaining
            if abs(float(item.final_validation.metrics[metric]) - best)
            <= SELECTION_NEAR_TIE_TOLERANCE
        ]
        if len(remaining) == 1:
            return remaining[0]
    return min(remaining, key=lambda item: item.parameter_count)


def _candidate_summary(candidate: TrainedCandidate) -> dict[str, Any]:
    return {
        "configuration": candidate.config.to_dict(),
        "seed": candidate.seed,
        "parameter_count": candidate.parameter_count,
        "best_epoch": candidate.best_epoch,
        "stopping_epoch": candidate.stopping_epoch,
        "early_stopping_activated": candidate.early_stopping_activated,
        "train_metrics_at_best_checkpoint": candidate.final_train.metrics,
        "validation_metrics_at_best_checkpoint": candidate.final_validation.metrics,
    }


def _robustness_summary(candidates: list[TrainedCandidate]) -> dict[str, Any]:
    names = {
        "roc_auc": "roc_auc",
        "pr_auc": "pr_auc",
        "f1": "f1",
        "brier_score": "brier_score",
    }
    aggregate = {}
    for output_name, metric_name in names.items():
        values = np.asarray(
            [item.final_validation.metrics[metric_name] for item in candidates],
            dtype=np.float64,
        )
        aggregate[output_name] = {
            "mean": float(np.mean(values)),
            "standard_deviation": float(np.std(values, ddof=0)),
        }
    return {
        "policy": "all configured seeds reported; seed is not a selection criterion",
        "runs": [
            {
                "seed": item.seed,
                "best_epoch": item.best_epoch,
                "stopping_epoch": item.stopping_epoch,
                "validation_metrics": item.final_validation.metrics,
            }
            for item in candidates
        ],
        "aggregate": aggregate,
    }


def _metric_deltas(neural: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float]:
    return {
        name: float(neural[name]) - float(baseline[name])
        for name in ("roc_auc", "pr_auc", "f1", "brier_score")
    }


def validate_checkpoint(checkpoint_path: Path, pipeline: PyTorchPipelineBundle) -> HowReliableMLP:
    """Reload a state_dict checkpoint and verify its input contracts."""
    value = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    required = {
        "state_dict",
        "architecture",
        "input_dimension",
        "target_definition_version",
        "cutoff",
        "feature_manifest_checksum",
        "preprocessor_checksum",
        "split_checksum",
        "seed",
        "training_configuration",
        "best_epoch",
        "best_validation_metrics",
        "torch_version",
    }
    if set(value) != required:
        raise ModelingDataError("checkpoint metadata schema mismatch")
    if (
        value["input_dimension"] != len(pipeline.preprocessor.transformed_features)
        or value["feature_manifest_checksum"] != pipeline.artifact_checksums["feature_manifest"]
        or value["preprocessor_checksum"] != pipeline.artifact_checksums["preprocessor"]
        or value["split_checksum"] != pipeline.preprocessor.split_checksum
    ):
        raise ModelingDataError("checkpoint input contract mismatch")
    architecture = value["architecture"]
    model = HowReliableMLP(
        int(value["input_dimension"]),
        tuple(architecture["hidden_dimensions"]),
        float(architecture["dropout"]),
    )
    model.load_state_dict(value["state_dict"], strict=True)
    model.eval()
    return model


def run_first_mlp_experiment(
    repository_root: Path,
    pipeline_artifact_directory: Path,
    output_directory: Path,
    *,
    candidates: tuple[MLPConfig, ...] = CANDIDATES,
    robustness_seeds: tuple[int, ...] = ROBUSTNESS_SEEDS,
    max_epochs: int = MAX_EPOCHS,
    patience: int = EARLY_STOPPING_PATIENCE,
    min_delta: float = EARLY_STOPPING_MIN_DELTA,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    progress: Callable[[str], None] | None = None,
    pipeline_override: PyTorchPipelineBundle | None = None,
    baseline_results_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the bounded validation-only search, robustness study, and one final test."""
    if output_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite neural output: {output_directory}")
    pipeline = pipeline_override or load_pytorch_pipeline(
        repository_root, pipeline_artifact_directory
    )
    device = torch.device("cpu")
    candidate_results: list[TrainedCandidate] = []
    for config in candidates:
        trained = train_candidate(
            pipeline.datasets,
            config,
            seed=PRIMARY_SEED,
            max_epochs=max_epochs,
            patience=patience,
            min_delta=min_delta,
            device=device,
        )
        candidate_results.append(trained)
        if progress:
            progress(
                f"{config.name}: best epoch {trained.best_epoch}, "
                f"validation ROC-AUC {trained.final_validation.metrics['roc_auc']:.6f}"
            )
    selected = select_candidate(candidate_results)
    if progress:
        progress(f"selected {selected.config.name} using validation metrics only")

    robustness = [selected]
    for seed in robustness_seeds:
        if seed == PRIMARY_SEED:
            continue
        trained = train_candidate(
            pipeline.datasets,
            selected.config,
            seed=seed,
            max_epochs=max_epochs,
            patience=patience,
            min_delta=min_delta,
            device=device,
        )
        robustness.append(trained)
        if progress:
            progress(
                f"robustness seed {seed}: validation ROC-AUC "
                f"{trained.final_validation.metrics['roc_auc']:.6f}"
            )

    model = HowReliableMLP(
        len(pipeline.preprocessor.transformed_features),
        selected.config.hidden_dimensions,
        selected.config.dropout,
    ).to(device)
    model.load_state_dict(selected.state_dict, strict=True)
    criterion = nn.BCEWithLogitsLoss()
    final_test = evaluate_model(
        model,
        _stable_loader(pipeline.datasets["TEST"]),
        criterion,
        device=device,
        threshold=CLASSIFICATION_THRESHOLD,
    )
    validation_calibration = calibration_table(selected.final_validation)
    test_calibration = calibration_table(final_test)
    test_subgroups = subgroup_metrics(final_test)
    family_importance = permutation_family_importance(
        model,
        pipeline.datasets["VALIDATION"],
        pipeline.preprocessor.manifest(),
        device=device,
        seed=PRIMARY_SEED,
    )

    baseline_results = baseline_results_override or json.loads(
        (repository_root / "artifacts/models/baseline-results.json").read_text(encoding="utf-8")
    )
    baseline_validation = baseline_results["best_model"]["validation_metrics"]
    baseline_test = baseline_results["best_model"]["test_metrics"]
    artifact_paths = {
        "checkpoint": output_directory / "first-mlp.pt",
        "history": output_directory / "first-mlp-history.json",
        "results": output_directory / "first-mlp-results.json",
    }
    history_artifact = {
        "experiment_version": EXPERIMENT_VERSION,
        "selected_configuration": selected.config.to_dict(),
        "seed": PRIMARY_SEED,
        "best_epoch": selected.best_epoch,
        "stopping_epoch": selected.stopping_epoch,
        "epochs": selected.history,
    }
    training_configuration = {
        "loss": "BCEWithLogitsLoss",
        "optimizer": "AdamW",
        "batch_size": DEFAULT_BATCH_SIZE,
        "max_epochs": max_epochs,
        "early_stopping_patience": patience,
        "early_stopping_minimum_roc_auc_improvement": min_delta,
        "classification_threshold": CLASSIFICATION_THRESHOLD,
        "device": str(device),
        "deterministic_algorithms": True,
    }
    checkpoint = {
        "state_dict": selected.state_dict,
        "architecture": selected.config.to_dict(),
        "input_dimension": len(pipeline.preprocessor.transformed_features),
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "cutoff": FEATURE_CUTOFF.isoformat(),
        "feature_manifest_checksum": pipeline.artifact_checksums["feature_manifest"],
        "preprocessor_checksum": pipeline.artifact_checksums["preprocessor"],
        "split_checksum": pipeline.preprocessor.split_checksum,
        "seed": PRIMARY_SEED,
        "training_configuration": training_configuration,
        "best_epoch": selected.best_epoch,
        "best_validation_metrics": selected.final_validation.metrics,
        "torch_version": str(torch.__version__),
    }
    output_directory.mkdir(parents=True, exist_ok=False)
    try:
        _write_json(artifact_paths["history"], history_artifact)
        torch.save(checkpoint, artifact_paths["checkpoint"])
        results = {
            "experiment_version": EXPERIMENT_VERSION,
            "creation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "scientific_purpose": "first neural comparison; no assumption of superiority",
            "input_contract": {
                "pipeline_version": pipeline.metadata["pipeline_version"],
                "tensor_dimension": len(pipeline.preprocessor.transformed_features),
                "feature_manifest_checksum": pipeline.artifact_checksums["feature_manifest"],
                "preprocessor_checksum": pipeline.artifact_checksums["preprocessor"],
                "split_checksum": pipeline.preprocessor.split_checksum,
                "target_checksum": pipeline.metadata["inputs"]["target"]["sha256"],
                "split_counts": {
                    name: len(dataset) for name, dataset in pipeline.datasets.items()
                },
                "positive_counts": {
                    name: int(dataset.targets.sum().item())
                    for name, dataset in pipeline.datasets.items()
                },
            },
            "selection_policy": (
                "validation ROC-AUC, PR-AUC, F1, lower Brier, then simpler parameter count; "
                "1e-4 near-tie tolerance at each metric; test excluded"
            ),
            "threshold_policy": "fixed 0.5; no threshold tuning",
            "candidate_results": [_candidate_summary(item) for item in candidate_results],
            "selected_configuration": _candidate_summary(selected),
            "final_test_metrics": final_test.metrics,
            "calibration": {
                "validation": validation_calibration,
                "test": test_calibration,
            },
            "test_subgroups": test_subgroups,
            "validation_permutation_family_importance": family_importance,
            "seed_robustness": _robustness_summary(robustness),
            "random_forest_baseline": {
                "contract": BASELINE_COMPARISON,
                "validation_metrics": baseline_validation,
                "test_metrics": baseline_test,
            },
            "neural_minus_random_forest": {
                "validation": _metric_deltas(
                    selected.final_validation.metrics, baseline_validation
                ),
                "test": _metric_deltas(final_test.metrics, baseline_test),
            },
            "training_configuration": training_configuration,
            "artifacts": {
                "checkpoint": {
                    "path": artifact_paths["checkpoint"].name,
                    "size_bytes": artifact_paths["checkpoint"].stat().st_size,
                    "sha256": sha256_file(artifact_paths["checkpoint"]),
                },
                "history": {
                    "path": artifact_paths["history"].name,
                    "size_bytes": artifact_paths["history"].stat().st_size,
                    "sha256": sha256_file(artifact_paths["history"]),
                },
            },
            "test_evaluation_count": 1,
            "phase_3e_started": False,
        }
        _write_json(artifact_paths["results"], results)
    except Exception:
        for path in artifact_paths.values():
            path.unlink(missing_ok=True)
        output_directory.rmdir()
        raise
    validate_checkpoint(artifact_paths["checkpoint"], pipeline)
    results["result_artifact_sha256"] = sha256_file(artifact_paths["results"])
    return results
