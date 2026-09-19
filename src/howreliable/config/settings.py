"""Environment-backed settings for the project foundation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

DEFAULT_ENVIRONMENT: Final = "development"
DEFAULT_LOG_LEVEL: Final = "INFO"
DEFAULT_LOG_FORMAT: Final = "text"
DEFAULT_ARTIFACT_BACKEND: Final = "local"
DEFAULT_MODEL_BUNDLE_ID: Final = "howreliable-rf-2022-cutoff-v1"
VALID_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_LOG_FORMATS: Final = frozenset({"json", "text"})
VALID_ARTIFACT_BACKENDS: Final = frozenset({"local", "s3"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime settings with deterministic defaults."""

    environment: str = DEFAULT_ENVIRONMENT
    log_level: str = DEFAULT_LOG_LEVEL
    log_format: str = DEFAULT_LOG_FORMAT
    artifact_backend: str = DEFAULT_ARTIFACT_BACKEND
    model_bundle_id: str = DEFAULT_MODEL_BUNDLE_ID
    s3_bucket: str | None = None
    s3_prefix: str = ""
    aws_region: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Create settings from a supplied mapping or the process environment."""
        values = os.environ if environ is None else environ
        environment = values.get("HOWRELIABLE_ENVIRONMENT", DEFAULT_ENVIRONMENT).strip()
        log_level = values.get("HOWRELIABLE_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        log_format = values.get("HOWRELIABLE_LOG_FORMAT", DEFAULT_LOG_FORMAT).strip().lower()
        artifact_backend = (
            values.get("HOWRELIABLE_ARTIFACT_BACKEND", DEFAULT_ARTIFACT_BACKEND).strip().lower()
        )
        model_bundle_id = values.get("HOWRELIABLE_MODEL_BUNDLE_ID", DEFAULT_MODEL_BUNDLE_ID).strip()
        s3_bucket = values.get("HOWRELIABLE_S3_BUCKET", "").strip() or None
        s3_prefix = values.get("HOWRELIABLE_S3_PREFIX", "").strip().strip("/")
        aws_region = values.get("HOWRELIABLE_AWS_REGION", "").strip() or None

        if not environment:
            raise ValueError("HOWRELIABLE_ENVIRONMENT must not be empty")
        if log_level not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"HOWRELIABLE_LOG_LEVEL must be one of: {allowed}")
        if log_format not in VALID_LOG_FORMATS:
            raise ValueError("HOWRELIABLE_LOG_FORMAT must be json or text")

        if artifact_backend not in VALID_ARTIFACT_BACKENDS:
            raise ValueError("HOWRELIABLE_ARTIFACT_BACKEND must be local or s3")
        if not model_bundle_id:
            raise ValueError("HOWRELIABLE_MODEL_BUNDLE_ID must not be empty")
        if artifact_backend == "s3" and s3_bucket is None:
            raise ValueError("HOWRELIABLE_S3_BUCKET is required for the s3 backend")
        if s3_prefix and (
            "\\" in s3_prefix
            or any(part in {"", ".", ".."} for part in s3_prefix.split("/"))
            or s3_prefix.casefold().startswith("s3://")
        ):
            raise ValueError("HOWRELIABLE_S3_PREFIX must be a safe relative key prefix")

        return cls(
            environment=environment,
            log_level=log_level,
            log_format=log_format,
            artifact_backend=artifact_backend,
            model_bundle_id=model_bundle_id,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            aws_region=aws_region,
        )
