"""Reusable standard-library logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Final, TextIO

from howreliable.config import Settings

LOGGER_NAME: Final = "howreliable"
LOG_FORMAT: Final = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%z"
DEFAULT_EVENT: Final = "log_message"
STABLE_EVENTS: Final = (
    "service_starting",
    "artifact_backend_selected",
    "bundle_load_started",
    "bundle_load_succeeded",
    "bundle_load_failed",
    "service_ready",
    "request_completed",
    "request_failed",
)
SAFE_CONTEXT_FIELDS: Final = (
    "api_version",
    "artifact_backend",
    "bundle_id",
    "bundle_load_duration_ms",
    "deployment_contract_version",
    "duration_ms",
    "error_category",
    "lifecycle_stage",
    "method",
    "model_identifier",
    "registry_version",
    "request_id",
    "route",
    "status_code",
    "supported_cohort_count",
)


class JsonLogFormatter(logging.Formatter):
    """Serialize a bounded allowlist of operational fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", DEFAULT_EVENT),
            "message": record.getMessage(),
        }
        for field in SAFE_CONTEXT_FIELDS:
            context = getattr(record, field, None)
            if isinstance(context, str | int | float | bool):
                value[field] = context
        if record.exc_info is not None and record.exc_info[0] is not None:
            value["exception_type"] = record.exc_info[0].__name__
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def configure_logging(
    settings: Settings | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the project logger without modifying the root logger."""
    resolved_settings = settings or Settings.from_env()
    if resolved_settings.log_format not in {"json", "text"}:
        raise ValueError("log format must be json or text")
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_settings.log_level)
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stderr)
    if resolved_settings.log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    logger.handlers.clear()
    logger.addHandler(handler)
    return logger
