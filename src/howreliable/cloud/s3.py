"""Checksum-first S3 storage and explicit frozen-bundle publication tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from howreliable.config.settings import DEFAULT_MODEL_BUNDLE_ID
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError
from howreliable.modeling.presentation import RESULT_CONTRACT_VERSION
from howreliable.modeling.registry import (
    API_COMPATIBILITY_VERSION,
    REGISTRY_CONTRACT_VERSION,
    ArtifactNotFoundError,
    ArtifactRole,
    ArtifactStore,
    LocalArtifactStore,
    ModelRegistry,
    RegistryError,
    UnsafeArtifactReferenceError,
    load_inference_bundle,
    validate_artifact_location,
)

S3_CONTRACT_VERSION: Final = "howreliable-s3-1.0"
CANONICAL_MANIFEST_SHA256: Final = (
    "831ecaa2cf31102bb13d2e414a380465cb948b07c45f9a4acb225e40aa2159cc"
)
MANIFEST_BASE: Final = "artifacts/registry/bundles"
NOT_FOUND_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})
LOGGER = logging.getLogger("howreliable.cloud.s3")


def _atomic_write_json(path: Path, value: object) -> None:
    """Atomically write stable UTF-8 JSON with platform-independent newlines."""
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


class S3StoreError(RegistryError):
    """An S3 operation failed without exposing provider or credential details."""


class PublishConflictError(S3StoreError):
    """A frozen logical key already contains different remote bytes."""


@dataclass(frozen=True, slots=True)
class S3ObjectLocation:
    """Non-public resolved object identity; deliberately not a URL."""

    bucket: str
    key: str


@dataclass(frozen=True, slots=True)
class PublishReport:
    """Safe deterministic publication result."""

    bundle_id: str
    uploaded: tuple[str, ...]
    already_present: tuple[str, ...]
    validation: str = "passed"

    def as_dict(self) -> dict[str, object]:
        return {
            "already_present": list(self.already_present),
            "bundle_id": self.bundle_id,
            "uploaded": list(self.uploaded),
            "validation": self.validation,
        }


def _error_code(error: ClientError) -> str:
    response = getattr(error, "response", {})
    details = response.get("Error", {}) if isinstance(response, dict) else {}
    return str(details.get("Code", "Unknown")) if isinstance(details, dict) else "Unknown"


class S3ArtifactStore(ArtifactStore):
    """S3-backed store for safe relative logical artifact keys."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        normalized = prefix.strip().strip("/")
        if normalized:
            try:
                validate_artifact_location(normalized)
            except ValueError as error:
                raise ValueError("S3 prefix must be a safe relative key prefix") from error
        self.bucket = bucket.strip()
        self.prefix = normalized
        self.region = region
        self._client = client or boto3.client("s3", region_name=region)

    @property
    def client(self) -> Any:
        """Return the injected SDK client for publication tooling."""
        return self._client

    def resolve(self, location: str) -> S3ObjectLocation:
        try:
            safe_location = validate_artifact_location(location)
        except ValueError as error:
            raise UnsafeArtifactReferenceError("unsafe artifact reference") from error
        key = f"{self.prefix}/{safe_location}" if self.prefix else safe_location
        return S3ObjectLocation(bucket=self.bucket, key=key)

    def exists(self, location: str) -> bool:
        resolved = self.resolve(location)
        try:
            self._client.head_object(Bucket=resolved.bucket, Key=resolved.key)
        except ClientError as error:
            if _error_code(error) in NOT_FOUND_CODES:
                return False
            LOGGER.warning("S3 artifact HEAD failed", extra={"error_code": _error_code(error)})
            raise S3StoreError("S3 HEAD failed for configured artifact") from error
        except BotoCoreError as error:
            raise S3StoreError("S3 HEAD failed for configured artifact") from error
        return True

    def read_bytes(self, location: str) -> bytes:
        resolved = self.resolve(location)
        try:
            response = self._client.get_object(Bucket=resolved.bucket, Key=resolved.key)
            return bytes(response["Body"].read())
        except ClientError as error:
            if _error_code(error) in NOT_FOUND_CODES:
                raise ArtifactNotFoundError("required S3 artifact is missing") from error
            LOGGER.warning("S3 artifact GET failed", extra={"error_code": _error_code(error)})
            raise S3StoreError("S3 GET failed for configured artifact") from error
        except BotoCoreError as error:
            raise S3StoreError("S3 GET failed for configured artifact") from error
        except (KeyError, AttributeError, TypeError) as error:
            raise S3StoreError("S3 returned an invalid object response") from error

    def checksum(self, location: str) -> str:
        """Hash downloaded object bytes; S3 ETag and metadata are never trusted."""
        return hashlib.sha256(self.read_bytes(location)).hexdigest()

    def list(self, prefix: str) -> tuple[str, ...]:
        resolved = self.resolve(prefix)
        object_prefix = resolved.key.rstrip("/") + "/"
        names: set[str] = set()
        continuation: str | None = None
        try:
            while True:
                arguments: dict[str, object] = {
                    "Bucket": resolved.bucket,
                    "Prefix": object_prefix,
                    "Delimiter": "/",
                }
                if continuation is not None:
                    arguments["ContinuationToken"] = continuation
                response = self._client.list_objects_v2(**arguments)
                for item in response.get("CommonPrefixes", []):
                    child = str(item["Prefix"])[len(object_prefix) :].rstrip("/")
                    if child and "/" not in child:
                        names.add(child)
                if not response.get("IsTruncated"):
                    break
                continuation = str(response["NextContinuationToken"])
        except ClientError as error:
            LOGGER.warning("S3 artifact LIST failed", extra={"error_code": _error_code(error)})
            raise S3StoreError("S3 LIST failed for configured artifact prefix") from error
        except BotoCoreError as error:
            raise S3StoreError("S3 LIST failed for configured artifact prefix") from error
        return tuple(sorted(names))

    def put_bytes(
        self,
        location: str,
        content: bytes,
        *,
        sha256: str,
        bundle_id: str,
        role: str,
    ) -> None:
        """Write one publication object using AWS-managed server-side encryption."""
        resolved = self.resolve(location)
        try:
            self._client.put_object(
                Bucket=resolved.bucket,
                Key=resolved.key,
                Body=content,
                ServerSideEncryption="AES256",
                Metadata={"sha256": sha256, "bundle-id": bundle_id, "artifact-role": role},
            )
        except ClientError as error:
            LOGGER.warning("S3 artifact PUT failed", extra={"error_code": _error_code(error)})
            raise S3StoreError("S3 PUT failed for configured artifact") from error
        except BotoCoreError as error:
            raise S3StoreError("S3 PUT failed for configured artifact") from error


def _publication_objects(root: Path, bundle_id: str) -> tuple[tuple[str, bytes, str, str], ...]:
    local = LocalArtifactStore(root)
    bundle = load_inference_bundle(ModelRegistry(local), bundle_id)
    objects = [
        (
            reference.location,
            local.read_bytes(reference.location),
            reference.sha256,
            reference.role.value,
        )
        for reference in bundle.manifest.artifacts
    ]
    base = f"{MANIFEST_BASE}/{bundle_id}"
    manifest_location = f"{base}/manifest.json"
    marker_location = f"{base}/manifest.sha256"
    manifest_bytes = local.read_bytes(manifest_location)
    marker_bytes = local.read_bytes(marker_location)
    objects.append(
        (manifest_location, manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest(), "MANIFEST")
    )
    objects.append(
        (marker_location, marker_bytes, hashlib.sha256(marker_bytes).hexdigest(), "CHECKSUM_MARKER")
    )
    return tuple(objects)


def publish_bundle(root: Path, store: S3ArtifactStore, bundle_id: str) -> PublishReport:
    """Validate locally, publish without overwrite, then validate the complete remote bundle."""
    LOGGER.info("S3 bundle publication started", extra={"bundle_id": bundle_id})
    objects = _publication_objects(root, bundle_id)
    uploaded: list[str] = []
    already_present: list[str] = []
    for location, content, expected, role in objects:
        if store.exists(location):
            if store.checksum(location) != expected:
                raise PublishConflictError("remote object conflicts with frozen local bytes")
            already_present.append(location)
            continue
        store.put_bytes(
            location,
            content,
            sha256=expected,
            bundle_id=bundle_id,
            role=role,
        )
        if store.checksum(location) != expected:
            raise S3StoreError("uploaded object failed remote byte verification")
        uploaded.append(location)
    load_inference_bundle(ModelRegistry(store), bundle_id)
    LOGGER.info(
        "S3 bundle publication and remote validation succeeded",
        extra={"bundle_id": bundle_id},
    )
    return PublishReport(bundle_id, tuple(uploaded), tuple(already_present))


def validate_remote_bundle(store: S3ArtifactStore, bundle_id: str) -> dict[str, object]:
    """Retrieve and completely validate one explicit remote bundle."""
    LOGGER.info("S3 bundle validation started", extra={"bundle_id": bundle_id})
    bundle = load_inference_bundle(ModelRegistry(store), bundle_id)
    LOGGER.info("S3 bundle validation succeeded", extra={"bundle_id": bundle_id})
    return {
        "artifact_roles": [item.role.value for item in bundle.manifest.artifacts],
        "bundle_id": bundle.manifest.bundle_id,
        "model_identifier": bundle.manifest.model_identifier,
        "model_status": bundle.manifest.model_status,
        "prediction_grain": bundle.manifest.prediction_grain,
        "registry_contract_version": bundle.manifest.registry_contract_version,
        "supported_cohort_count": bundle.manifest.supported_cohort_count,
        "target_version": bundle.manifest.target_version,
        "threshold": bundle.manifest.threshold,
        "validation": "passed",
    }


def generate_s3_contract_artifact(
    output: Path,
    *,
    regenerate: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    """Persist the deterministic machine-readable Phase 6A storage contract."""
    if output.exists() and not regenerate:
        raise ArtifactExistsError(f"refusing to overwrite S3 contract: {output}")
    contract: dict[str, object] = {
        "api_compatibility_version": API_COMPATIBILITY_VERSION,
        "artifact_roles": [role.value for role in ArtifactRole],
        "canonical_bundle_id": DEFAULT_MODEL_BUNDLE_ID,
        "checksum_algorithm": "SHA-256 over downloaded bytes",
        "cloud_contract_version": S3_CONTRACT_VERSION,
        "generation_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "iam_actions_required": ["s3:GetObject", "s3:ListBucket"],
        "logical_key_layout": {
            "artifacts": "<prefix>/<manifest artifact location>",
            "manifest": f"<prefix>/{MANIFEST_BASE}/{DEFAULT_MODEL_BUNDLE_ID}/manifest.json",
            "manifest_checksum": (
                f"<prefix>/{MANIFEST_BASE}/{DEFAULT_MODEL_BUNDLE_ID}/manifest.sha256"
            ),
        },
        "manifest_sha256": CANONICAL_MANIFEST_SHA256,
        "overwrite_behavior": "fail on conflicting bytes; matching bytes are idempotent",
        "publication_order": [
            "registered artifacts",
            "manifest.json",
            "manifest.sha256",
        ],
        "registry_contract_version": REGISTRY_CONTRACT_VERSION,
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "supported_backends": ["local", "s3"],
    }
    _atomic_write_json(output, contract)
    return contract


def _configured_store(args: argparse.Namespace) -> S3ArtifactStore:
    bucket = args.bucket or os.environ.get("HOWRELIABLE_S3_BUCKET", "").strip()
    if not bucket:
        raise ValueError("S3 bucket is required via --bucket or HOWRELIABLE_S3_BUCKET")
    prefix = args.prefix
    if prefix is None:
        prefix = os.environ.get("HOWRELIABLE_S3_PREFIX", "")
    region = args.region or os.environ.get("HOWRELIABLE_AWS_REGION") or None
    return S3ArtifactStore(bucket=bucket, prefix=prefix, region=region)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish, validate, or inspect an S3 bundle")
    parser.add_argument(
        "operation", choices=("publish-bundle", "validate-bundle", "inspect-bundle")
    )
    parser.add_argument("bundle_id")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--bucket")
    parser.add_argument("--prefix")
    parser.add_argument("--region")
    args = parser.parse_args(argv)
    store = _configured_store(args)
    if args.operation == "publish-bundle":
        result: dict[str, object] = publish_bundle(args.root, store, args.bundle_id).as_dict()
    else:
        result = validate_remote_bundle(store, args.bundle_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
