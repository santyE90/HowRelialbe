"""Deterministic explanations for the frozen preferred random forest."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, cast

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.inspection import permutation_importance  # type: ignore[import-untyped]

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_SCHEMA
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL
from howreliable.modeling.features import (
    FEATURE_FAMILIES,
    FEATURE_SCHEMA,
    ModelingDataError,
    _jsonl,
)
from howreliable.modeling.pytorch.training_infrastructure import atomic_write_json
from howreliable.modeling.splits import SPLIT_SCHEMA

EXPLAINABILITY_VERSION: Final = "howreliable-explainability-1.0"


def _family(raw: str) -> str:
    base = raw.removeprefix("missingindicator_")
    for family, names in FEATURE_FAMILIES.items():
        if base in names:
            return family
    return "STATIC"


def _source(family: str) -> str:
    if family == "COMMUNICATIONS":
        return "manufacturer_communications"
    if family == "RECALLS":
        return "recalls"
    if family == "STATIC":
        return "static_cohort_context"
    return "complaints"


def _label(raw: str) -> str:
    base = raw.removeprefix("missingindicator_")
    special = {
        "historical_complaint_count": "Historical complaint volume",
        "complaints_last_12m": "Recent complaint activity",
        "days_since_last_historical_complaint": "Time since most recent complaint",
        "communication_unique_count": "Manufacturer communication history",
        "recall_campaign_count": "Recall campaign history",
    }
    return special.get(
        base,
        base.replace("historical_", "").replace("_", " ").title()
        + (" missing" if raw.startswith("missingindicator_") else ""),
    )


def feature_manifest(model: Any) -> list[dict[str, Any]]:
    names = [
        str(x).removeprefix("numeric__")
        for x in model.named_steps["preprocess"].get_feature_names_out()
    ]
    result = []
    for index, raw in enumerate(names):
        family = _family(raw)
        base = raw.removeprefix("missingindicator_")
        result.append(
            {
                "index": index,
                "transformed_feature_name": raw,
                "raw_feature": base,
                "feature_family": family,
                "source_system": _source(family),
                "human_readable_label": _label(raw),
                "description": f"Model input derived from {base}",
                "value_semantics": "missingness indicator"
                if raw.startswith("missingindicator_")
                else "frozen as-of-cutoff evidence value",
                "feature_type": "binary"
                if raw.startswith("missingindicator_") or base.endswith("_observed_by_cutoff")
                else "share"
                if base.endswith("_share")
                else "count"
                if "count" in base or base.startswith("complaints_last_")
                else "continuous",
                "higher_value_simple_interpretation": not raw.startswith("missingindicator_"),
                "known_caveat": (
                    "association in the fitted model; not a causal or mechanical-failure claim"
                ),
            }
        )
    return result


def tree_path_contributions(classifier: Any, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    contributions = np.zeros((len(matrix), matrix.shape[1]), dtype=float)
    baselines = np.zeros(len(matrix))
    for estimator in classifier.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, 1] / tree.value[:, 0, :].sum(axis=1)
        baselines += values[0] / len(classifier.estimators_)
        paths = estimator.decision_path(matrix)
        for row in range(len(matrix)):
            nodes = paths.indices[paths.indptr[row] : paths.indptr[row + 1]]
            for parent, child in pairwise(nodes):
                feature = tree.feature[parent]
                if feature >= 0:
                    contributions[row, feature] += (values[child] - values[parent]) / len(
                        classifier.estimators_
                    )
    return baselines, contributions


def _aggregate(values: np.ndarray, manifest: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: defaultdict[str, float] = defaultdict(float)
    for item, value in zip(manifest, values, strict=True):
        out[item[key]] += float(value)
    return dict(sorted(out.items(), key=lambda x: -abs(x[1])))


def local_explanation(
    row: dict[str, Any],
    matrix_row: np.ndarray,
    probability: float,
    baseline: float,
    contributions: np.ndarray,
    manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    details = []
    for item, value, feature_value in zip(manifest, contributions, matrix_row, strict=True):
        details.append(
            {
                **item,
                "feature_value": float(feature_value),
                "contribution_probability": float(value),
            }
        )
    positive = sorted(
        (x for x in details if x["contribution_probability"] > 0),
        key=lambda x: -x["contribution_probability"],
    )[:10]
    negative = sorted(
        (x for x in details if x["contribution_probability"] < 0),
        key=lambda x: x["contribution_probability"],
    )[:10]
    warnings = []
    if row["historical_complaint_count"] == 1:
        warnings.append(
            "Historical evaluation is weak for support=1 cohorts; drivers do not imply "
            "prediction confidence."
        )
    if row["cohort_age_at_cutoff"] >= 21:
        warnings.append(
            "Historical evaluation is weak for age-21+ cohorts; drivers do not imply "
            "prediction confidence."
        )
    result = {
        "cohort_id": row["cohort_id"] if "cohort_id" in row else row["broad_vehicle_id"],
        "predicted_future_complaint_probability": probability,
        "predicted_class": int(probability >= 0.5),
        "method": "mean random-forest tree-path probability contribution",
        "baseline_probability": float(baseline),
        "reconstructed_probability": float(baseline + contributions.sum()),
        "reconstruction_absolute_error": abs(float(baseline + contributions.sum() - probability)),
        "top_positive_contributors": positive,
        "top_negative_contributors": negative,
        "family_contributions": _aggregate(contributions, manifest, "feature_family"),
        "source_contributions": _aggregate(contributions, manifest, "source_system"),
        "evaluation_warnings": warnings,
        "caveat": (
            "Contributions describe the fitted complaint-activity estimate, not causation, "
            "failure, repair, safety, or reliability."
        ),
    }
    if "target" in row:
        result["target"] = row["target"]
    return result


def generate_explainability(
    root: Path, output: Path, *, regenerate: bool = False
) -> dict[str, Any]:
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite explainability: {output}")
    output.mkdir(parents=True, exist_ok=True)
    handoff = json.loads((root / "artifacts/evaluation/preferred-model-handoff.json").read_text())
    report_path = root / "artifacts/evaluation/evaluation-report.json"
    if handoff["evaluation_report_checksum"] != sha256_file(report_path):
        raise ModelingDataError("evaluation handoff checksum mismatch")
    model_path = root / handoff["preferred_model_artifact"]
    if sha256_file(model_path) != handoff["preferred_model_checksum"]:
        raise ModelingDataError("preferred model checksum mismatch")
    model = joblib.load(model_path)
    manifest = feature_manifest(model)
    features = pd.DataFrame(
        _jsonl(
            root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl", FEATURE_SCHEMA
        )
    ).set_index("broad_vehicle_id")
    splits = {
        x["broad_vehicle_id"]: x["split"]
        for x in _jsonl(
            root / "data/processed/modeling/cohort-split-2022-12-31.jsonl", SPLIT_SCHEMA
        )
    }
    validation = features.loc[[x for x in features.index if splits[x] == "VALIDATION"]]
    transformed = np.asarray(
        model.named_steps["preprocess"].transform(validation[ALL_EVIDENCE_MODEL])
    )
    classifier = model.named_steps["classifier"]
    _, global_contrib = tree_path_contributions(classifier, transformed)
    individual = np.mean(np.abs(global_contrib), axis=0)
    # Cross-check uses frozen labels from the target artifact.
    targets = {
        x["broad_vehicle_id"]: int(x["future_any_complaint"])
        for x in _jsonl(
            root / "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl",
            TARGET_SCHEMA,
        )
    }
    perm = permutation_importance(
        model,
        validation[ALL_EVIDENCE_MODEL],
        np.asarray([targets[x] for x in validation.index]),
        scoring="roc_auc",
        n_repeats=10,
        random_state=20220913,
        n_jobs=1,
    )
    globals_ = [
        {
            **item,
            "mean_absolute_contribution": float(individual[item["index"]]),
            "permutation_mean_roc_auc_decrease": float(
                perm.importances_mean[ALL_EVIDENCE_MODEL.index(item["raw_feature"])]
            )
            if item["raw_feature"] in ALL_EVIDENCE_MODEL
            and not item["transformed_feature_name"].startswith("missingindicator_")
            else None,
        }
        for item in manifest
    ]
    predictions = [
        json.loads(x)
        for x in (root / "artifacts/evaluation/predictions/random-forest-test.jsonl")
        .read_text()
        .splitlines()
    ]
    rules: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("high_probability_observed_positive", lambda x: x["target"] == 1),
        ("low_probability_observed_zero", lambda x: x["target"] == 0),
        ("false_positive", lambda x: x["target"] == 0 and x["predicted_class"] == 1),
        ("false_negative", lambda x: x["target"] == 1 and x["predicted_class"] == 0),
        ("support_1", lambda x: x["historical_complaint_count"] == 1),
        ("age_21_plus", lambda x: x["cohort_age_at_cutoff"] >= 21),
    ]
    selected: list[tuple[str, dict[str, Any]]] = []
    for name, rule in rules:
        candidates = [x for x in predictions if rule(x)]
        chosen = (
            max(candidates, key=lambda x: x["predicted_future_complaint_probability"])
            if name in {"high_probability_observed_positive", "false_positive"}
            else min(candidates, key=lambda x: x["predicted_future_complaint_probability"])
        )
        if name not in {x[0] for x in selected}:
            selected.append((name, chosen))
    test_frame = features.loc[[x[1]["cohort_id"] for x in selected]]
    test_matrix = np.asarray(
        model.named_steps["preprocess"].transform(test_frame[ALL_EVIDENCE_MODEL])
    )
    base, local_values = tree_path_contributions(classifier, test_matrix)
    locals_ = [
        {
            "selection_rule": name,
            **local_explanation(
                row,
                test_matrix[i],
                row["predicted_future_complaint_probability"],
                base[i],
                local_values[i],
                manifest,
            ),
        }
        for i, (name, row) in enumerate(selected)
    ]
    atomic_write_json(
        output / "feature-manifest.json", {"version": EXPLAINABILITY_VERSION, "features": manifest}
    )
    global_art = {
        "method": "mean absolute tree-path probability contribution",
        "top_20": sorted(globals_, key=lambda x: -x["mean_absolute_contribution"])[:20],
        "family_importance": _aggregate(individual, manifest, "feature_family"),
        "source_importance": _aggregate(individual, manifest, "source_system"),
        "permutation_cross_check": sorted(
            [
                {
                    "raw_feature": name,
                    "mean_roc_auc_decrease": float(mean),
                    "standard_deviation": float(std),
                }
                for name, mean, std in zip(
                    ALL_EVIDENCE_MODEL, perm.importances_mean, perm.importances_std, strict=True
                )
            ],
            key=lambda x: -cast(float, x["mean_roc_auc_decrease"]),
        ),
    }
    atomic_write_json(output / "global-importance.json", global_art)
    atomic_write_json(output / "representative-local-explanations.json", {"examples": locals_})
    result = {
        "explainability_version": EXPLAINABILITY_VERSION,
        "preferred_model_identifier": "phase-3b-random-forest",
        "preferred_model_checksum": sha256_file(model_path),
        "evaluation_report_checksum": sha256_file(report_path),
        "feature_artifact_checksum": handoff["feature_contract_checksum"],
        "split_checksum": handoff["split_checksum"],
        "method": "exact random-forest tree-path probability contributions; SHAP unavailable",
        "method_configuration": {
            "permutation_repeats": 10,
            "seed": 20220913,
            "permutation_split": "VALIDATION",
        },
        "feature_manifest_checksum": sha256_file(output / "feature-manifest.json"),
        "global_importance": global_art,
        "representative_local_explanations": locals_,
        "maximum_reconstruction_absolute_error": max(
            x["reconstruction_absolute_error"] for x in locals_
        ),
        "limitations": [
            "correlated features redistribute importance",
            "associational model contributions are not causal",
            "sparse and old cohorts have weak historical performance",
        ],
        "supported_language": "feature contributed to predicted future complaint probability",
        "prohibited_language": [
            "risk factor",
            "failure cause",
            "repair probability",
            "reliability explanation",
            "safety probability",
        ],
        "generation_timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    atomic_write_json(output / "explainability-report.json", result)
    result["report_sha256"] = sha256_file(output / "explainability-report.json")
    return result
