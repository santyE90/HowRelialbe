"""Tests for mapping immutable NHTSA records to canonical reliability events."""

from __future__ import annotations

from datetime import date

import pytest

from howreliable.data.ingestion.nhtsa_complaints import (
    DATASET_NAME,
    SOURCE_COLUMNS,
    SOURCE_ORGANIZATION,
    NhtsaComplaintRecord,
)
from howreliable.data.mapping import (
    EventMappingError,
    MappingFailureReason,
    NhtsaSourceContext,
    map_nhtsa_complaint,
    map_nhtsa_component,
)
from howreliable.domain import Component, EventType, Severity, SourceType


def source_record(**overrides: str) -> NhtsaComplaintRecord:
    values = dict.fromkeys(SOURCE_COLUMNS, "")
    values.update(
        {
            "CMPLID": "1234567",
            "ODINO": "11223344",
            "MFR_NAME": "Mazda North American Operations",
            "MAKETXT": "MAZDA",
            "MODELTXT": "MAZDA3",
            "YEARTXT": "2017",
            "CRASH": "N",
            "FAILDATE": "20200101",
            "FIRE": "N",
            "INJURED": "0",
            "DEATHS": "0",
            "COMPDESC": "SERVICE BRAKES",
            "LDATE": "20200102",
            "MILES": "12345",
            "CDESCR": "Brake pedal felt unusual.",
            "DRIVE_TRAIN": "FWD",
            "TRANS_TYPE": "AUTO",
            "PROD_TYPE": "V",
        }
    )
    values.update(overrides)
    return NhtsaComplaintRecord.from_columns([values[name] for name in SOURCE_COLUMNS])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, Component.UNKNOWN),
        ("   ", Component.UNKNOWN),
        ("ENGINE", Component.ENGINE),
        ("ENGINE AND ENGINE COOLING:COOLING SYSTEM:RADIATOR", Component.COOLING),
        ("ENGINE AND ENGINE COOLING:EXHAUST SYSTEM", Component.EXHAUST),
        ("POWER TRAIN:AUTOMATIC TRANSMISSION", Component.TRANSMISSION),
        ("ELECTRICAL SYSTEM:BATTERY", Component.ELECTRICAL),
        ("SUSPENSION:FRONT", Component.SUSPENSION),
        ("SERVICE BRAKES, HYDRAULIC", Component.BRAKES),
        ("STEERING:COLUMN", Component.STEERING),
        ("FUEL/PROPULSION SYSTEM", Component.FUEL_SYSTEM),
        ("VISIBILITY:DEFROSTER/DEFOGGER/HVAC SYSTEM", Component.HVAC),
        ("STRUCTURE:BODY", Component.BODY),
        ("AIR BAGS:FRONTAL", Component.AIRBAGS_RESTRAINTS),
        ("SEAT BELTS:FRONT", Component.AIRBAGS_RESTRAINTS),
        ("TIRES:TREAD/BELT", Component.TIRES_WHEELS),
        ("WHEELS", Component.TIRES_WHEELS),
        ("FORWARD COLLISION AVOIDANCE", Component.OTHER),
    ],
)
def test_nhtsa_component_mapping_is_conservative(raw: str | None, expected: Component) -> None:
    assert map_nhtsa_component(raw) is expected


def test_successful_mapping_preserves_traceability_and_source_detail() -> None:
    record = source_record(COMPDESC="SERVICE BRAKES, HYDRAULIC:FOUNDATION COMPONENTS")
    context = NhtsaSourceContext(
        artifact_filename="COMPLAINTS_RECEIVED_2020-2024.zip",
        artifact_sha256="a" * 64,
    )

    event = map_nhtsa_complaint(record, source_context=context)

    assert event.event_type is EventType.COMPLAINT
    assert event.component is Component.BRAKES
    assert event.source.source_type is SourceType.NHTSA_ODI_COMPLAINT
    assert event.source.organization == SOURCE_ORGANIZATION
    assert event.source.dataset == DATASET_NAME
    assert event.source.source_record_id == "1234567"
    assert event.source.source_reference_id == "11223344"
    assert event.source.original_component == record.component
    assert event.source.artifact_filename == context.artifact_filename
    assert event.source.artifact_sha256 == context.artifact_sha256
    assert event.description == record.narrative


def test_mapping_parses_mileage_dates_and_known_vehicle_configuration() -> None:
    event = map_nhtsa_complaint(source_record())

    assert event.mileage == 12345
    assert event.occurrence_date == date(2020, 1, 1)
    assert event.report_date == date(2020, 1, 2)
    assert event.vehicle.make == "mazda"
    assert event.vehicle.model == "mazda3"
    assert event.vehicle.year == 2017
    assert event.vehicle.known_configuration_id is not None


def test_missing_optional_values_remain_none_and_do_not_block_broad_vehicle() -> None:
    event = map_nhtsa_complaint(
        source_record(MILES="", FAILDATE="", LDATE="", TRANS_TYPE="", DRIVE_TRAIN="")
    )

    assert event.mileage is None
    assert event.occurrence_date is None
    assert event.report_date is None
    assert event.vehicle.known_configuration_id is None
    assert event.vehicle.broad_identity_id.startswith("vehicle_association_")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"DEATHS": "1", "INJURED": "0", "CRASH": "N", "FIRE": "N"}, Severity.CRITICAL),
        ({"DEATHS": "0", "INJURED": "2", "CRASH": "N", "FIRE": "N"}, Severity.HIGH),
        ({"DEATHS": "0", "INJURED": "0", "CRASH": "Y", "FIRE": "N"}, Severity.MODERATE),
        ({"DEATHS": "0", "INJURED": "0", "CRASH": "N", "FIRE": "Y"}, Severity.MODERATE),
        ({"DEATHS": "0", "INJURED": "0", "CRASH": "N", "FIRE": "N"}, Severity.LOW),
        ({"DEATHS": "", "INJURED": "", "CRASH": "", "FIRE": ""}, Severity.UNKNOWN),
    ],
)
def test_mapping_derives_severity_only_from_explicit_indicators(
    overrides: dict[str, str], expected: Severity
) -> None:
    assert map_nhtsa_complaint(source_record(**overrides)).severity is expected


@pytest.mark.parametrize("mileage", ["-1", "12.5", "unknown", " 100"])
def test_malformed_mileage_fails_explicitly(mileage: str) -> None:
    with pytest.raises(EventMappingError) as error:
        map_nhtsa_complaint(source_record(MILES=mileage))

    assert error.value.reason is MappingFailureReason.MALFORMED_MILEAGE


@pytest.mark.parametrize(
    "overrides",
    [
        {"FAILDATE": "20200230"},
        {"FAILDATE": "2020-01-01"},
        {"LDATE": "notadate"},
    ],
)
def test_malformed_dates_fail_explicitly(overrides: dict[str, str]) -> None:
    with pytest.raises(EventMappingError) as error:
        map_nhtsa_complaint(source_record(**overrides))

    assert error.value.reason is MappingFailureReason.MALFORMED_DATE


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"PROD_TYPE": "T"}, MappingFailureReason.UNSUPPORTED_PRODUCT_TYPE),
        ({"CMPLID": ""}, MappingFailureReason.MISSING_SOURCE_IDENTIFIER),
        ({"CMPLID": "ABC"}, MappingFailureReason.MALFORMED_SOURCE_IDENTIFIER),
        ({"MAKETXT": ""}, MappingFailureReason.MISSING_VEHICLE_IDENTITY),
        ({"MODELTXT": "   "}, MappingFailureReason.MISSING_VEHICLE_IDENTITY),
        ({"YEARTXT": "9999"}, MappingFailureReason.INVALID_MODEL_YEAR),
        ({"YEARTXT": "1800"}, MappingFailureReason.INVALID_MODEL_YEAR),
        ({"INJURED": "many"}, MappingFailureReason.MALFORMED_COUNT),
        ({"CRASH": "U"}, MappingFailureReason.MALFORMED_INDICATOR),
    ],
)
def test_mapping_failures_have_stable_reasons(
    overrides: dict[str, str], reason: MappingFailureReason
) -> None:
    with pytest.raises(EventMappingError) as error:
        map_nhtsa_complaint(source_record(**overrides))

    assert error.value.reason is reason


def test_repeated_odi_number_across_component_rows_does_not_collide() -> None:
    brakes = map_nhtsa_complaint(
        source_record(CMPLID="1234567", ODINO="11223344", COMPDESC="SERVICE BRAKES")
    )
    steering = map_nhtsa_complaint(
        source_record(CMPLID="7654321", ODINO="11223344", COMPDESC="STEERING")
    )

    assert brakes.source.source_reference_id == steering.source.source_reference_id
    assert brakes.event_id != steering.event_id


def test_same_source_row_identity_is_stable_across_updateable_attributes() -> None:
    original = map_nhtsa_complaint(source_record(MILES="100", COMPDESC="SERVICE BRAKES"))
    updated = map_nhtsa_complaint(source_record(MILES="200", COMPDESC="STEERING"))

    assert original.event_id == updated.event_id


def test_mapping_does_not_mutate_source_record() -> None:
    record = source_record()
    before = record.as_source_dict()

    map_nhtsa_complaint(record)

    assert record.as_source_dict() == before
