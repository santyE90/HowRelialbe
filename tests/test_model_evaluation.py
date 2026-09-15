"""Phase 3F frozen-prediction evaluation tests."""

from __future__ import annotations

from typing import Any

import pytest

from howreliable.modeling.evaluation_report import (
    aggregate_metrics,
    agreement,
    bootstrap_intervals,
    calibration,
    critical_subgroup_intervals,
    error_analysis,
    paired_bootstrap,
    subgroup_results,
    threshold_sensitivity,
    validate_alignment,
    validate_predictions,
)
from howreliable.modeling.features import ModelingDataError


def _rows(offset: float = 0.0) -> list[dict[str, Any]]:
    probabilities = (0.1, 0.2, 0.4, 0.7, 0.8, 0.9, 0.3, 0.6, 0.15, 0.85)
    labels = (0, 0, 1, 1, 0, 1, 0, 1, 1, 0)
    rows = []
    for index, (probability, target) in enumerate(zip(probabilities, labels, strict=True)):
        score = min(1.0, max(0.0, probability + offset))
        rows.append(
            {
                "cohort_id": f"c{index}",
                "target": target,
                "predicted_future_complaint_probability": score,
                "predicted_class": int(score >= 0.5),
                "split": "TEST",
                "model_year": 1998 + index * 3,
                "cohort_age_at_cutoff": 24 - index * 2,
                "historical_complaint_count": (1, 3, 7, 20, 60)[index % 5],
                "communication_observed_by_cutoff": bool(index % 2),
                "recall_observed_by_cutoff": bool((index // 2) % 2),
            }
        )
    return rows


def test_prediction_metrics_bootstrap_calibration_and_thresholds() -> None:
    rows = _rows()
    validate_predictions(rows)
    metrics = aggregate_metrics(rows)
    assert metrics["row_count"] == 10
    assert sum(sum(part) for part in metrics["confusion_matrix"]) == 10
    first = bootstrap_intervals(rows, samples=30, seed=7)
    assert first == bootstrap_intervals(rows, samples=30, seed=7)
    assert all(value["lower_95"] <= value["upper_95"] for value in first.values())
    diagnostic = calibration(rows)
    assert sum(item["row_count"] for item in diagnostic["bins"]) == 10
    sensitivity = threshold_sensitivity(rows)
    assert [item["threshold"] for item in sensitivity] == [0.3, 0.4, 0.5, 0.6, 0.7]


def test_paired_subgroups_errors_and_agreement_accounting() -> None:
    forest, mlp = _rows(), _rows(0.03)
    paired = paired_bootstrap(forest, mlp, samples=30, seed=9)
    assert paired == paired_bootstrap(forest, mlp, samples=30, seed=9)
    assert paired["f1"]["lower_95"] <= paired["f1"]["upper_95"]
    groups = subgroup_results(forest)
    assert sum(value["row_count"] for value in groups["age"].values()) == 10
    assert groups["historical_support"]["50+"]["roc_auc"] is None
    intervals = critical_subgroup_intervals(forest, samples=30)
    assert set(intervals) == {"support_1", "age_21_plus"}
    errors = error_analysis(forest)
    assert sum(value["row_count"] for value in errors["categories"].values()) == 10
    model_agreement = agreement(forest, mlp)
    assert (
        model_agreement["both_correct"]
        + model_agreement["forest_only_correct"]
        + model_agreement["mlp_only_correct"]
        + model_agreement["both_incorrect"]
        == 10
    )


def test_prediction_validation_and_pair_alignment_guards() -> None:
    invalid = _rows()
    invalid[0]["predicted_future_complaint_probability"] = 2.0
    with pytest.raises(ModelingDataError, match="outside"):
        validate_predictions(invalid)
    misaligned = _rows()
    misaligned.reverse()
    with pytest.raises(ModelingDataError, match="paired"):
        validate_alignment(_rows(), misaligned)
