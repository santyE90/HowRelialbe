"""Canonical complaint-activity presentation and decision contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, cast

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION
from howreliable.modeling.baselines import ALL_EVIDENCE_MODEL
from howreliable.modeling.evaluation_report import EVALUATION_VERSION
from howreliable.modeling.explainability import (
    EXPLAINABILITY_VERSION,
    feature_manifest,
    local_explanation,
    tree_path_contributions,
)
from howreliable.modeling.features import FEATURE_SCHEMA, ModelingDataError, _jsonl
from howreliable.modeling.pytorch.training_infrastructure import atomic_write_json

RESULT_CONTRACT_VERSION: Final = "complaint-activity-result-1.0"
SCHEMA_IDENTIFIER: Final = "ComplaintActivityResult"
MODEL_IDENTIFIER: Final = "phase-3b-random-forest"
MODEL_STATUS: Final = "PREFERRED"
MODEL_CHECKSUM: Final = "e1b9b382409d8edff43a2c7edd63576c3aeaaaedb00e334db9ba78cf061b40df"
EVALUATION_REPORT_CHECKSUM: Final = (
    "1d0c13477b2846d98fa54926e2bde6679cc9e68c539e11f26631fc332db673d8"
)
EXPLAINABILITY_REPORT_CHECKSUM: Final = (
    "ce1bc6907bb84079dd426192b3bfb9b1b1657a519b7e6e94d75d05ccce3d5b95"
)
THRESHOLD: Final = 0.5
GENERAL_LIMITATION: Final = (
    "This estimate describes observed NHTSA complaint reporting for a "
    "make/model/model-year cohort. It is not a mechanical-failure, repair, reliability, "
    "safety, or individual-vehicle probability."
)
PROHIBITED_TERMINOLOGY: Final = (
    "reliability score",
    "repair risk",
    "failure risk",
    "safety score",
    "risk level",
    "high risk",
    "low risk",
    "confidence score",
    "likely to fail",
    "buy",
    "avoid",
)


class FrozenModel(BaseModel):
    """Strict immutable base for the public result contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ComplaintActivityClassification(StrEnum):
    OBSERVED_COMPLAINT_ACTIVITY_EXPECTED = "OBSERVED_COMPLAINT_ACTIVITY_EXPECTED"
    NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED = "NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED"


class ResultIdentity(FrozenModel):
    cohort_id: str = Field(min_length=1)
    normalized_make: str = Field(min_length=1)
    normalized_model: str = Field(min_length=1)
    model_year: int
    prediction_grain: Literal["make_model_model_year_cohort"] = "make_model_model_year_cohort"


class TargetWindow(FrozenModel):
    target: Literal["future_12m_complaint_activity"] = "future_12m_complaint_activity"
    history_cutoff: Literal["2022-12-31"] = "2022-12-31"
    future_window_start: Literal["2023-01-01"] = "2023-01-01"
    future_window_end: Literal["2023-12-31"] = "2023-12-31"


class Prediction(FrozenModel):
    predicted_future_complaint_probability: float = Field(ge=0.0, le=1.0)
    predicted_future_complaint_percentage: float = Field(ge=0.0, le=100.0)
    target_window: TargetWindow = Field(default_factory=TargetWindow)

    @model_validator(mode="after")
    def validate_percentage(self) -> Prediction:
        if self.predicted_future_complaint_percentage != (
            self.predicted_future_complaint_probability * 100.0
        ):
            raise ValueError("display percentage must equal probability * 100")
        return self


class Classification(FrozenModel):
    threshold: float = Field(default=THRESHOLD, ge=THRESHOLD, le=THRESHOLD)
    threshold_source: Literal["frozen_phase_3_model_contract"] = "frozen_phase_3_model_contract"
    predicted_classification: ComplaintActivityClassification
    presentation_label: str

    @model_validator(mode="after")
    def validate_label(self) -> Classification:
        labels = {
            ComplaintActivityClassification.OBSERVED_COMPLAINT_ACTIVITY_EXPECTED: (
                "Complaint activity predicted"
            ),
            ComplaintActivityClassification.NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED: (
                "No complaint activity predicted"
            ),
        }
        if self.presentation_label != labels[self.predicted_classification]:
            raise ValueError("presentation label does not match classification")
        return self


class ExplanationDriver(FrozenModel):
    feature_name: str
    human_readable_label: str
    transformed_value: float
    contribution_probability: float
    feature_family: str
    evidence_source: str


class Explanation(FrozenModel):
    method: Literal["mean random-forest tree-path probability contribution"]
    baseline_probability: float
    top_positive_contributors: tuple[ExplanationDriver, ...]
    top_negative_contributors: tuple[ExplanationDriver, ...]
    feature_family_contributions: dict[str, float]
    evidence_source_contributions: dict[str, float]
    reconstructed_probability: float
    reconstruction_absolute_error: float = Field(ge=0.0)
    behavior_not_causation: Literal[True] = True


class EvidenceProfile(FrozenModel):
    historical_complaint_support: int = Field(ge=1)
    cohort_age_years: int = Field(ge=0)
    historical_years_observed: float = Field(ge=0.0)
    days_since_most_recent_complaint: int = Field(ge=0)
    has_manufacturer_communications: bool
    manufacturer_communication_status: str
    has_recall_evidence: bool
    recall_status: str


class LimitationCode(StrEnum):
    SPARSE_HISTORICAL_SUPPORT = "SPARSE_HISTORICAL_SUPPORT"
    OLD_COHORT_WEAK_EVALUATION = "OLD_COHORT_WEAK_EVALUATION"


class LimitationFlag(FrozenModel):
    code: LimitationCode
    title: str
    message: str
    source_evaluation_basis: str


class Provenance(FrozenModel):
    result_contract_version: Literal["complaint-activity-result-1.0"] = RESULT_CONTRACT_VERSION
    model_identifier: Literal["phase-3b-random-forest"] = MODEL_IDENTIFIER
    model_status: Literal["PREFERRED"] = MODEL_STATUS
    model_checksum: str
    target_version: str
    feature_contract_checksum: str
    preprocessing_contract: str
    evaluation_version: str
    evaluation_report_checksum: str
    explainability_version: str
    explainability_report_checksum: str
    threshold: float = Field(default=THRESHOLD, ge=THRESHOLD, le=THRESHOLD)
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


class EvaluationContext(FrozenModel):
    """Optional example/evaluation metadata; never required for inference."""

    selection_rule: str
    observed_target: Literal[0, 1]


class ComplaintActivityResult(FrozenModel):
    identity: ResultIdentity
    prediction: Prediction
    classification: Classification
    explanation: Explanation
    evidence: EvidenceProfile
    limitation_flags: tuple[LimitationFlag, ...]
    general_limitation: Literal[
        "This estimate describes observed NHTSA complaint reporting for a make/model/model-year "
        "cohort. It is not a mechanical-failure, repair, reliability, safety, or "
        "individual-vehicle probability."
    ] = GENERAL_LIMITATION
    provenance: Provenance
    evaluation_context: EvaluationContext | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ComplaintActivityResult:
        probability = self.prediction.predicted_future_complaint_probability
        expected = (
            ComplaintActivityClassification.OBSERVED_COMPLAINT_ACTIVITY_EXPECTED
            if probability >= THRESHOLD
            else ComplaintActivityClassification.NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED
        )
        if self.classification.predicted_classification != expected:
            raise ValueError("classification does not match the frozen threshold")
        if abs(self.explanation.reconstructed_probability - probability) > 1e-12:
            raise ValueError("explanation does not reconstruct prediction")
        return self


def classify_probability(
    probability: float,
) -> tuple[ComplaintActivityClassification, str]:
    """Apply the frozen binary target threshold and its literal presentation label."""
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    if probability >= THRESHOLD:
        return (
            ComplaintActivityClassification.OBSERVED_COMPLAINT_ACTIVITY_EXPECTED,
            "Complaint activity predicted",
        )
    return (
        ComplaintActivityClassification.NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED,
        "No complaint activity predicted",
    )


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelingDataError(f"cannot read presentation dependency: {path}") from error
    if not isinstance(value, dict):
        raise ModelingDataError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _validate_contracts(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    handoff_path = root / "artifacts/evaluation/preferred-model-handoff.json"
    evaluation_path = root / "artifacts/evaluation/evaluation-report.json"
    explanation_path = root / "artifacts/explainability/explainability-report.json"
    handoff = _read_object(handoff_path)
    evaluation = _read_object(evaluation_path)
    explanation = _read_object(explanation_path)
    feature_path = root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl"
    split_path = root / "data/processed/modeling/cohort-split-2022-12-31.jsonl"
    manifest_path = root / "artifacts/explainability/feature-manifest.json"
    target_path = root / "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl"
    target_provenance = _read_object(target_path.with_name(f"{target_path.stem}.provenance.json"))
    checks = (
        (handoff.get("preferred_model_identifier"), MODEL_IDENTIFIER, "model identifier"),
        (handoff.get("preferred_model_checksum"), MODEL_CHECKSUM, "model checksum"),
        (handoff.get("evaluation_version"), EVALUATION_VERSION, "evaluation version"),
        (handoff.get("threshold"), THRESHOLD, "threshold"),
        (
            handoff.get("evaluation_report_checksum"),
            EVALUATION_REPORT_CHECKSUM,
            "handoff evaluation checksum",
        ),
        (sha256_file(evaluation_path), EVALUATION_REPORT_CHECKSUM, "evaluation report checksum"),
        (
            sha256_file(explanation_path),
            EXPLAINABILITY_REPORT_CHECKSUM,
            "explainability report checksum",
        ),
        (evaluation.get("evaluation_version"), EVALUATION_VERSION, "evaluation contract"),
        (
            explanation.get("explainability_version"),
            EXPLAINABILITY_VERSION,
            "explainability contract",
        ),
        (explanation.get("preferred_model_checksum"), MODEL_CHECKSUM, "explanation model"),
        (
            explanation.get("evaluation_report_checksum"),
            EVALUATION_REPORT_CHECKSUM,
            "explanation evaluation",
        ),
        (
            sha256_file(feature_path),
            handoff.get("feature_contract_checksum"),
            "feature contract checksum",
        ),
        (sha256_file(split_path), handoff.get("split_checksum"), "split checksum"),
        (
            sha256_file(manifest_path),
            explanation.get("feature_manifest_checksum"),
            "explanation feature manifest checksum",
        ),
        (
            target_provenance.get("target_definition_version"),
            TARGET_DEFINITION_VERSION,
            "target version",
        ),
        (
            sha256_file(target_path),
            target_provenance.get("output_sha256"),
            "target artifact checksum",
        ),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise ModelingDataError(f"{label} mismatch")
    model_path = root / cast(str, handoff["preferred_model_artifact"])
    if sha256_file(model_path) != MODEL_CHECKSUM:
        raise ModelingDataError("preferred model artifact checksum mismatch")
    return handoff, explanation


def _driver(item: dict[str, Any]) -> ExplanationDriver:
    return ExplanationDriver(
        feature_name=cast(str, item["raw_feature"]),
        human_readable_label=cast(str, item["human_readable_label"]),
        transformed_value=float(item["feature_value"]),
        contribution_probability=float(item["contribution_probability"]),
        feature_family=cast(str, item["feature_family"]),
        evidence_source=cast(str, item["source_system"]),
    )


def _flags(row: dict[str, Any]) -> tuple[LimitationFlag, ...]:
    result = []
    if row["historical_complaint_count"] == 1:
        result.append(
            LimitationFlag(
                code=LimitationCode.SPARSE_HISTORICAL_SUPPORT,
                title="Sparse historical support",
                message=(
                    "Historical evaluation is weak for cohorts with only one historical "
                    "complaint; prediction drivers do not imply prediction confidence."
                ),
                source_evaluation_basis="Phase 3F support=1 test subgroup",
            )
        )
    if row["cohort_age_at_cutoff"] >= 21:
        result.append(
            LimitationFlag(
                code=LimitationCode.OLD_COHORT_WEAK_EVALUATION,
                title="Old cohort evaluation limitation",
                message=(
                    "Historical evaluation is weak for cohorts aged 21 years or more at the "
                    "cutoff; prediction drivers do not imply prediction confidence."
                ),
                source_evaluation_basis="Phase 3F age-21+ test subgroup",
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PresentationResources:
    """Validated read-only resources reusable for multiple result builds."""

    handoff: Mapping[str, Any]
    model: Any
    rows_by_id: Mapping[str, dict[str, Any]]
    manifest: tuple[dict[str, Any], ...]


def load_presentation_resources(root: Path) -> PresentationResources:
    """Validate and load the frozen Phase 4B resources exactly once for a service."""
    handoff, _ = _validate_contracts(root)
    model_path = root / cast(str, handoff["preferred_model_artifact"])
    model = joblib.load(model_path)
    rows = _jsonl(
        root / "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl",
        FEATURE_SCHEMA,
    )
    rows_by_id = {cast(str, row["broad_vehicle_id"]): row for row in rows}
    if len(rows_by_id) != len(rows):
        raise ModelingDataError("feature artifact contains duplicate cohort identities")
    return PresentationResources(
        handoff=MappingProxyType(handoff),
        model=model,
        rows_by_id=MappingProxyType(rows_by_id),
        manifest=tuple(feature_manifest(model)),
    )


def build_complaint_activity_result(
    root: Path,
    cohort_id: str,
    *,
    generation_utc: datetime | None = None,
    evaluation_context: EvaluationContext | None = None,
    resources: PresentationResources | None = None,
) -> ComplaintActivityResult:
    """Build the sole authoritative result for one supported frozen-feature cohort."""
    loaded = resources or load_presentation_resources(root)
    handoff = loaded.handoff
    model = loaded.model
    row = loaded.rows_by_id.get(cohort_id)
    if row is None:
        raise ModelingDataError(f"expected one supported cohort row: {cohort_id}")
    frame = pd.DataFrame([row])
    probability = float(model.predict_proba(frame[ALL_EVIDENCE_MODEL])[0, 1])
    matrix = np.asarray(model.named_steps["preprocess"].transform(frame[ALL_EVIDENCE_MODEL]))
    baseline, contributions = tree_path_contributions(model.named_steps["classifier"], matrix)
    local = local_explanation(
        row,
        matrix[0],
        probability,
        baseline[0],
        contributions[0],
        list(loaded.manifest),
    )
    classification, label = classify_probability(probability)
    generated = generation_utc or datetime.now(UTC)
    return ComplaintActivityResult(
        identity=ResultIdentity(
            cohort_id=cohort_id,
            normalized_make=cast(str, row["normalized_make"]),
            normalized_model=cast(str, row["normalized_model"]),
            model_year=cast(int, row["model_year"]),
        ),
        prediction=Prediction(
            predicted_future_complaint_probability=probability,
            predicted_future_complaint_percentage=probability * 100.0,
        ),
        classification=Classification(
            predicted_classification=classification,
            presentation_label=label,
        ),
        explanation=Explanation(
            method=cast(Any, local["method"]),
            baseline_probability=float(local["baseline_probability"]),
            top_positive_contributors=tuple(
                _driver(x) for x in local["top_positive_contributors"][:5]
            ),
            top_negative_contributors=tuple(
                _driver(x) for x in local["top_negative_contributors"][:5]
            ),
            feature_family_contributions=dict(local["family_contributions"]),
            evidence_source_contributions=dict(local["source_contributions"]),
            reconstructed_probability=float(local["reconstructed_probability"]),
            reconstruction_absolute_error=float(local["reconstruction_absolute_error"]),
        ),
        evidence=EvidenceProfile(
            historical_complaint_support=cast(int, row["historical_complaint_count"]),
            cohort_age_years=cast(int, row["cohort_age_at_cutoff"]),
            historical_years_observed=float(row["historical_years_observed"]),
            days_since_most_recent_complaint=cast(int, row["days_since_last_historical_complaint"]),
            has_manufacturer_communications=cast(bool, row["communication_observed_by_cutoff"]),
            manufacturer_communication_status=cast(str, row["communication_asof_status"]),
            has_recall_evidence=cast(bool, row["recall_observed_by_cutoff"]),
            recall_status=cast(str, row["recall_asof_status"]),
        ),
        limitation_flags=_flags(row),
        provenance=Provenance(
            model_checksum=MODEL_CHECKSUM,
            target_version=TARGET_DEFINITION_VERSION,
            feature_contract_checksum=cast(str, handoff["feature_contract_checksum"]),
            preprocessing_contract=cast(str, handoff["preprocessing"]),
            evaluation_version=EVALUATION_VERSION,
            evaluation_report_checksum=EVALUATION_REPORT_CHECKSUM,
            explainability_version=EXPLAINABILITY_VERSION,
            explainability_report_checksum=EXPLAINABILITY_REPORT_CHECKSUM,
            generation_utc=generated.astimezone(UTC),
        ),
        evaluation_context=evaluation_context,
    )


def deterministic_json(value: BaseModel | dict[str, Any]) -> str:
    """Serialize in lexical field order with explicit nulls and finite JSON floats."""
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def generate_presentation_artifacts(
    root: Path,
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Generate the contract, representative results, and Phase 5A handoff."""
    paths = tuple(
        output / name
        for name in (
            "presentation-contract.json",
            "representative-results.json",
            "api-handoff.json",
        )
    )
    existing = next((path for path in paths if path.exists()), None)
    if existing and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite presentation artifact: {existing}")
    output.mkdir(parents=True, exist_ok=True)
    generated = clock().astimezone(UTC)
    source = _read_object(root / "artifacts/explainability/representative-local-explanations.json")[
        "examples"
    ]
    resources = load_presentation_resources(root)
    examples = []
    for item in source:
        context = EvaluationContext(
            selection_rule=cast(str, item["selection_rule"]),
            observed_target=cast(Literal[0, 1], item["target"]),
        )
        examples.append(
            build_complaint_activity_result(
                root,
                cast(str, item["cohort_id"]),
                generation_utc=generated,
                evaluation_context=context,
                resources=resources,
            ).model_dump(mode="json")
        )
    contract = {
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "canonical_schema_identifier": SCHEMA_IDENTIFIER,
        "json_schema": ComplaintActivityResult.model_json_schema(),
        "serialization": {
            "field_order": "lexicographic object-key order",
            "enums": "serialized as their string values",
            "floats": "finite JSON numbers using Python shortest round-trip representation",
            "timestamps": "timezone-aware UTC ISO 8601 with Z on JSON serialization",
            "optional_fields": "present as null when absent",
            "encoding": "UTF-8 with two-space indentation and trailing newline",
        },
    }
    atomic_write_json(paths[0], contract)
    representatives = {
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "generation_utc": generated.isoformat().replace("+00:00", "Z"),
        "examples": examples,
    }
    atomic_write_json(paths[1], representatives)
    handoff = {
        "handoff_version": "phase-5a-presentation-handoff-1.0",
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "canonical_schema_identifier": SCHEMA_IDENTIFIER,
        "presentation_contract_checksum": sha256_file(paths[0]),
        "representative_result_artifact_checksum": sha256_file(paths[1]),
        "preferred_model": {"identifier": MODEL_IDENTIFIER, "checksum": MODEL_CHECKSUM},
        "feature_preprocessing_contract": {
            "checksum": examples[0]["provenance"]["feature_contract_checksum"],
            "description": examples[0]["provenance"]["preprocessing_contract"],
        },
        "evaluation_contract": {
            "version": EVALUATION_VERSION,
            "report_checksum": EVALUATION_REPORT_CHECKSUM,
        },
        "explainability_contract": {
            "version": EXPLAINABILITY_VERSION,
            "report_checksum": EXPLAINABILITY_REPORT_CHECKSUM,
        },
        "threshold": THRESHOLD,
        "supported_input_grain": "make_model_model_year_cohort",
        "supported_output_semantics": (
            "probability of at least one accepted observed NHTSA complaint report in 2023"
        ),
        "required_limitation_language": GENERAL_LIMITATION,
        "prohibited_terminology": list(PROHIBITED_TERMINOLOGY),
        "future_ground_truth_required": False,
        "model_status": MODEL_STATUS,
    }
    atomic_write_json(paths[2], handoff)
    return {
        "presentation_contract_checksum": sha256_file(paths[0]),
        "representative_results_checksum": sha256_file(paths[1]),
        "api_handoff_checksum": sha256_file(paths[2]),
        "representative_count": len(examples),
    }
