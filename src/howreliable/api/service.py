"""Validated, read-only service boundary for frozen cohort inference."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from howreliable.api.schemas import CohortPage, CohortSummary, ModelMetadataResponse
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.modeling.evaluation_report import EVALUATION_VERSION
from howreliable.modeling.explainability import EXPLAINABILITY_VERSION
from howreliable.modeling.presentation import (
    EVALUATION_REPORT_CHECKSUM,
    EXPLAINABILITY_REPORT_CHECKSUM,
    GENERAL_LIMITATION,
    MODEL_CHECKSUM,
    MODEL_IDENTIFIER,
    PROHIBITED_TERMINOLOGY,
    RESULT_CONTRACT_VERSION,
    THRESHOLD,
    ComplaintActivityResult,
    build_complaint_activity_result,
)
from howreliable.modeling.pytorch.training_infrastructure import atomic_write_json
from howreliable.modeling.registry import (
    API_COMPATIBILITY_VERSION,
    DEFAULT_BUNDLE_ID,
    PRESENTATION_HANDOFF_CHECKSUM,
    REGISTRY_CONTRACT_VERSION,
    InferenceBundle,
    LocalArtifactStore,
    ModelRegistry,
    RegistryError,
    load_inference_bundle,
)

API_CONTRACT_VERSION: Final = API_COMPATIBILITY_VERSION
API_HANDOFF_CHECKSUM: Final = PRESENTATION_HANDOFF_CHECKSUM
SUPPORTED_COHORT_COUNT: Final = 8_416


class ArtifactContractError(RuntimeError):
    """Raised when required local artifacts do not satisfy the frozen contract."""


class CohortNotFoundError(LookupError):
    """Raised for an ID outside the frozen supported cohort index."""


class PredictionService:
    """Application-lifetime index and resource holder with read-only request operations."""

    def __init__(
        self,
        root: Path,
        bundle: InferenceBundle,
    ) -> None:
        self._root = root
        self._bundle = bundle
        self._resources = bundle.resources
        self._api_handoff = bundle.presentation_handoff
        self._cohorts = tuple(
            CohortSummary(
                cohort_id=identifier,
                make=cast(str, row["normalized_make"]),
                model=cast(str, row["normalized_model"]),
                model_year=cast(int, row["model_year"]),
            )
            for identifier, row in sorted(
                self._resources.rows_by_id.items(),
                key=lambda item: (
                    item[1]["normalized_make"],
                    item[1]["normalized_model"],
                    item[1]["model_year"],
                    item[0],
                ),
            )
        )
        if len(self._cohorts) != SUPPORTED_COHORT_COUNT:
            raise ArtifactContractError("supported cohort count does not match the API contract")

    @classmethod
    def load(cls, root: Path, *, bundle_id: str = DEFAULT_BUNDLE_ID) -> PredictionService:
        """Fail fast while loading one explicit validated inference bundle."""
        try:
            bundle = load_inference_bundle(ModelRegistry(LocalArtifactStore(root)), bundle_id)
        except RegistryError as error:
            raise ArtifactContractError(
                "required frozen artifact contract validation failed"
            ) from error
        return cls(root, bundle)

    @property
    def resource_identity(self) -> tuple[int, int, int]:
        """Expose stable object identities for lifecycle verification without payload access."""
        return (
            id(self._resources.model),
            id(self._resources.rows_by_id),
            id(self._resources.manifest),
        )

    def metadata(self) -> ModelMetadataResponse:
        return ModelMetadataResponse(
            api_contract_version=API_CONTRACT_VERSION,
            registry_contract_version=REGISTRY_CONTRACT_VERSION,
            bundle_id=self._bundle.manifest.bundle_id,
            result_contract_version=RESULT_CONTRACT_VERSION,
            model_identifier=MODEL_IDENTIFIER,
            model_status="PREFERRED",
            prediction_grain="make_model_model_year_cohort",
            target="future_12m_complaint_activity",
            threshold=THRESHOLD,
            evaluation_version=EVALUATION_VERSION,
            explainability_version=EXPLAINABILITY_VERSION,
            supported_semantics=cast(str, self._api_handoff["supported_output_semantics"]),
            general_limitation=GENERAL_LIMITATION,
        )

    def list_cohorts(self, *, limit: int, offset: int) -> CohortPage:
        return CohortPage(
            items=self._cohorts[offset : offset + limit],
            total=len(self._cohorts),
            limit=limit,
            offset=offset,
        )

    def predict(self, cohort_id: str) -> ComplaintActivityResult:
        if cohort_id not in self._resources.rows_by_id:
            raise CohortNotFoundError(cohort_id)
        return build_complaint_activity_result(
            self._root,
            cohort_id,
            resources=self._resources,
        )


def generate_api_contract_artifact(
    root: Path,
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Persist the deterministic Phase 5A machine-readable API contract."""
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite API contract: {output}")
    PredictionService.load(root)
    generated = clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
    contract = {
        "api_contract_version": API_CONTRACT_VERSION,
        "routes": [
            {"method": "GET", "path": "/health"},
            {"method": "GET", "path": "/api/v1/model"},
            {"method": "GET", "path": "/api/v1/cohorts"},
            {"method": "GET", "path": "/api/v1/cohorts/{cohort_id}"},
        ],
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "preferred_model": {"identifier": MODEL_IDENTIFIER, "checksum": MODEL_CHECKSUM},
        "supported_prediction_grain": "make_model_model_year_cohort",
        "supported_cohort_count": SUPPORTED_COHORT_COUNT,
        "target": "future_12m_complaint_activity",
        "threshold": THRESHOLD,
        "evaluation": {
            "version": EVALUATION_VERSION,
            "report_checksum": EVALUATION_REPORT_CHECKSUM,
        },
        "explainability": {
            "version": EXPLAINABILITY_VERSION,
            "report_checksum": EXPLAINABILITY_REPORT_CHECKSUM,
        },
        "presentation_handoff_checksum": API_HANDOFF_CHECKSUM,
        "required_limitation_semantics": GENERAL_LIMITATION,
        "prohibited_terminology": list(PROHIBITED_TERMINOLOGY),
        "generation_utc": generated,
    }
    atomic_write_json(output, contract)
    return {"api_contract_checksum": sha256_file(output), **contract}
