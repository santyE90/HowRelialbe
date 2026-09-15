"""Authoritative frozen-prediction evaluation for Phase 3F."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import torch
from numpy.typing import NDArray

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL, classification_metrics
from howreliable.modeling.features import FEATURE_CUTOFF, FEATURE_SCHEMA, ModelingDataError, _jsonl
from howreliable.modeling.pytorch.model import HowReliableMLP
from howreliable.modeling.pytorch.pipeline import PyTorchPipelineBundle, load_pytorch_pipeline
from howreliable.modeling.pytorch.training_infrastructure import atomic_write_json

EVALUATION_VERSION: Final = "howreliable-evaluation-1.0"
BOOTSTRAP_SEED: Final = 20220913
BOOTSTRAP_SAMPLES: Final = 1_000
THRESHOLDS: Final = (0.3, 0.4, 0.5, 0.6, 0.7)
METRICS: Final = ("accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "brier_score")


def _arrays(rows: list[dict[str, Any]]) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    return (
        np.asarray([row["target"] for row in rows], dtype=np.int64),
        np.asarray([row["predicted_future_complaint_probability"] for row in rows]),
    )


def validate_predictions(rows: list[dict[str, Any]]) -> None:
    if not rows or len({row["cohort_id"] for row in rows}) != len(rows):
        raise ModelingDataError("predictions require unique nonempty cohort identities")
    labels, probabilities = _arrays(rows)
    if np.any((probabilities < 0) | (probabilities > 1)):
        raise ModelingDataError("prediction probability is outside [0, 1]")
    if any(row["split"] != "TEST" for row in rows):
        raise ModelingDataError("Phase 3F prediction artifact must contain only TEST")
    if any(
        row["predicted_class"] != int(probability >= 0.5)
        for row, probability in zip(rows, probabilities, strict=True)
    ):
        raise ModelingDataError("prediction class does not use the frozen 0.5 threshold")
    if int(labels.sum()) <= 0:
        raise ModelingDataError("prediction artifact has no positives")


def aggregate_metrics(rows: list[dict[str, Any]], threshold: float = 0.5) -> dict[str, Any]:
    labels, probabilities = _arrays(rows)
    return classification_metrics(labels, probabilities, threshold=threshold)


def bootstrap_intervals(
    rows: list[dict[str, Any]], *, samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED
) -> dict[str, dict[str, Any]]:
    labels, probabilities = _arrays(rows)
    generator = np.random.default_rng(seed)
    values: dict[str, list[float]] = {name: [] for name in METRICS}
    for _ in range(samples):
        indexes = generator.integers(0, len(rows), len(rows))
        result = classification_metrics(labels[indexes], probabilities[indexes])
        for name in METRICS:
            value = result[name]
            if value is not None:
                values[name].append(float(value))
    return {
        name: {
            "lower_95": float(np.percentile(items, 2.5)) if items else None,
            "upper_95": float(np.percentile(items, 97.5)) if items else None,
            "valid_samples": len(items),
        }
        for name, items in values.items()
    }


def paired_bootstrap(
    forest: list[dict[str, Any]],
    mlp: list[dict[str, Any]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, float]]:
    validate_alignment(forest, mlp)
    labels, forest_probability = _arrays(forest)
    _, mlp_probability = _arrays(mlp)
    generator = np.random.default_rng(seed)
    names = ("roc_auc", "pr_auc", "f1", "brier_score")
    differences: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(samples):
        indexes = generator.integers(0, len(labels), len(labels))
        left = classification_metrics(labels[indexes], forest_probability[indexes])
        right = classification_metrics(labels[indexes], mlp_probability[indexes])
        for name in names:
            if left[name] is not None and right[name] is not None:
                differences[name].append(float(right[name]) - float(left[name]))
    observed_left = aggregate_metrics(forest)
    observed_right = aggregate_metrics(mlp)
    return {
        name: {
            "observed_mlp_minus_forest": float(observed_right[name]) - float(observed_left[name]),
            "lower_95": float(np.percentile(differences[name], 2.5)),
            "upper_95": float(np.percentile(differences[name], 97.5)),
            "valid_samples": len(differences[name]),
        }
        for name in names
    }


def calibration(rows: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    labels, probabilities = _arrays(rows)
    assignments = np.minimum((probabilities * bins).astype(int), bins - 1)
    result = []
    weighted_gap = 0.0
    for index in range(bins):
        selected = assignments == index
        count = int(selected.sum())
        predicted = float(probabilities[selected].mean()) if count else None
        observed = float(labels[selected].mean()) if count else None
        gap = predicted - observed if predicted is not None and observed is not None else None
        if gap is not None:
            weighted_gap += count / len(rows) * abs(gap)
        result.append(
            {
                "lower_bound": index / bins,
                "upper_bound": (index + 1) / bins,
                "row_count": count,
                "mean_predicted_probability": predicted,
                "observed_positive_fraction": observed,
                "calibration_gap": gap,
            }
        )
    return {
        "bins": result,
        "expected_calibration_error": weighted_gap,
        "brier_score": aggregate_metrics(rows)["brier_score"],
    }


def threshold_sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "threshold": threshold,
            **{
                name: value
                for name, value in aggregate_metrics(rows, threshold).items()
                if name
                in {
                    "precision",
                    "recall",
                    "f1",
                    "predicted_positive_prevalence",
                    "confusion_matrix",
                }
            },
        }
        for threshold in THRESHOLDS
    ]


def _age(value: int) -> str:
    return (
        "0-2"
        if value <= 2
        else "3-5"
        if value <= 5
        else "6-10"
        if value <= 10
        else "11-20"
        if value <= 20
        else "21+"
    )


def _support(value: int) -> str:
    return (
        "1"
        if value == 1
        else "2-4"
        if value <= 4
        else "5-9"
        if value <= 9
        else "10-49"
        if value <= 49
        else "50+"
    )


def _source(row: Mapping[str, Any]) -> str:
    communication, recall = (
        row["communication_observed_by_cutoff"],
        row["recall_observed_by_cutoff"],
    )
    return (
        "communications_and_recalls"
        if communication and recall
        else "communications_only"
        if communication
        else "recalls_only"
        if recall
        else "complaints_only_or_limited"
    )


def subgroup_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    definitions = {
        "age": lambda row: _age(int(row["cohort_age_at_cutoff"])),
        "historical_support": lambda row: _support(int(row["historical_complaint_count"])),
        "source_coverage": _source,
    }
    output: dict[str, Any] = {}
    for family, function in definitions.items():
        groups: dict[str, Any] = {}
        for name in sorted({function(row) for row in rows}):
            selected = [row for row in rows if function(row) == name]
            metrics = aggregate_metrics(selected)
            metrics["positive_count"] = sum(int(row["target"]) for row in selected)
            groups[name] = metrics
        output[family] = groups
    return output


def critical_subgroup_intervals(
    rows: list[dict[str, Any]], *, samples: int = BOOTSTRAP_SAMPLES
) -> dict[str, Any]:
    groups = {
        "support_1": [row for row in rows if int(row["historical_complaint_count"]) == 1],
        "age_21_plus": [row for row in rows if int(row["cohort_age_at_cutoff"]) >= 21],
    }
    return {
        name: {
            metric: interval
            for metric, interval in bootstrap_intervals(
                selected, samples=samples, seed=BOOTSTRAP_SEED + index
            ).items()
            if metric in {"recall", "f1", "roc_auc"}
        }
        for index, (name, selected) in enumerate(groups.items())
    }


def error_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("true_positive", "true_negative", "false_positive", "false_negative")
    }
    for row in rows:
        key = ("true_" if row["target"] == row["predicted_class"] else "false_") + (
            "positive" if row["predicted_class"] else "negative"
        )
        categories[key].append(row)

    def summary(selected: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "row_count": len(selected),
            "age": dict(
                sorted(Counter(_age(int(row["cohort_age_at_cutoff"])) for row in selected).items())
            ),
            "support": dict(
                sorted(
                    Counter(
                        _support(int(row["historical_complaint_count"])) for row in selected
                    ).items()
                )
            ),
            "communication_available": dict(
                sorted(
                    Counter(
                        str(bool(row["communication_observed_by_cutoff"])).lower()
                        for row in selected
                    ).items()
                )
            ),
            "recall_available": dict(
                sorted(
                    Counter(
                        str(bool(row["recall_observed_by_cutoff"])).lower() for row in selected
                    ).items()
                )
            ),
            "model_year": dict(sorted(Counter(str(row["model_year"]) for row in selected).items())),
        }

    high_fp = [
        row
        for row in categories["false_positive"]
        if row["predicted_future_complaint_probability"] >= 0.8
    ]
    high_fn = [
        row
        for row in categories["false_negative"]
        if row["predicted_future_complaint_probability"] <= 0.2
    ]
    return {
        "categories": {name: summary(selected) for name, selected in categories.items()},
        "high_confidence": {
            "false_positive_probability_gte_0_8": summary(high_fp),
            "false_negative_probability_lte_0_2": summary(high_fn),
        },
    }


def validate_alignment(forest: list[dict[str, Any]], mlp: list[dict[str, Any]]) -> None:
    if [(row["cohort_id"], row["target"]) for row in forest] != [
        (row["cohort_id"], row["target"]) for row in mlp
    ]:
        raise ModelingDataError("frozen model predictions are not exactly paired")


def agreement(forest: list[dict[str, Any]], mlp: list[dict[str, Any]]) -> dict[str, Any]:
    validate_alignment(forest, mlp)
    labels, forest_probability = _arrays(forest)
    _, mlp_probability = _arrays(mlp)
    fp, mp = forest_probability >= 0.5, mlp_probability >= 0.5
    fc, mc = fp == labels, mp == labels
    disagreements = [row for row, left, right in zip(forest, fp, mp, strict=True) if left != right]
    return {
        "class_agreement_rate": float(np.mean(fp == mp)),
        "probability_correlation": float(np.corrcoef(forest_probability, mlp_probability)[0, 1]),
        "both_correct": int(np.sum(fc & mc)),
        "forest_only_correct": int(np.sum(fc & ~mc)),
        "mlp_only_correct": int(np.sum(~fc & mc)),
        "both_incorrect": int(np.sum(~fc & ~mc)),
        "disagreement_count": len(disagreements),
        "disagreements_by_age": dict(
            sorted(Counter(_age(int(row["cohort_age_at_cutoff"])) for row in disagreements).items())
        ),
        "disagreements_by_support": dict(
            sorted(
                Counter(
                    _support(int(row["historical_complaint_count"])) for row in disagreements
                ).items()
            )
        ),
    }


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(name, path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def _prediction_rows(
    pipeline: PyTorchPipelineBundle, probabilities: NDArray[np.float64], model_id: str
) -> list[dict[str, Any]]:
    dataset = pipeline.datasets["TEST"]
    rows = []
    for index, probability in enumerate(probabilities):
        metadata = dataset.metadata[index]
        rows.append(
            {
                "cohort_id": dataset.cohort_ids[index],
                "normalized_make": metadata["normalized_make"],
                "normalized_model": metadata["normalized_model"],
                "model_year": metadata["model_year"],
                "target": int(dataset.targets[index].item()),
                "predicted_future_complaint_probability": float(probability),
                "predicted_class": int(probability >= 0.5),
                "split": "TEST",
                "model_identifier": model_id,
                "target_version": TARGET_DEFINITION_VERSION,
                "cutoff": FEATURE_CUTOFF.isoformat(),
                "cohort_age_at_cutoff": metadata["cohort_age_at_cutoff"],
                "historical_complaint_count": metadata["historical_complaint_count"],
                "communication_observed_by_cutoff": metadata["communication_observed_by_cutoff"],
                "recall_observed_by_cutoff": metadata["recall_observed_by_cutoff"],
            }
        )
    validate_predictions(rows)
    return rows


def generate_evaluation(
    repository_root: Path,
    output_directory: Path,
    *,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    regenerate: bool = False,
) -> dict[str, Any]:
    """Infer with two frozen models and evaluate their aligned TEST predictions."""
    if not regenerate and (
        (output_directory / "evaluation-report.json").exists()
        or (output_directory / "preferred-model-handoff.json").exists()
    ):
        raise ArtifactExistsError(f"refusing to overwrite evaluation: {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    pipeline = load_pytorch_pipeline(
        repository_root, repository_root / "artifacts/modeling/pytorch"
    )
    baseline = json.loads((repository_root / "artifacts/models/baseline-results.json").read_text())
    model_path = (
        repository_root / "artifacts/models/baselines" / baseline["best_model"]["model_artifact"]
    )
    forest_model = joblib.load(model_path)
    features = pd.DataFrame(
        _jsonl(
            repository_root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl",
            FEATURE_SCHEMA,
        )
    ).set_index("broad_vehicle_id")
    ordered = features.loc[list(pipeline.datasets["TEST"].cohort_ids)]
    forest_probability = np.asarray(forest_model.predict_proba(ordered[ALL_EVIDENCE_MODEL]))[:, 1]
    run_id = "a6db5f7eadae67fe1c0a1f63f01682c889b4718483027fe50cc3fc25921222fd"
    mlp_path = repository_root / "artifacts/training/runs" / run_id / "best-checkpoint.pt"
    checkpoint = torch.load(mlp_path, map_location="cpu", weights_only=True)
    mlp_model = HowReliableMLP(90, (64, 32), 0.1)
    mlp_model.load_state_dict(checkpoint["state_dict"])
    mlp_model.eval()
    with torch.no_grad():
        mlp_logits = (
            mlp_model(pipeline.datasets["TEST"].features).numpy().reshape(-1).astype(np.float64)
        )
        mlp_probability = torch.sigmoid(torch.from_numpy(mlp_logits)).numpy()
    forest = _prediction_rows(pipeline, forest_probability, "phase-3b-random-forest")
    mlp = _prediction_rows(pipeline, mlp_probability, f"phase-3e-mlp:{run_id}")
    validate_alignment(forest, mlp)
    prediction_directory = output_directory / "predictions"
    forest_path, mlp_prediction_path = (
        prediction_directory / "random-forest-test.jsonl",
        prediction_directory / "first-mlp-test.jsonl",
    )
    _atomic_jsonl(forest_path, forest)
    _atomic_jsonl(mlp_prediction_path, mlp)
    models = {}
    for name, rows in (("random_forest", forest), ("first_mlp", mlp)):
        models[name] = {
            "status": "PREFERRED" if name == "random_forest" else "COMPARISON",
            "aggregate_metrics": aggregate_metrics(rows),
            "bootstrap_95_percentile": bootstrap_intervals(rows, samples=bootstrap_samples),
            "calibration": calibration(rows),
            "threshold_sensitivity": threshold_sensitivity(rows),
            "subgroups": subgroup_results(rows),
            "critical_subgroup_uncertainty": critical_subgroup_intervals(
                rows, samples=bootstrap_samples
            ),
            "error_analysis": error_analysis(rows),
        }
    report = {
        "evaluation_version": EVALUATION_VERSION,
        "generation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "target_contract": {
            "name": "future_12m_complaint_activity",
            "semantics": (
                "probability of at least one observed accepted NHTSA complaint report in 2023"
            ),
            "cutoff": "2022-12-31",
            "not": [
                "failure probability",
                "repair probability",
                "individual-vehicle risk",
                "reliability score",
                "safety probability",
            ],
        },
        "test_contract": {
            "rows": 1263,
            "positives": 660,
            "negatives": 603,
            "split_checksum": pipeline.preprocessor.split_checksum,
            "threshold": 0.5,
        },
        "prediction_artifacts": {
            "random_forest": {
                "path": forest_path.relative_to(output_directory).as_posix(),
                "sha256": sha256_file(forest_path),
            },
            "first_mlp": {
                "path": mlp_prediction_path.relative_to(output_directory).as_posix(),
                "sha256": sha256_file(mlp_prediction_path),
            },
        },
        "frozen_models": {
            "random_forest": {
                "path": str(model_path.relative_to(repository_root)),
                "sha256": sha256_file(model_path),
            },
            "first_mlp": {
                "run_id": run_id,
                "path": str(mlp_path.relative_to(repository_root)),
                "sha256": sha256_file(mlp_path),
            },
        },
        "models": models,
        "paired_mlp_minus_forest": paired_bootstrap(forest, mlp, samples=bootstrap_samples),
        "model_agreement": agreement(forest, mlp),
        "preferred_model": "random_forest",
        "preferred_model_rationale": (
            "ranking and Brier are slightly better, paired evidence shows no meaningful MLP "
            "advantage, critical subgroup weaknesses persist, and the forest is simpler"
        ),
        "supported_claims": [
            "probability of observed future cohort complaint activity",
            "performance on the frozen 2023 observed outcome",
            "weak performance for sparse historical support",
        ],
        "unsupported_claims": [
            "mechanical failure or repair probability",
            "individual-vehicle risk",
            "healthy/unhealthy classification",
            "reliability or safety probability",
            "causal recall or communication effects",
            "universal exposure-normalized risk",
        ],
    }
    report_path = output_directory / "evaluation-report.json"
    atomic_write_json(report_path, report)
    handoff = {
        "preferred_model_identifier": "phase-3b-random-forest",
        "preferred_model_artifact": str(model_path.relative_to(repository_root)),
        "preferred_model_checksum": sha256_file(model_path),
        "preprocessing": "serialized sklearn Pipeline fit on Phase 3B TRAIN",
        "feature_contract_checksum": pipeline.metadata["inputs"]["features"]["sha256"],
        "target_contract": report["target_contract"],
        "split_checksum": pipeline.preprocessor.split_checksum,
        "threshold": 0.5,
        "evaluation_version": EVALUATION_VERSION,
        "evaluation_report_checksum": sha256_file(report_path),
    }
    atomic_write_json(output_directory / "preferred-model-handoff.json", handoff)
    report["evaluation_report_sha256"] = sha256_file(report_path)
    report["preferred_model_handoff_sha256"] = sha256_file(
        output_directory / "preferred-model-handoff.json"
    )
    return report
