"""Phase 7A deterministic CI contract and workflow tests."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from howreliable.ci.contract import (
    CI_CONTRACT_VERSION,
    CONTRACT_VALIDATION_COMMAND,
    JOBS,
    PYTHON_VERSION,
    QUALITY_COMMANDS,
    TEST_COMMAND,
    TRIGGERS,
    CIContract,
    ci_contract,
    generate_ci_contract_artifact,
    validate_repository_contracts,
    validate_workflow,
)

ROOT = Path(".")
WORKFLOW = ROOT / ".github/workflows/ci.yml"
FIXED_TIME = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
CI_CONTRACT_SHA256 = "ba3e92cb34850b6114a943934435d6757330b246cd815a0b5ce2def2648e7ced"


def test_ci_contract_version_environment_and_non_deployment_scope() -> None:
    contract = CIContract.model_validate(ci_contract(generation_utc=FIXED_TIME))
    assert contract.ci_contract_version == CI_CONTRACT_VERSION == "howreliable-ci-1.0"
    assert contract.python_version == PYTHON_VERSION == "3.12.11"
    assert contract.trigger_categories == TRIGGERS
    assert contract.permissions == {"contents": "read"}
    assert contract.jobs == JOBS
    assert contract.aws_credentials_required is False
    assert contract.live_aws_access is False
    assert contract.deployment_performed is False
    assert contract.docker_push is False
    assert contract.docker_build_required is True


def test_ci_commands_and_regression_categories_are_explicit() -> None:
    contract = CIContract.model_validate(ci_contract(generation_utc=FIXED_TIME))
    assert contract.quality_commands == QUALITY_COMMANDS
    assert contract.test_command == TEST_COMMAND == "python -m pytest"
    assert contract.contract_validation_command == CONTRACT_VALIDATION_COMMAND
    regressions = " ".join(contract.scientific_regression_categories).casefold()
    for category in ("api", "probability", "explanation", "limitation", "monitoring"):
        assert category in regressions


def test_ci_contract_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_ci_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_ci_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == CI_CONTRACT_SHA256


def test_workflow_structure_triggers_permissions_jobs_and_commands() -> None:
    workflow = validate_workflow(WORKFLOW)
    assert set(cast(dict[str, Any], workflow["on"])) == set(TRIGGERS)
    assert workflow["permissions"] == {"contents": "read"}
    jobs = cast(dict[str, Any], workflow["jobs"])
    assert set(jobs) == set(JOBS)
    source = WORKFLOW.read_text().casefold()
    assert "python -m ruff check ." in source
    assert "python -m mypy" in source
    assert "python -m pip check" in source
    assert "python -m pytest" in source
    assert "docker build" in source
    assert "howreliable-api:ci-${github_sha}" in source
    assert ":latest" not in source


def test_workflow_is_secret_free_aws_independent_and_non_deploying() -> None:
    source = WORKFLOW.read_text().casefold()
    forbidden = (
        "secrets.",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_role_arn",
        "id-token: write",
        "docker push",
        "ecr get-login-password",
        "aws ecs",
        "aws s3",
    )
    assert not [term for term in forbidden if term in source]


def test_clean_checkout_contract_and_static_cloud_validation() -> None:
    result = validate_repository_contracts(ROOT)
    assert result["clean_checkout_contracts"] == "passed"
    assert result["full_artifact_status"] in {
        "canonical registry, API, and inference bundle validated",
        "not provisioned in clean checkout",
    }


def test_full_artifact_classification_is_explicit() -> None:
    conftest = (ROOT / "tests/conftest.py").read_text()
    for module in (
        "test_api.py",
        "test_deployment.py",
        "test_explainability.py",
        "test_monitoring.py",
        "test_presentation.py",
        "test_registry.py",
        "test_s3.py",
    ):
        assert module in conftest
    assert "full_artifacts" in conftest


def test_ci_contract_checksum_is_stable() -> None:
    contract = ci_contract(generation_utc=FIXED_TIME)
    value = json.loads(contract.model_dump_json())
    assert value["generation_utc"] == "2026-09-19T20:00:00Z"
