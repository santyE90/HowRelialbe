"""Phase 4B complaint-activity presentation contract tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import joblib  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

from howreliable.data.ingestion.nhtsa_complaints import sha256_file
from howreliable.modeling import presentation
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL
from howreliable.modeling.features import FEATURE_SCHEMA, ModelingDataError, _jsonl
from howreliable.modeling.presentation import (
    GENERAL_LIMITATION,
    MODEL_CHECKSUM,
    RESULT_CONTRACT_VERSION,
    THRESHOLD,
    ComplaintActivityClassification,
    ComplaintActivityResult,
    EvaluationContext,
    Prediction,
    build_complaint_activity_result,
    classify_probability,
    deterministic_json,
    generate_presentation_artifacts,
)

ROOT = Path(".")
FIXED_TIME = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def representative_source() -> list[dict[str, object]]:
    value = json.loads(
        Path("artifacts/explainability/representative-local-explanations.json").read_text()
    )
    return cast(list[dict[str, object]], value["examples"])


@pytest.fixture(scope="module")
def result(representative_source: list[dict[str, object]]) -> ComplaintActivityResult:
    return build_complaint_activity_result(
        ROOT, str(representative_source[0]["cohort_id"]), generation_utc=FIXED_TIME
    )


def test_schema_is_strict_immutable_and_probability_is_bounded(
    result: ComplaintActivityResult,
) -> None:
    assert ComplaintActivityResult.model_validate(result.model_dump()) == result
    with pytest.raises(ValidationError):
        result.identity.model_year = 2000
    with pytest.raises(ValidationError):
        Prediction(
            predicted_future_complaint_probability=1.01,
            predicted_future_complaint_percentage=101.0,
        )
    with pytest.raises(ValidationError):
        Prediction(
            predicted_future_complaint_probability=0.4,
            predicted_future_complaint_percentage=41.0,
        )


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.499999, ComplaintActivityClassification.NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED),
        (0.5, ComplaintActivityClassification.OBSERVED_COMPLAINT_ACTIVITY_EXPECTED),
        (0.500001, ComplaintActivityClassification.OBSERVED_COMPLAINT_ACTIVITY_EXPECTED),
    ],
)
def test_frozen_threshold_classification(
    probability: float, expected: ComplaintActivityClassification
) -> None:
    classification, label = classify_probability(probability)
    assert THRESHOLD == 0.5
    assert classification is expected
    assert "risk" not in label.casefold()


def test_result_semantics_lineage_and_exact_frozen_inference(
    result: ComplaintActivityResult,
) -> None:
    assert result.identity.prediction_grain == "make_model_model_year_cohort"
    assert result.prediction.predicted_future_complaint_percentage == (
        result.prediction.predicted_future_complaint_probability * 100.0
    )
    window = result.prediction.target_window
    assert (
        window.target,
        window.history_cutoff,
        window.future_window_start,
        window.future_window_end,
    ) == ("future_12m_complaint_activity", "2022-12-31", "2023-01-01", "2023-12-31")
    assert result.general_limitation == GENERAL_LIMITATION
    provenance = result.provenance
    assert provenance.result_contract_version == RESULT_CONTRACT_VERSION
    assert provenance.model_checksum == MODEL_CHECKSUM
    assert provenance.model_status == "PREFERRED"
    assert provenance.evaluation_version == "howreliable-evaluation-1.0"
    assert provenance.explainability_version == "howreliable-explainability-1.0"
    model = joblib.load(
        "artifacts/models/baselines/complaints_communications_recalls--random_forest.joblib"
    )
    assert (
        sha256_file(
            Path(
                "artifacts/models/baselines/complaints_communications_recalls--random_forest.joblib"
            )
        )
        == MODEL_CHECKSUM
    )
    rows = _jsonl(
        Path("data/processed/modeling/cohort-features-asof-2022-12-31.jsonl"), FEATURE_SCHEMA
    )
    row = next(x for x in rows if x["broad_vehicle_id"] == result.identity.cohort_id)
    frozen_probability = float(model.predict_proba(pd.DataFrame([row])[ALL_EVIDENCE_MODEL])[0, 1])
    assert result.prediction.predicted_future_complaint_probability == frozen_probability
    assert result.explanation.reconstruction_absolute_error < 1e-12
    assert result.explanation.reconstructed_probability == pytest.approx(
        result.prediction.predicted_future_complaint_probability, abs=1e-12
    )


def test_explanation_is_compact_ordered_and_preserves_aggregations(
    result: ComplaintActivityResult, representative_source: list[dict[str, object]]
) -> None:
    positive = result.explanation.top_positive_contributors
    negative = result.explanation.top_negative_contributors
    assert len(positive) <= 5 and len(negative) <= 5
    assert [x.contribution_probability for x in positive] == sorted(
        (x.contribution_probability for x in positive), reverse=True
    )
    assert [x.contribution_probability for x in negative] == sorted(
        x.contribution_probability for x in negative
    )
    assert result.explanation.feature_family_contributions
    assert result.explanation.evidence_source_contributions
    source = representative_source[0]
    assert result.explanation.feature_family_contributions == source["family_contributions"]
    assert result.explanation.evidence_source_contributions == source["source_contributions"]
    assert all(
        driver.feature_family and driver.evidence_source for driver in (*positive, *negative)
    )


def test_evidence_profile_and_only_supported_limitation_flags(
    representative_source: list[dict[str, object]],
) -> None:
    by_rule = {str(item["selection_rule"]): item for item in representative_source}
    sparse = build_complaint_activity_result(
        ROOT, str(by_rule["support_1"]["cohort_id"]), generation_utc=FIXED_TIME
    )
    old = build_complaint_activity_result(
        ROOT, str(by_rule["age_21_plus"]["cohort_id"]), generation_utc=FIXED_TIME
    )
    assert sparse.evidence.historical_complaint_support == 1
    assert {x.code.value for x in sparse.limitation_flags} == {"SPARSE_HISTORICAL_SUPPORT"}
    assert old.evidence.cohort_age_years >= 21
    assert "OLD_COHORT_WEAK_EVALUATION" in {x.code.value for x in old.limitation_flags}
    allowed = {"SPARSE_HISTORICAL_SUPPORT", "OLD_COHORT_WEAK_EVALUATION"}
    assert {x.code.value for x in (*sparse.limitation_flags, *old.limitation_flags)} <= allowed
    assert isinstance(sparse.evidence.has_manufacturer_communications, bool)
    assert isinstance(sparse.evidence.has_recall_evidence, bool)


def test_future_ground_truth_is_optional_and_separate(
    result: ComplaintActivityResult, representative_source: list[dict[str, object]]
) -> None:
    assert result.evaluation_context is None
    source = representative_source[0]
    example = build_complaint_activity_result(
        ROOT,
        str(source["cohort_id"]),
        generation_utc=FIXED_TIME,
        evaluation_context=EvaluationContext(
            selection_rule=str(source["selection_rule"]),
            observed_target=cast(Literal[0, 1], source["target"]),
        ),
    )
    assert example.evaluation_context is not None
    assert "observed_target" not in result.model_dump()["prediction"]


def test_contract_checksum_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation, "EVALUATION_REPORT_CHECKSUM", "0" * 64)
    with pytest.raises(ModelingDataError, match="evaluation checksum"):
        presentation._validate_contracts(ROOT)


def test_deterministic_serialization_and_no_score_or_band_fields(
    result: ComplaintActivityResult,
) -> None:
    first = deterministic_json(result)
    second = deterministic_json(ComplaintActivityResult.model_validate_json(first))
    assert first == second
    fields = first.casefold()
    assert "risk_score" not in fields
    assert "confidence_score" not in fields
    assert "risk_band" not in fields
    assert "recommendation" not in fields


def test_representative_artifacts_and_api_handoff(
    tmp_path: Path, representative_source: list[dict[str, object]]
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    left = generate_presentation_artifacts(ROOT, first, clock=lambda: FIXED_TIME)
    right = generate_presentation_artifacts(ROOT, second, clock=lambda: FIXED_TIME)
    assert left == right
    assert left["representative_count"] == 6
    assert (first / "representative-results.json").read_bytes() == (
        second / "representative-results.json"
    ).read_bytes()
    handoff = json.loads((first / "api-handoff.json").read_text())
    assert handoff["future_ground_truth_required"] is False
    assert handoff["model_status"] == "PREFERRED"
    assert handoff["supported_input_grain"] == "make_model_model_year_cohort"
    results = json.loads((first / "representative-results.json").read_text())["examples"]
    assert {item["evaluation_context"]["selection_rule"] for item in results} == {
        str(item["selection_rule"]) for item in representative_source
    }
    assert all(
        item["prediction"]["predicted_future_complaint_probability"]
        == pytest.approx(item["explanation"]["reconstructed_probability"], abs=1e-12)
        for item in results
    )


def test_phase_does_not_implement_training_or_api() -> None:
    source = Path("src/howreliable/modeling/presentation.py").read_text().casefold()
    assert ".fit(" not in source
    assert "fastapi" not in source
    assert not Path("src/howreliable/api").exists()
