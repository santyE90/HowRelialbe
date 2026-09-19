"""Phase 6C structured logging and operational-monitoring contract tests."""

import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import howreliable.api.service as api_service
from howreliable.api.app import REQUEST_ID_HEADER, create_app
from howreliable.api.service import ArtifactContractError, PredictionService
from howreliable.cloud.monitoring import (
    DEPLOYMENT_CONTRACT_SHA256,
    LOG_DELIVERY_PERMISSIONS,
    LOG_GROUP,
    LOG_RETENTION_DAYS,
    MONITORING_CONTRACT_VERSION,
    REQUEST_ID_MAX_LENGTH,
    AlarmContract,
    MonitoringContract,
    generate_monitoring_contract_artifact,
    validate_alarm_specification,
    validate_log_group_specification,
    validate_task_log_configuration,
)
from howreliable.common.logging import STABLE_EVENTS, configure_logging
from howreliable.config.settings import Settings
from howreliable.modeling.presentation import MODEL_CHECKSUM
from howreliable.modeling.registry import RegistryError

ROOT = Path(".")
TASK_DEFINITION = ROOT / "infrastructure/aws/ecs-task-definition.json"
ALARM_SPECIFICATION = ROOT / "infrastructure/aws/monitoring/cloudwatch-alarms.json"
LOG_GROUP_SPECIFICATION = ROOT / "infrastructure/aws/monitoring/cloudwatch-log-group.json"
CONTRACT_ARTIFACT = ROOT / "artifacts/cloud/monitoring-contract.json"
FIXED_TIME = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)


def _records(output: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line]


@pytest.fixture(scope="module")
def service() -> PredictionService:
    return PredictionService.load(ROOT)


def _monitored_client(
    service: PredictionService, *, raise_server_exceptions: bool = True
) -> tuple[TestClient, io.StringIO]:
    output = io.StringIO()
    settings = Settings(environment="test", log_level="INFO", log_format="json")
    app = create_app(service=service, settings=settings, log_stream=output)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), output


def test_monitoring_contract_schema_linkage_and_scope() -> None:
    contract = MonitoringContract.model_validate_json(CONTRACT_ARTIFACT.read_bytes())
    assert contract.monitoring_contract_version == MONITORING_CONTRACT_VERSION
    assert contract.deployment_contract_version == "howreliable-deployment-1.0"
    assert contract.deployment_contract_sha256 == DEPLOYMENT_CONTRACT_SHA256
    assert contract.api_version == "howreliable-api-1.0"
    assert contract.compute_platform == "ECS_FARGATE"
    assert contract.log_group == LOG_GROUP == "/howreliable/api"
    assert contract.retention_days == LOG_RETENTION_DAYS == 14
    assert contract.log_delivery_permissions == LOG_DELIVERY_PERMISSIONS
    assert contract.stable_events == STABLE_EVENTS
    assert contract.request_id_max_length == REQUEST_ID_MAX_LENGTH == 64
    assert contract.ml_quality_monitoring_implemented is False
    assert contract.running_task_count_monitoring.startswith("not implemented")


def test_monitoring_contract_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_monitoring_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_monitoring_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes() == CONTRACT_ARTIFACT.read_bytes()


def test_json_log_is_one_line_utc_and_stable() -> None:
    output = io.StringIO()
    logger = configure_logging(Settings(log_format="json"), stream=output)
    logger.info("service began", extra={"event": "service_starting", "bundle_id": "bundle"})
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert value["timestamp"].endswith("Z")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value["timestamp"])
    assert value == {
        "timestamp": value["timestamp"],
        "level": "INFO",
        "logger": "howreliable",
        "event": "service_starting",
        "message": "service began",
        "bundle_id": "bundle",
    }


def test_json_exception_logging_exposes_type_not_exception_secret() -> None:
    output = io.StringIO()
    logger = configure_logging(Settings(log_format="json"), stream=output)
    try:
        raise RuntimeError("AWS_SECRET_ACCESS_KEY=must-not-appear")
    except RuntimeError:
        logger.exception("safe operation failure", extra={"event": "request_failed"})
    value = _records(output)[0]
    assert value["exception_type"] == "RuntimeError"
    assert "must-not-appear" not in output.getvalue()


def test_text_logging_remains_available() -> None:
    output = io.StringIO()
    logger = configure_logging(Settings(log_format="text"), stream=output)
    logger.warning("local text")
    assert "| WARNING | howreliable | local text" in output.getvalue()


def test_invalid_log_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="json or text"):
        Settings.from_env({"HOWRELIABLE_LOG_FORMAT": "yaml"})
    with pytest.raises(ValueError, match="log format"):
        configure_logging(Settings(log_format="yaml"))


def test_successful_startup_emits_complete_lifecycle() -> None:
    output = io.StringIO()
    create_app(
        root=ROOT,
        settings=Settings(environment="test", log_format="json"),
        log_stream=output,
    )
    records = _records(output)
    events = [item["event"] for item in records]
    assert events == [
        "service_starting",
        "artifact_backend_selected",
        "bundle_load_started",
        "bundle_load_succeeded",
        "service_ready",
    ]
    loaded = next(item for item in records if item["event"] == "bundle_load_succeeded")
    assert loaded["supported_cohort_count"] == 8_416
    assert loaded["bundle_load_duration_ms"] >= 0
    assert loaded["model_identifier"] == "phase-3b-random-forest"


def test_bundle_failure_event_is_safe_and_startup_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()

    def fail(_registry: object, _bundle_id: str) -> None:
        raise RegistryError("AWS_SECRET_ACCESS_KEY=must-not-appear")

    monkeypatch.setattr(api_service, "load_inference_bundle", fail)
    with pytest.raises(ArtifactContractError):
        create_app(
            root=ROOT,
            settings=Settings(environment="test", log_format="json"),
            log_stream=output,
        )
    failure = next(item for item in _records(output) if item["event"] == "bundle_load_failed")
    assert failure["error_category"] == "RegistryError"
    assert failure["lifecycle_stage"] == "bundle_validation"
    assert "must-not-appear" not in output.getvalue()


def test_request_id_generated_returned_and_logged(service: PredictionService) -> None:
    client, output = _monitored_client(service)
    response = client.get("/api/v1/model")
    request_id = response.headers[REQUEST_ID_HEADER]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    completion = next(item for item in _records(output) if item["event"] == "request_completed")
    assert completion["request_id"] == request_id
    assert completion["method"] == "GET"
    assert completion["route"] == "/api/v1/model"
    assert completion["status_code"] == 200
    assert completion["duration_ms"] >= 0


def test_valid_incoming_request_id_is_preserved(service: PredictionService) -> None:
    client, output = _monitored_client(service)
    response = client.get("/api/v1/model", headers={REQUEST_ID_HEADER: "client-Req_123:abc"})
    assert response.headers[REQUEST_ID_HEADER] == "client-Req_123:abc"
    assert any(item.get("request_id") == "client-Req_123:abc" for item in _records(output))


@pytest.mark.parametrize("invalid", ["contains space", "bad!character", "x" * 65, "-leading"])
def test_invalid_incoming_request_id_is_replaced_and_bounded(
    service: PredictionService, invalid: str
) -> None:
    client, _output = _monitored_client(service)
    response = client.get("/api/v1/model", headers={REQUEST_ID_HEADER: invalid})
    replacement = response.headers[REQUEST_ID_HEADER]
    assert replacement != invalid
    assert len(replacement) <= REQUEST_ID_MAX_LENGTH
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", replacement)


def test_successful_health_check_is_suppressed_at_info(service: PredictionService) -> None:
    client, output = _monitored_client(service)
    response = client.get("/health")
    assert response.status_code == 200
    assert REQUEST_ID_HEADER in response.headers
    assert not [item for item in _records(output) if item["event"].startswith("request_")]


def test_unexpected_error_is_sanitized_correlated_and_data_minimized(
    service: PredictionService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_self: PredictionService, _cohort_id: str) -> None:
        raise RuntimeError("Authorization: Bearer secret-value")

    monkeypatch.setattr(PredictionService, "predict", fail)
    client, output = _monitored_client(service, raise_server_exceptions=False)
    response = client.get(
        "/api/v1/cohorts/vehicle_association_" + "0" * 64,
        headers={REQUEST_ID_HEADER: "error-correlation-1", "Cookie": "secret-cookie"},
    )
    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "error-correlation-1"
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed."}
    }
    failure = next(item for item in _records(output) if item["event"] == "request_failed")
    assert failure["request_id"] == "error-correlation-1"
    assert failure["error_category"] == "RuntimeError"
    assert failure["route"] == "/api/v1/cohorts/{cohort_id}"
    assert "secret-value" not in output.getvalue()
    assert "secret-cookie" not in output.getvalue()


def test_prediction_request_log_contains_no_prediction_content(service: PredictionService) -> None:
    client, output = _monitored_client(service)
    example = json.loads((ROOT / "artifacts/presentation/representative-results.json").read_text())[
        "examples"
    ][0]
    cohort_id = cast(str, example["identity"]["cohort_id"])
    response = client.get(f"/api/v1/cohorts/{cohort_id}")
    assert response.status_code == 200
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert all("predicted_future_complaint_probability" not in record for record in records)
    assert all("prediction" not in record for record in records)
    assert all(cohort_id not in json.dumps(record) for record in records)
    assert all("top_positive_contributors" not in record for record in records)
    completion = next(item for item in _records(output) if item["event"] == "request_completed")
    assert completion["route"] == "/api/v1/cohorts/{cohort_id}"


def test_ecs_awslogs_and_log_group_contract() -> None:
    task = validate_task_log_configuration(TASK_DEFINITION)
    configuration = task["containerDefinitions"][0]["logConfiguration"]
    assert configuration["logDriver"] == "awslogs"
    assert configuration["options"] == {
        "awslogs-group": "/howreliable/api",
        "awslogs-region": "<AWS_REGION>",
        "awslogs-stream-prefix": "ecs",
    }
    assert validate_log_group_specification(LOG_GROUP_SPECIFICATION) == {
        "logGroupName": "/howreliable/api",
        "retentionInDays": 14,
    }
    environment = {
        item["name"]: item["value"] for item in task["containerDefinitions"][0]["environment"]
    }
    assert environment["HOWRELIABLE_LOG_FORMAT"] == "json"


def test_alarm_specification_has_four_bounded_native_signals() -> None:
    value = validate_alarm_specification(ALARM_SPECIFICATION)
    by_metric = {item["MetricName"]: item for item in value["alarms"]}
    assert set(by_metric) == {
        "CPUUtilization",
        "MemoryUtilization",
        "UnHealthyHostCount",
        "HTTPCode_Target_5XX_Count",
    }
    assert by_metric["CPUUtilization"]["Threshold"] == 80.0
    assert by_metric["MemoryUtilization"]["Threshold"] == 80.0
    assert by_metric["UnHealthyHostCount"]["Threshold"] == 0.0
    assert by_metric["HTTPCode_Target_5XX_Count"]["Threshold"] == 5.0
    assert all(len(item["Dimensions"]) == 2 for item in value["alarms"])


def test_alarm_contract_excludes_ml_quality_and_task_count_invention() -> None:
    contract = MonitoringContract.model_validate_json(CONTRACT_ARTIFACT.read_bytes())
    metrics = {alarm.metric for alarm in contract.alarms}
    assert not metrics & {
        "ModelAccuracy",
        "PredictionDistribution",
        "FeatureDrift",
        "ProbabilityDistribution",
    }
    assert all(isinstance(alarm, AlarmContract) for alarm in contract.alarms)
    assert contract.running_task_count_monitoring.startswith("not implemented")


def test_execution_and_task_role_monitoring_distinction_is_preserved() -> None:
    task = json.loads(TASK_DEFINITION.read_text())
    assert task["executionRoleArn"] != task["taskRoleArn"]
    policy = json.loads((ROOT / "infrastructure/iam/howreliable-s3-reader-policy.json").read_text())
    task_actions = {item["Action"] for item in policy["Statement"]}
    assert not task_actions & set(LOG_DELIVERY_PERMISSIONS)
    documentation = (ROOT / "docs/aws-deployment.md").read_text()
    assert "AmazonECSTaskExecutionRolePolicy" in documentation


def test_api_and_frozen_prediction_semantics_remain_exact(service: PredictionService) -> None:
    client, _output = _monitored_client(service)
    assert set(client.get("/openapi.json").json()["paths"]) == {
        "/health",
        "/api/v1/model",
        "/api/v1/cohorts",
        "/api/v1/cohorts/{cohort_id}",
    }
    example = json.loads((ROOT / "artifacts/presentation/representative-results.json").read_text())[
        "examples"
    ][0]
    result = client.get(f"/api/v1/cohorts/{example['identity']['cohort_id']}").json()
    assert result["prediction"] == example["prediction"]
    assert result["classification"] == example["classification"]
    assert result["explanation"]["reconstruction_absolute_error"] < 1e-12
    assert result["limitation_flags"] == example["limitation_flags"]
    assert result["provenance"]["model_checksum"] == MODEL_CHECKSUM
    assert "evaluation_context" not in result


def test_monitoring_adds_no_training_terraform_or_live_aws_dependency() -> None:
    source = (ROOT / "src/howreliable/cloud/monitoring.py").read_text().casefold()
    assert ".fit(" not in source and "torch" not in source
    assert "boto3" not in source and "webhook" not in source
    assert not list(ROOT.glob("**/*.tf"))
