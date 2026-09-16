"""Phase 6A S3 storage, publication, and backend-equivalence tests."""

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import joblib  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

import howreliable.api.service as api_service
import howreliable.cloud.s3 as s3_module
from howreliable.api.app import create_app
from howreliable.api.service import ArtifactContractError, PredictionService
from howreliable.cloud.s3 import (
    CANONICAL_MANIFEST_SHA256,
    PublishConflictError,
    S3ArtifactStore,
    S3ObjectLocation,
    S3StoreError,
    generate_s3_contract_artifact,
    publish_bundle,
    validate_remote_bundle,
)
from howreliable.cloud.s3 import (
    main as s3_main,
)
from howreliable.config.settings import Settings
from howreliable.modeling.registry import (
    DEFAULT_BUNDLE_ID,
    ArtifactChecksumError,
    ArtifactNotFoundError,
    ArtifactRole,
    InferenceBundle,
    LocalArtifactStore,
    ModelRegistry,
    RegistryError,
    UnsafeArtifactReferenceError,
    load_inference_bundle,
)

ROOT = Path(".")
BUCKET = "private-howreliable-artifacts"
PREFIX = "project/frozen"
FIXED_TIME = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "sanitized test error"}}, operation)


class FakeS3Client:
    """Small deterministic fake implementing exactly the boto3 methods used by Phase 6A."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.calls: list[tuple[str, str]] = []
        self.put_keys: list[str] = []
        self.put_arguments: list[dict[str, Any]] = []
        self.denied: set[str] = set()

    def _allow(self, operation: str) -> None:
        if operation in self.denied:
            raise _client_error("AccessDenied", operation)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, str]:
        self._allow("HeadObject")
        self.calls.append(("head", Key))
        if Key not in self.objects:
            raise _client_error("404", "HeadObject")
        return {"ETag": '"etag-is-not-a-sha256"'}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        self._allow("GetObject")
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise _client_error("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self._allow("ListObjectsV2")
        prefix = cast(str, kwargs["Prefix"])
        self.calls.append(("list", prefix))
        children = {
            key[len(prefix) :].split("/", 1)[0]
            for key in self.objects
            if key.startswith(prefix) and "/" in key[len(prefix) :]
        }
        return {
            "CommonPrefixes": [{"Prefix": f"{prefix}{child}/"} for child in sorted(children)],
            "IsTruncated": False,
        }

    def put_object(self, **kwargs: object) -> dict[str, str]:
        self._allow("PutObject")
        key = cast(str, kwargs["Key"])
        body = bytes(cast(bytes, kwargs["Body"]))
        self.calls.append(("put", key))
        self.put_keys.append(key)
        self.put_arguments.append(dict(kwargs))
        self.objects[key] = body
        return {"ETag": '"still-not-a-sha256"'}


def _store(client: FakeS3Client | None = None) -> S3ArtifactStore:
    return S3ArtifactStore(
        bucket=BUCKET,
        prefix=PREFIX,
        region="ca-central-1",
        client=client or FakeS3Client(),
    )


@pytest.fixture(scope="module")
def published_client() -> FakeS3Client:
    client = FakeS3Client()
    report = publish_bundle(ROOT, _store(client), DEFAULT_BUNDLE_ID)
    assert len(report.uploaded) == 14
    return client


@pytest.fixture(scope="module")
def remote_bundle(published_client: FakeS3Client) -> InferenceBundle:
    return load_inference_bundle(ModelRegistry(_store(published_client)), DEFAULT_BUNDLE_ID)


@pytest.fixture(scope="module")
def local_bundle() -> InferenceBundle:
    return load_inference_bundle(ModelRegistry(LocalArtifactStore(ROOT)), DEFAULT_BUNDLE_ID)


def test_settings_default_and_s3_configuration_validation() -> None:
    defaults = Settings.from_env({})
    assert defaults.artifact_backend == "local"
    assert defaults.model_bundle_id == DEFAULT_BUNDLE_ID
    configured = Settings.from_env(
        {
            "HOWRELIABLE_ARTIFACT_BACKEND": " S3 ",
            "HOWRELIABLE_S3_BUCKET": BUCKET,
            "HOWRELIABLE_S3_PREFIX": "/project/frozen/",
            "HOWRELIABLE_AWS_REGION": "ca-central-1",
        }
    )
    assert configured.s3_prefix == PREFIX
    assert configured.aws_region == "ca-central-1"
    with pytest.raises(ValueError, match="BUCKET"):
        Settings.from_env({"HOWRELIABLE_ARTIFACT_BACKEND": "s3"})
    with pytest.raises(ValueError, match="local or s3"):
        Settings.from_env({"HOWRELIABLE_ARTIFACT_BACKEND": "automatic"})
    with pytest.raises(ValueError, match="safe relative"):
        Settings.from_env(
            {
                "HOWRELIABLE_ARTIFACT_BACKEND": "s3",
                "HOWRELIABLE_S3_BUCKET": BUCKET,
                "HOWRELIABLE_S3_PREFIX": "project/../escape",
            }
        )


@pytest.mark.parametrize(
    "location",
    [
        "/absolute/key",
        "../escape",
        "nested/../escape",
        "a//b",
        "a/./b",
        "C:\\key",
        "s3://bucket/key",
    ],
)
def test_s3_logical_key_safety(location: str) -> None:
    with pytest.raises(UnsafeArtifactReferenceError):
        _store().resolve(location)


def test_s3_resolve_exists_read_checksum_and_list_do_not_trust_etag() -> None:
    client = FakeS3Client(
        {
            f"{PREFIX}/objects/value.bin": b"downloaded bytes",
            f"{PREFIX}/registry/bundles/a/manifest.json": b"{}",
            f"{PREFIX}/registry/bundles/b/manifest.json": b"{}",
        }
    )
    store = _store(client)
    assert store.resolve("objects/value.bin") == S3ObjectLocation(
        bucket=BUCKET, key=f"{PREFIX}/objects/value.bin"
    )
    assert store.exists("objects/value.bin")
    assert not store.exists("objects/missing.bin")
    assert store.read_bytes("objects/value.bin") == b"downloaded bytes"
    assert store.checksum("objects/value.bin") == hashlib.sha256(b"downloaded bytes").hexdigest()
    assert store.list("registry/bundles") == ("a", "b")
    assert "etag-is-not-a-sha256" not in store.checksum("objects/value.bin")


def test_publish_order_layout_encryption_metadata_and_idempotency(
    published_client: FakeS3Client,
) -> None:
    expected_base = f"{PREFIX}/artifacts/registry/bundles/{DEFAULT_BUNDLE_ID}"
    assert published_client.put_keys[-2:] == [
        f"{expected_base}/manifest.json",
        f"{expected_base}/manifest.sha256",
    ]
    assert len(published_client.put_keys[:-2]) == 12
    assert all(item["ServerSideEncryption"] == "AES256" for item in published_client.put_arguments)
    assert all(
        "sha256" in cast(dict[str, str], item["Metadata"])
        for item in published_client.put_arguments
    )
    before = list(published_client.put_keys)
    report = publish_bundle(ROOT, _store(published_client), DEFAULT_BUNDLE_ID)
    assert report.uploaded == ()
    assert len(report.already_present) == 14
    assert published_client.put_keys == before


def test_conflicting_remote_bytes_fail_closed(published_client: FakeS3Client) -> None:
    client = FakeS3Client(published_client.objects)
    manifest = ModelRegistry(_store(client)).load_manifest(DEFAULT_BUNDLE_ID)
    location = manifest.artifact(ArtifactRole.MODEL).location
    client.objects[f"{PREFIX}/{location}"] += b"conflict"
    with pytest.raises(PublishConflictError, match="conflicts"):
        publish_bundle(ROOT, _store(client), DEFAULT_BUNDLE_ID)


def test_publish_validates_local_bundle_before_any_remote_write(tmp_path: Path) -> None:
    client = FakeS3Client()
    with pytest.raises(RegistryError):
        publish_bundle(tmp_path, _store(client), DEFAULT_BUNDLE_ID)
    assert client.calls == []


def test_remote_manifest_all_roles_and_typed_bundle(remote_bundle: InferenceBundle) -> None:
    assert isinstance(remote_bundle, InferenceBundle)
    assert remote_bundle.manifest.bundle_id == DEFAULT_BUNDLE_ID
    assert {item.role for item in remote_bundle.manifest.artifacts} == set(ArtifactRole)
    assert len(remote_bundle.resources.rows_by_id) == 8_416


def test_local_and_s3_bundle_and_prediction_equivalence(
    local_bundle: InferenceBundle, remote_bundle: InferenceBundle
) -> None:
    assert local_bundle.manifest == remote_bundle.manifest
    assert (
        local_bundle.manifest.artifact(ArtifactRole.MODEL).sha256
        == remote_bundle.manifest.artifact(ArtifactRole.MODEL).sha256
    )
    local_service = PredictionService(ROOT, local_bundle)
    remote_service = PredictionService(ROOT, remote_bundle)
    examples = json.loads(Path("artifacts/presentation/representative-results.json").read_text())
    for example in examples["examples"]:
        cohort_id = cast(str, example["identity"]["cohort_id"])
        local_result = local_service.predict(cohort_id)
        remote_result = remote_service.predict(cohort_id)
        local_value = local_result.model_dump(mode="json")
        remote_value = remote_result.model_dump(mode="json")
        cast(dict[str, object], local_value["provenance"]).pop("generation_utc")
        cast(dict[str, object], remote_value["provenance"]).pop("generation_utc")
        assert local_value == remote_value
        assert local_result.explanation.reconstructed_probability == pytest.approx(
            remote_result.explanation.reconstructed_probability, abs=1e-12
        )
        assert local_result.limitation_flags == remote_result.limitation_flags


def test_remote_model_corruption_fails_before_deserialization(
    published_client: FakeS3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeS3Client(published_client.objects)
    manifest = ModelRegistry(_store(client)).load_manifest(DEFAULT_BUNDLE_ID)
    location = manifest.artifact(ArtifactRole.MODEL).location
    client.objects[f"{PREFIX}/{location}"] += b"corrupt"
    called = False

    def forbidden(_source: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(joblib, "load", forbidden)
    with pytest.raises(ArtifactChecksumError, match="MODEL"):
        load_inference_bundle(ModelRegistry(_store(client)), DEFAULT_BUNDLE_ID)
    assert called is False


def test_remote_json_corruption_and_missing_artifact_fail_closed(
    published_client: FakeS3Client,
) -> None:
    corrupt = FakeS3Client(published_client.objects)
    manifest = ModelRegistry(_store(corrupt)).load_manifest(DEFAULT_BUNDLE_ID)
    json_location = manifest.artifact(ArtifactRole.API_CONTRACT).location
    corrupt.objects[f"{PREFIX}/{json_location}"] = b"{"
    with pytest.raises(ArtifactChecksumError, match="API_CONTRACT"):
        load_inference_bundle(ModelRegistry(_store(corrupt)), DEFAULT_BUNDLE_ID)

    missing = FakeS3Client(published_client.objects)
    feature_location = manifest.artifact(ArtifactRole.FEATURE_DATA).location
    del missing.objects[f"{PREFIX}/{feature_location}"]
    with pytest.raises(ArtifactNotFoundError, match="FEATURE_DATA"):
        load_inference_bundle(ModelRegistry(_store(missing)), DEFAULT_BUNDLE_ID)


def test_access_denied_is_sanitized_and_s3_has_no_local_fallback(
    published_client: FakeS3Client,
) -> None:
    denied = FakeS3Client(published_client.objects)
    denied.denied.add("ListObjectsV2")
    with pytest.raises(S3StoreError, match="S3 LIST") as raised:
        load_inference_bundle(ModelRegistry(_store(denied)), DEFAULT_BUNDLE_ID)
    assert BUCKET not in str(raised.value)
    empty = _store(FakeS3Client())
    settings = Settings(artifact_backend="s3", s3_bucket=BUCKET, s3_prefix=PREFIX)
    with pytest.raises(ArtifactContractError):
        PredictionService.load(ROOT, settings=settings, store=empty)


def test_explicit_s3_backend_selection_and_load_once(
    published_client: FakeS3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _store(published_client)
    constructions: list[dict[str, object]] = []

    def construct(_root: Path, selected_settings: Settings) -> S3ArtifactStore:
        constructions.append(
            {
                "bucket": selected_settings.s3_bucket,
                "prefix": selected_settings.s3_prefix,
                "region": selected_settings.aws_region,
            }
        )
        return selected

    monkeypatch.setattr(api_service, "artifact_store_from_settings", construct)
    settings = Settings(
        artifact_backend="s3",
        model_bundle_id=DEFAULT_BUNDLE_ID,
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        aws_region="ca-central-1",
    )
    service = PredictionService.load(ROOT, settings=settings)
    calls_after_load = len(published_client.calls)
    cohort_id = next(iter(service._bundle.resources.rows_by_id))
    service.predict(cohort_id)
    assert len(published_client.calls) == calls_after_load
    assert constructions == [{"bucket": BUCKET, "prefix": PREFIX, "region": "ca-central-1"}]


def test_api_factory_s3_backend_mocked_smoke(
    published_client: FakeS3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _store(published_client)
    selections = 0

    def construct(_root: Path, settings: Settings) -> S3ArtifactStore:
        nonlocal selections
        selections += 1
        assert settings.artifact_backend == "s3"
        return selected

    monkeypatch.setattr(api_service, "artifact_store_from_settings", construct)
    monkeypatch.setenv("HOWRELIABLE_ARTIFACT_BACKEND", "s3")
    monkeypatch.setenv("HOWRELIABLE_S3_BUCKET", BUCKET)
    monkeypatch.setenv("HOWRELIABLE_S3_PREFIX", PREFIX)
    client = TestClient(create_app(root=ROOT))
    calls_after_startup = len(published_client.calls)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/model").status_code == 200
    assert selections == 1
    assert len(published_client.calls) == calls_after_startup


def test_remote_validation_and_inspection_result(published_client: FakeS3Client) -> None:
    result = validate_remote_bundle(_store(published_client), DEFAULT_BUNDLE_ID)
    assert result["validation"] == "passed"
    assert result["bundle_id"] == DEFAULT_BUNDLE_ID
    assert len(cast(list[str], result["artifact_roles"])) == 12


def test_remote_validation_cli_uses_injected_store(
    published_client: FakeS3Client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(s3_module, "_configured_store", lambda _args: _store(published_client))
    assert s3_main(["validate-bundle", DEFAULT_BUNDLE_ID]) == 0
    assert json.loads(capsys.readouterr().out)["validation"] == "passed"


def test_s3_contract_is_deterministic_and_matches_canonical_artifact(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_s3_contract_artifact(first, clock=lambda: FIXED_TIME)
    generate_s3_contract_artifact(second, clock=lambda: FIXED_TIME)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == Path("artifacts/cloud/s3-contract.json").read_bytes()
    value = json.loads(first.read_text())
    assert value["manifest_sha256"] == CANONICAL_MANIFEST_SHA256
    assert value["publication_order"][-1] == "manifest.sha256"


def test_reader_policy_is_least_privilege_private_and_prefix_scoped() -> None:
    policy = json.loads(Path("infrastructure/iam/howreliable-s3-reader-policy.json").read_text())
    statements = policy["Statement"]
    actions = {statement["Action"] for statement in statements}
    assert actions == {"s3:GetObject", "s3:ListBucket"}
    assert not actions & {"s3:*", "s3:PutObject", "s3:DeleteObject"}
    assert all("<bucket-name>" in statement["Resource"] for statement in statements)
    serialized = json.dumps(policy).casefold()
    assert "public" not in serialized and "presign" not in serialized
