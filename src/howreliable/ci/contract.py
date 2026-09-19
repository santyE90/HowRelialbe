"""Deterministic Phase 7A CI contract generation and structural validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from howreliable.cloud.deployment import (
    DEPLOYMENT_CONTRACT_VERSION,
    generate_deployment_contract_artifact,
    validate_ecs_service_definition,
    validate_ecs_task_definition,
)
from howreliable.cloud.monitoring import (
    MONITORING_CONTRACT_VERSION,
    generate_monitoring_contract_artifact,
    validate_alarm_specification,
    validate_log_group_specification,
    validate_task_log_configuration,
)
from howreliable.cloud.s3 import S3_CONTRACT_VERSION, generate_s3_contract_artifact
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.registry import (
    API_COMPATIBILITY_VERSION,
    DEFAULT_BUNDLE_ID,
    REGISTRY_CONTRACT_VERSION,
    LocalArtifactStore,
    ModelRegistry,
    load_inference_bundle,
)

CI_CONTRACT_VERSION: Final = "howreliable-ci-1.0"
PYTHON_VERSION: Final = "3.12.11"
WORKFLOW_PATH: Final = Path(".github/workflows/ci.yml")
TRIGGERS: Final = ("pull_request", "push", "workflow_dispatch")
JOBS: Final = ("quality", "tests", "contracts", "docker")
QUALITY_COMMANDS: Final = (
    "python -m pip install -e .[dev]",
    "python -m ruff check .",
    "python -m mypy",
    "python -m pip check",
    'python -c "import howreliable; print(howreliable.__version__)"',
    "python -m pip wheel --no-deps --wheel-dir dist .",
)
TEST_COMMAND: Final = "python -m pytest"
CONTRACT_VALIDATION_COMMAND: Final = "python -m howreliable.ci.contract validate --root ."
REGISTRY_MANIFEST_SHA256: Final = "831ecaa2cf31102bb13d2e414a380465cb948b07c45f9a4acb225e40aa2159cc"
S3_CONTRACT_SHA256: Final = "8f55b5164ef35c3a7758c4867af784b6ea27d4e3a4e28bbf7b779948b1e0163a"
DEPLOYMENT_CONTRACT_SHA256: Final = (
    "c4e6806743421eb1e0feebf27522aec5caa1e9fc87675daaee18c1e5b30b71e4"
)
MONITORING_CONTRACT_SHA256: Final = (
    "f7ea11a809cc1945f7dbfd2156c23e4127c1734ef3780e42eca9066b987325c0"
)
FORBIDDEN_WORKFLOW_TERMS: Final = (
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_role_arn",
    "id-token: write",
    "packages: write",
    "deployments: write",
    "ecr get-login-password",
    "docker push",
    "aws ecs",
    "aws s3",
)


class CIValidationError(ValueError):
    """The workflow or deterministic CI contract violates the Phase 7A boundary."""


class CIContract(BaseModel):
    """Strict machine-readable Phase 7A quality-gate contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    ci_contract_version: str
    python_version: str
    trigger_categories: tuple[str, ...]
    permissions: dict[str, str]
    jobs: tuple[str, ...]
    quality_commands: tuple[str, ...]
    test_command: str
    contract_validation_command: str
    docker_build_required: bool
    docker_push: bool
    aws_credentials_required: bool
    live_aws_access: bool
    deployment_performed: bool
    clean_checkout_test_policy: str
    scientific_regression_categories: tuple[str, ...]
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


def ci_contract(*, generation_utc: datetime) -> CIContract:
    return CIContract(
        ci_contract_version=CI_CONTRACT_VERSION,
        python_version=PYTHON_VERSION,
        trigger_categories=TRIGGERS,
        permissions={"contents": "read"},
        jobs=JOBS,
        quality_commands=QUALITY_COMMANDS,
        test_command=TEST_COMMAND,
        contract_validation_command=CONTRACT_VALIDATION_COMMAND,
        docker_build_required=True,
        docker_push=False,
        aws_credentials_required=False,
        live_aws_access=False,
        deployment_performed=False,
        clean_checkout_test_policy=(
            "repository-contained tests run on every change; tests marked full_artifacts run "
            "when the ignored canonical bundle is provisioned"
        ),
        scientific_regression_categories=(
            "API surface and error behavior",
            "frozen probability and classification",
            "explanation reconstruction",
            "limitation preservation",
            "structured monitoring",
        ),
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


def generate_ci_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CIContract:
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite CI contract: {output}")
    contract = ci_contract(generation_utc=clock())
    _atomic_write_json(output, contract.model_dump(mode="json"))
    return contract


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CIValidationError(message)


def validate_workflow(path: Path) -> dict[str, Any]:
    """Parse and enforce the minimal, non-deploying GitHub Actions workflow."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CIValidationError("CI workflow is not valid YAML") from error
    _require(isinstance(value, dict), "CI workflow must be a mapping")
    workflow = cast(dict[str, Any], value)
    triggers = workflow.get("on")
    _require(isinstance(triggers, dict), "CI triggers must be explicit")
    _require(set(cast(dict[str, Any], triggers)) == set(TRIGGERS), "CI trigger mismatch")
    _require(workflow.get("permissions") == {"contents": "read"}, "CI permissions mismatch")
    jobs = workflow.get("jobs")
    _require(isinstance(jobs, dict), "CI jobs must be a mapping")
    _require(set(cast(dict[str, Any], jobs)) == set(JOBS), "CI job mismatch")
    source = path.read_text(encoding="utf-8").casefold()
    _require(not any(term in source for term in FORBIDDEN_WORKFLOW_TERMS), "deployment term in CI")
    _require("python -m pytest" in source, "full pytest command missing")
    _require("docker build" in source, "Docker build missing")
    _require("docker push" not in source and ":latest" not in source, "unsafe Docker behavior")
    return workflow


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_repository_contracts(root: Path) -> dict[str, object]:
    """Validate all clean-checkout contracts without credentials or network access."""
    validate_workflow(root / WORKFLOW_PATH)
    validate_ecs_task_definition(root / "infrastructure/aws/ecs-task-definition.json")
    validate_ecs_service_definition(root / "infrastructure/aws/ecs-service.json")
    validate_task_log_configuration(root / "infrastructure/aws/ecs-task-definition.json")
    validate_alarm_specification(root / "infrastructure/aws/monitoring/cloudwatch-alarms.json")
    validate_log_group_specification(
        root / "infrastructure/aws/monitoring/cloudwatch-log-group.json"
    )
    for path in (
        root / "infrastructure/aws/ecs-tasks-trust-policy.json",
        root / "infrastructure/iam/howreliable-s3-reader-policy.json",
    ):
        _require(isinstance(json.loads(path.read_text(encoding="utf-8")), dict), f"invalid {path}")

    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        s3_path = temporary / "s3.json"
        deployment_path = temporary / "deployment.json"
        monitoring_path = temporary / "monitoring.json"
        generate_s3_contract_artifact(
            s3_path, clock=lambda: datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
        )
        generate_deployment_contract_artifact(
            deployment_path, clock=lambda: datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
        )
        generate_monitoring_contract_artifact(
            monitoring_path, clock=lambda: datetime(2026, 9, 19, 18, 0, tzinfo=UTC)
        )
        _require(_sha256(s3_path) == S3_CONTRACT_SHA256, "S3 contract checksum mismatch")
        _require(
            _sha256(deployment_path) == DEPLOYMENT_CONTRACT_SHA256,
            "deployment contract checksum mismatch",
        )
        _require(
            _sha256(monitoring_path) == MONITORING_CONTRACT_SHA256,
            "monitoring contract checksum mismatch",
        )

    _require(REGISTRY_CONTRACT_VERSION == "howreliable-model-registry-1.0", "registry version")
    _require(API_COMPATIBILITY_VERSION == "howreliable-api-1.0", "API version")
    _require(S3_CONTRACT_VERSION == "howreliable-s3-1.0", "S3 version")
    _require(DEPLOYMENT_CONTRACT_VERSION == "howreliable-deployment-1.0", "deployment version")
    _require(MONITORING_CONTRACT_VERSION == "howreliable-monitoring-1.0", "monitoring version")
    manifest = root / "artifacts/registry/bundles/howreliable-rf-2022-cutoff-v1/manifest.json"
    full_artifact_status = "not provisioned in clean checkout"
    if manifest.is_file():
        _require(_sha256(manifest) == REGISTRY_MANIFEST_SHA256, "registry checksum mismatch")
        load_inference_bundle(ModelRegistry(LocalArtifactStore(root)), DEFAULT_BUNDLE_ID)
        full_artifact_status = "canonical registry, API, and inference bundle validated"
    return {
        "clean_checkout_contracts": "passed",
        "full_artifact_status": full_artifact_status,
    }


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("generation timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase 7A CI contract")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--generation-utc", type=_parse_utc, required=True)
    generate.add_argument("--regenerate", action="store_true")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.operation == "generate":
        generate_ci_contract_artifact(
            args.output,
            regenerate=args.regenerate,
            clock=lambda: cast(datetime, args.generation_utc),
        )
        return 0
    print(json.dumps(validate_repository_contracts(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
