"""Deterministic Phase 6C operational-monitoring contracts and static validation."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from howreliable.cloud.deployment import COMPUTE_PLATFORM, DEPLOYMENT_CONTRACT_VERSION
from howreliable.common.logging import STABLE_EVENTS
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.registry import API_COMPATIBILITY_VERSION

MONITORING_CONTRACT_VERSION: Final = "howreliable-monitoring-1.0"
DEPLOYMENT_CONTRACT_SHA256: Final = (
    "c4e6806743421eb1e0feebf27522aec5caa1e9fc87675daaee18c1e5b30b71e4"
)
LOG_GROUP: Final = "/howreliable/api"
LOG_RETENTION_DAYS: Final = 14
LOG_STREAM_PREFIX: Final = "ecs"
LOG_DELIVERY_PERMISSIONS: Final = ("logs:CreateLogStream", "logs:PutLogEvents")
REQUEST_ID_HEADER: Final = "X-Request-ID"
REQUEST_ID_MAX_LENGTH: Final = 64
ALARM_SPECIFICATION_VERSION: Final = "howreliable-cloudwatch-alarms-1.0"
EXPECTED_ALARMS: Final = {
    "howreliable-ecs-cpu-high": ("AWS/ECS", "CPUUtilization", 80.0, 300, 3, 2),
    "howreliable-ecs-memory-high": ("AWS/ECS", "MemoryUtilization", 80.0, 300, 3, 2),
    "howreliable-alb-unhealthy-target": (
        "AWS/ApplicationELB",
        "UnHealthyHostCount",
        0.0,
        60,
        2,
        1,
    ),
    "howreliable-alb-target-5xx": (
        "AWS/ApplicationELB",
        "HTTPCode_Target_5XX_Count",
        5.0,
        300,
        1,
        1,
    ),
}


class MonitoringValidationError(ValueError):
    """A monitoring definition violates the Phase 6C operational contract."""


class AlarmContract(BaseModel):
    """Stable alarm semantics included in the monitoring handoff."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    namespace: str
    metric: str
    statistic: str
    threshold: float
    comparison: str
    period_seconds: int = Field(gt=0)
    evaluation_periods: int = Field(gt=0)
    datapoints_to_alarm: int = Field(gt=0)


class MonitoringContract(BaseModel):
    """Strict machine-readable Phase 6C monitoring schema."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    monitoring_contract_version: str
    deployment_contract_version: str
    deployment_contract_sha256: str
    api_version: str
    compute_platform: str
    log_delivery_permissions: tuple[str, ...]
    log_format: str
    log_group: str
    log_stream_prefix: str
    retention_days: int = Field(gt=0)
    stable_events: tuple[str, ...]
    request_id_header: str
    request_id_max_length: int = Field(gt=0)
    health_success_log_level: str
    alarms: tuple[AlarmContract, ...]
    running_task_count_monitoring: str
    ml_quality_monitoring_implemented: bool
    generation_utc: datetime

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value


def monitoring_contract(*, generation_utc: datetime) -> MonitoringContract:
    """Build the Phase 6C contract for an explicit generation instant."""
    return MonitoringContract(
        monitoring_contract_version=MONITORING_CONTRACT_VERSION,
        deployment_contract_version=DEPLOYMENT_CONTRACT_VERSION,
        deployment_contract_sha256=DEPLOYMENT_CONTRACT_SHA256,
        api_version=API_COMPATIBILITY_VERSION,
        compute_platform=COMPUTE_PLATFORM,
        log_delivery_permissions=LOG_DELIVERY_PERMISSIONS,
        log_format="newline-delimited JSON on stdout/stderr",
        log_group=LOG_GROUP,
        log_stream_prefix=LOG_STREAM_PREFIX,
        retention_days=LOG_RETENTION_DAYS,
        stable_events=STABLE_EVENTS,
        request_id_header=REQUEST_ID_HEADER,
        request_id_max_length=REQUEST_ID_MAX_LENGTH,
        health_success_log_level="DEBUG",
        alarms=tuple(
            AlarmContract(
                name=name,
                namespace=values[0],
                metric=values[1],
                statistic=(
                    "Maximum"
                    if name.endswith("unhealthy-target")
                    else "Sum"
                    if name.endswith("target-5xx")
                    else "Average"
                ),
                threshold=values[2],
                comparison=(
                    "GreaterThanThreshold"
                    if name.endswith("unhealthy-target")
                    else "GreaterThanOrEqualToThreshold"
                ),
                period_seconds=values[3],
                evaluation_periods=values[4],
                datapoints_to_alarm=values[5],
            )
            for name, values in EXPECTED_ALARMS.items()
        ),
        running_task_count_monitoring=(
            "not implemented; standard ECS service metrics do not expose desired-versus-running "
            "count without Container Insights or a custom signal"
        ),
        ml_quality_monitoring_implemented=False,
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


def generate_monitoring_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MonitoringContract:
    """Persist a deterministic monitoring contract without cloud or secret access."""
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite monitoring contract: {output}")
    contract = monitoring_contract(generation_utc=clock())
    _atomic_write_json(output, contract.model_dump(mode="json"))
    return contract


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MonitoringValidationError("monitoring specification is not valid JSON") from error
    if not isinstance(value, dict):
        raise MonitoringValidationError("monitoring specification must be a JSON object")
    return cast(dict[str, Any], value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MonitoringValidationError(message)


def validate_log_group_specification(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    _require(
        value == {"logGroupName": LOG_GROUP, "retentionInDays": LOG_RETENTION_DAYS},
        "log group contract mismatch",
    )
    return value


def validate_alarm_specification(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    _require(
        value.get("specificationVersion") == ALARM_SPECIFICATION_VERSION,
        "alarm specification version mismatch",
    )
    alarms = cast(list[dict[str, Any]], value.get("alarms"))
    _require(isinstance(alarms, list) and len(alarms) == 4, "exactly four alarms are required")
    by_name = {str(alarm.get("AlarmName")): alarm for alarm in alarms}
    _require(set(by_name) == set(EXPECTED_ALARMS), "alarm names mismatch")
    for name, expected in EXPECTED_ALARMS.items():
        alarm = by_name[name]
        _require(alarm.get("Namespace") == expected[0], f"{name} namespace mismatch")
        _require(alarm.get("MetricName") == expected[1], f"{name} metric mismatch")
        _require(alarm.get("Threshold") == expected[2], f"{name} threshold mismatch")
        _require(alarm.get("Period") == expected[3], f"{name} period mismatch")
        _require(alarm.get("EvaluationPeriods") == expected[4], f"{name} periods mismatch")
        _require(alarm.get("DatapointsToAlarm") == expected[5], f"{name} datapoints mismatch")
        dimensions = cast(list[dict[str, object]], alarm.get("Dimensions"))
        _require(
            isinstance(dimensions, list) and len(dimensions) == 2, f"{name} dimensions mismatch"
        )
        _require(
            all(
                str(item.get("Value", "")).startswith("<") or item.get("Value") == "howreliable-api"
                for item in dimensions
            ),
            f"{name} dimension values must be safe placeholders",
        )
    return value


def validate_task_log_configuration(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    containers = cast(list[dict[str, Any]], value.get("containerDefinitions"))
    _require(isinstance(containers, list) and len(containers) == 1, "one container is required")
    configuration = cast(dict[str, Any], containers[0].get("logConfiguration"))
    _require(configuration.get("logDriver") == "awslogs", "awslogs driver is required")
    options = cast(dict[str, object], configuration.get("options"))
    _require(options.get("awslogs-group") == LOG_GROUP, "awslogs group mismatch")
    _require(options.get("awslogs-region") == "<AWS_REGION>", "awslogs region mismatch")
    _require(options.get("awslogs-stream-prefix") == LOG_STREAM_PREFIX, "stream prefix mismatch")
    return value
