"""Phase 5A FastAPI application-boundary tests."""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import howreliable.api.app as api_app
import howreliable.api.service as api_service
from howreliable.api.app import create_app
from howreliable.api.service import (
    API_CONTRACT_VERSION,
    SUPPORTED_COHORT_COUNT,
    ArtifactContractError,
    PredictionService,
    generate_api_contract_artifact,
)
from howreliable.modeling.presentation import (
    ComplaintActivityResult,
)
from howreliable.modeling.registry import (
    ModelRegistry,
    RegistryError,
)
from howreliable.modeling.registry import (
    load_inference_bundle as real_load_inference_bundle,
)

ROOT = Path(".")
FIXED_TIME = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def service() -> PredictionService:
    return PredictionService.load(ROOT)


@pytest.fixture(scope="module")
def client(service: PredictionService) -> TestClient:
    return TestClient(create_app(service=service))


@pytest.fixture(scope="module")
def examples() -> dict[str, dict[str, Any]]:
    payload = json.loads(Path("artifacts/presentation/representative-results.json").read_text())
    return {
        item["evaluation_context"]["selection_rule"]: cast(dict[str, Any], item)
        for item in payload["examples"]
    }


def test_import_has_no_eager_resource_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_registry: ModelRegistry, _bundle_id: str) -> None:
        raise AssertionError("API import attempted heavy resource loading")

    monkeypatch.setattr(api_service, "load_inference_bundle", fail)
    importlib.reload(api_app)


def test_application_factory_and_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "howreliable"}


def test_model_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/model")
    assert response.status_code == 200
    value = response.json()
    assert value["api_contract_version"] == API_CONTRACT_VERSION
    assert value["registry_contract_version"] == "howreliable-model-registry-1.0"
    assert value["bundle_id"] == "howreliable-rf-2022-cutoff-v1"
    assert value["result_contract_version"] == "complaint-activity-result-1.0"
    assert value["model_identifier"] == "phase-3b-random-forest"
    assert value["model_status"] == "PREFERRED"
    assert value["prediction_grain"] == "make_model_model_year_cohort"
    assert value["target"] == "future_12m_complaint_activity"
    assert value["threshold"] == 0.5
    assert value["evaluation_version"] == "howreliable-evaluation-1.0"
    assert value["explainability_version"] == "howreliable-explainability-1.0"
    assert "filesystem" not in json.dumps(value).casefold()


def test_cohort_listing_order_and_default_pagination(client: TestClient) -> None:
    value = client.get("/api/v1/cohorts").json()
    assert value["total"] == SUPPORTED_COHORT_COUNT
    assert value["limit"] == 50 and value["offset"] == 0
    assert len(value["items"]) == 50
    keys = [(x["make"], x["model"], x["model_year"], x["cohort_id"]) for x in value["items"]]
    assert keys == sorted(keys)


def test_cohort_pagination_limit_offset_and_stability(client: TestClient) -> None:
    first = client.get("/api/v1/cohorts", params={"limit": 3, "offset": 2}).json()
    second = client.get("/api/v1/cohorts", params={"limit": 2, "offset": 3}).json()
    repeat = client.get("/api/v1/cohorts", params={"limit": 3, "offset": 2}).json()
    assert first == repeat
    assert first["limit"] == 3 and first["offset"] == 2
    assert first["items"][1:] == second["items"]
    assert len(client.get("/api/v1/cohorts", params={"limit": 100}).json()["items"]) == 100


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_malformed_pagination_returns_422(client: TestClient, params: dict[str, int]) -> None:
    assert client.get("/api/v1/cohorts", params=params).status_code == 422


def test_cohort_retrieval_is_exact_canonical_result(
    client: TestClient, examples: dict[str, dict[str, Any]]
) -> None:
    expected = examples["high_probability_observed_positive"]
    response = client.get(f"/api/v1/cohorts/{expected['identity']['cohort_id']}")
    assert response.status_code == 200
    result = ComplaintActivityResult.model_validate_json(response.text)
    assert (
        result.prediction.predicted_future_complaint_probability
        == expected["prediction"]["predicted_future_complaint_probability"]
    )
    assert (
        result.classification.predicted_classification.value
        == expected["classification"]["predicted_classification"]
    )
    assert result.explanation.reconstructed_probability == pytest.approx(
        result.prediction.predicted_future_complaint_probability, abs=1e-12
    )
    assert "evaluation_context" not in response.json()


@pytest.mark.parametrize(
    ("rule", "required_flags"),
    [
        ("support_1", {"SPARSE_HISTORICAL_SUPPORT"}),
        (
            "age_21_plus",
            {"SPARSE_HISTORICAL_SUPPORT", "OLD_COHORT_WEAK_EVALUATION"},
        ),
    ],
)
def test_prediction_preserves_evaluated_limitations(
    client: TestClient,
    examples: dict[str, dict[str, Any]],
    rule: str,
    required_flags: set[str],
) -> None:
    cohort_id = examples[rule]["identity"]["cohort_id"]
    value = client.get(f"/api/v1/cohorts/{cohort_id}").json()
    assert {item["code"] for item in value["limitation_flags"]} == required_flags


def test_unknown_cohort_has_stable_404_without_fuzzy_fallback(client: TestClient) -> None:
    response = client.get("/api/v1/cohorts/vehicle_association_" + "0" * 64)
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "COHORT_NOT_FOUND",
            "message": "The requested cohort is not supported by the frozen model.",
        }
    }


def test_artifact_contract_failure_prevents_factory_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_registry: ModelRegistry, _bundle_id: str) -> None:
        raise RegistryError("registry failure")

    monkeypatch.setattr(api_service, "load_inference_bundle", fail)
    with pytest.raises(ArtifactContractError, match="artifact contract validation"):
        PredictionService.load(ROOT)
    with pytest.raises(ArtifactContractError):
        create_app(root=ROOT)


def test_resources_remain_identical_across_predictions(
    service: PredictionService, examples: dict[str, dict[str, Any]]
) -> None:
    before = service.resource_identity
    service.predict(
        cast(str, examples["high_probability_observed_positive"]["identity"]["cohort_id"])
    )
    service.predict(cast(str, examples["low_probability_observed_zero"]["identity"]["cohort_id"]))
    assert service.resource_identity == before


def test_factory_loads_resources_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def counted(registry: ModelRegistry, bundle_id: str) -> Any:
        nonlocal calls
        calls += 1
        return real_load_inference_bundle(registry, bundle_id)

    monkeypatch.setattr(api_service, "load_inference_bundle", counted)
    local_client = TestClient(create_app(root=ROOT))
    assert local_client.get("/health").status_code == 200
    assert local_client.get("/api/v1/model").status_code == 200
    assert local_client.get("/api/v1/cohorts", params={"limit": 1}).status_code == 200
    assert calls == 1


def test_openapi_documents_only_the_supported_boundary(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "HowReliable? API"
    assert "complaint activity" in schema["info"]["description"].casefold()
    paths = schema["paths"]
    assert set(paths) == {
        "/health",
        "/api/v1/model",
        "/api/v1/cohorts",
        "/api/v1/cohorts/{cohort_id}",
    }
    assert paths["/api/v1/cohorts/{cohort_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("ComplaintActivityResult")
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_api_surface_has_no_arbitrary_vehicle_or_training_operation(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert all("vehicle" not in path for path in paths)
    assert all("train" not in path and "tune" not in path for path in paths)
    source = Path("src/howreliable/api/service.py").read_text().casefold()
    assert ".fit(" not in source and "gridsearch" not in source


def test_openapi_user_facing_terminology_is_safe(client: TestClient) -> None:
    text = json.dumps(client.get("/openapi.json").json()).casefold()
    prohibited = (
        "reliability score",
        "repair risk",
        "failure risk",
        "safety score",
        "risk level",
        "high risk",
        "low risk",
        "confidence score",
        "likely to fail",
    )
    assert not [term for term in prohibited if term in text]


def test_api_contract_artifact_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    left = generate_api_contract_artifact(ROOT, first, clock=lambda: FIXED_TIME)
    right = generate_api_contract_artifact(ROOT, second, clock=lambda: FIXED_TIME)
    assert left["api_contract_checksum"] == right["api_contract_checksum"]
    assert first.read_bytes() == second.read_bytes()
    value = json.loads(first.read_text())
    assert value["supported_cohort_count"] == SUPPORTED_COHORT_COUNT
    assert value["presentation_handoff_checksum"] == api_service.API_HANDOFF_CHECKSUM
