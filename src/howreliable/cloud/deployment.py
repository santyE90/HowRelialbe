"""Deterministic Phase 6B ECS Fargate deployment contracts and static validation."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, field_validator

from howreliable.cloud.s3 import S3_CONTRACT_VERSION
from howreliable.config.settings import DEFAULT_MODEL_BUNDLE_ID
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.presentation import RESULT_CONTRACT_VERSION
from howreliable.modeling.registry import API_COMPATIBILITY_VERSION, REGISTRY_CONTRACT_VERSION

DEPLOYMENT_CONTRACT_VERSION: Final = "howreliable-deployment-1.0"
COMPUTE_PLATFORM: Final = "ECS_FARGATE"
CONTAINER_PORT: Final = 8000
HEALTH_PATH: Final = "/health"
TASK_CPU: Final = 512
TASK_MEMORY_MIB: Final = 1024
DESIRED_COUNT: Final = 1
CANONICAL_IMAGE_TAG: Final = "1.0.0"
S3_CONTRACT_SHA256: Final = "8f55b5164ef35c3a7758c4867af784b6ea27d4e3a4e28bbf7b779948b1e0163a"
REQUIRED_TASK_ROLE_ACTIONS: Final = ("s3:GetObject", "s3:ListBucket")
REQUIRED_ENVIRONMENT_VARIABLES: Final = (
    "HOWRELIABLE_ARTIFACT_BACKEND",
    "HOWRELIABLE_S3_BUCKET",
    "HOWRELIABLE_S3_PREFIX",
    "HOWRELIABLE_AWS_REGION",
    "HOWRELIABLE_MODEL_BUNDLE_ID",
)
AWS_CREDENTIAL_ENVIRONMENT_VARIABLES: Final = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)


class DeploymentValidationError(ValueError):
    """A deployment definition violates the frozen Phase 6B contract."""


class DeploymentContract(BaseModel):
    """Strict machine-readable Phase 6B contract schema."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    deployment_contract_version: str
    api_version: str
    result_contract_version: str
    s3_contract_version: str
    s3_contract_sha256: str
    registry_version: str
    canonical_bundle_id: str
    compute_platform: str
    container_port: int
    health_path: str
    image_contract: dict[str, object]
    artifact_backend: str
    required_environment_variables: tuple[str, ...]
    required_task_role_actions: tuple[str, ...]
    role_distinction: dict[str, str]
    task_cpu_units: int
    task_memory_mib: int
    desired_count: int
    network_exposure: str
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


def deployment_contract(*, generation_utc: datetime) -> DeploymentContract:
    """Build the frozen Phase 6B contract for an explicit generation instant."""
    return DeploymentContract(
        deployment_contract_version=DEPLOYMENT_CONTRACT_VERSION,
        api_version=API_COMPATIBILITY_VERSION,
        result_contract_version=RESULT_CONTRACT_VERSION,
        s3_contract_version=S3_CONTRACT_VERSION,
        s3_contract_sha256=S3_CONTRACT_SHA256,
        registry_version=REGISTRY_CONTRACT_VERSION,
        canonical_bundle_id=DEFAULT_MODEL_BUNDLE_ID,
        compute_platform=COMPUTE_PLATFORM,
        container_port=CONTAINER_PORT,
        health_path=HEALTH_PATH,
        image_contract={
            "ecr_repository": "howreliable-api",
            "explicit_tag": CANONICAL_IMAGE_TAG,
            "latest_permitted_for_deployment": False,
            "tag_strategy": "semantic phase tag or immutable git SHA",
        },
        artifact_backend="s3",
        required_environment_variables=REQUIRED_ENVIRONMENT_VARIABLES,
        required_task_role_actions=REQUIRED_TASK_ROLE_ACTIONS,
        role_distinction={
            "execution_role": "pull ECR image and support ECS platform operations",
            "task_role": "application access to the private S3 bundle only",
        },
        task_cpu_units=TASK_CPU,
        task_memory_mib=TASK_MEMORY_MIB,
        desired_count=DESIRED_COUNT,
        network_exposure="APPLICATION_LOAD_BALANCER",
        generation_utc=generation_utc.astimezone(UTC),
    )


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def generate_deployment_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DeploymentContract:
    """Persist a deterministic deployment contract without credentials or account data."""
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite deployment contract: {output}")
    contract = deployment_contract(generation_utc=clock())
    _atomic_write_json(output, contract.model_dump(mode="json"))
    return contract


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentValidationError("deployment definition is not valid JSON") from error
    if not isinstance(value, dict):
        raise DeploymentValidationError("deployment definition must be a JSON object")
    return cast(dict[str, Any], value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentValidationError(message)


def validate_ecs_task_definition(path: Path) -> dict[str, Any]:
    """Validate the deployable task template against the Phase 6B runtime boundary."""
    value = _load_object(path)
    _require(value.get("family") == "howreliable-api", "task family mismatch")
    _require(value.get("networkMode") == "awsvpc", "task must use awsvpc")
    _require(value.get("requiresCompatibilities") == ["FARGATE"], "task must use Fargate")
    _require(value.get("cpu") == str(TASK_CPU), "task CPU mismatch")
    _require(value.get("memory") == str(TASK_MEMORY_MIB), "task memory mismatch")
    task_role = value.get("taskRoleArn")
    execution_role = value.get("executionRoleArn")
    _require(bool(task_role) and bool(execution_role), "task and execution roles are required")
    _require(task_role != execution_role, "task and execution roles must be distinct")
    containers = cast(list[dict[str, Any]], value.get("containerDefinitions"))
    _require(isinstance(containers, list) and len(containers) == 1, "one container is required")
    container = containers[0]
    image = str(container.get("image", ""))
    _require(":" in image and not image.endswith(":latest"), "explicit image tag is required")
    ports = cast(list[Mapping[str, object]], container.get("portMappings", []))
    _require(
        len(ports) == 1 and ports[0].get("containerPort") == CONTAINER_PORT,
        "container port mismatch",
    )
    environment = cast(list[Mapping[str, object]], container.get("environment", []))
    environment_values = {str(item.get("name")): item.get("value") for item in environment}
    _require(
        set(REQUIRED_ENVIRONMENT_VARIABLES) <= environment_values.keys(),
        "required environment variable is missing",
    )
    _require(
        not AWS_CREDENTIAL_ENVIRONMENT_VARIABLES & environment_values.keys(),
        "AWS credentials must not be in the task definition",
    )
    _require(
        environment_values["HOWRELIABLE_ARTIFACT_BACKEND"] == "s3",
        "AWS task must select S3",
    )
    _require(
        environment_values["HOWRELIABLE_MODEL_BUNDLE_ID"] == DEFAULT_MODEL_BUNDLE_ID,
        "canonical bundle ID mismatch",
    )
    health = cast(Mapping[str, object], container.get("healthCheck", {}))
    health_command = " ".join(cast(list[str], health.get("command", [])))
    _require(HEALTH_PATH in health_command, "health path mismatch")
    return value


def validate_ecs_service_definition(path: Path) -> dict[str, Any]:
    """Validate the minimal one-task ALB-backed Fargate service template."""
    value = _load_object(path)
    _require(value.get("launchType") == "FARGATE", "service must use Fargate")
    _require(value.get("desiredCount") == DESIRED_COUNT, "desired count mismatch")
    _require("autoScaling" not in value, "autoscaling is outside Phase 6B")
    task_definition = str(value.get("taskDefinition", ""))
    _require(":" in task_definition, "explicit task-definition revision is required")
    load_balancers = cast(list[Mapping[str, object]], value.get("loadBalancers", []))
    _require(len(load_balancers) == 1, "one ALB target group is required")
    _require(
        load_balancers[0].get("containerPort") == CONTAINER_PORT,
        "service container port mismatch",
    )
    network = cast(Mapping[str, object], value.get("networkConfiguration", {}))
    _require("awsvpcConfiguration" in network, "service must define awsvpc networking")
    return value
