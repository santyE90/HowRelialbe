"""Phase 7C Terraform contract, security, and ownership tests."""

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from howreliable.infrastructure.terraform_contract import (
    AWS_PROVIDER_CONSTRAINT,
    CD_CONTRACT_SHA256,
    TERRAFORM_CONTRACT_VERSION,
    TERRAFORM_VERSION_CONSTRAINT,
    TerraformContract,
    generate_terraform_contract_artifact,
    terraform_contract,
    validate_terraform_repository,
)

ROOT = Path(".")
TERRAFORM = ROOT / "infrastructure/terraform"
FIXED_TIME = datetime(2026, 9, 19, 23, 0, tzinfo=UTC)
TERRAFORM_CONTRACT_SHA256 = "62399c7b3fc6a2bcdd286fe5665937273056efb1eeace4de187f31599a481dba"


def source(name: str) -> str:
    return (TERRAFORM / name).read_text(encoding="utf-8")


def test_contract_versions_lineage_and_ownership() -> None:
    contract = TerraformContract.model_validate(terraform_contract(generation_utc=FIXED_TIME))
    assert contract.terraform_contract_version == TERRAFORM_CONTRACT_VERSION
    assert contract.required_terraform_version == TERRAFORM_VERSION_CONSTRAINT
    assert contract.aws_provider_constraint == AWS_PROVIDER_CONSTRAINT
    assert contract.s3_contract_version == "howreliable-s3-1.0"
    assert contract.deployment_contract_version == "howreliable-deployment-1.0"
    assert contract.monitoring_contract_version == "howreliable-monitoring-1.0"
    assert contract.cd_contract_version == "howreliable-cd-1.0"
    assert contract.cd_contract_sha256 == CD_CONTRACT_SHA256
    assert contract.canonical_bundle_id == "howreliable-rf-2022-cutoff-v1"
    assert contract.application_deployment_owner == "Phase 7B continuous delivery"
    assert "Phase 6A" in contract.model_artifact_publication_owner


def test_contract_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_terraform_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_terraform_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == TERRAFORM_CONTRACT_SHA256


def test_versions_provider_and_local_state_strategy() -> None:
    versions = source("versions.tf")
    all_tf = "\n".join(path.read_text(encoding="utf-8") for path in TERRAFORM.glob("*.tf"))
    assert f'required_version = "{TERRAFORM_VERSION_CONSTRAINT}"' in versions
    assert 'source  = "hashicorp/aws"' in versions
    assert f'version = "{AWS_PROVIDER_CONSTRAINT}"' in versions
    lock = source(".terraform.lock.hcl")
    assert 'version     = "6.65.0"' in lock
    assert 'constraints = "~> 6.0"' in lock
    assert 'provider "github"' not in all_tf
    assert 'backend "' not in all_tf
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.tfstate" in ignore and "**/.terraform/" in ignore
    if (ROOT / ".git").exists():
        tracked_state = subprocess.run(
            ["git", "ls-files", "*.tfstate", "*.tfstate.*"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert tracked_state == ""
    else:
        assert not list(ROOT.glob("**/*.tfstate*"))


def test_network_has_two_public_az_subnets_without_nat_or_public_task_ingress() -> None:
    network = source("network.tf")
    variables = source("variables.tf")
    assert 'resource "aws_vpc" "main"' in network
    assert 'default     = ["10.42.0.0/24", "10.42.1.0/24"]' in variables
    assert "data.aws_availability_zones.available.names" in network
    assert 'resource "aws_internet_gateway" "main"' in network
    assert 'resource "aws_route_table" "public"' in network
    assert "aws_nat_gateway" not in network
    task_rule = network.split(
        'resource "aws_vpc_security_group_ingress_rule" "task_from_alb"', maxsplit=1
    )[1].split("resource ", maxsplit=1)[0]
    assert "referenced_security_group_id = aws_security_group.alb.id" in task_rule
    assert "from_port                    = 8000" in task_rule
    assert "cidr_ipv4" not in task_rule
    assert "assign_public_ip = true" in source("ecs.tf")


def test_alb_target_health_and_optional_https_contract() -> None:
    alb = source("alb.tf")
    assert 'load_balancer_type = "application"' in alb
    assert 'target_type = "ip"' in alb
    assert "port        = 8000" in alb
    assert 'path                = "/health"' in alb
    assert 'matcher             = "200"' in alb
    assert 'resource "aws_lb_listener" "http"' in alb
    assert 'resource "aws_lb_listener" "https"' in alb
    assert "certificate_arn   = var.certificate_arn" in alb
    assert 'status_code = "HTTP_301"' in alb


def test_private_immutable_ecr_has_scan_and_conservative_lifecycle() -> None:
    ecr = source("ecr.tf")
    assert 'resource "aws_ecr_repository" "api"' in ecr
    assert 'image_tag_mutability = "IMMUTABLE"' in ecr
    assert "scan_on_push = true" in ecr
    assert 'encryption_type = "AES256"' in ecr
    assert "countNumber   = 50" in ecr
    assert "countNumber = 14" in ecr
    assert "aws_ecrpublic" not in ecr


def test_s3_is_private_encrypted_versioned_and_destruction_protected() -> None:
    s3 = source("s3.tf")
    assert "force_destroy = false" in s3
    assert "prevent_destroy = true" in s3
    for setting in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert setting in s3
    assert 'object_ownership = "BucketOwnerEnforced"' in s3
    assert 'sse_algorithm = "AES256"' in s3
    assert 'status = "Enabled"' in s3
    assert "aws_s3_object" not in s3


def test_three_roles_are_distinct_and_task_role_is_s3_read_only() -> None:
    iam = source("iam.tf")
    oidc = source("github_oidc.tf")
    assert 'resource "aws_iam_role" "task"' in iam
    assert 'resource "aws_iam_role" "execution"' in iam
    assert 'resource "aws_iam_role" "github_deploy"' in oidc
    assert 'actions   = ["s3:GetObject"]' in iam
    assert 'actions   = ["s3:ListBucket"]' in iam
    combined = (iam + oidc).casefold()
    assert "s3:putobject" not in combined
    assert "s3:deleteobject" not in combined
    assert '"s3:*"' not in combined
    assert "AmazonECSTaskExecutionRolePolicy" in iam


def test_deploy_role_oidc_and_passrole_are_exact() -> None:
    oidc = source("github_oidc.tf")
    assert 'url            = "https://token.actions.githubusercontent.com"' in oidc
    assert 'client_id_list = ["sts.amazonaws.com"]' in oidc
    assert "repo:${var.github_owner}/${var.github_repository}:environment:production" in oidc
    assert "existing_github_oidc_provider_arn" in source("locals.tf")
    assert "resources = [aws_iam_role.task.arn, aws_iam_role.execution.arn]" in oidc
    assert 'variable = "iam:PassedToService"' in oidc
    assert 'values   = ["ecs-tasks.amazonaws.com"]' in oidc
    folded = oidc.casefold()
    for broad in ('"ecs:*"', '"ecr:*"', '"iam:*"', '"s3:*"'):
        assert broad not in folded


def test_ecs_bootstrap_fargate_service_and_cd_drift_boundary() -> None:
    ecs = source("ecs.tf")
    variables = source("variables.tf")
    assert 'resource "aws_ecs_cluster" "api"' in ecs
    assert 'resource "aws_ecs_task_definition" "bootstrap"' in ecs
    assert 'requires_compatibilities = ["FARGATE"]' in ecs
    assert 'network_mode             = "awsvpc"' in ecs
    assert "cpu                      = 512" in ecs
    assert "memory                   = 1024" in ecs
    assert "desired_count                     = 1" in ecs
    assert "deployment_circuit_breaker" in ecs
    assert "rollback = true" in ecs
    assert "ignore_changes = [task_definition]" in ecs
    assert "aws_appautoscaling" not in ecs
    assert '@sha256:[0-9a-f]{64}$' in variables


def test_cloudwatch_preserves_log_and_four_alarm_contracts() -> None:
    monitoring = source("monitoring.tf")
    assert "retention_in_days = 14" in monitoring
    assert monitoring.count('resource "aws_cloudwatch_metric_alarm"') == 4
    assert 'metric_name         = "CPUUtilization"' in monitoring
    assert 'metric_name         = "MemoryUtilization"' in monitoring
    assert 'metric_name         = "UnHealthyHostCount"' in monitoring
    assert 'metric_name         = "HTTPCode_Target_5XX_Count"' in monitoring
    assert "drift" not in monitoring.casefold()
    assert "alarm_actions" not in monitoring


def test_outputs_map_exact_phase_7b_environment_variables() -> None:
    outputs = source("outputs.tf")
    expected = {
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
    }
    assert all(name in outputs for name in expected)
    assert "sensitive = true" not in outputs


def test_terraform_neither_deploys_application_nor_publishes_or_trains() -> None:
    folded = "\n".join(
        path.read_text(encoding="utf-8") for path in TERRAFORM.glob("*.tf")
    ).casefold()
    for forbidden in (
        "docker build",
        "docker push",
        "publish-bundle",
        "publish_bundle",
        "terraform apply",
        ".fit(",
        "phase 8",
    ):
        assert forbidden not in folded


def test_complete_offline_terraform_validation() -> None:
    validator = "python -m howreliable.infrastructure.terraform_contract validate --root ."
    assert validator in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert validate_terraform_repository(ROOT) == {
        "terraform_contract": "howreliable-terraform-1.0",
        "validation": "passed",
    }
