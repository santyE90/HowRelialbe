"""Explicit artifact-store construction from validated application settings."""

from pathlib import Path

from howreliable.cloud.s3 import S3ArtifactStore
from howreliable.config.settings import Settings
from howreliable.modeling.registry import ArtifactStore, LocalArtifactStore


def artifact_store_from_settings(root: Path, settings: Settings) -> ArtifactStore:
    """Construct exactly the selected backend; never fall back between stores."""
    if settings.artifact_backend == "local":
        return LocalArtifactStore(root)
    if settings.s3_bucket is None:  # protected by Settings.from_env validation
        raise ValueError("S3 artifact backend requires a bucket")
    return S3ArtifactStore(
        bucket=settings.s3_bucket,
        prefix=settings.s3_prefix,
        region=settings.aws_region,
    )
