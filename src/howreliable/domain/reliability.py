"""Source-independent reliability event domain model."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from howreliable.domain.vehicle import MAX_MODEL_YEAR, MIN_MODEL_YEAR, Vehicle

SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def _normalize_identity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


class EventType(StrEnum):
    """Small source-independent reliability event taxonomy."""

    COMPLAINT = "complaint"
    FAILURE = "failure"
    RECALL = "recall"
    MANUFACTURER_COMMUNICATION = "manufacturer_communication"
    OTHER = "other"
    UNKNOWN = "unknown"


class Component(StrEnum):
    """Coarse component groups intended for cross-source analysis."""

    ENGINE = "engine"
    TRANSMISSION = "transmission"
    ELECTRICAL = "electrical"
    COOLING = "cooling"
    SUSPENSION = "suspension"
    BRAKES = "brakes"
    STEERING = "steering"
    FUEL_SYSTEM = "fuel_system"
    HVAC = "hvac"
    EXHAUST = "exhaust"
    BODY = "body"
    AIRBAGS_RESTRAINTS = "airbags_restraints"
    TIRES_WHEELS = "tires_wheels"
    OTHER = "other"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    """Interpretable evidence severity, not repair cost or failure probability."""

    UNKNOWN = "unknown"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SourceType(StrEnum):
    """Implemented source namespaces plus explicit fallback states."""

    NHTSA_ODI_COMPLAINT = "nhtsa_odi_complaint"
    OTHER = "other"
    UNKNOWN = "unknown"


class VehicleAssociation(BaseModel):
    """Strongest canonical vehicle identity supported by an event source."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    make: str
    model: str
    year: int = Field(ge=MIN_MODEL_YEAR, le=MAX_MODEL_YEAR)
    generation: str | None = None
    known_configuration_id: str | None = None

    @field_validator("make", "model", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("vehicle identity value must be text")
        normalized = _normalize_identity_text(value)
        if not normalized:
            raise ValueError("vehicle identity value must not be empty")
        return normalized

    @field_validator("generation", mode="before")
    @classmethod
    def normalize_generation(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("generation must be text or None")
        normalized = _normalize_identity_text(value)
        if not normalized:
            raise ValueError("generation must be None rather than empty")
        return normalized

    @field_validator("known_configuration_id")
    @classmethod
    def validate_configuration_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        prefix = "configuration_"
        digest = value.removeprefix(prefix)
        if not value.startswith(prefix) or SHA256_PATTERN.fullmatch(digest) is None:
            raise ValueError("known_configuration_id is not a canonical Vehicle identifier")
        return value

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def broad_identity_id(self) -> str:
        """Stable identity for known make/model/year/generation fields."""
        identity = json.dumps(
            {
                "generation": self.generation,
                "make": self.make,
                "model": self.model,
                "year": self.year,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"vehicle_association_{digest}"

    @classmethod
    def from_vehicle(cls, vehicle: Vehicle) -> VehicleAssociation:
        """Associate an event with every configuration field known by Vehicle."""
        return cls(
            make=vehicle.make,
            model=vehicle.model,
            year=vehicle.year,
            generation=vehicle.generation,
            known_configuration_id=vehicle.configuration_id,
        )


class SourceReference(BaseModel):
    """Minimal source traceability retained by a canonical event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    source_type: SourceType
    organization: str
    dataset: str
    source_record_id: str
    source_reference_id: str | None = None
    original_component: str | None = None
    artifact_filename: str | None = None
    artifact_sha256: str | None = None

    @field_validator("organization", "dataset", "source_record_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("source reference value must not be empty")
        return normalized

    @field_validator(
        "source_reference_id", "original_component", "artifact_filename", mode="before"
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("optional source reference value must be text or None")
        if not value:
            raise ValueError("optional source reference value must be None rather than empty")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def validate_artifact_checksum(cls, value: str | None) -> str | None:
        if value is not None and SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 digest")
        return value


class SeverityEvidence(BaseModel):
    """Explicit safety indicators used to derive event severity."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    death_count: int | None = Field(default=None, ge=0)
    injury_count: int | None = Field(default=None, ge=0)
    crash: bool | None = None
    fire: bool | None = None


class ReliabilityEvent(BaseModel):
    """Immutable canonical representation of one source-observed reliability event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_type: EventType
    component: Component
    severity: Severity
    severity_evidence: SeverityEvidence
    vehicle: VehicleAssociation
    source: SourceReference
    mileage: int | None = Field(default=None, ge=0)
    occurrence_date: date | None = None
    report_date: date | None = None
    description: str | None = None

    @field_validator("description", mode="before")
    @classmethod
    def validate_optional_description(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("description must be text or None")
        if not value:
            raise ValueError("description must be None rather than empty")
        return value

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def event_identity_key(self) -> str:
        """Canonical source-row identity, deliberately excluding updateable attributes."""
        return json.dumps(
            {
                "dataset": _normalize_identity_text(self.source.dataset),
                "organization": _normalize_identity_text(self.source.organization),
                "source_record_id": self.source.source_record_id,
                "source_type": self.source.source_type.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def event_id(self) -> str:
        """Stable SHA-256 identifier namespaced by source type."""
        digest = hashlib.sha256(self.event_identity_key.encode("utf-8")).hexdigest()
        return f"event_{digest}"


def derive_severity(evidence: SeverityEvidence) -> Severity:
    """Derive conservative severity solely from explicit safety indicators."""
    if evidence.death_count is not None and evidence.death_count > 0:
        return Severity.CRITICAL
    if evidence.injury_count is not None and evidence.injury_count > 0:
        return Severity.HIGH
    if evidence.crash is True or evidence.fire is True:
        return Severity.MODERATE
    if (
        evidence.death_count == 0
        and evidence.injury_count == 0
        and evidence.crash is False
        and evidence.fire is False
    ):
        return Severity.LOW
    return Severity.UNKNOWN
