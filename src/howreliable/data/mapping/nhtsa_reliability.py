"""Conservative mapping from NHTSA complaint rows to canonical reliability events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from pydantic import ValidationError

from howreliable.data.ingestion.nhtsa_complaints import (
    DATASET_NAME,
    SOURCE_ORGANIZATION,
    NhtsaComplaintRecord,
    Provenance,
    VehicleMappingError,
    map_record_to_vehicle,
)
from howreliable.domain import (
    Component,
    EventType,
    ReliabilityEvent,
    SeverityEvidence,
    SourceReference,
    SourceType,
    VehicleAssociation,
    derive_severity,
)

SOURCE_ID_PATTERN: Final = re.compile(r"^[0-9]{1,9}$")
UNSIGNED_INTEGER_PATTERN: Final = re.compile(r"^[0-9]+$")
SOURCE_DATE_PATTERN: Final = re.compile(r"^[0-9]{8}$")


class MappingFailureReason(StrEnum):
    """Stable categories for observable NHTSA mapping rejection."""

    UNSUPPORTED_PRODUCT_TYPE = "unsupported_product_type"
    MISSING_SOURCE_IDENTIFIER = "missing_source_identifier"
    MALFORMED_SOURCE_IDENTIFIER = "malformed_source_identifier"
    MISSING_VEHICLE_IDENTITY = "missing_vehicle_identity"
    INVALID_MODEL_YEAR = "invalid_model_year"
    MALFORMED_MILEAGE = "malformed_mileage"
    MALFORMED_DATE = "malformed_date"
    MALFORMED_COUNT = "malformed_count"
    MALFORMED_INDICATOR = "malformed_indicator"


class EventMappingError(Exception):
    """Explicit failure to construct a valid canonical event from one source row."""

    def __init__(self, reason: MappingFailureReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NhtsaSourceContext:
    """Artifact-level traceability supplied alongside an individual source row."""

    artifact_filename: str
    artifact_sha256: str

    @classmethod
    def from_provenance(cls, provenance: Provenance) -> NhtsaSourceContext:
        return cls(
            artifact_filename=provenance.source_artifact_filename,
            artifact_sha256=provenance.source_artifact_sha256,
        )


def _component_matches(value: str, root: str) -> bool:
    return value == root or value.startswith(f"{root}:") or value.startswith(f"{root},")


def map_nhtsa_component(source_component: str | None) -> Component:
    """Map exact NHTSA component roots without fuzzy or narrative interpretation."""
    if source_component is None or not source_component.strip():
        return Component.UNKNOWN
    component = source_component.strip().upper()

    ordered_roots = (
        (Component.EXHAUST, ("ENGINE AND ENGINE COOLING:EXHAUST SYSTEM",)),
        (Component.COOLING, ("ENGINE AND ENGINE COOLING:COOLING SYSTEM",)),
        (
            Component.TRANSMISSION,
            (
                "POWER TRAIN:AUTOMATIC TRANSMISSION",
                "POWER TRAIN:MANUAL TRANSMISSION",
                "POWER TRAIN:CLUTCH ASSEMBLY",
            ),
        ),
        (Component.ENGINE, ("ENGINE", "ENGINE AND ENGINE COOLING")),
        (Component.ELECTRICAL, ("ELECTRICAL SYSTEM",)),
        (Component.SUSPENSION, ("SUSPENSION",)),
        (Component.BRAKES, ("SERVICE BRAKES", "SERVICE BRAKES, HYDRAULIC")),
        (Component.STEERING, ("STEERING",)),
        (
            Component.FUEL_SYSTEM,
            ("FUEL/PROPULSION SYSTEM", "FUEL SYSTEM, GASOLINE", "FUEL SYSTEM, DIESEL"),
        ),
        (
            Component.HVAC,
            ("VISIBILITY:DEFROSTER/DEFOGGER/HVAC SYSTEM", "EQUIPMENT:APPLIANCE:AIR CONDITIONER"),
        ),
        (Component.BODY, ("STRUCTURE",)),
        (Component.AIRBAGS_RESTRAINTS, ("AIR BAGS", "SEAT BELTS", "CHILD SEAT")),
        (Component.TIRES_WHEELS, ("TIRES", "WHEELS")),
    )
    for canonical, roots in ordered_roots:
        if any(_component_matches(component, root) for root in roots):
            return canonical
    return Component.OTHER


def _require_source_id(value: str | None, field_name: str) -> str:
    if value is None:
        raise EventMappingError(
            MappingFailureReason.MISSING_SOURCE_IDENTIFIER,
            f"missing required NHTSA source identifier {field_name}",
        )
    if SOURCE_ID_PATTERN.fullmatch(value) is None:
        raise EventMappingError(
            MappingFailureReason.MALFORMED_SOURCE_IDENTIFIER,
            f"invalid NHTSA source identifier {field_name}: {value!r}",
        )
    return value


def _parse_optional_unsigned(
    value: str | None,
    field_name: str,
    reason: MappingFailureReason,
) -> int | None:
    if value is None:
        return None
    if UNSIGNED_INTEGER_PATTERN.fullmatch(value) is None:
        raise EventMappingError(reason, f"invalid {field_name}: {value!r}")
    return int(value)


def _parse_optional_flag(value: str | None, field_name: str) -> bool | None:
    if value is None:
        return None
    if value == "Y":
        return True
    if value == "N":
        return False
    raise EventMappingError(
        MappingFailureReason.MALFORMED_INDICATOR,
        f"invalid {field_name} indicator: {value!r}",
    )


def _parse_optional_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    if SOURCE_DATE_PATTERN.fullmatch(value) is None:
        raise EventMappingError(
            MappingFailureReason.MALFORMED_DATE,
            f"invalid {field_name}: {value!r}",
        )
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise EventMappingError(
            MappingFailureReason.MALFORMED_DATE,
            f"invalid {field_name}: {value!r}",
        ) from error


def _vehicle_association(
    record: NhtsaComplaintRecord, fields: dict[str, str | None]
) -> VehicleAssociation:
    make = fields["MAKETXT"]
    model = fields["MODELTXT"]
    year = fields["YEARTXT"]
    if make is None or not make.strip() or model is None or not model.strip():
        raise EventMappingError(
            MappingFailureReason.MISSING_VEHICLE_IDENTITY,
            "NHTSA complaint lacks make or model",
        )
    if year is None or year == "9999" or re.fullmatch(r"[0-9]{4}", year) is None:
        raise EventMappingError(
            MappingFailureReason.INVALID_MODEL_YEAR,
            f"invalid or unknown NHTSA model year: {year!r}",
        )

    try:
        association = VehicleAssociation.model_validate(
            {"make": make, "model": model, "year": int(year)}
        )
    except ValidationError as error:
        raise EventMappingError(
            MappingFailureReason.INVALID_MODEL_YEAR,
            f"invalid NHTSA vehicle identity: {error}",
        ) from error

    try:
        vehicle = map_record_to_vehicle(record)
    except VehicleMappingError:
        return association
    return VehicleAssociation.from_vehicle(vehicle)


def map_nhtsa_complaint(
    record: NhtsaComplaintRecord,
    *,
    source_context: NhtsaSourceContext | None = None,
) -> ReliabilityEvent:
    """Map one NHTSA vehicle complaint to a canonical event or fail explicitly."""
    fields = record.as_source_dict()
    if fields["PROD_TYPE"] != "V":
        raise EventMappingError(
            MappingFailureReason.UNSUPPORTED_PRODUCT_TYPE,
            f"unsupported NHTSA product type: {fields['PROD_TYPE']!r}",
        )

    complaint_id = _require_source_id(fields["CMPLID"], "CMPLID")
    odi_number = _require_source_id(fields["ODINO"], "ODINO")
    vehicle = _vehicle_association(record, fields)
    mileage = _parse_optional_unsigned(
        fields["MILES"], "mileage", MappingFailureReason.MALFORMED_MILEAGE
    )
    evidence = SeverityEvidence(
        death_count=_parse_optional_unsigned(
            fields["DEATHS"], "death count", MappingFailureReason.MALFORMED_COUNT
        ),
        injury_count=_parse_optional_unsigned(
            fields["INJURED"], "injury count", MappingFailureReason.MALFORMED_COUNT
        ),
        crash=_parse_optional_flag(fields["CRASH"], "crash"),
        fire=_parse_optional_flag(fields["FIRE"], "fire"),
    )
    source = SourceReference(
        source_type=SourceType.NHTSA_ODI_COMPLAINT,
        organization=SOURCE_ORGANIZATION,
        dataset=DATASET_NAME,
        source_record_id=complaint_id,
        source_reference_id=odi_number,
        original_component=fields["COMPDESC"],
        artifact_filename=source_context.artifact_filename if source_context else None,
        artifact_sha256=source_context.artifact_sha256 if source_context else None,
    )
    return ReliabilityEvent(
        event_type=EventType.COMPLAINT,
        component=map_nhtsa_component(fields["COMPDESC"]),
        severity=derive_severity(evidence),
        severity_evidence=evidence,
        vehicle=vehicle,
        source=source,
        mileage=mileage,
        occurrence_date=_parse_optional_date(fields["FAILDATE"], "occurrence date"),
        report_date=_parse_optional_date(fields["LDATE"], "report date"),
        description=fields["CDESCR"],
    )
