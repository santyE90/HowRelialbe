"""Metrics, calibration, subgroups, and simple neural feature-family sensitivity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import torch
from numpy.typing import NDArray
from torch import nn
from torch.utils.data import DataLoader

from howreliable.modeling.baselines import classification_metrics
from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset


@dataclass(frozen=True)
class EvaluationOutput:
    """Aggregate metrics plus row-aligned outputs needed for diagnostics."""

    metrics: dict[str, Any]
    logits: NDArray[np.float64]
    probabilities: NDArray[np.float64]
    targets: NDArray[np.int64]
    cohort_ids: tuple[str, ...]
    metadata: tuple[dict[str, Any], ...]


def _metadata_value(value: Any, index: int) -> Any:
    if isinstance(value, torch.Tensor):
        return value[index].item()
    return value[index]


def evaluate_model(
    model: nn.Module,
    loader: DataLoader[dict[str, Any]],
    criterion: nn.Module,
    *,
    device: torch.device,
    threshold: float = 0.5,
) -> EvaluationOutput:
    """Evaluate without gradients or parameter updates."""
    model.eval()
    logits_parts: list[torch.Tensor] = []
    target_parts: list[torch.Tensor] = []
    cohort_ids: list[str] = []
    metadata: list[dict[str, Any]] = []
    loss_sum = 0.0
    with torch.no_grad():
        for batch in loader:
            features = cast(torch.Tensor, batch["features"]).to(device)
            targets = cast(torch.Tensor, batch["target"]).to(device)
            logits = model(features)
            loss_sum += float(criterion(logits, targets).item()) * len(targets)
            logits_parts.append(logits.detach().cpu())
            target_parts.append(targets.detach().cpu())
            identifiers = cast(list[str], batch["cohort_id"])
            cohort_ids.extend(identifiers)
            batched_metadata = cast(dict[str, Any], batch["metadata"])
            for index in range(len(identifiers)):
                metadata.append(
                    {
                        name: _metadata_value(value, index)
                        for name, value in batched_metadata.items()
                    }
                )
    logits_array = torch.cat(logits_parts).numpy().reshape(-1).astype(np.float64)
    probabilities = torch.sigmoid(torch.from_numpy(logits_array)).numpy()
    targets_array = torch.cat(target_parts).numpy().reshape(-1).astype(np.int64)
    metrics = classification_metrics(targets_array, probabilities, threshold=threshold)
    metrics["loss"] = loss_sum / len(targets_array)
    return EvaluationOutput(
        metrics,
        logits_array,
        probabilities,
        targets_array,
        tuple(cohort_ids),
        tuple(metadata),
    )


def calibration_table(output: EvaluationOutput, bins: int = 10) -> list[dict[str, Any]]:
    """Summarize predicted and observed activity in equal-width probability bins."""
    assignments = np.minimum((output.probabilities * bins).astype(int), bins - 1)
    result: list[dict[str, Any]] = []
    for index in range(bins):
        selected = assignments == index
        count = int(np.sum(selected))
        result.append(
            {
                "probability_bin": f"[{index / bins:.1f},{(index + 1) / bins:.1f}"
                + ("]" if index == bins - 1 else ")"),
                "row_count": count,
                "mean_predicted_probability": (
                    float(np.mean(output.probabilities[selected])) if count else None
                ),
                "observed_positive_rate": (
                    float(np.mean(output.targets[selected])) if count else None
                ),
            }
        )
    return result


def _age_bucket(value: int) -> str:
    if value <= 2:
        return "0-2"
    if value <= 5:
        return "3-5"
    if value <= 10:
        return "6-10"
    if value <= 20:
        return "11-20"
    return "21+"


def _support_bucket(value: int) -> str:
    if value == 1:
        return "1"
    if value <= 4:
        return "2-4"
    if value <= 9:
        return "5-9"
    if value <= 49:
        return "10-49"
    return "50+"


def _coverage_bucket(metadata: dict[str, Any]) -> str:
    communication = bool(metadata["communication_observed_by_cutoff"])
    recall = bool(metadata["recall_observed_by_cutoff"])
    if communication and recall:
        return "communications_and_recalls"
    if communication:
        return "communications_only"
    if recall:
        return "recalls_only"
    return "complaints_only_or_limited"


def _group_metrics(output: EvaluationOutput, groups: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    group_array = np.asarray(groups)
    for group in sorted(set(groups)):
        selected = group_array == group
        metrics = classification_metrics(output.targets[selected], output.probabilities[selected])
        metrics["positive_count"] = int(np.sum(output.targets[selected]))
        result[group] = metrics
    return result


def subgroup_metrics(output: EvaluationOutput) -> dict[str, dict[str, dict[str, Any]]]:
    """Evaluate the exact Phase 3B age, support, and source groups."""
    return {
        "age": _group_metrics(
            output,
            [_age_bucket(int(item["cohort_age_at_cutoff"])) for item in output.metadata],
        ),
        "historical_support": _group_metrics(
            output,
            [_support_bucket(int(item["historical_complaint_count"])) for item in output.metadata],
        ),
        "source_coverage": _group_metrics(
            output, [_coverage_bucket(item) for item in output.metadata]
        ),
    }


def _tensor_probabilities(
    model: nn.Module,
    features: torch.Tensor,
    *,
    device: torch.device,
    batch_size: int = 256,
) -> NDArray[np.float64]:
    model.eval()
    values: list[torch.Tensor] = []
    with torch.no_grad():
        for start in range(0, len(features), batch_size):
            logits = model(features[start : start + batch_size].to(device))
            values.append(torch.sigmoid(logits).cpu())
    return torch.cat(values).numpy().reshape(-1).astype(np.float64)


def permutation_family_importance(
    model: nn.Module,
    dataset: HowReliableCohortDataset,
    manifest: dict[str, Any],
    *,
    device: torch.device,
    seed: int,
) -> list[dict[str, Any]]:
    """Measure validation ROC-AUC decrease from one deterministic family permutation."""
    targets = dataset.targets.numpy().reshape(-1).astype(np.int64)
    baseline_probabilities = _tensor_probabilities(model, dataset.features, device=device)
    baseline_roc = cast(float, classification_metrics(targets, baseline_probabilities)["roc_auc"])
    family_indexes: dict[str, list[int]] = {}
    for item in manifest["features"]:
        family_indexes.setdefault(cast(str, item["feature_family"]), []).append(
            int(item["tensor_index"])
        )
    generator = torch.Generator().manual_seed(seed)
    result: list[dict[str, Any]] = []
    for family, indexes in sorted(family_indexes.items()):
        permuted = dataset.features.clone()
        order = torch.randperm(len(permuted), generator=generator)
        permuted[:, indexes] = permuted[order][:, indexes]
        probabilities = _tensor_probabilities(model, permuted, device=device)
        roc_auc = cast(float, classification_metrics(targets, probabilities)["roc_auc"])
        result.append(
            {
                "feature_family": family,
                "tensor_feature_count": len(indexes),
                "permuted_validation_roc_auc": roc_auc,
                "mean_roc_auc_decrease": baseline_roc - roc_auc,
            }
        )
    return sorted(result, key=lambda item: cast(float, item["mean_roc_auc_decrease"]), reverse=True)
