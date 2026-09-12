"""Behavioral tests for the canonical reliability event model."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from howreliable.domain import (
    Component,
    EventType,
    ReliabilityEvent,
    Severity,
    SeverityEvidence,
    SourceReference,
    SourceType,
    Vehicle,
    VehicleAssociation,
    derive_severity,
)


def make_source(**overrides: object) -> SourceReference:
    values: dict[str, object] = {
        "source_type": SourceType.NHTSA_ODI_COMPLAINT,
        "organization": "National Highway Traffic Safety Administration (NHTSA)",
        "dataset": "Office of Defects Investigation Vehicle Owner Complaints",
        "source_record_id": "1234567",
        "source_reference_id": "11223344",
        "original_component": "SERVICE BRAKES",
        "artifact_filename": None,
        "artifact_sha256": None,
    }
    values.update(overrides)
    return SourceReference.model_validate(values)


def event_values(**overrides: object) -> dict[str, object]:
    evidence = SeverityEvidence(death_count=0, injury_count=0, crash=False, fire=False)
    values: dict[str, object] = {
        "event_type": EventType.COMPLAINT,
        "component": Component.BRAKES,
        "severity": derive_severity(evidence),
        "severity_evidence": evidence,
        "vehicle": VehicleAssociation(make="Mazda", model="Mazda3", year=2017),
        "source": make_source(),
        "mileage": 12345,
        "occurrence_date": date(2020, 1, 1),
        "report_date": date(2020, 1, 2),
        "description": "Source-preserved complaint narrative.",
    }
    values.update(overrides)
    return values


def make_event(**overrides: object) -> ReliabilityEvent:
    return ReliabilityEvent.model_validate(event_values(**overrides))


def test_valid_event_construction_and_predictable_serialization() -> None:
    event = make_event()
    serialized = event.model_dump(mode="json")

    assert event.event_type is EventType.COMPLAINT
    assert event.component is Component.BRAKES
    assert event.severity is Severity.LOW
    assert serialized["occurrence_date"] == "2020-01-01"
    assert serialized["report_date"] == "2020-01-02"
    assert serialized["event_id"] == event.event_id
    assert json.loads(event.event_identity_key) == {
        "dataset": "office of defects investigation vehicle owner complaints",
        "organization": "national highway traffic safety administration (nhtsa)",
        "source_record_id": "1234567",
        "source_type": "nhtsa_odi_complaint",
    }


def test_event_is_immutable() -> None:
    event = make_event()

    with pytest.raises(ValidationError):
        event.mileage = 99


@pytest.mark.parametrize(
    "field",
    ["event_type", "component", "severity", "severity_evidence", "vehicle", "source"],
)
def test_required_event_fields_cannot_be_omitted(field: str) -> None:
    values = event_values()
    del values[field]

    with pytest.raises(ValidationError):
        ReliabilityEvent.model_validate(values)


def test_optional_event_fields_accept_none() -> None:
    event = make_event(mileage=None, occurrence_date=None, report_date=None, description=None)

    assert event.mileage is None
    assert event.occurrence_date is None
    assert event.report_date is None
    assert event.description is None


def test_event_type_taxonomy_is_small_and_explicit() -> None:
    assert set(EventType) == {
        EventType.COMPLAINT,
        EventType.FAILURE,
        EventType.RECALL,
        EventType.MANUFACTURER_COMMUNICATION,
        EventType.OTHER,
        EventType.UNKNOWN,
    }
    with pytest.raises(ValidationError):
        make_event(event_type="inspection")


def test_component_taxonomy_includes_explicit_other_and_unknown() -> None:
    assert len({Component.OTHER, Component.UNKNOWN}) == 2
    assert {Component.ENGINE, Component.TRANSMISSION, Component.ELECTRICAL}.issubset(
        set(Component)
    )
    with pytest.raises(ValidationError):
        make_event(component="battery_management")


def test_severity_taxonomy_is_ordered_by_explicit_meaning_not_numeric_score() -> None:
    assert set(Severity) == {
        Severity.UNKNOWN,
        Severity.LOW,
        Severity.MODERATE,
        Severity.HIGH,
        Severity.CRITICAL,
    }


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            SeverityEvidence(death_count=1, injury_count=0, crash=False, fire=False),
            Severity.CRITICAL,
        ),
        (SeverityEvidence(death_count=0, injury_count=2, crash=False, fire=False), Severity.HIGH),
        (
            SeverityEvidence(death_count=0, injury_count=0, crash=True, fire=False),
            Severity.MODERATE,
        ),
        (
            SeverityEvidence(death_count=0, injury_count=0, crash=False, fire=True),
            Severity.MODERATE,
        ),
        (SeverityEvidence(death_count=0, injury_count=0, crash=False, fire=False), Severity.LOW),
        (
            SeverityEvidence(death_count=None, injury_count=0, crash=False, fire=False),
            Severity.UNKNOWN,
        ),
        (SeverityEvidence(death_count=None, injury_count=1, crash=None, fire=None), Severity.HIGH),
    ],
)
def test_severity_derivation_is_deterministic(
    evidence: SeverityEvidence, expected: Severity
) -> None:
    assert derive_severity(evidence) is expected


@pytest.mark.parametrize("mileage", [-1, True, "12000"])
def test_event_rejects_invalid_or_coerced_mileage(mileage: object) -> None:
    with pytest.raises(ValidationError):
        make_event(mileage=mileage)


def test_vehicle_association_normalizes_broad_identity() -> None:
    first = VehicleAssociation(make=" Mazda ", model="MAZDA3", year=2017)
    second = VehicleAssociation(make="mazda", model="mazda3", year=2017)

    assert first == second
    assert first.broad_identity_id == second.broad_identity_id
    assert first.known_configuration_id is None


def test_vehicle_association_can_retain_known_configuration() -> None:
    vehicle = Vehicle.model_validate(
        {
            "make": "Mazda",
            "model": "Mazda3",
            "year": 2017,
            "transmission": "automatic",
            "drivetrain": "fwd",
        }
    )

    association = VehicleAssociation.from_vehicle(vehicle)

    assert association.known_configuration_id == vehicle.configuration_id
    assert association.make == vehicle.make


def test_event_identifiers_are_deterministic_for_equivalent_identity() -> None:
    first = make_event()
    second = make_event(mileage=99999, component=Component.OTHER, description="Updated source")

    assert first.event_identity_key == second.event_identity_key
    assert first.event_id == second.event_id


def test_source_namespace_or_record_change_changes_event_identifier() -> None:
    base = make_event()
    different_record = make_event(source=make_source(source_record_id="7654321"))
    different_source = make_event(
        source=make_source(source_type=SourceType.OTHER, source_record_id="1234567")
    )
    different_dataset = make_event(source=make_source(dataset="Another dataset"))

    assert base.event_id != different_record.event_id
    assert base.event_id != different_source.event_id
    assert base.event_id != different_dataset.event_id


def test_source_reference_validates_traceability_fields() -> None:
    with pytest.raises(ValidationError):
        make_source(source_record_id="   ")
    with pytest.raises(ValidationError):
        make_source(artifact_sha256="not-a-checksum")
