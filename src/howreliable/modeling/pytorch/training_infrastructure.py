"""Minimal resumable and auditable Phase 3E PyTorch training infrastructure."""

from __future__ import annotations

import json
import os
import platform
import random
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import torch
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION
from howreliable.modeling.features import FEATURE_CUTOFF, ModelingDataError
from howreliable.modeling.pytorch.evaluation import (
    calibration_table,
    evaluate_model,
    subgroup_metrics,
)
from howreliable.modeling.pytorch.loaders import create_dataloaders
from howreliable.modeling.pytorch.model import HowReliableMLP
from howreliable.modeling.pytorch.pipeline import PyTorchPipelineBundle, load_pytorch_pipeline
from howreliable.modeling.pytorch.training import EarlyStopping, set_deterministic_seed
from howreliable.modeling.pytorch.training_config import (
    TRAINING_INFRASTRUCTURE_VERSION,
    TrainingConfig,
    config_checksum,
    derive_run_id,
)

RUN_STATUSES: Final = {"CREATED", "RUNNING", "INTERRUPTED", "COMPLETED", "FAILED"}


def _timestamp(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(UTC).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON completely in the destination directory before atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def atomic_torch_save(path: Path, value: Any) -> None:
    """Atomically replace a torch state artifact without serializing a module."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(value, temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _lineage(pipeline: PyTorchPipelineBundle) -> dict[str, str]:
    inputs = pipeline.metadata["inputs"]
    return {
        "target_version": TARGET_DEFINITION_VERSION,
        "target_checksum": str(inputs["target"]["sha256"]),
        "feature_checksum": str(inputs["features"]["sha256"]),
        "feature_manifest_checksum": pipeline.artifact_checksums["feature_manifest"],
        "preprocessor_checksum": pipeline.artifact_checksums["preprocessor"],
        "dataset_metadata_checksum": pipeline.artifact_checksums["dataset_metadata"],
        "split_checksum": pipeline.preprocessor.split_checksum,
    }


def _git_commit(repository_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _rng_state(generator: torch.Generator) -> dict[str, Any]:
    numpy_state = cast(tuple[str, Any, int, int, float], np.random.get_state())
    return {
        "python": random.getstate(),
        "numpy": {
            "bit_generator": numpy_state[0],
            "keys": numpy_state[1].tolist(),
            "position": numpy_state[2],
            "has_gauss": numpy_state[3],
            "cached_gaussian": numpy_state[4],
        },
        "torch": torch.get_rng_state(),
        "train_loader_generator": generator.get_state(),
    }


def _restore_rng_state(value: Mapping[str, Any], generator: torch.Generator) -> None:
    random.setstate(tuple(value["python"]))
    numpy_state = value["numpy"]
    np.random.set_state(
        (
            numpy_state["bit_generator"],
            np.asarray(numpy_state["keys"], dtype=np.uint32),
            int(numpy_state["position"]),
            int(numpy_state["has_gauss"]),
            float(numpy_state["cached_gaussian"]),
        )
    )
    torch.set_rng_state(value["torch"])
    generator.set_state(value["train_loader_generator"])


def _stable_loader(dataset: Any, batch_size: int) -> DataLoader[dict[str, Any]]:
    return DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)


def compare_with_random_forest(
    neural_metrics: Mapping[str, Any], baseline_metrics: Mapping[str, Any]
) -> dict[str, Any]:
    """Report signed comparable deltas without inferring a winner from tiny changes."""
    return {
        "neural_minus_random_forest": {
            name: float(neural_metrics[name]) - float(baseline_metrics[name])
            for name in ("roc_auc", "pr_auc", "f1", "brier_score")
        },
        "interpretation_policy": (
            "random forest remains preferred absent stable meaningful aggregate, calibration, "
            "support=1, and age-21+ improvement"
        ),
    }


def _checkpoint(
    model_state: dict[str, torch.Tensor],
    config: TrainingConfig,
    lineage: dict[str, str],
    input_dimension: int,
    epoch: int,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "state_dict": model_state,
        "epoch": epoch,
        "architecture": {
            "name": config.architecture_name,
            "hidden_dimensions": list(config.hidden_dimensions),
            "dropout": config.dropout,
        },
        "input_dimension": input_dimension,
        "optimizer": {
            "name": "AdamW",
            "learning_rate": config.learning_rate,
            "weight_decay": config.weight_decay,
        },
        "training_configuration": config.to_dict(),
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "cutoff": FEATURE_CUTOFF.isoformat(),
        "lineage": lineage,
        "training_infrastructure_version": TRAINING_INFRASTRUCTURE_VERSION,
        "validation_metrics": metrics,
        "torch_version": str(torch.__version__),
    }


def _validate_run_contract(
    run_directory: Path,
    pipeline: PyTorchPipelineBundle,
) -> tuple[TrainingConfig, dict[str, Any]]:
    config = TrainingConfig.from_dict(_read_json(run_directory / "config.json"))
    metadata = _read_json(run_directory / "run-metadata.json")
    if metadata.get("training_infrastructure_version") != TRAINING_INFRASTRUCTURE_VERSION:
        raise ModelingDataError("training infrastructure version mismatch")
    lineage = _lineage(pipeline)
    if metadata.get("lineage") != lineage:
        raise ModelingDataError("training run input contract mismatch")
    if metadata.get("config_checksum") != config_checksum(config):
        raise ModelingDataError("training run configuration checksum mismatch")
    if derive_run_id(config, lineage) != metadata.get("run_id"):
        raise ModelingDataError("training run identity mismatch")
    return config, metadata


def _continue_training(
    run_directory: Path,
    pipeline: PyTorchPipelineBundle,
    config: TrainingConfig,
    metadata: dict[str, Any],
    *,
    clock: Callable[[], datetime],
    resume_state: dict[str, Any] | None,
    stop_after_epoch: int | None,
) -> dict[str, Any]:
    if config.device == "cuda" and not torch.cuda.is_available():
        raise ModelingDataError("CUDA was requested but is unavailable")
    device = torch.device(config.device)
    set_deterministic_seed(config.seed)
    input_dimension = len(pipeline.preprocessor.transformed_features)
    model = HowReliableMLP(input_dimension, config.hidden_dimensions, config.dropout).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    loaders = create_dataloaders(
        pipeline.datasets, batch_size=config.batch_size, seed=config.seed
    )
    generator = loaders.train.generator
    if generator is None:
        raise AssertionError("training loader requires a generator")
    stopper = EarlyStopping(config.early_stopping_patience, config.minimum_improvement)
    history: list[dict[str, Any]] = []
    start_epoch = 1
    if resume_state is not None:
        if resume_state["config_checksum"] != config_checksum(config):
            raise ModelingDataError("resume configuration is incompatible")
        if resume_state["lineage"] != _lineage(pipeline):
            raise ModelingDataError("resume input contract is incompatible")
        model.load_state_dict(resume_state["model_state_dict"], strict=True)
        optimizer.load_state_dict(resume_state["optimizer_state_dict"])
        stopper.best_metric = float(resume_state["early_stopping"]["best_metric"])
        stopper.best_epoch = int(resume_state["early_stopping"]["best_epoch"])
        stopper.epochs_without_improvement = int(
            resume_state["early_stopping"]["epochs_without_improvement"]
        )
        stopper.best_state_dict = resume_state["early_stopping"]["best_state_dict"]
        history = list(resume_state["history"])
        start_epoch = int(resume_state["current_epoch"]) + 1
        _restore_rng_state(resume_state["rng_state"], generator)

    metadata["status"] = "RUNNING"
    metadata["failure"] = None
    atomic_write_json(run_directory / "run-metadata.json", metadata)
    stopping_epoch = config.max_epochs
    completed = False
    for epoch in range(start_epoch, config.max_epochs + 1):
        model.train()
        loss_sum = 0.0
        row_count = 0
        for batch in loaders.train:
            features = cast(torch.Tensor, batch["features"]).to(device)
            targets = cast(torch.Tensor, batch["target"]).to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(features), targets)
            loss.backward()
            optimizer.step()
            loss_sum += float(loss.item()) * len(targets)
            row_count += len(targets)
        validation = evaluate_model(
            model,
            loaders.validation,
            criterion,
            device=device,
            threshold=config.threshold,
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
                "learning_rate": optimizer.param_groups[0]["lr"],
            }
        )
        should_stop = stopper.update(
            float(validation.metrics["roc_auc"]), epoch, model
        )
        if stopper.best_epoch == epoch:
            if stopper.best_state_dict is None:
                raise AssertionError("best state was not retained")
            atomic_torch_save(
                run_directory / "best-checkpoint.pt",
                _checkpoint(
                    stopper.best_state_dict,
                    config,
                    _lineage(pipeline),
                    input_dimension,
                    epoch,
                    validation.metrics,
                ),
            )
        state = {
            "model_state_dict": {
                name: tensor.detach().cpu().clone()
                for name, tensor in model.state_dict().items()
            },
            "optimizer_state_dict": optimizer.state_dict(),
            "current_epoch": epoch,
            "early_stopping": {
                "best_epoch": stopper.best_epoch,
                "best_metric": stopper.best_metric,
                "epochs_without_improvement": stopper.epochs_without_improvement,
                "best_state_dict": stopper.best_state_dict,
            },
            "rng_state": _rng_state(generator),
            "history": history,
            "config_checksum": config_checksum(config),
            "lineage": _lineage(pipeline),
            "training_infrastructure_version": TRAINING_INFRASTRUCTURE_VERSION,
        }
        atomic_write_json(run_directory / "history.json", {"epochs": history})
        atomic_torch_save(run_directory / "latest-state.pt", state)
        metadata["current_epoch"] = epoch
        metadata["best_epoch"] = stopper.best_epoch
        metadata["best_validation_roc_auc"] = stopper.best_metric
        atomic_write_json(run_directory / "run-metadata.json", metadata)
        if should_stop:
            stopping_epoch = epoch
            completed = True
            break
        if stop_after_epoch is not None and epoch >= stop_after_epoch:
            metadata["status"] = "INTERRUPTED"
            atomic_write_json(run_directory / "run-metadata.json", metadata)
            return metadata
    else:
        completed = True
    if not completed or stopper.best_state_dict is None:
        raise AssertionError("training did not produce a best checkpoint")
    model.load_state_dict(stopper.best_state_dict, strict=True)
    final_validation = evaluate_model(
        model,
        _stable_loader(pipeline.datasets["VALIDATION"], config.batch_size),
        criterion,
        device=device,
        threshold=config.threshold,
    )
    baseline = _read_json(
        Path(metadata["repository_root"]) / "artifacts/models/baseline-results.json"
    )
    validation_result = {
        "split": "VALIDATION",
        "metrics": final_validation.metrics,
        "calibration": calibration_table(final_validation),
        "random_forest_comparison": compare_with_random_forest(
            final_validation.metrics,
            baseline["best_model"]["validation_metrics"],
        ),
    }
    atomic_write_json(run_directory / "validation-results.json", validation_result)
    metadata.update(
        {
            "status": "COMPLETED",
            "stopping_epoch": stopping_epoch,
            "best_epoch": stopper.best_epoch,
            "best_validation_metrics": final_validation.metrics,
            "completion_timestamp_utc": _timestamp(clock),
            "artifacts": {
                name: {"sha256": sha256_file(run_directory / filename), "path": filename}
                for name, filename in {
                    "config": "config.json",
                    "history": "history.json",
                    "latest_state": "latest-state.pt",
                    "best_checkpoint": "best-checkpoint.pt",
                    "validation_results": "validation-results.json",
                }.items()
            },
        }
    )
    atomic_write_json(run_directory / "run-metadata.json", metadata)
    return metadata


def create_training_run(
    repository_root: Path,
    pipeline_artifact_directory: Path,
    runs_root: Path,
    config: TrainingConfig | None = None,
    *,
    pipeline_override: PyTorchPipelineBundle | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    stop_after_epoch: int | None = None,
) -> dict[str, Any]:
    """Create and train one deterministic run without accessing the test split."""
    config = config or TrainingConfig()
    pipeline = pipeline_override or load_pytorch_pipeline(
        repository_root, pipeline_artifact_directory
    )
    lineage = _lineage(pipeline)
    run_id = derive_run_id(config, lineage)
    run_directory = runs_root / run_id
    if run_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite training run: {run_directory}")
    run_directory.mkdir(parents=True)
    atomic_write_json(run_directory / "config.json", config.to_dict())
    metadata: dict[str, Any] = {
        "training_infrastructure_version": TRAINING_INFRASTRUCTURE_VERSION,
        "run_id": run_id,
        "status": "CREATED",
        "config_checksum": config_checksum(config),
        "lineage": lineage,
        "seed": config.seed,
        "device": config.device,
        "start_timestamp_utc": _timestamp(clock),
        "completion_timestamp_utc": None,
        "current_epoch": 0,
        "best_epoch": None,
        "failure": None,
        "repository_root": str(repository_root.resolve()),
        "environment": {
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "torch_version": str(torch.__version__),
            "sklearn_version": version("scikit-learn"),
            "git_commit": _git_commit(repository_root),
        },
    }
    atomic_write_json(run_directory / "run-metadata.json", metadata)
    try:
        return _continue_training(
            run_directory,
            pipeline,
            config,
            metadata,
            clock=clock,
            resume_state=None,
            stop_after_epoch=stop_after_epoch,
        )
    except Exception as error:
        metadata["status"] = "FAILED"
        metadata["failure"] = {
            "error_type": type(error).__name__,
            "message": str(error),
            "last_completed_epoch": metadata.get("current_epoch", 0),
        }
        atomic_write_json(run_directory / "run-metadata.json", metadata)
        raise


def resume_training_run(
    repository_root: Path,
    pipeline_artifact_directory: Path,
    run_directory: Path,
    *,
    pipeline_override: PyTorchPipelineBundle | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Resume the exact next epoch after validating configuration and lineage."""
    pipeline = pipeline_override or load_pytorch_pipeline(
        repository_root, pipeline_artifact_directory
    )
    config, metadata = _validate_run_contract(run_directory, pipeline)
    if metadata["status"] == "COMPLETED":
        raise ArtifactExistsError("completed training runs cannot be resumed")
    latest_path = run_directory / "latest-state.pt"
    if not latest_path.is_file():
        raise ModelingDataError("run has no resumable training state")
    state = torch.load(latest_path, map_location="cpu", weights_only=True)
    try:
        return _continue_training(
            run_directory,
            pipeline,
            config,
            metadata,
            clock=clock,
            resume_state=state,
            stop_after_epoch=None,
        )
    except Exception as error:
        metadata["status"] = "FAILED"
        metadata["failure"] = {
            "error_type": type(error).__name__,
            "message": str(error),
            "last_completed_epoch": metadata.get("current_epoch", 0),
        }
        atomic_write_json(run_directory / "run-metadata.json", metadata)
        raise


def evaluate_training_run(
    repository_root: Path,
    pipeline_artifact_directory: Path,
    run_directory: Path,
    *,
    split: str = "VALIDATION",
    pipeline_override: PyTorchPipelineBundle | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Explicitly evaluate a completed best checkpoint on validation or test."""
    if split not in {"VALIDATION", "TEST"}:
        raise ValueError("evaluation split must be VALIDATION or TEST")
    pipeline = pipeline_override or load_pytorch_pipeline(
        repository_root, pipeline_artifact_directory
    )
    config, metadata = _validate_run_contract(run_directory, pipeline)
    if metadata["status"] != "COMPLETED":
        raise ModelingDataError("only completed runs can be evaluated")
    output_path = run_directory / f"{split.lower()}-evaluation.json"
    if output_path.exists():
        raise ArtifactExistsError(f"refusing to overwrite evaluation: {output_path}")
    checkpoint = torch.load(
        run_directory / "best-checkpoint.pt", map_location="cpu", weights_only=True
    )
    if checkpoint["lineage"] != _lineage(pipeline):
        raise ModelingDataError("best checkpoint lineage mismatch")
    model = HowReliableMLP(
        int(checkpoint["input_dimension"]), config.hidden_dimensions, config.dropout
    )
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    device = torch.device(config.device)
    model.to(device)
    output = evaluate_model(
        model,
        _stable_loader(pipeline.datasets[split], config.batch_size),
        nn.BCEWithLogitsLoss(),
        device=device,
        threshold=config.threshold,
    )
    baseline = _read_json(repository_root / "artifacts/models/baseline-results.json")
    baseline_metrics = baseline["best_model"][
        "validation_metrics" if split == "VALIDATION" else "test_metrics"
    ]
    result = {
        "run_id": metadata["run_id"],
        "split": split,
        "evaluation_timestamp_utc": _timestamp(clock),
        "metrics": output.metrics,
        "calibration": calibration_table(output),
        "subgroups": subgroup_metrics(output),
        "random_forest_comparison": compare_with_random_forest(output.metrics, baseline_metrics),
        "promotion_policy": {
            "preferred_model": "random_forest",
            "candidate_promoted": False,
            "reason": (
                "Phase 3D found no stable meaningful aggregate/calibration improvement and "
                "material age-21+ and support=1 improvement was absent"
            ),
        },
        "checkpoint_sha256": sha256_file(run_directory / "best-checkpoint.pt"),
    }
    atomic_write_json(output_path, result)
    return result


def inspect_training_run(run_directory: Path) -> dict[str, Any]:
    """Return an artifact/checksum view without training or evaluation."""
    metadata = _read_json(run_directory / "run-metadata.json")
    if metadata.get("status") not in RUN_STATUSES:
        raise ModelingDataError("unknown training run status")
    artifacts = dict(metadata.get("artifacts", {}))
    for split in ("validation", "test"):
        path = run_directory / f"{split}-evaluation.json"
        if path.is_file():
            artifacts[f"{split}_evaluation"] = {
                "path": path.name,
                "sha256": sha256_file(path),
            }
    return {
        "run_id": metadata["run_id"],
        "status": metadata["status"],
        "config": _read_json(run_directory / "config.json"),
        "lineage": metadata["lineage"],
        "current_epoch": metadata.get("current_epoch"),
        "best_epoch": metadata.get("best_epoch"),
        "best_validation_metrics": metadata.get("best_validation_metrics"),
        "artifacts": artifacts,
        "run_metadata_sha256": sha256_file(run_directory / "run-metadata.json"),
    }
