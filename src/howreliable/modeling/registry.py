"""Checksum-first local registry and frozen inference-bundle loader."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Final, cast

import joblib  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from howreliable.data.ingestion.nhtsa_complaints import sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.evaluation_report import EVALUATION_VERSION
from howreliable.modeling.explainability import EXPLAINABILITY_VERSION, feature_manifest
from howreliable.modeling.features import FEATURE_SCHEMA
from howreliable.modeling.presentation import (
    EVALUATION_REPORT_CHECKSUM,
    GENERAL_LIMITATION,
    MODEL_CHECKSUM,
    MODEL_IDENTIFIER,
    MODEL_STATUS,
    RESULT_CONTRACT_VERSION,
    THRESHOLD,
    PresentationResources,
)

REGISTRY_CONTRACT_VERSION: Final = "howreliable-model-registry-1.0"
DEFAULT_BUNDLE_ID: Final = "howreliable-rf-2022-cutoff-v1"
API_COMPATIBILITY_VERSION: Final = "howreliable-api-1.0"
API_CONTRACT_CHECKSUM: Final = "7f90b16a3da84f2506a5126840d4b3df696b995e791c99d17af3674f9c7923a4"
PRESENTATION_CONTRACT_CHECKSUM: Final = (
    "524dd4512dcc8e0e9e250d50a210865bc3b9842c3536abb4c0344e49b25732f0"
)
PRESENTATION_HANDOFF_CHECKSUM: Final = (
    "e473214b25616886a02585d9689c06f0b852cd3bb84676301f42328f6e9e7047"
)
TARGET_CHECKSUM: Final = "56b04f3f842d4f857f39b050c310b6fee2a83b20b9afeefc82c5c5967c19864e"
FEATURE_CHECKSUM: Final = "1cffb203298af438639c102a924f205ffb8ec25e6ea87035e670cecc902dfdfe"
SPLIT_CHECKSUM: Final = "43211c19328054f21ed730da6b9e65b78fae6e4b0af5983411b2a6811b34297a"
EXPLANATION_FEATURE_MANIFEST_CHECKSUM: Final = (
    "ce0bf89336b72f971fff94f7669fa72b439ab9d1a0213621c3312bf6c6fa7ef9"
)
SUPPORTED_COHORT_COUNT: Final = 8_416
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")


class RegistryError(RuntimeError):
    """Base registry failure."""


class UnknownBundleError(RegistryError):
    """Requested explicit bundle ID is not registered."""


class ArtifactNotFoundError(RegistryError):
    """A required artifact is absent."""


class ArtifactChecksumError(RegistryError):
    """Artifact bytes do not match their registered digest."""


class BundleCompatibilityError(RegistryError):
    """Checksum-valid components disagree semantically."""


class UnsafeArtifactReferenceError(RegistryError):
    """An artifact reference is absolute, traversing, or escapes its store."""


class ArtifactRole(StrEnum):
    MODEL = "MODEL"
    FEATURE_DATA = "FEATURE_DATA"
    TARGET_DATA = "TARGET_DATA"
    TARGET_PROVENANCE = "TARGET_PROVENANCE"
    SPLIT_DATA = "SPLIT_DATA"
    PREFERRED_MODEL_HANDOFF = "PREFERRED_MODEL_HANDOFF"
    EXPLANATION_FEATURE_MANIFEST = "EXPLANATION_FEATURE_MANIFEST"
    EVALUATION_REPORT = "EVALUATION_REPORT"
    EXPLAINABILITY_REPORT = "EXPLAINABILITY_REPORT"
    PRESENTATION_CONTRACT = "PRESENTATION_CONTRACT"
    PRESENTATION_HANDOFF = "PRESENTATION_HANDOFF"
    API_CONTRACT = "API_CONTRACT"


REQUIRED_ROLES: Final = frozenset(ArtifactRole)


class RegistryModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ArtifactReference(RegistryModel):
    role: ArtifactRole
    location: str = Field(min_length=1)
    sha256: str
    contract_version: str | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        return validate_artifact_location(value)


def validate_artifact_location(value: str) -> str:
    """Validate a backend-neutral relative logical artifact key."""
    try:
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if (
            not value
            or posix.is_absolute()
            or windows.is_absolute()
            or "\\" in value
            or value.casefold().startswith("s3://")
            or any(part == ".." for part in posix.parts)
            or value.startswith("/")
            or any(part in {"", "."} for part in value.split("/"))
        ):
            raise ValueError("artifact location must be a safe relative POSIX path")
        return value
    except TypeError as error:
        raise ValueError("artifact location must be text") from error


class InferenceBundleManifest(RegistryModel):
    registry_contract_version: str
    bundle_id: str
    model_identifier: str
    model_status: str
    model_type: str
    target_version: str
    history_cutoff: str
    future_window_start: str
    future_window_end: str
    threshold: float
    prediction_grain: str
    preprocessing: str
    evaluation_version: str
    explainability_version: str
    result_contract_version: str
    api_compatibility_version: str
    supported_cohort_count: int = Field(gt=0)
    required_limitation_semantics: str
    artifacts: tuple[ArtifactReference, ...]
    generation_utc: datetime

    @field_validator("registry_contract_version")
    @classmethod
    def validate_registry_version(cls, value: str) -> str:
        if value != REGISTRY_CONTRACT_VERSION:
            raise ValueError("unsupported registry contract version")
        return value

    @field_validator("bundle_id")
    @classmethod
    def validate_bundle_id(cls, value: str) -> str:
        if not BUNDLE_ID_RE.fullmatch(value):
            raise ValueError("invalid explicit bundle ID")
        return value

    @field_validator("generation_utc")
    @classmethod
    def validate_generation_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generation_utc must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_artifact_roles(self) -> InferenceBundleManifest:
        roles = [item.role for item in self.artifacts]
        if len(roles) != len(set(roles)):
            raise ValueError("artifact roles must be unique")
        if set(roles) != REQUIRED_ROLES:
            raise ValueError("manifest does not contain the exact required artifact roles")
        return self

    def artifact(self, role: ArtifactRole) -> ArtifactReference:
        return next(item for item in self.artifacts if item.role is role)


class ArtifactStore(ABC):
    """Narrow artifact access boundary suitable for a later remote implementation."""

    @abstractmethod
    def resolve(self, location: str) -> object:
        raise NotImplementedError

    @abstractmethod
    def exists(self, location: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def checksum(self, location: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def read_bytes(self, location: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def list(self, prefix: str) -> tuple[str, ...]:
        raise NotImplementedError


class LocalArtifactStore(ArtifactStore):
    """Path-safe repository-relative local artifact access."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def resolve(self, location: str) -> Path:
        try:
            validate_artifact_location(location)
        except ValueError as error:
            raise UnsafeArtifactReferenceError("unsafe artifact reference") from error
        resolved = (self._root / Path(*PurePosixPath(location).parts)).resolve()
        if not resolved.is_relative_to(self._root):
            raise UnsafeArtifactReferenceError("artifact reference escapes store root")
        return resolved

    def exists(self, location: str) -> bool:
        return self.resolve(location).is_file()

    def checksum(self, location: str) -> str:
        path = self.resolve(location)
        if not path.is_file():
            raise ArtifactNotFoundError("required artifact is missing")
        return sha256_file(path)

    def read_bytes(self, location: str) -> bytes:
        path = self.resolve(location)
        if not path.is_file():
            raise ArtifactNotFoundError("required artifact is missing")
        return path.read_bytes()

    def list(self, prefix: str) -> tuple[str, ...]:
        directory = self.resolve(prefix)
        if not directory.is_dir():
            return ()
        return tuple(sorted(item.name for item in directory.iterdir() if item.is_dir()))


class ModelRegistry:
    """Explicit-ID manifest registry; intentionally has no latest-selection operation."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def available_bundle_ids(self) -> tuple[str, ...]:
        return self.store.list("artifacts/registry/bundles")

    def load_manifest(self, bundle_id: str) -> InferenceBundleManifest:
        if not BUNDLE_ID_RE.fullmatch(bundle_id) or bundle_id not in self.available_bundle_ids():
            raise UnknownBundleError("unknown explicit bundle ID")
        base = f"artifacts/registry/bundles/{bundle_id}"
        digest_location = f"{base}/manifest.sha256"
        manifest_location = f"{base}/manifest.json"
        if not self.store.exists(digest_location) or not self.store.exists(manifest_location):
            raise ArtifactNotFoundError("bundle manifest or checksum is missing")
        expected = self.store.read_bytes(digest_location).decode("ascii").strip()
        if not SHA256_RE.fullmatch(expected):
            raise ArtifactChecksumError("manifest checksum record is malformed")
        actual = self.store.checksum(manifest_location)
        if actual != expected:
            raise ArtifactChecksumError("bundle manifest checksum mismatch")
        try:
            manifest = InferenceBundleManifest.model_validate_json(
                self.store.read_bytes(manifest_location)
            )
        except (ValueError, UnicodeDecodeError) as error:
            raise BundleCompatibilityError("bundle manifest schema is invalid") from error
        if manifest.bundle_id != bundle_id:
            raise BundleCompatibilityError("manifest bundle ID mismatch")
        return manifest


@dataclass(frozen=True, slots=True)
class InferenceBundle:
    """Typed validated resources consumed by the prediction service."""

    manifest: InferenceBundleManifest
    resources: PresentationResources
    presentation_handoff: Mapping[str, Any]


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleCompatibilityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise BundleCompatibilityError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _jsonl(data: bytes, schema: Sequence[str], label: str) -> list[dict[str, Any]]:
    rows = []
    try:
        for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
            row = json.loads(line)
            if not isinstance(row, dict) or tuple(row) != tuple(schema):
                raise BundleCompatibilityError(f"{label} schema mismatch at line {number}")
            rows.append(cast(dict[str, Any], row))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleCompatibilityError(f"{label} is not valid JSON Lines") from error
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleCompatibilityError(message)


def load_inference_bundle(registry: ModelRegistry, bundle_id: str) -> InferenceBundle:
    """Verify every checksum, then parse and validate the complete explicit bundle."""
    manifest = registry.load_manifest(bundle_id)
    store = registry.store
    data: dict[ArtifactRole, bytes] = {}
    # Integrity is established for every registered artifact before any is parsed or loaded.
    for reference in manifest.artifacts:
        if not store.exists(reference.location):
            raise ArtifactNotFoundError(f"required artifact is missing: {reference.role.value}")
        if store.checksum(reference.location) != reference.sha256:
            raise ArtifactChecksumError(f"artifact checksum mismatch: {reference.role.value}")
        data[reference.role] = store.read_bytes(reference.location)

    preferred = _json_object(data[ArtifactRole.PREFERRED_MODEL_HANDOFF], "model handoff")
    target_provenance = _json_object(data[ArtifactRole.TARGET_PROVENANCE], "target provenance")
    evaluation = _json_object(data[ArtifactRole.EVALUATION_REPORT], "evaluation report")
    explanation = _json_object(data[ArtifactRole.EXPLAINABILITY_REPORT], "explainability report")
    explanation_manifest = _json_object(
        data[ArtifactRole.EXPLANATION_FEATURE_MANIFEST], "explanation feature manifest"
    )
    presentation = _json_object(data[ArtifactRole.PRESENTATION_CONTRACT], "presentation contract")
    presentation_handoff = _json_object(
        data[ArtifactRole.PRESENTATION_HANDOFF], "presentation handoff"
    )
    api_contract = _json_object(data[ArtifactRole.API_CONTRACT], "API contract")
    features = _jsonl(data[ArtifactRole.FEATURE_DATA], FEATURE_SCHEMA, "feature data")
    targets = _jsonl(data[ArtifactRole.TARGET_DATA], TARGET_SCHEMA, "target data")

    _require(manifest.registry_contract_version == REGISTRY_CONTRACT_VERSION, "registry mismatch")
    _require(manifest.model_identifier == MODEL_IDENTIFIER, "model identifier mismatch")
    _require(manifest.model_status == MODEL_STATUS, "model status mismatch")
    _require(manifest.target_version == TARGET_DEFINITION_VERSION, "target version mismatch")
    _require(manifest.history_cutoff == "2022-12-31", "history cutoff mismatch")
    _require(manifest.future_window_start == "2023-01-01", "future window start mismatch")
    _require(manifest.future_window_end == "2023-12-31", "future window end mismatch")
    _require(manifest.threshold == THRESHOLD, "threshold mismatch")
    _require(manifest.prediction_grain == "make_model_model_year_cohort", "grain mismatch")
    _require(manifest.evaluation_version == EVALUATION_VERSION, "evaluation version mismatch")
    _require(
        manifest.explainability_version == EXPLAINABILITY_VERSION,
        "explainability version mismatch",
    )
    _require(manifest.result_contract_version == RESULT_CONTRACT_VERSION, "result mismatch")
    _require(
        manifest.api_compatibility_version == API_COMPATIBILITY_VERSION,
        "API compatibility mismatch",
    )
    _require(manifest.supported_cohort_count == SUPPORTED_COHORT_COUNT, "cohort count mismatch")
    _require(manifest.required_limitation_semantics == GENERAL_LIMITATION, "limitation mismatch")
    _require(len(features) == SUPPORTED_COHORT_COUNT, "feature cohort count mismatch")
    _require(len(targets) == SUPPORTED_COHORT_COUNT, "target cohort count mismatch")

    target_definition = cast(dict[str, Any], target_provenance.get("target_definition", {}))
    _require(
        target_provenance.get("target_definition_version") == manifest.target_version,
        "target mismatch",
    )
    _require(target_provenance.get("output_sha256") == TARGET_CHECKSUM, "target lineage mismatch")
    _require(target_definition.get("cutoff") == manifest.history_cutoff, "target cutoff mismatch")
    _require(
        target_definition.get("future_window_start") == manifest.future_window_start,
        "window mismatch",
    )
    _require(
        target_definition.get("future_window_end") == manifest.future_window_end, "window mismatch"
    )

    _require(
        preferred.get("preferred_model_identifier") == manifest.model_identifier,
        "handoff model mismatch",
    )
    _require(
        preferred.get("preferred_model_checksum") == MODEL_CHECKSUM,
        "handoff model checksum mismatch",
    )
    _require(
        preferred.get("feature_contract_checksum") == FEATURE_CHECKSUM, "feature lineage mismatch"
    )
    _require(preferred.get("split_checksum") == SPLIT_CHECKSUM, "split lineage mismatch")
    _require(preferred.get("threshold") == manifest.threshold, "handoff threshold mismatch")
    _require(
        preferred.get("evaluation_version") == manifest.evaluation_version,
        "handoff evaluation mismatch",
    )
    _require(preferred.get("preprocessing") == manifest.preprocessing, "preprocessing mismatch")

    frozen_model = cast(dict[str, Any], evaluation.get("frozen_models", {})).get(
        "random_forest", {}
    )
    _require(
        evaluation.get("evaluation_version") == manifest.evaluation_version, "evaluation mismatch"
    )
    _require(
        cast(dict[str, Any], frozen_model).get("sha256") == MODEL_CHECKSUM,
        "evaluation model mismatch",
    )
    _require(
        explanation.get("explainability_version") == manifest.explainability_version,
        "explanation mismatch",
    )
    _require(
        explanation.get("preferred_model_checksum") == MODEL_CHECKSUM, "explanation model mismatch"
    )
    _require(
        explanation.get("evaluation_report_checksum") == EVALUATION_REPORT_CHECKSUM,
        "explanation evaluation mismatch",
    )
    _require(
        explanation.get("feature_manifest_checksum") == EXPLANATION_FEATURE_MANIFEST_CHECKSUM,
        "explanation manifest mismatch",
    )
    _require(
        explanation_manifest.get("version") == EXPLAINABILITY_VERSION,
        "feature manifest version mismatch",
    )

    _require(
        presentation.get("result_contract_version") == manifest.result_contract_version,
        "presentation version mismatch",
    )
    _require(
        presentation.get("canonical_schema_identifier") == "ComplaintActivityResult",
        "presentation schema mismatch",
    )
    _require(
        presentation_handoff.get("result_contract_version") == manifest.result_contract_version,
        "presentation handoff mismatch",
    )
    _require(
        presentation_handoff.get("presentation_contract_checksum")
        == PRESENTATION_CONTRACT_CHECKSUM,
        "presentation checksum mismatch",
    )
    _require(
        presentation_handoff.get("threshold") == manifest.threshold,
        "presentation threshold mismatch",
    )
    _require(
        presentation_handoff.get("supported_input_grain") == manifest.prediction_grain,
        "presentation grain mismatch",
    )
    _require(
        presentation_handoff.get("preferred_model")
        == {"identifier": MODEL_IDENTIFIER, "checksum": MODEL_CHECKSUM},
        "presentation model mismatch",
    )
    _require(
        api_contract.get("api_contract_version") == manifest.api_compatibility_version,
        "API version mismatch",
    )
    _require(
        api_contract.get("supported_cohort_count") == manifest.supported_cohort_count,
        "API cohort count mismatch",
    )
    _require(
        api_contract.get("presentation_handoff_checksum") == PRESENTATION_HANDOFF_CHECKSUM,
        "API handoff mismatch",
    )

    # joblib/pickle is loaded only after checksum and semantic metadata verification. It is
    # trusted project input; integrity verification does not make hostile pickle safe.
    try:
        model = joblib.load(io.BytesIO(data[ArtifactRole.MODEL]))
    except Exception as error:
        raise BundleCompatibilityError(
            "trusted model artifact could not be deserialized"
        ) from error
    model_manifest = tuple(cast(list[dict[str, Any]], explanation_manifest["features"]))
    _require(feature_manifest(model) == list(model_manifest), "model feature manifest mismatch")
    rows_by_id = {cast(str, row["broad_vehicle_id"]): row for row in features}
    _require(len(rows_by_id) == len(features), "duplicate feature cohort identity")
    resources = PresentationResources(
        handoff=MappingProxyType(preferred),
        model=model,
        rows_by_id=MappingProxyType(rows_by_id),
        manifest=model_manifest,
    )
    return InferenceBundle(
        manifest=manifest,
        resources=resources,
        presentation_handoff=MappingProxyType(presentation_handoff),
    )


def canonical_manifest(root: Path, *, generation_utc: datetime) -> InferenceBundleManifest:
    """Build the one approved manifest from exact repository-relative artifacts."""
    locations = {
        ArtifactRole.MODEL: (
            "artifacts/models/baselines/complaints_communications_recalls--random_forest.joblib"
        ),
        ArtifactRole.FEATURE_DATA: "data/processed/modeling/cohort-features-asof-2022-12-31.jsonl",
        ArtifactRole.TARGET_DATA: (
            "data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl"
        ),
        ArtifactRole.TARGET_PROVENANCE: (
            "data/processed/targets/future-complaint-activity-2022-12-31-12m.provenance.json"
        ),
        ArtifactRole.SPLIT_DATA: "data/processed/modeling/cohort-split-2022-12-31.jsonl",
        ArtifactRole.PREFERRED_MODEL_HANDOFF: "artifacts/evaluation/preferred-model-handoff.json",
        ArtifactRole.EXPLANATION_FEATURE_MANIFEST: "artifacts/explainability/feature-manifest.json",
        ArtifactRole.EVALUATION_REPORT: "artifacts/evaluation/evaluation-report.json",
        ArtifactRole.EXPLAINABILITY_REPORT: "artifacts/explainability/explainability-report.json",
        ArtifactRole.PRESENTATION_CONTRACT: "artifacts/presentation/presentation-contract.json",
        ArtifactRole.PRESENTATION_HANDOFF: "artifacts/presentation/api-handoff.json",
        ArtifactRole.API_CONTRACT: "artifacts/api/api-contract.json",
    }
    versions = {
        ArtifactRole.TARGET_DATA: TARGET_DEFINITION_VERSION,
        ArtifactRole.EVALUATION_REPORT: EVALUATION_VERSION,
        ArtifactRole.EXPLAINABILITY_REPORT: EXPLAINABILITY_VERSION,
        ArtifactRole.PRESENTATION_CONTRACT: RESULT_CONTRACT_VERSION,
        ArtifactRole.API_CONTRACT: API_COMPATIBILITY_VERSION,
    }
    return InferenceBundleManifest(
        registry_contract_version=REGISTRY_CONTRACT_VERSION,
        bundle_id=DEFAULT_BUNDLE_ID,
        model_identifier=MODEL_IDENTIFIER,
        model_status=MODEL_STATUS,
        model_type="sklearn.pipeline.Pipeline[random_forest_classifier]",
        target_version=TARGET_DEFINITION_VERSION,
        history_cutoff="2022-12-31",
        future_window_start="2023-01-01",
        future_window_end="2023-12-31",
        threshold=THRESHOLD,
        prediction_grain="make_model_model_year_cohort",
        preprocessing="serialized sklearn Pipeline fit on Phase 3B TRAIN",
        evaluation_version=EVALUATION_VERSION,
        explainability_version=EXPLAINABILITY_VERSION,
        result_contract_version=RESULT_CONTRACT_VERSION,
        api_compatibility_version=API_COMPATIBILITY_VERSION,
        supported_cohort_count=SUPPORTED_COHORT_COUNT,
        required_limitation_semantics=GENERAL_LIMITATION,
        artifacts=tuple(
            ArtifactReference(
                role=role,
                location=location,
                sha256=sha256_file(root / Path(*PurePosixPath(location).parts)),
                contract_version=versions.get(role),
            )
            for role, location in locations.items()
        ),
        generation_utc=generation_utc.astimezone(UTC),
    )


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_canonical_manifest(
    root: Path,
    *,
    generation_utc: datetime | None = None,
    regenerate: bool = False,
) -> tuple[Path, str]:
    """Write the canonical manifest and sidecar digest deterministically."""
    directory = root / "artifacts/registry/bundles" / DEFAULT_BUNDLE_ID
    path = directory / "manifest.json"
    sidecar = directory / "manifest.sha256"
    if not regenerate and (path.exists() or sidecar.exists()):
        raise RegistryError("refusing to overwrite registry manifest")
    manifest = canonical_manifest(root, generation_utc=generation_utc or datetime.now(UTC))
    payload = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    _atomic_text(path, payload)
    digest = sha256_file(path)
    _atomic_text(sidecar, digest + "\n")
    return path, digest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or inspect an explicit local bundle")
    parser.add_argument("operation", choices=("validate", "inspect"))
    parser.add_argument("bundle_id")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    bundle = load_inference_bundle(ModelRegistry(LocalArtifactStore(args.root)), args.bundle_id)
    summary = {
        "bundle_id": bundle.manifest.bundle_id,
        "registry_contract_version": bundle.manifest.registry_contract_version,
        "model_identifier": bundle.manifest.model_identifier,
        "model_status": bundle.manifest.model_status,
        "supported_cohort_count": len(bundle.resources.rows_by_id),
        "artifact_roles": [item.role.value for item in bundle.manifest.artifacts],
        "validation": "passed",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
