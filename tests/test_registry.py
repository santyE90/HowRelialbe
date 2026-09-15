"""Phase 5B local model-registry and inference-bundle tests."""

import dataclasses
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

from howreliable.api.service import PredictionService
from howreliable.modeling.registry import (
    API_COMPATIBILITY_VERSION,
    DEFAULT_BUNDLE_ID,
    REGISTRY_CONTRACT_VERSION,
    ArtifactChecksumError,
    ArtifactNotFoundError,
    ArtifactReference,
    ArtifactRole,
    ArtifactStore,
    BundleCompatibilityError,
    InferenceBundle,
    InferenceBundleManifest,
    LocalArtifactStore,
    ModelRegistry,
    UnknownBundleError,
    UnsafeArtifactReferenceError,
    canonical_manifest,
    load_inference_bundle,
    main,
)

ROOT = Path(".")
FIXED_TIME = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


class MemoryStore(ArtifactStore):
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def resolve(self, location: str) -> Path:
        return Path(location)

    def exists(self, location: str) -> bool:
        return location in self.values

    def checksum(self, location: str) -> str:
        if location not in self.values:
            raise ArtifactNotFoundError("missing")
        return hashlib.sha256(self.values[location]).hexdigest()

    def read_bytes(self, location: str) -> bytes:
        if location not in self.values:
            raise ArtifactNotFoundError("missing")
        return self.values[location]

    def list(self, prefix: str) -> tuple[str, ...]:
        expected = f"{prefix}/{DEFAULT_BUNDLE_ID}/manifest.json"
        return (DEFAULT_BUNDLE_ID,) if expected in self.values else ()


@pytest.fixture(scope="module")
def manifest() -> InferenceBundleManifest:
    return ModelRegistry(LocalArtifactStore(ROOT)).load_manifest(DEFAULT_BUNDLE_ID)


@pytest.fixture(scope="module")
def bundle() -> InferenceBundle:
    return load_inference_bundle(ModelRegistry(LocalArtifactStore(ROOT)), DEFAULT_BUNDLE_ID)


@pytest.fixture
def memory_values(manifest: InferenceBundleManifest) -> dict[str, bytes]:
    local = LocalArtifactStore(ROOT)
    base = f"artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}"
    values = {
        f"{base}/manifest.json": local.read_bytes(f"{base}/manifest.json"),
        f"{base}/manifest.sha256": local.read_bytes(f"{base}/manifest.sha256"),
    }
    values.update({item.location: local.read_bytes(item.location) for item in manifest.artifacts})
    return values


def _rewrite_manifest(values: dict[str, bytes], payload: dict[str, Any]) -> None:
    base = f"artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}"
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    values[f"{base}/manifest.json"] = encoded
    values[f"{base}/manifest.sha256"] = (hashlib.sha256(encoded).hexdigest() + "\n").encode()


def _replace_artifact(values: dict[str, bytes], role: ArtifactRole, content: bytes) -> None:
    base = f"artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}"
    payload = json.loads(values[f"{base}/manifest.json"])
    reference = next(item for item in payload["artifacts"] if item["role"] == role.value)
    values[reference["location"]] = content
    reference["sha256"] = hashlib.sha256(content).hexdigest()
    _rewrite_manifest(values, payload)


def test_registry_schema_version_roles_and_deterministic_serialization(
    manifest: InferenceBundleManifest,
) -> None:
    assert manifest.registry_contract_version == REGISTRY_CONTRACT_VERSION
    assert manifest.bundle_id == DEFAULT_BUNDLE_ID
    assert {item.role for item in manifest.artifacts} == set(ArtifactRole)
    first = canonical_manifest(ROOT, generation_utc=FIXED_TIME)
    second = canonical_manifest(ROOT, generation_utc=FIXED_TIME)
    assert first.model_dump_json() == second.model_dump_json()
    bad = first.model_dump()
    bad["registry_contract_version"] = "unsupported"
    with pytest.raises(ValidationError, match="unsupported registry"):
        InferenceBundleManifest.model_validate(bad)
    duplicate = first.model_dump()
    duplicate["artifacts"] = (*duplicate["artifacts"], duplicate["artifacts"][0])
    with pytest.raises(ValidationError, match="unique"):
        InferenceBundleManifest.model_validate(duplicate)


@pytest.mark.parametrize("location", ["/tmp/model.joblib", "../model.joblib", "C:\\model.joblib"])
def test_artifact_reference_rejects_unsafe_paths(location: str) -> None:
    with pytest.raises(ValidationError, match="safe relative"):
        ArtifactReference(role=ArtifactRole.MODEL, location=location, sha256="0" * 64)
    with pytest.raises(ValidationError, match="sha256"):
        ArtifactReference(role=ArtifactRole.MODEL, location="model.joblib", sha256="bad")


def test_local_store_is_portable_and_confined(tmp_path: Path) -> None:
    other_root = tmp_path / "relocated-project"
    artifact = other_root / "objects" / "value.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"portable")
    store = LocalArtifactStore(other_root)
    assert store.resolve("objects/value.bin") == artifact.resolve()
    assert store.exists("objects/value.bin")
    assert store.read_bytes("objects/value.bin") == b"portable"
    assert store.checksum("objects/value.bin") == hashlib.sha256(b"portable").hexdigest()
    with pytest.raises(UnsafeArtifactReferenceError):
        store.resolve("../outside.bin")


def test_explicit_lookup_unknown_rejection_and_no_latest(manifest: InferenceBundleManifest) -> None:
    registry = ModelRegistry(LocalArtifactStore(ROOT))
    assert registry.available_bundle_ids() == (DEFAULT_BUNDLE_ID,)
    assert registry.load_manifest(DEFAULT_BUNDLE_ID) == manifest
    with pytest.raises(UnknownBundleError):
        registry.load_manifest("unknown-bundle")
    assert not hasattr(registry, "get_latest")


def test_full_bundle_is_typed_immutable_and_read_only(bundle: InferenceBundle) -> None:
    assert isinstance(bundle, InferenceBundle)
    assert len(bundle.resources.rows_by_id) == 8_416
    assert bundle.manifest.model_status == "PREFERRED"
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.manifest = bundle.manifest  # type: ignore[misc]
    with pytest.raises(TypeError):
        bundle.resources.rows_by_id["new"] = {}  # type: ignore[index]


def test_manifest_checksum_mismatch_fails_before_parsing(memory_values: dict[str, bytes]) -> None:
    base = f"artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}/manifest.json"
    memory_values[base] += b" "
    with pytest.raises(ArtifactChecksumError, match="manifest"):
        ModelRegistry(MemoryStore(memory_values)).load_manifest(DEFAULT_BUNDLE_ID)


def test_missing_artifact_fails_closed(
    memory_values: dict[str, bytes], manifest: InferenceBundleManifest
) -> None:
    del memory_values[manifest.artifact(ArtifactRole.FEATURE_DATA).location]
    with pytest.raises(ArtifactNotFoundError, match="FEATURE_DATA"):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)


def test_model_checksum_is_checked_before_deserialization(
    memory_values: dict[str, bytes],
    manifest: InferenceBundleManifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = manifest.artifact(ArtifactRole.MODEL).location
    memory_values[location] += b"corrupt"
    called = False

    def forbidden(_source: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(joblib, "load", forbidden)
    with pytest.raises(ArtifactChecksumError, match="MODEL"):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)
    assert called is False


def test_json_checksum_mismatch_and_checksum_valid_corruption(
    memory_values: dict[str, bytes], manifest: InferenceBundleManifest
) -> None:
    location = manifest.artifact(ArtifactRole.API_CONTRACT).location
    memory_values[location] = b"{"
    with pytest.raises(ArtifactChecksumError, match="API_CONTRACT"):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)
    _replace_artifact(memory_values, ArtifactRole.API_CONTRACT, b"{")
    with pytest.raises(BundleCompatibilityError, match="valid JSON"):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)


def test_checksum_valid_cross_artifact_model_mismatch(memory_values: dict[str, bytes]) -> None:
    manifest = ModelRegistry(MemoryStore(memory_values)).load_manifest(DEFAULT_BUNDLE_ID)
    role = ArtifactRole.PREFERRED_MODEL_HANDOFF
    handoff = json.loads(memory_values[manifest.artifact(role).location])
    handoff["preferred_model_identifier"] = "different-model"
    _replace_artifact(memory_values, role, json.dumps(handoff).encode())
    with pytest.raises(BundleCompatibilityError, match="handoff model"):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_identifier", "other-model", "model identifier"),
        ("target_version", "other-target", "target version"),
        ("history_cutoff", "2021-12-31", "history cutoff"),
        ("future_window_start", "2024-01-01", "future window start"),
        ("future_window_end", "2024-12-31", "future window end"),
        ("threshold", 0.4, "threshold"),
        ("prediction_grain", "individual_vehicle", "grain"),
        ("api_compatibility_version", "howreliable-api-2.0", "API compatibility"),
        ("supported_cohort_count", 1, "cohort count"),
    ],
)
def test_semantically_incompatible_manifest_fails_closed(
    memory_values: dict[str, bytes], field: str, value: object, message: str
) -> None:
    base = f"artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}/manifest.json"
    payload = json.loads(memory_values[base])
    payload[field] = value
    _rewrite_manifest(memory_values, payload)
    with pytest.raises(BundleCompatibilityError, match=message):
        load_inference_bundle(ModelRegistry(MemoryStore(memory_values)), DEFAULT_BUNDLE_ID)


def test_prediction_service_consumes_bundle_and_preserves_result(
    bundle: InferenceBundle,
) -> None:
    service = PredictionService(ROOT, bundle)
    cohort_id = next(iter(bundle.resources.rows_by_id))
    result = service.predict(cohort_id)
    assert result.prediction.predicted_future_complaint_probability >= 0
    assert result.explanation.reconstruction_absolute_error < 1e-12
    assert result.evaluation_context is None


def test_cli_validates_explicit_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", DEFAULT_BUNDLE_ID, "--root", str(ROOT)]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["validation"] == "passed"
    assert value["bundle_id"] == DEFAULT_BUNDLE_ID


def test_trusted_deserialization_and_local_only_scope_are_explicit() -> None:
    source = Path("src/howreliable/modeling/registry.py").read_text().casefold()
    assert "trusted project input" in source
    assert "does not make hostile pickle safe" in source
    assert ".fit(" not in source
    assert "boto3" not in source and "s3artifactstore" not in source
    assert "requests." not in source and "httpx" not in source
    assert API_COMPATIBILITY_VERSION == "howreliable-api-1.0"
