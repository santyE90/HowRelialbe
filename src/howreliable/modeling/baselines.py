"""Reproducible traditional baseline experiments for future complaint activity."""

from __future__ import annotations

import platform
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import sklearn  # type: ignore[import-untyped]
from sklearn.compose import ColumnTransformer  # type: ignore[import-untyped]
from sklearn.ensemble import (  # type: ignore[import-untyped]
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.inspection import permutation_importance  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import (  # type: ignore[import-untyped]
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[import-untyped]

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.features import (
    COMMUNICATION_COLUMNS,
    COMPONENT_COLUMNS,
    FEATURE_CUTOFF,
    FEATURE_MATRIX_VERSION,
    FEATURE_SCHEMA,
    MILEAGE_COLUMNS,
    RECALL_COLUMNS,
    RECENCY_COLUMNS,
    SEVERITY_COLUMNS,
    VOLUME_COLUMNS,
    ModelingDataError,
    _json,
    _jsonl,
    _write_json,
)
from howreliable.modeling.splits import SPLIT_SCHEMA, SPLIT_SEED, SPLIT_VERSION

BASELINE_VERSION: Final = "baseline-models-1.0"
RANDOM_SEED: Final = 20220913
TARGET_COLUMN: Final = "future_any_complaint"


def _model_columns(columns: Sequence[str]) -> list[str]:
    return [
        item
        for item in columns
        if not item.endswith("_date")
        and not item.endswith("_status")
        and item not in {"broad_vehicle_id", "normalized_make", "normalized_model"}
    ]


STATIC_NUMERIC: Final = ["model_year", "cohort_age_at_cutoff"]
VOLUME_MODEL: Final = _model_columns(VOLUME_COLUMNS)
RECENCY_MODEL: Final = _model_columns(RECENCY_COLUMNS)
COMPLAINT_MODEL: Final = [
    *STATIC_NUMERIC,
    *VOLUME_MODEL,
    *RECENCY_MODEL,
    *_model_columns(COMPONENT_COLUMNS),
    *_model_columns(SEVERITY_COLUMNS),
    *_model_columns(MILEAGE_COLUMNS),
    "historical_crash_positive_count",
    "historical_fire_positive_count",
    "historical_injury_positive_count",
    "historical_death_positive_count",
]
COMMUNICATION_MODEL: Final = _model_columns(COMMUNICATION_COLUMNS)
RECALL_MODEL: Final = _model_columns(RECALL_COLUMNS)
ALL_EVIDENCE_MODEL: Final = [*COMPLAINT_MODEL, *COMMUNICATION_MODEL, *RECALL_MODEL]

EXPERIMENTS: Final = {
    "historical_count_only": ["historical_complaint_count"],
    "complaint_volume_recency": [*VOLUME_MODEL, *RECENCY_MODEL],
    "complaint_features": COMPLAINT_MODEL,
    "complaints_plus_communications": [*COMPLAINT_MODEL, *COMMUNICATION_MODEL],
    "complaints_plus_recalls": [*COMPLAINT_MODEL, *RECALL_MODEL],
    "complaints_communications_recalls": ALL_EVIDENCE_MODEL,
    "all_evidence_make_model_identity": [
        *ALL_EVIDENCE_MODEL,
        "normalized_make",
        "normalized_model",
    ],
}


def classification_metrics(
    labels: Any, probabilities: Any, *, threshold: float = 0.5
) -> dict[str, Any]:
    """Calculate the fixed classifier metric contract."""
    truth = np.asarray(labels, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    if len(truth) != len(scores) or len(truth) == 0:
        raise ModelingDataError("metrics require equal nonempty labels and probabilities")
    if np.any((scores < 0) | (scores > 1)):
        raise ModelingDataError("predicted complaint probabilities must be within [0, 1]")
    predicted = (scores >= threshold).astype(int)
    matrix = confusion_matrix(truth, predicted, labels=[0, 1])
    return {
        "row_count": len(truth),
        "positive_prevalence": float(np.mean(truth)),
        "predicted_positive_prevalence": float(np.mean(predicted)),
        "accuracy": float(accuracy_score(truth, predicted)),
        "precision": float(precision_score(truth, predicted, zero_division=0)),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "f1": float(f1_score(truth, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(truth, scores)) if len(set(truth)) == 2 else None,
        "pr_auc": float(average_precision_score(truth, scores)) if np.any(truth) else 0.0,
        "brier_score": float(brier_score_loss(truth, scores)),
        "confusion_matrix": [[int(value) for value in row] for row in matrix.tolist()],
        "threshold": threshold,
    }


def _numeric_pipeline(model: Any, *, scale: bool) -> Pipeline:
    steps: list[tuple[str, Any]] = [
        ("imputer", SimpleImputer(strategy="median", add_indicator=True))
    ]
    if scale:
        steps.append(("scaler", StandardScaler()))
    return Pipeline(
        [
            ("preprocess", ColumnTransformer([("numeric", Pipeline(steps), slice(0, None))])),
            ("classifier", model),
        ]
    )


def _identity_pipeline(columns: Sequence[str], c_value: float) -> Pipeline:
    categorical = ["normalized_make", "normalized_model"]
    numeric = [item for item in columns if item not in categorical]
    preprocess = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scaler", StandardScaler()),
                    ]
                ),
                numeric,
            ),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", min_frequency=2),
                categorical,
            ),
        ]
    )
    return Pipeline(
        [
            ("preprocess", preprocess),
            (
                "classifier",
                LogisticRegression(C=c_value, max_iter=1_000, random_state=RANDOM_SEED),
            ),
        ]
    )


def _probabilities(model: Any, values: Any) -> Any:
    result = np.asarray(model.predict_proba(values), dtype=float)
    return result[:, 1]


def _select_rule(
    values: pd.Series, labels: Sequence[int], candidates: Sequence[int]
) -> tuple[int, dict[str, Any]]:
    best_threshold = candidates[0]
    best_metrics: dict[str, Any] | None = None
    for threshold in candidates:
        probabilities = (values.to_numpy() >= threshold).astype(float)
        metrics = classification_metrics(labels, probabilities)
        if best_metrics is None or (metrics["f1"], metrics["roc_auc"]) > (
            best_metrics["f1"],
            best_metrics["roc_auc"],
        ):
            best_threshold = threshold
            best_metrics = metrics
    assert best_metrics is not None
    return best_threshold, best_metrics


def _subgroup_metrics(
    frame: pd.DataFrame, labels: Any, probabilities: Any, group: pd.Series
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    label_array = np.asarray(labels)
    probability_array = np.asarray(probabilities)
    for value in sorted(group.unique()):
        mask = (group == value).to_numpy()
        result[str(value)] = classification_metrics(
            label_array[mask].tolist(), probability_array[mask].tolist()
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


def _coverage_bucket(row: pd.Series) -> str:
    communication = bool(row["communication_observed_by_cutoff"])
    recall = bool(row["recall_observed_by_cutoff"])
    if communication and recall:
        return "communications_and_recalls"
    if communication:
        return "communications_only"
    if recall:
        return "recalls_only"
    return "complaints_only_or_limited"


def run_baseline_experiments(
    data_directory: Path,
    artifact_directory: Path,
    results_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Fit validation-selected sklearn baselines and evaluate the untouched test split."""
    if results_path.exists() or artifact_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite baseline output: {results_path}")
    feature_path = data_directory / "modeling/cohort-features-asof-2022-12-31.jsonl"
    split_path = data_directory / "modeling/cohort-split-2022-12-31.jsonl"
    target_path = data_directory / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    feature_provenance = _json(feature_path.with_name(f"{feature_path.stem}.provenance.json"))
    split_provenance = _json(split_path.with_name(f"{split_path.stem}.provenance.json"))
    target_provenance = _json(target_path.with_name(f"{target_path.stem}.provenance.json"))
    if (
        feature_provenance.get("feature_matrix_version") != FEATURE_MATRIX_VERSION
        or feature_provenance.get("cutoff") != FEATURE_CUTOFF.isoformat()
    ):
        raise ModelingDataError("incompatible feature provenance")
    if (
        split_provenance.get("split_version") != SPLIT_VERSION
        or split_provenance.get("seed") != SPLIT_SEED
    ):
        raise ModelingDataError("incompatible split provenance")
    if target_provenance.get("target_definition_version") != TARGET_DEFINITION_VERSION:
        raise ModelingDataError("incompatible target provenance")
    for path, provenance, label in (
        (feature_path, feature_provenance, "feature"),
        (split_path, split_provenance, "split"),
        (target_path, target_provenance, "target"),
    ):
        if sha256_file(path) != provenance.get("output_sha256"):
            raise ModelingDataError(f"{label} checksum mismatch")
    features = _jsonl(feature_path, FEATURE_SCHEMA)
    splits = _jsonl(split_path, SPLIT_SCHEMA)
    targets = _jsonl(target_path, TARGET_SCHEMA)
    feature_by_id = {row["broad_vehicle_id"]: row for row in features}
    target_by_id = {row["broad_vehicle_id"]: row for row in targets}
    split_by_id = {row["broad_vehicle_id"]: row["split"] for row in splits}
    if (
        len(feature_by_id) != len(features)
        or len(target_by_id) != len(targets)
        or len(split_by_id) != len(splits)
    ):
        raise ModelingDataError("duplicate modeling artifact identity")
    if not (set(feature_by_id) == set(target_by_id) == set(split_by_id)):
        raise ModelingDataError("modeling artifact identities do not align")
    if set(split_by_id.values()) != {"TRAIN", "VALIDATION", "TEST"}:
        raise ModelingDataError("invalid split assignments")
    frame = pd.DataFrame(features).set_index("broad_vehicle_id", drop=False)
    labels = pd.Series(
        {identifier: int(target_by_id[identifier][TARGET_COLUMN]) for identifier in frame.index},
        name=TARGET_COLUMN,
    )
    masks = {name: pd.Series(split_by_id) == name for name in ("TRAIN", "VALIDATION", "TEST")}
    indexes = {name: masks[name][masks[name]].index for name in masks}
    y = {name: labels.loc[indexes[name]].to_numpy(dtype=int) for name in indexes}
    created = clock().astimezone(UTC).isoformat().replace("+00:00", "Z")

    majority_probability = float(np.mean(y["TRAIN"]))
    majority = {
        name: classification_metrics(y[name], np.full(len(y[name]), majority_probability))
        for name in y
    }
    count_threshold, count_validation = _select_rule(
        frame.loc[indexes["VALIDATION"], "historical_complaint_count"],
        y["VALIDATION"],
        tuple(range(2, 51)),
    )
    recent_threshold, recent_validation = _select_rule(
        frame.loc[indexes["VALIDATION"], "complaints_last_12m"],
        y["VALIDATION"],
        tuple(range(1, 21)),
    )
    trivial = {
        "majority_class": {
            "training_positive_prevalence": majority_probability,
            "metrics": majority,
        },
        "historical_any_complaint": {
            "degenerate": True,
            "reason": "eligibility requires at least one historical complaint",
        },
        "historical_count_rule": {
            "selected_threshold": count_threshold,
            "validation_selection_metrics": count_validation,
            "metrics": {
                name: classification_metrics(
                    y[name],
                    (
                        frame.loc[indexes[name], "historical_complaint_count"].to_numpy()
                        >= count_threshold
                    ).astype(float),
                )
                for name in y
            },
        },
        "recent_activity_rule": {
            "selected_threshold": recent_threshold,
            "validation_selection_metrics": recent_validation,
            "metrics": {
                name: classification_metrics(
                    y[name],
                    (
                        frame.loc[indexes[name], "complaints_last_12m"].to_numpy()
                        >= recent_threshold
                    ).astype(float),
                )
                for name in y
            },
        },
    }

    artifact_directory.mkdir(parents=True, exist_ok=False)
    model_results: list[dict[str, Any]] = []

    def evaluate_candidates(
        experiment: str,
        model_name: str,
        columns: Sequence[str],
        candidates: Sequence[tuple[str, Any]],
    ) -> None:
        best: tuple[Any, str, dict[str, Any]] | None = None
        identity = experiment == "all_evidence_make_model_identity"
        for candidate_name, model in candidates:
            x_train = frame.loc[indexes["TRAIN"], list(columns)]
            fitted = model.fit(x_train, y["TRAIN"])
            validation = classification_metrics(
                y["VALIDATION"],
                _probabilities(fitted, frame.loc[indexes["VALIDATION"], list(columns)]),
            )
            if best is None or (validation["roc_auc"], validation["pr_auc"], validation["f1"]) > (
                best[2]["roc_auc"],
                best[2]["pr_auc"],
                best[2]["f1"],
            ):
                best = (fitted, candidate_name, validation)
        assert best is not None
        fitted, candidate_name, validation = best
        metrics = {
            name: classification_metrics(
                y[name], _probabilities(fitted, frame.loc[indexes[name], list(columns)])
            )
            for name in y
        }
        filename = f"{experiment}--{model_name}.joblib"
        model_path = artifact_directory / filename
        joblib.dump(fitted, model_path, compress=3)
        model_results.append(
            {
                "experiment": experiment,
                "model": model_name,
                "creation_timestamp_utc": created,
                "sklearn_version": sklearn.__version__,
                "target_definition_version": target_provenance["target_definition_version"],
                "cutoff": "2022-12-31",
                "split_seed": RANDOM_SEED,
                "feature_columns": list(columns),
                "feature_families": _experiment_families(experiment),
                "identity_features": identity,
                "selected_candidate": candidate_name,
                "hyperparameters": fitted.named_steps["classifier"].get_params(deep=False),
                "metrics": metrics,
                "model_artifact": filename,
                "model_artifact_size_bytes": model_path.stat().st_size,
                "model_artifact_sha256": sha256_file(model_path),
            }
        )

    for experiment, columns in EXPERIMENTS.items():
        candidates = [
            (f"C={value}", _identity_pipeline(columns, value))
            if experiment == "all_evidence_make_model_identity"
            else (
                f"C={value}",
                _numeric_pipeline(
                    LogisticRegression(C=value, max_iter=1_000, random_state=RANDOM_SEED),
                    scale=True,
                ),
            )
            for value in (0.1, 1.0)
        ]
        evaluate_candidates(experiment, "logistic_regression", columns, candidates)

    evaluate_candidates(
        "complaints_communications_recalls",
        "random_forest",
        ALL_EVIDENCE_MODEL,
        [
            (
                f"depth={depth},min_leaf={leaf}",
                _numeric_pipeline(
                    RandomForestClassifier(
                        n_estimators=200,
                        max_depth=depth,
                        min_samples_leaf=leaf,
                        random_state=RANDOM_SEED,
                        n_jobs=1,
                    ),
                    scale=False,
                ),
            )
            for depth, leaf in ((10, 5), (None, 10))
        ],
    )
    evaluate_candidates(
        "complaints_communications_recalls",
        "hist_gradient_boosting",
        ALL_EVIDENCE_MODEL,
        [
            (
                f"learning_rate={rate}",
                _numeric_pipeline(
                    HistGradientBoostingClassifier(
                        learning_rate=rate,
                        max_iter=150,
                        max_leaf_nodes=15,
                        min_samples_leaf=20,
                        random_state=RANDOM_SEED,
                    ),
                    scale=False,
                ),
            )
            for rate in (0.05, 0.1)
        ],
    )
    best = max(
        model_results,
        key=lambda item: (
            item["metrics"]["VALIDATION"]["roc_auc"],
            item["metrics"]["VALIDATION"]["pr_auc"],
        ),
    )
    best_model = joblib.load(artifact_directory / best["model_artifact"])
    best_columns = cast(list[str], best["feature_columns"])
    validation_importance = permutation_importance(
        best_model,
        frame.loc[indexes["VALIDATION"], best_columns],
        y["VALIDATION"],
        scoring="roc_auc",
        n_repeats=5,
        random_state=RANDOM_SEED,
        n_jobs=1,
    )
    importances = sorted(
        (
            {
                "feature": column,
                "mean_roc_auc_decrease": float(mean),
                "standard_deviation": float(std),
            }
            for column, mean, std in zip(
                best_columns,
                validation_importance.importances_mean,
                validation_importance.importances_std,
                strict=True,
            )
        ),
        key=lambda item: cast(float, item["mean_roc_auc_decrease"]),
        reverse=True,
    )
    test_frame = frame.loc[indexes["TEST"]]
    test_probabilities = _probabilities(best_model, test_frame[best_columns])
    subgroup = {
        "age": _subgroup_metrics(
            test_frame,
            y["TEST"],
            test_probabilities,
            test_frame["cohort_age_at_cutoff"].map(_age_bucket),
        ),
        "historical_support": _subgroup_metrics(
            test_frame,
            y["TEST"],
            test_probabilities,
            test_frame["historical_complaint_count"].map(_support_bucket),
        ),
        "source_coverage": _subgroup_metrics(
            test_frame,
            y["TEST"],
            test_probabilities,
            test_frame.apply(_coverage_bucket, axis=1),
        ),
    }
    result = {
        "baseline_version": BASELINE_VERSION,
        "creation_timestamp_utc": created,
        "target": {
            "name": "future_12m_complaint_activity",
            "column": TARGET_COLUMN,
            "definition_version": target_provenance["target_definition_version"],
            "cutoff": "2022-12-31",
            "threshold": 1,
        },
        "reproducibility": {
            "seed": RANDOM_SEED,
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "sklearn_version": sklearn.__version__,
            "joblib_version": joblib.__version__,
            "feature_checksum": sha256_file(feature_path),
            "split_checksum": sha256_file(split_path),
            "target_checksum": sha256_file(target_path),
        },
        "split_counts": {name: len(indexes[name]) for name in indexes},
        "trivial_baselines": trivial,
        "model_results": model_results,
        "best_model": {
            "experiment": best["experiment"],
            "model": best["model"],
            "model_artifact": best["model_artifact"],
            "validation_metrics": best["metrics"]["VALIDATION"],
            "test_metrics": best["metrics"]["TEST"],
        },
        "permutation_importance_validation": importances[:20],
        "best_model_test_subgroups": subgroup,
        "policies": {
            "preprocessing_fit": "training rows only through sklearn Pipeline",
            "model_selection": "validation ROC-AUC, then PR-AUC and F1; test untouched",
            "classification_threshold": 0.5,
            "production": "excluded from primary models",
            "probability_semantics": "estimated probability of observed future complaint activity",
        },
    }
    _write_json(results_path, result)
    return result


def _experiment_families(experiment: str) -> list[str]:
    mapping = {
        "historical_count_only": ["HISTORICAL_COMPLAINT_VOLUME"],
        "complaint_volume_recency": [
            "HISTORICAL_COMPLAINT_VOLUME",
            "HISTORICAL_COMPLAINT_RECENCY",
        ],
        "complaint_features": ["STATIC", "HISTORICAL_COMPLAINTS"],
        "complaints_plus_communications": ["STATIC", "HISTORICAL_COMPLAINTS", "COMMUNICATIONS"],
        "complaints_plus_recalls": ["STATIC", "HISTORICAL_COMPLAINTS", "RECALLS"],
        "complaints_communications_recalls": [
            "STATIC",
            "HISTORICAL_COMPLAINTS",
            "COMMUNICATIONS",
            "RECALLS",
        ],
        "all_evidence_make_model_identity": [
            "STATIC",
            "HISTORICAL_COMPLAINTS",
            "COMMUNICATIONS",
            "RECALLS",
            "MAKE_MODEL_IDENTITY",
        ],
    }
    return mapping[experiment]
