"""Deterministic Phase 7C Terraform contract and offline validation."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel, ConfigDict, field_validator

from howreliable.cd.contract import CD_CONTRACT_VERSION
from howreliable.cloud.deployment import (
    CONTAINER_PORT,
    DEPLOYMENT_CONTRACT_VERSION,
    DESIRED_COUNT,
    HEALTH_PATH,
    TASK_CPU,
    TASK_MEMORY_MIB,
)
from howreliable.cloud.monitoring import EXPECTED_ALARMS, LOG_GROUP, MONITORING_CONTRACT_VERSION
from howreliable.cloud.s3 import S3_CONTRACT_VERSION
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.registry import DEFAULT_BUNDLE_ID

TERRAFORM_CONTRACT_VERSION: Final = "howreliable-terraform-1.0"
TERRAFORM_VERSION_CONSTRAINT: Final = ">= 1.14.0, < 1.17.0"
AWS_PROVIDER_CONSTRAINT: Final = "~> 6.0"
CD_CONTRACT_SHA256: Final = "8f144088b22e28e78b6e9b05f4c866239c6864a83a5c0969900d7fc464ed65d1"
TERRAFORM_DIRECTORY: Final = Path("infrastructure/terraform")

EXPECTED_FILES: Final = frozenset(
    {
        ".terraform.lock.hcl",
        "alb.tf",
        "ecr.tf",
        "ecs.tf",
        "github_oidc.tf",
        "iam.tf",
        "locals.tf",
        "monitoring.tf",
        "network.tf",
        "outputs.tf",
        "providers.tf",
        "s3.tf",
        "terraform.tfvars.example",
        "variables.tf",
        "versions.tf",
    }
)


class TerraformValidationError(ValueError):
    """Terraform configuration violates the Phase 7C ownership or security contract."""


class TerraformContract(BaseModel):
    """Strict machine-readable infrastructure contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    terraform_contract_version: str
    required_terraform_version: str
    aws_provider_constraint: str
    environment: str
    network_model: str
    resource_ownership: dict[str, str]
    s3_contract_version: str
    deployment_contract_version: str
    monitoring_contract_version: str
    cd_contract_version: str
    cd_contract_sha256: str
    canonical_bundle_id: str
    desired_ecs_count: int
    task_cpu_units: int
    task_memory_mib: int
    container_port: int
    health_path: str
    log_group: str
    monitoring_alarms: tuple[str, ...]
    oidc_trust_model: str
    state_backend_mode: str
    application_deployment_owner: str
    model_artifact_publication_owner: str
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


def terraform_contract(*, generation_utc: datetime) -> TerraformContract:
    """Build the frozen Phase 7C contract for an explicit generation instant."""
    return TerraformContract(
        terraform_contract_version=TERRAFORM_CONTRACT_VERSION,
        required_terraform_version=TERRAFORM_VERSION_CONSTRAINT,
        aws_provider_constraint=AWS_PROVIDER_CONSTRAINT,
        environment="production",
        network_model=(
            "one VPC; two public subnets in distinct available AZs; public ALB; "
            "public-IP Fargate task with no direct public ingress; no NAT gateway"
        ),
        resource_ownership={
            "terraform": (
                "network, S3, ECR, IAM, ECS foundation, ALB, CloudWatch, GitHub OIDC identity"
            ),
            "continuous_delivery": "application image and ECS application task revisions",
        },
        s3_contract_version=S3_CONTRACT_VERSION,
        deployment_contract_version=DEPLOYMENT_CONTRACT_VERSION,
        monitoring_contract_version=MONITORING_CONTRACT_VERSION,
        cd_contract_version=CD_CONTRACT_VERSION,
        cd_contract_sha256=CD_CONTRACT_SHA256,
        canonical_bundle_id=DEFAULT_BUNDLE_ID,
        desired_ecs_count=DESIRED_COUNT,
        task_cpu_units=TASK_CPU,
        task_memory_mib=TASK_MEMORY_MIB,
        container_port=CONTAINER_PORT,
        health_path=HEALTH_PATH,
        log_group=LOG_GROUP,
        monitoring_alarms=tuple(EXPECTED_ALARMS),
        oidc_trust_model=(
            "immutable GitHub owner and repository IDs with exact production environment subject; "
            "sts.amazonaws.com audience"
        ),
        state_backend_mode="local",
        application_deployment_owner="Phase 7B continuous delivery",
        model_artifact_publication_owner="separate Phase 6A controlled operation",
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


def generate_terraform_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> TerraformContract:
    """Write a deterministic, overwrite-protected Terraform contract artifact."""
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite Terraform contract: {output}")
    contract = terraform_contract(generation_utc=clock())
    _atomic_write_json(output, contract.model_dump(mode="json"))
    return contract


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TerraformValidationError(message)


def _read(directory: Path, filename: str) -> str:
    try:
        return (directory / filename).read_text(encoding="utf-8")
    except OSError as error:
        raise TerraformValidationError(f"missing Terraform file: {filename}") from error


def _resource_block(source: str, resource_type: str, name: str) -> str:
    marker = f'resource "{resource_type}" "{name}"'
    start = source.find(marker)
    _require(start >= 0, f"missing resource {resource_type}.{name}")
    next_resource = source.find('\nresource "', start + len(marker))
    return source[start:] if next_resource < 0 else source[start:next_resource]


def validate_terraform_repository(root: Path) -> dict[str, str]:
    """Validate Terraform structure and security without credentials or provider access."""
    directory = root / TERRAFORM_DIRECTORY
    names = {path.name for path in directory.iterdir() if path.is_file()}
    _require(names >= EXPECTED_FILES, "Terraform layout is incomplete")

    sources = {name: _read(directory, name) for name in EXPECTED_FILES}
    terraform_sources = {name: value for name, value in sources.items() if name.endswith(".tf")}
    joined = "\n".join(terraform_sources.values())
    folded = joined.casefold()

    versions = sources["versions.tf"]
    _require(
        f'required_version = "{TERRAFORM_VERSION_CONSTRAINT}"' in versions,
        "Terraform version",
    )
    _require(f'version = "{AWS_PROVIDER_CONSTRAINT}"' in versions, "AWS provider version")
    _require('source  = "hashicorp/aws"' in versions, "AWS provider source")
    lock = sources[".terraform.lock.hcl"]
    _require('constraints = "~> 6.0"' in lock, "AWS provider lock constraint")
    _require(
        'provider "github"' not in folded and "integrations/github" not in folded,
        "GitHub provider",
    )
    _require('backend "' not in folded, "Phase 7C state must remain local")

    network = sources["network.tf"]
    _require('resource "aws_vpc" "main"' in network, "VPC")
    _require('resource "aws_subnet" "public"' in network and "for_each" in network, "subnets")
    _require('resource "aws_internet_gateway" "main"' in network, "internet gateway")
    _require('resource "aws_route_table" "public"' in network, "route table")
    task_ingress = _resource_block(network, "aws_vpc_security_group_ingress_rule", "task_from_alb")
    _require(
        "referenced_security_group_id = aws_security_group.alb.id" in task_ingress,
        "task ingress",
    )
    _require("cidr_ipv4" not in task_ingress, "task port must not be public")
    _require("from_port                    = 8000" in task_ingress, "task port")
    _require("aws_nat_gateway" not in folded, "NAT gateway is outside the selected architecture")

    alb = sources["alb.tf"]
    _require('resource "aws_lb" "api"' in alb, "ALB")
    _require('target_type = "ip"' in alb and "port        = 8000" in alb, "target group")
    _require('path                = "/health"' in alb, "health path")
    _require('resource "aws_lb_listener" "https"' in alb and "certificate_arn" in alb, "HTTPS")

    ecr = sources["ecr.tf"]
    _require('image_tag_mutability = "IMMUTABLE"' in ecr, "ECR immutability")
    _require("scan_on_push = true" in ecr, "ECR scanning")
    _require("aws_ecr_lifecycle_policy" in ecr and "countNumber   = 50" in ecr, "ECR lifecycle")

    s3 = sources["s3.tf"]
    _require("force_destroy = false" in s3 and "prevent_destroy = true" in s3, "S3 safety")
    public_access_settings = (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    )
    for setting in public_access_settings:
        _require(f"{setting}" in s3 and f"{setting.ljust(23)} = true" in s3, f"S3 {setting}")
    _require('sse_algorithm = "AES256"' in s3, "S3 encryption")
    _require('status = "Enabled"' in s3, "S3 versioning")
    _require("aws_s3_bucket_policy" not in s3, "public bucket policy")

    iam = sources["iam.tf"]
    oidc = sources["github_oidc.tf"]
    _require(iam.count('resource "aws_iam_role"') == 2, "task and execution roles")
    _require('resource "aws_iam_role" "github_deploy"' in oidc, "deployment role")
    _require(
        'actions   = ["s3:GetObject"]' in iam and 'actions   = ["s3:ListBucket"]' in iam,
        "S3 reads",
    )
    forbidden_permissions = (
        '"s3:putobject"',
        '"s3:deleteobject"',
        '"s3:*"',
        '"ecs:*"',
        '"ecr:*"',
        '"iam:*"',
    )
    _require(
        not any(item in folded for item in forbidden_permissions),
        "broad/write IAM permission",
    )
    _require(
        "resources = [aws_iam_role.task.arn, aws_iam_role.execution.arn]" in oidc,
        "PassRole scope",
    )
    _require(
        'variable = "iam:PassedToService"' in oidc
        and 'values   = ["ecs-tasks.amazonaws.com"]' in oidc,
        "PassRole service",
    )
    _require(
        (
            "repo:${var.github_owner}@${var.github_owner_id}/"
            "${var.github_repository}@${var.github_repository_id}:environment:production"
        )
        in oidc,
        "immutable OIDC subject",
    )
    _require("variable \"github_owner_id\"" in sources["variables.tf"], "GitHub owner ID input")
    _require(
        "variable \"github_repository_id\"" in sources["variables.tf"],
        "GitHub repository ID input",
    )
    _require('values   = ["sts.amazonaws.com"]' in oidc, "OIDC audience")
    _require("existing_github_oidc_provider_arn" in joined, "existing OIDC reuse")

    ecs = sources["ecs.tf"]
    _require('requires_compatibilities = ["FARGATE"]' in ecs, "Fargate")
    _require('network_mode             = "awsvpc"' in ecs, "awsvpc")
    _require("desired_count                     = 1" in ecs, "desired count")
    _require("assign_public_ip = true" in ecs, "public-IP architecture")
    _require("ignore_changes = [task_definition]" in ecs, "CD task-definition drift")
    _require("aws_appautoscaling" not in folded, "autoscaling")

    monitoring = sources["monitoring.tf"]
    _require('name              = local.log_group_name' in monitoring, "log group")
    _require("retention_in_days = 14" in monitoring, "log retention")
    for alarm_name in EXPECTED_ALARMS:
        _require(f'alarm_name          = "{alarm_name}"' in monitoring, f"alarm {alarm_name}")
    _require("drift" not in monitoring.casefold(), "ML drift alarm")

    outputs = sources["outputs.tf"]
    expected_variables = (
        "HOWRELIABLE_DEPLOY_ROLE_ARN",
        "HOWRELIABLE_AWS_REGION",
        "HOWRELIABLE_ECR_REPOSITORY",
        "HOWRELIABLE_ECS_CLUSTER",
        "HOWRELIABLE_ECS_SERVICE",
        "HOWRELIABLE_API_BASE_URL",
        "HOWRELIABLE_S3_BUCKET",
        "HOWRELIABLE_S3_PREFIX",
        "HOWRELIABLE_TASK_ROLE_ARN",
        "HOWRELIABLE_EXECUTION_ROLE_ARN",
        "HOWRELIABLE_ALLOW_HTTP_SMOKE",
    )
    _require(all(name in outputs for name in expected_variables), "CD output mapping")

    forbidden_operations = (
        "docker build",
        "docker push",
        "publish-bundle",
        "publish_bundle",
        "terraform apply",
        ".fit(",
    )
    _require(
        not any(term in folded for term in forbidden_operations),
        "deployment/training operation",
    )
    ignore = _read(root, ".gitignore")
    _require("*.tfstate" in ignore and "**/.terraform/" in ignore, "Terraform state ignore")
    return {"terraform_contract": TERRAFORM_CONTRACT_VERSION, "validation": "passed"}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("generation timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase 7C contract")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--generation-utc", type=_parse_utc, required=True)
    generate.add_argument("--regenerate", action="store_true")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.operation == "generate":
        generate_terraform_contract_artifact(
            args.output,
            regenerate=args.regenerate,
            clock=lambda: cast(datetime, args.generation_utc),
        )
    else:
        print(json.dumps(validate_terraform_repository(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
