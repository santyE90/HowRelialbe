"""Deterministic Phase 7B CD contracts and offline structural validation."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from howreliable.ci.contract import CI_CONTRACT_VERSION
from howreliable.cloud.deployment import (
    DEPLOYMENT_CONTRACT_VERSION,
    validate_ecs_task_definition,
)
from howreliable.cloud.monitoring import MONITORING_CONTRACT_VERSION
from howreliable.cloud.s3 import S3_CONTRACT_VERSION
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.presentation import RESULT_CONTRACT_VERSION
from howreliable.modeling.registry import (
    API_COMPATIBILITY_VERSION,
    DEFAULT_BUNDLE_ID,
    REGISTRY_CONTRACT_VERSION,
)

CD_CONTRACT_VERSION: Final = "howreliable-cd-1.0"
CI_CONTRACT_SHA256: Final = "ba3e92cb34850b6114a943934435d6757330b246cd815a0b5ce2def2648e7ced"
DEPLOYMENT_CONTRACT_SHA256: Final = (
    "c4e6806743421eb1e0feebf27522aec5caa1e9fc87675daaee18c1e5b30b71e4"
)
MONITORING_CONTRACT_SHA256: Final = (
    "f7ea11a809cc1945f7dbfd2156c23e4127c1734ef3780e42eca9066b987325c0"
)
WORKFLOW_PATH: Final = Path(".github/workflows/deploy.yml")
TRUST_POLICY_PATH: Final = Path("infrastructure/iam/github-deploy-trust-policy.json")
DEPLOYMENT_POLICY_PATH: Final = Path("infrastructure/iam/github-deploy-policy.json")
EXPECTED_CONTAINER_NAME: Final = "howreliable-api"
EXPECTED_COHORT_COUNT: Final = 8_416
SHA_TAG_RE: Final = re.compile(r"^[0-9a-f]{40}$")


class CDValidationError(ValueError):
    """A CD definition violates the Phase 7B deployment boundary."""


class CDContract(BaseModel):
    """Strict machine-readable Phase 7B deployment-automation contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    cd_contract_version: str
    ci_contract_version: str
    ci_contract_sha256: str
    deployment_contract_version: str
    deployment_contract_sha256: str
    monitoring_contract_version: str
    monitoring_contract_sha256: str
    registry_version: str
    s3_version: str
    api_version: str
    result_version: str
    canonical_bundle_id: str
    github_workflow_path: str
    authentication: str
    target_platform: str
    image_registry: str
    image_tag_strategy: str
    deployment_environment: str
    concurrency: dict[str, object]
    smoke_requirements: tuple[str, ...]
    rollback_behavior: str
    artifact_publication: bool
    infrastructure_creation: bool
    terraform: bool
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


def cd_contract(*, generation_utc: datetime) -> CDContract:
    return CDContract(
        cd_contract_version=CD_CONTRACT_VERSION,
        ci_contract_version=CI_CONTRACT_VERSION,
        ci_contract_sha256=CI_CONTRACT_SHA256,
        deployment_contract_version=DEPLOYMENT_CONTRACT_VERSION,
        deployment_contract_sha256=DEPLOYMENT_CONTRACT_SHA256,
        monitoring_contract_version=MONITORING_CONTRACT_VERSION,
        monitoring_contract_sha256=MONITORING_CONTRACT_SHA256,
        registry_version=REGISTRY_CONTRACT_VERSION,
        s3_version=S3_CONTRACT_VERSION,
        api_version=API_COMPATIBILITY_VERSION,
        result_version=RESULT_CONTRACT_VERSION,
        canonical_bundle_id=DEFAULT_BUNDLE_ID,
        github_workflow_path=WORKFLOW_PATH.as_posix(),
        authentication="GitHub OIDC",
        target_platform="ECS Fargate",
        image_registry="Amazon ECR",
        image_tag_strategy="sha-<full 40-character Git commit SHA>",
        deployment_environment="production",
        concurrency={"group": "howreliable-production", "cancel_in_progress": False},
        smoke_requirements=(
            "GET /health returns status=ok and service=howreliable",
            "GET /api/v1/model returns frozen API, registry, and bundle versions",
            "GET /api/v1/cohorts?limit=1 returns total=8416",
        ),
        rollback_behavior=(
            "after service update, any stability or smoke failure restores the previous task "
            "definition, waits for rollback stability, and preserves workflow failure"
        ),
        artifact_publication=False,
        infrastructure_creation=False,
        terraform=False,
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


def generate_cd_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CDContract:
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite CD contract: {output}")
    contract = cd_contract(generation_utc=clock())
    _atomic_write_json(output, contract.model_dump(mode="json"))
    return contract


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CDValidationError(message)


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CDValidationError(f"invalid JSON: {path}") from error
    _require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return cast(dict[str, Any], value)


def validate_deploy_workflow(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CDValidationError("deployment workflow is not valid YAML") from error
    _require(isinstance(value, dict), "deployment workflow must be a mapping")
    workflow = cast(dict[str, Any], value)
    triggers = workflow.get("on")
    _require(
        isinstance(triggers, dict) and set(cast(dict[str, Any], triggers)) == {"workflow_dispatch"},
        "deployment must be manual-only",
    )
    _require(workflow.get("permissions") == {"contents": "read"}, "top-level permissions")
    concurrency = cast(Mapping[str, object], workflow.get("concurrency", {}))
    _require(concurrency.get("group") == "howreliable-production", "production concurrency")
    _require(concurrency.get("cancel-in-progress") is False, "deployment cancellation policy")
    jobs = cast(dict[str, Any], workflow.get("jobs"))
    _require(isinstance(jobs, dict) and set(jobs) == {"validate", "deploy"}, "job boundary")
    deploy = cast(dict[str, Any], jobs["deploy"])
    _require(deploy.get("environment") == "production", "production environment is required")
    _require(deploy.get("needs") == "validate", "deployment must depend on validation")
    _require(
        deploy.get("permissions") == {"contents": "read", "id-token": "write"},
        "OIDC permissions mismatch",
    )
    source = path.read_text(encoding="utf-8").casefold()
    required = (
        "aws-actions/configure-aws-credentials@v6.3.0",
        "aws-actions/amazon-ecr-login@v2",
        "docker build",
        "docker push",
        "aws ecs register-task-definition",
        "aws ecs update-service",
        "aws ecs wait services-stable",
        "howreliable.cd.contract smoke",
        "previous_task_definition",
        "rollback",
    )
    _require(all(term in source for term in required), "deployment workflow step missing")
    forbidden = (
        "pull_request_target",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        ":latest",
        "publish-bundle",
        "s3:putobject",
        "terraform",
        "aws ecs create-service",
        "aws ecr create-repository",
        "aws s3 mb",
    )
    _require(not any(term in source for term in forbidden), "forbidden deployment behavior")
    return workflow


def validate_oidc_trust_policy(path: Path) -> dict[str, Any]:
    value = _json_object(path)
    statements = cast(list[dict[str, Any]], value.get("Statement"))
    _require(isinstance(statements, list) and len(statements) == 1, "one trust statement")
    statement = statements[0]
    _require(statement.get("Action") == "sts:AssumeRoleWithWebIdentity", "OIDC action")
    principal = cast(dict[str, str], statement.get("Principal"))
    _require("token.actions.githubusercontent.com" in principal.get("Federated", ""), "OIDC")
    conditions = json.dumps(statement.get("Condition", {}))
    _require(
        "repo:<GITHUB_OWNER>/<GITHUB_REPOSITORY>:environment:production" in conditions, "repo scope"
    )
    _require("repo:*" not in conditions, "wildcard repositories are forbidden")
    return value


def validate_deployment_role_policy(path: Path) -> dict[str, Any]:
    value = _json_object(path)
    statements = cast(list[dict[str, Any]], value.get("Statement"))
    _require(isinstance(statements, list), "policy statements are required")
    actions = {
        action
        for statement in statements
        for action in cast(list[str], statement.get("Action", []))
    }
    required = {
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
        "ecr:DescribeImages",
        "ecs:DescribeServices",
        "ecs:DescribeTaskDefinition",
        "ecs:RegisterTaskDefinition",
        "ecs:UpdateService",
        "iam:PassRole",
    }
    _require(required <= actions, "deployment role action missing")
    forbidden = {"s3:PutObject", "s3:DeleteObject", "s3:*", "ecs:*", "ecr:*", "iam:*"}
    _require(not actions & forbidden, "broad or artifact-writing action is forbidden")
    pass_role = next(item for item in statements if "iam:PassRole" in item.get("Action", []))
    resources = cast(list[str], pass_role.get("Resource"))
    _require(
        resources
        == [
            "arn:aws:iam::<AWS_ACCOUNT_ID>:role/<ECS_TASK_ROLE_NAME>",
            "arn:aws:iam::<AWS_ACCOUNT_ID>:role/<ECS_EXECUTION_ROLE_NAME>",
        ],
        "PassRole resources must be exact",
    )
    _require(
        "ecs-tasks.amazonaws.com" in json.dumps(pass_role.get("Condition")), "PassRole service"
    )
    return value


def validate_cd_repository(root: Path) -> dict[str, str]:
    validate_deploy_workflow(root / WORKFLOW_PATH)
    validate_oidc_trust_policy(root / TRUST_POLICY_PATH)
    validate_deployment_role_policy(root / DEPLOYMENT_POLICY_PATH)
    validate_ecs_task_definition(root / "infrastructure/aws/ecs-task-definition.json")
    return {"cd_contract": CD_CONTRACT_VERSION, "validation": "passed"}


def render_task_definition(
    source: Path,
    output: Path,
    *,
    image_uri: str,
    region: str,
    s3_bucket: str,
    s3_prefix: str,
    task_role_arn: str,
    execution_role_arn: str,
) -> dict[str, Any]:
    validate_ecs_task_definition(source)
    _require(
        "@sha256:" in image_uri or re.search(r":sha-[0-9a-f]{40}$", image_uri) is not None,
        "immutable image URI",
    )
    value = _json_object(source)
    value["taskRoleArn"] = task_role_arn
    value["executionRoleArn"] = execution_role_arn
    _require(task_role_arn != execution_role_arn, "task and execution roles must differ")
    container = cast(list[dict[str, Any]], value["containerDefinitions"])[0]
    _require(container.get("name") == EXPECTED_CONTAINER_NAME, "container name mismatch")
    container["image"] = image_uri
    environment = cast(list[dict[str, str]], container["environment"])
    replacements = {
        "HOWRELIABLE_S3_BUCKET": s3_bucket,
        "HOWRELIABLE_S3_PREFIX": s3_prefix,
        "HOWRELIABLE_AWS_REGION": region,
    }
    for item in environment:
        if item["name"] in replacements:
            item["value"] = replacements[item["name"]]
    logs = cast(dict[str, Any], container["logConfiguration"])
    cast(dict[str, str], logs["options"])["awslogs-region"] = region
    _atomic_write_json(output, value)
    validate_ecs_task_definition(output)
    _require(
        cast(dict[str, str], logs["options"])["awslogs-group"] == "/howreliable/api",
        "log group mismatch",
    )
    return value


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        _require(response.status == 200, f"smoke request failed: {url}")
        value = json.loads(response.read())
    _require(isinstance(value, dict), "smoke response must be an object")
    return cast(dict[str, Any], value)


def smoke(base_url: str, *, allow_http: bool = False) -> None:
    base = base_url.rstrip("/")
    _require(
        base.startswith("https://") or (allow_http and base.startswith("http://")),
        "smoke URL must use HTTPS",
    )
    _require(
        _get_json(f"{base}/health") == {"status": "ok", "service": "howreliable"},
        "health contract mismatch",
    )
    metadata = _get_json(f"{base}/api/v1/model")
    _require(metadata.get("api_contract_version") == API_COMPATIBILITY_VERSION, "API version")
    _require(
        metadata.get("registry_contract_version") == REGISTRY_CONTRACT_VERSION, "registry version"
    )
    _require(metadata.get("bundle_id") == DEFAULT_BUNDLE_ID, "bundle ID")
    cohorts = _get_json(f"{base}/api/v1/cohorts?limit=1&offset=0")
    _require(cohorts.get("total") == EXPECTED_COHORT_COUNT, "cohort count")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("generation timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate, validate, or support Phase 7B CD")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--generation-utc", type=_parse_utc, required=True)
    generate.add_argument("--regenerate", action="store_true")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--root", type=Path, default=Path("."))
    render = subparsers.add_parser("render-task")
    render.add_argument("--source", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--image-uri", required=True)
    render.add_argument("--region", required=True)
    render.add_argument("--s3-bucket", required=True)
    render.add_argument("--s3-prefix", required=True)
    render.add_argument("--task-role-arn", required=True)
    render.add_argument("--execution-role-arn", required=True)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--base-url", required=True)
    smoke_parser.add_argument("--allow-http", action="store_true")
    args = parser.parse_args(argv)
    if args.operation == "generate":
        generate_cd_contract_artifact(
            args.output,
            regenerate=args.regenerate,
            clock=lambda: cast(datetime, args.generation_utc),
        )
    elif args.operation == "validate":
        print(json.dumps(validate_cd_repository(args.root), sort_keys=True))
    elif args.operation == "render-task":
        render_task_definition(
            args.source,
            args.output,
            image_uri=args.image_uri,
            region=args.region,
            s3_bucket=args.s3_bucket,
            s3_prefix=args.s3_prefix,
            task_role_arn=args.task_role_arn,
            execution_role_arn=args.execution_role_arn,
        )
    else:
        smoke(args.base_url, allow_http=args.allow_http)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
