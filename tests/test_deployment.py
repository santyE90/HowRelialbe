"""Phase 6B deterministic container and ECS Fargate deployment-contract tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from howreliable.api.app import create_app
from howreliable.api.service import PredictionService
from howreliable.cloud.deployment import (
    CANONICAL_IMAGE_TAG,
    COMPUTE_PLATFORM,
    CONTAINER_PORT,
    DEPLOYMENT_CONTRACT_VERSION,
    DESIRED_COUNT,
    HEALTH_PATH,
    REQUIRED_ENVIRONMENT_VARIABLES,
    REQUIRED_TASK_ROLE_ACTIONS,
    TASK_CPU,
    TASK_MEMORY_MIB,
    DeploymentContract,
    generate_deployment_contract_artifact,
    validate_ecs_service_definition,
    validate_ecs_task_definition,
)
from howreliable.modeling.presentation import MODEL_CHECKSUM
from howreliable.modeling.registry import DEFAULT_BUNDLE_ID

ROOT = Path(".")
TASK_DEFINITION = ROOT / "infrastructure/aws/ecs-task-definition.json"
SERVICE_DEFINITION = ROOT / "infrastructure/aws/ecs-service.json"
TASK_POLICY = ROOT / "infrastructure/iam/howreliable-s3-reader-policy.json"
CONTRACT_ARTIFACT = ROOT / "artifacts/cloud/deployment-contract.json"
FIXED_TIME = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_deployment_contract_schema_and_versions() -> None:
    contract = DeploymentContract.model_validate_json(CONTRACT_ARTIFACT.read_bytes())
    assert contract.deployment_contract_version == DEPLOYMENT_CONTRACT_VERSION
    assert contract.compute_platform == COMPUTE_PLATFORM == "ECS_FARGATE"
    assert contract.api_version == "howreliable-api-1.0"
    assert contract.result_contract_version == "complaint-activity-result-1.0"
    assert contract.s3_contract_version == "howreliable-s3-1.0"
    assert contract.registry_version == "howreliable-model-registry-1.0"
    assert contract.canonical_bundle_id == DEFAULT_BUNDLE_ID
    assert contract.artifact_backend == "s3"
    assert contract.container_port == CONTAINER_PORT == 8000
    assert contract.health_path == HEALTH_PATH == "/health"
    assert contract.task_cpu_units == TASK_CPU == 512
    assert contract.task_memory_mib == TASK_MEMORY_MIB == 1024
    assert contract.desired_count == DESIRED_COUNT == 1
    assert contract.required_environment_variables == REQUIRED_ENVIRONMENT_VARIABLES
    assert contract.required_task_role_actions == REQUIRED_TASK_ROLE_ACTIONS


def test_deployment_contract_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_deployment_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_deployment_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes() == CONTRACT_ARTIFACT.read_bytes()


def test_task_definition_is_valid_fargate_contract() -> None:
    task = validate_ecs_task_definition(TASK_DEFINITION)
    assert task["requiresCompatibilities"] == ["FARGATE"]
    assert task["networkMode"] == "awsvpc"
    assert task["cpu"] == "512" and task["memory"] == "1024"
    assert task["taskRoleArn"] != task["executionRoleArn"]
    container = task["containerDefinitions"][0]
    assert container["portMappings"][0]["containerPort"] == 8000
    assert "/health" in container["healthCheck"]["command"][1]
    assert container["readonlyRootFilesystem"] is True


def test_task_definition_uses_explicit_image_tag_and_required_environment() -> None:
    task = json.loads(TASK_DEFINITION.read_text())
    container = task["containerDefinitions"][0]
    assert container["image"].endswith(f":{CANONICAL_IMAGE_TAG}")
    assert not container["image"].endswith(":latest")
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert set(REQUIRED_ENVIRONMENT_VARIABLES) <= environment.keys()
    assert environment["HOWRELIABLE_ARTIFACT_BACKEND"] == "s3"
    assert environment["HOWRELIABLE_MODEL_BUNDLE_ID"] == DEFAULT_BUNDLE_ID
    assert (
        not {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        }
        & environment.keys()
    )


def test_service_is_one_task_alb_backed_fargate_without_autoscaling() -> None:
    service = validate_ecs_service_definition(SERVICE_DEFINITION)
    assert service["launchType"] == "FARGATE"
    assert service["desiredCount"] == 1
    assert service["loadBalancers"][0]["containerPort"] == 8000
    assert service["taskDefinition"].startswith("howreliable-api:")
    assert "autoScaling" not in service


def test_runtime_task_policy_is_exact_least_privilege_s3_reader() -> None:
    policy = json.loads(TASK_POLICY.read_text())
    actions = {statement["Action"] for statement in policy["Statement"]}
    assert actions == set(REQUIRED_TASK_ROLE_ACTIONS)
    assert not actions & {"s3:*", "s3:PutObject", "s3:DeleteObject"}
    list_statement = next(item for item in policy["Statement"] if item["Action"] == "s3:ListBucket")
    assert "s3:prefix" in list_statement["Condition"]["StringLike"]


def test_dockerfile_is_pinned_non_root_factory_image_without_bundle_copy() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    expected_base = (
        "python:3.12-slim-bookworm@"
        "sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a"
    )
    assert f"ARG PYTHON_BASE={expected_base}" in dockerfile
    assert dockerfile.count("FROM ${PYTHON_BASE}") == 2
    assert dockerfile.count("@sha256:") == 1
    assert "USER 10001:10001" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "howreliable.api.app:create_app" in dockerfile
    assert '"--factory"' in dockerfile and '"--port", "8000"' in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile
    assert "COPY src ./src" in dockerfile
    assert "COPY artifacts" not in dockerfile
    assert "COPY data" not in dockerfile
    assert "AWS_ACCESS_KEY" not in dockerfile
    assert ".[dev]" not in dockerfile


@pytest.mark.parametrize(
    "entry",
    [
        ".git",
        ".venv",
        ".env",
        ".aws",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "data",
        "artifacts",
        "notebooks",
        "tests",
    ],
)
def test_dockerignore_excludes_sensitive_heavy_and_development_content(entry: str) -> None:
    assert entry in (ROOT / ".dockerignore").read_text().splitlines()


def test_deployment_definitions_contain_no_credentials_or_personal_paths() -> None:
    values = "\n".join(
        path.read_text()
        for path in [ROOT / "Dockerfile", TASK_DEFINITION, SERVICE_DEFINITION, CONTRACT_ARTIFACT]
    )
    folded = values.casefold()
    assert "secret_access_key" not in folded
    assert "session_token" not in folded
    assert "c:\\users" not in folded
    assert "-----begin" not in folded


def test_deployment_code_invokes_no_terraform_or_forbidden_compute_platform() -> None:
    deployment_files = [
        ROOT / "Dockerfile",
        ROOT / "src/howreliable/cloud/deployment.py",
        TASK_DEFINITION,
        SERVICE_DEFINITION,
    ]
    values = "\n".join(path.read_text().casefold() for path in deployment_files)
    assert "terraform" not in values
    assert "terraform" not in values
    assert "eks" not in values
    assert "aws_lambda" not in values and "lambda_function" not in values
    assert "ec2" not in values
    assert "train(" not in values and "fit(" not in values


def test_api_routes_and_frozen_result_are_unchanged() -> None:
    service = PredictionService.load(ROOT)
    client = TestClient(create_app(service=service))
    assert client.get("/health").json() == {"status": "ok", "service": "howreliable"}
    metadata = client.get("/api/v1/model").json()
    assert metadata["api_contract_version"] == "howreliable-api-1.0"
    assert metadata["bundle_id"] == DEFAULT_BUNDLE_ID
    assert client.get("/api/v1/cohorts").json()["total"] == 8_416

    representative = json.loads(
        (ROOT / "artifacts/presentation/representative-results.json").read_text()
    )["examples"][0]
    cohort_id = cast(str, representative["identity"]["cohort_id"])
    result = client.get(f"/api/v1/cohorts/{cohort_id}").json()
    assert (
        result["prediction"]["predicted_future_complaint_probability"]
        == representative["prediction"]["predicted_future_complaint_probability"]
    )
    assert result["explanation"]["reconstruction_absolute_error"] < 1e-12
    assert result["limitation_flags"] == representative["limitation_flags"]
    assert result["provenance"]["model_checksum"] == MODEL_CHECKSUM


def test_templates_are_json_objects_without_unresolved_secret_values() -> None:
    for path in [TASK_DEFINITION, SERVICE_DEFINITION, TASK_POLICY]:
        value = json.loads(path.read_text())
        assert isinstance(cast(Any, value), dict)
