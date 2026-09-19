"""Phase 7B deterministic CD contract and deployment-workflow tests."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import howreliable.cd.contract as cd_module
from howreliable.cd.contract import (
    CD_CONTRACT_VERSION,
    CI_CONTRACT_SHA256,
    DEPLOYMENT_CONTRACT_SHA256,
    MONITORING_CONTRACT_SHA256,
    CDContract,
    cd_contract,
    generate_cd_contract_artifact,
    render_task_definition,
    smoke,
    validate_cd_repository,
    validate_deploy_workflow,
    validate_deployment_role_policy,
    validate_oidc_trust_policy,
)
from howreliable.ci.contract import validate_workflow

ROOT = Path(".")
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
TRUST_POLICY = ROOT / "infrastructure/iam/github-deploy-trust-policy.json"
DEPLOYMENT_POLICY = ROOT / "infrastructure/iam/github-deploy-policy.json"
TASK_DEFINITION = ROOT / "infrastructure/aws/ecs-task-definition.json"
FIXED_TIME = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)
CD_CONTRACT_SHA256 = "8f144088b22e28e78b6e9b05f4c866239c6864a83a5c0969900d7fc464ed65d1"


def test_cd_contract_versions_linkage_and_scope() -> None:
    contract = CDContract.model_validate(cd_contract(generation_utc=FIXED_TIME))
    assert contract.cd_contract_version == CD_CONTRACT_VERSION == "howreliable-cd-1.0"
    assert contract.ci_contract_version == "howreliable-ci-1.0"
    assert contract.ci_contract_sha256 == CI_CONTRACT_SHA256
    assert contract.deployment_contract_version == "howreliable-deployment-1.0"
    assert contract.deployment_contract_sha256 == DEPLOYMENT_CONTRACT_SHA256
    assert contract.monitoring_contract_version == "howreliable-monitoring-1.0"
    assert contract.monitoring_contract_sha256 == MONITORING_CONTRACT_SHA256
    assert contract.registry_version == "howreliable-model-registry-1.0"
    assert contract.s3_version == "howreliable-s3-1.0"
    assert contract.api_version == "howreliable-api-1.0"
    assert contract.result_version == "complaint-activity-result-1.0"
    assert contract.canonical_bundle_id == "howreliable-rf-2022-cutoff-v1"
    assert contract.artifact_publication is False
    assert contract.infrastructure_creation is False
    assert contract.terraform is False


def test_cd_contract_generation_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_cd_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_cd_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == CD_CONTRACT_SHA256


def test_deployment_workflow_is_manual_trusted_and_production_serialized() -> None:
    workflow = validate_deploy_workflow(WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "howreliable-production",
        "cancel-in-progress": False,
    }
    deploy = workflow["jobs"]["deploy"]
    assert deploy["environment"] == "production"
    assert deploy["permissions"] == {"contents": "read", "id-token": "write"}
    assert deploy["needs"] == "validate"


def test_ci_remains_read_only_and_non_deploying() -> None:
    workflow = validate_workflow(ROOT / ".github/workflows/ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert "id-token" not in workflow["permissions"]
    source = (ROOT / ".github/workflows/ci.yml").read_text().casefold()
    assert "docker push" not in source and "aws ecs" not in source


def test_oidc_trust_is_repository_and_environment_scoped() -> None:
    policy = validate_oidc_trust_policy(TRUST_POLICY)
    text = json.dumps(policy)
    assert "sts:AssumeRoleWithWebIdentity" in text
    assert "repo:<GITHUB_OWNER>/<GITHUB_REPOSITORY>:environment:production" in text
    assert "repo:*" not in text
    assert "<AWS_ACCOUNT_ID>" in text


def test_deployment_policy_is_narrow_and_passrole_is_exact() -> None:
    policy = validate_deployment_role_policy(DEPLOYMENT_POLICY)
    text = json.dumps(policy)
    assert "iam:PassRole" in text
    assert "ecs-tasks.amazonaws.com" in text
    assert "s3:PutObject" not in text and "s3:DeleteObject" not in text
    assert '"ecs:*"' not in text and '"ecr:*"' not in text and '"iam:*"' not in text
    assert "cloudwatch" not in text.casefold()


def test_workflow_uses_oidc_sha_tag_ecr_and_no_long_lived_credentials() -> None:
    source = WORKFLOW.read_text()
    folded = source.casefold()
    assert "aws-actions/configure-aws-credentials@v6.3.0" in source
    assert "aws-actions/amazon-ecr-login@v2" in source
    assert "sha-${{ needs.validate.outputs.commit_sha }}" in source
    assert "docker push" in source
    assert ":latest" not in source
    assert "AWS_ACCESS_KEY_ID" not in source
    assert "AWS_SECRET_ACCESS_KEY" not in source
    assert "AWS_SESSION_TOKEN" not in source
    assert "/home/howreliable/.aws" in source
    assert "/root/.aws" not in source
    assert "pull_request" not in folded
    assert "pull_request_target" not in folded


def test_workflow_reruns_gate_without_training_or_artifact_publication() -> None:
    source = WORKFLOW.read_text().casefold()
    for command in (
        "python -m ruff check .",
        "python -m mypy",
        "python -m pip check",
        "python -m pytest",
        "python -m howreliable.ci.contract validate",
        "python -m howreliable.cd.contract validate",
    ):
        assert command.casefold() in source
    assert "publish-bundle" not in source
    assert "howreliable.modeling" not in source
    assert ".fit(" not in source


def test_task_rendering_preserves_runtime_contract(tmp_path: Path) -> None:
    output = tmp_path / "task.json"
    value = render_task_definition(
        TASK_DEFINITION,
        output,
        image_uri=(
            f"123456789012.dkr.ecr.ca-central-1.amazonaws.com/howreliable-api:sha-{'a' * 40}"
        ),
        region="ca-central-1",
        s3_bucket="validated-private-bucket",
        s3_prefix="howreliable/frozen",
        task_role_arn="arn:aws:iam::123456789012:role/howreliable-task",
        execution_role_arn="arn:aws:iam::123456789012:role/howreliable-execution",
    )
    container = value["containerDefinitions"][0]
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert container["name"] == "howreliable-api"
    assert container["image"].endswith(f":sha-{'a' * 40}")
    assert container["portMappings"][0]["containerPort"] == 8000
    assert environment["HOWRELIABLE_ARTIFACT_BACKEND"] == "s3"
    assert environment["HOWRELIABLE_MODEL_BUNDLE_ID"] == "howreliable-rf-2022-cutoff-v1"
    assert environment["HOWRELIABLE_S3_BUCKET"] == "validated-private-bucket"
    assert container["logConfiguration"]["options"]["awslogs-group"] == "/howreliable/api"
    assert container["logConfiguration"]["options"]["awslogs-region"] == "ca-central-1"
    assert value["taskRoleArn"] != value["executionRoleArn"]


def test_task_render_rejects_mutable_image(tmp_path: Path) -> None:
    with pytest.raises(cd_module.CDValidationError, match="immutable image"):
        render_task_definition(
            TASK_DEFINITION,
            tmp_path / "task.json",
            image_uri="example.invalid/howreliable-api:latest",
            region="ca-central-1",
            s3_bucket="bucket",
            s3_prefix="prefix",
            task_role_arn="task-role",
            execution_role_arn="execution-role",
        )


def test_smoke_validates_health_metadata_bundle_and_cohort_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: dict[str, dict[str, Any]] = {
        "https://api.example/health": {"status": "ok", "service": "howreliable"},
        "https://api.example/api/v1/model": {
            "api_contract_version": "howreliable-api-1.0",
            "registry_contract_version": "howreliable-model-registry-1.0",
            "bundle_id": "howreliable-rf-2022-cutoff-v1",
        },
        "https://api.example/api/v1/cohorts?limit=1&offset=0": {"total": 8_416},
    }
    monkeypatch.setattr(cd_module, "_get_json", responses.__getitem__)
    smoke("https://api.example")


def test_smoke_rejects_http_and_wrong_cohort_count(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(cd_module.CDValidationError, match="HTTPS"):
        smoke("http://api.example")

    def wrong_count(url: str) -> dict[str, Any]:
        return (
            {"status": "ok", "service": "howreliable"}
            if url.endswith("/health")
            else {
                "api_contract_version": "howreliable-api-1.0",
                "registry_contract_version": "howreliable-model-registry-1.0",
                "bundle_id": "howreliable-rf-2022-cutoff-v1",
            }
            if url.endswith("/model")
            else {"total": 1}
        )

    monkeypatch.setattr(cd_module, "_get_json", wrong_count)
    with pytest.raises(cd_module.CDValidationError, match="cohort count"):
        smoke("https://api.example")


def test_rollback_and_stability_failure_semantics_are_explicit() -> None:
    source = WORKFLOW.read_text()
    assert "previous_task_definition=" in source
    assert "trap rollback ERR" in source
    assert source.count("aws ecs wait services-stable") == 2
    assert 'exit "$original_status"' in source
    assert "timeout 20m" in source


def test_no_infrastructure_creation_terraform_or_phase_7c() -> None:
    source = WORKFLOW.read_text().casefold()
    forbidden = (
        "create-service",
        "create-cluster",
        "create-repository",
        "create-log-group",
        "cloudformation",
        "terraform",
    )
    assert not [term for term in forbidden if term in source]
    assert not list(ROOT.glob("**/*.tf"))


def test_complete_offline_cd_validation() -> None:
    assert validate_cd_repository(ROOT) == {
        "cd_contract": "howreliable-cd-1.0",
        "validation": "passed",
    }
