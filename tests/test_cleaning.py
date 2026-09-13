"""Tests for deterministic, evidence-preserving NHTSA cleaning."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from howreliable.data.cleaning import (
    CleaningError,
    QualityFlag,
    clean_nhtsa_complaint,
    clean_structured_artifact,
)
from howreliable.data.ingestion.nhtsa_complaints import (
    SOURCE_COLUMNS,
    ArtifactExistsError,
    NhtsaComplaintRecord,
)
from howreliable.data.mapping import EventMappingError

FIXED_CLEANING_TIME = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)


def source_record(**overrides: str) -> NhtsaComplaintRecord:
    values = dict.fromkeys(SOURCE_COLUMNS, "")
    values.update(
        {
            "CMPLID": "1234567",
            "ODINO": "11223344",
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


def write_interim(path: Path, records: list[NhtsaComplaintRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record.as_source_dict(), ensure_ascii=False) + "\n")
    provenance = {
        "source_organization": "NHTSA",
        "dataset_name": "complaints",
        "source_artifact_filename": "complaints.zip",
        "source_artifact_sha256": "a" * 64,
        "adapter_version": "1.0",
        "source_schema_version": "test",
        "record_count": len(records),
        "complete_artifact": True,
    }
    path.with_suffix(f"{path.suffix}.provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )


def test_clean_record_preserves_originals_and_uses_canonical_normalization() -> None:
    source_make = "  \uff2d\uff21\uff3a\uff24\uff21  "
    original = source_record(MAKETXT=source_make, MODELTXT="CX\t 5")

    clean = clean_nhtsa_complaint(original)

    assert original.make == source_make
    assert clean.source_make == source_make
    assert clean.normalized_make == "mazda"
    assert clean.source_model == "CX\t 5"
    assert clean.normalized_model == "cx 5"
    assert clean.source_model_year == "2017"
    assert clean.model_year == 2017
    assert clean.narrative == original.narrative
    assert clean.source_component == original.component
    assert clean.event_id.startswith("event_")
    assert clean.broad_vehicle_id.startswith("vehicle_association_")
    assert clean.mileage == 12345
    assert clean.occurrence_date == date(2020, 1, 1)
    assert clean.report_date == date(2020, 1, 2)
    assert clean.quality_flags == ()
    assert clean.event_id == clean_nhtsa_complaint(original).event_id


@pytest.mark.parametrize(
    ("mileage", "expected"),
    [
        ("", QualityFlag.MISSING_MILEAGE),
        ("0", QualityFlag.ZERO_MILEAGE),
        ("500001", QualityFlag.EXTREME_MILEAGE),
    ],
)
def test_mileage_anomalies_are_preserved_and_flagged(
    mileage: str, expected: QualityFlag
) -> None:
    clean = clean_nhtsa_complaint(source_record(MILES=mileage))

    assert expected in clean.quality_flags
    assert clean.mileage == (None if mileage == "" else int(mileage))
    assert clean.mileage_unit == "miles"


def test_mileage_threshold_is_strictly_above_500000() -> None:
    assert QualityFlag.EXTREME_MILEAGE not in clean_nhtsa_complaint(
        source_record(MILES="500000")
    ).quality_flags


def test_temporal_and_future_year_anomalies_are_flagged_without_rewriting() -> None:
    negative = clean_nhtsa_complaint(
        source_record(YEARTXT="2021", FAILDATE="20200102", LDATE="20200101")
    )
    extreme = clean_nhtsa_complaint(source_record(FAILDATE="20000101", LDATE="20200101"))

    assert QualityFlag.FUTURE_MODEL_YEAR in negative.quality_flags
    assert QualityFlag.NEGATIVE_REPORT_DELAY in negative.quality_flags
    assert negative.occurrence_date == date(2020, 1, 2)
    assert negative.report_date == date(2020, 1, 1)
    assert QualityFlag.EXTREME_REPORT_DELAY in extreme.quality_flags


def test_unknown_model_year_remains_an_explicit_mapping_rejection() -> None:
    with pytest.raises(EventMappingError, match="unknown NHTSA model year"):
        clean_nhtsa_complaint(source_record(YEARTXT="9999"))


def test_narrative_flags_preserve_text_at_boundaries() -> None:
    short_text = "0123456789"
    long_text = "x" * 2049

    short = clean_nhtsa_complaint(source_record(CDESCR=short_text))
    long = clean_nhtsa_complaint(source_record(CDESCR=long_text))

    assert short.narrative == short_text
    assert QualityFlag.SHORT_NARRATIVE in short.quality_flags
    assert long.narrative == long_text
    assert QualityFlag.OVERSIZED_NARRATIVE in long.quality_flags


def test_broad_component_configuration_and_encoding_are_observable() -> None:
    clean = clean_nhtsa_complaint(
        source_record(
            COMPDESC="POWER TRAIN",
            TRANS_TYPE="",
            DRIVE_TRAIN="",
            CDESCR="preserved \u0081 byte mapping",
        )
    )

    assert clean.component == "other"
    assert clean.known_configuration_id is None
    assert QualityFlag.BROAD_OTHER_COMPONENT in clean.quality_flags
    assert QualityFlag.MISSING_CONFIGURATION_DETAIL in clean.quality_flags
    assert QualityFlag.ENCODING_CONTROL_CHARACTER in clean.quality_flags


def test_cleaning_is_deterministic_conserves_rows_and_retains_repeated_odino(
    tmp_path: Path,
) -> None:
    records = [
        source_record(CMPLID="100", ODINO="900", COMPDESC="SERVICE BRAKES"),
        source_record(CMPLID="101", ODINO="900", COMPDESC="STEERING"),
        source_record(CMPLID="102", YEARTXT="9999"),
        source_record(CMPLID="103", PROD_TYPE="T"),
    ]
    source = tmp_path / "interim.jsonl"
    write_interim(source, records)
    first = clean_structured_artifact(
        source, tmp_path / "one" / "clean.jsonl", clock=lambda: FIXED_CLEANING_TIME
    )
    second = clean_structured_artifact(
        source, tmp_path / "two" / "clean.jsonl", clock=lambda: FIXED_CLEANING_TIME
    )

    assert first.input_count == first.cleaned_count + first.excluded_count == 4
    assert first.cleaned_count == 2
    assert first.excluded_count == 2
    assert first.output_path.read_bytes() == second.output_path.read_bytes()
    assert first.exclusions_path.read_bytes() == second.exclusions_path.read_bytes()
    assert first.provenance_path.read_bytes() == second.provenance_path.read_bytes()
    clean_rows = [json.loads(line) for line in first.output_path.read_text().splitlines()]
    assert [row["source_reference_id"] for row in clean_rows] == ["900", "900"]
    assert clean_rows[0]["source_record_id"] != clean_rows[1]["source_record_id"]
    excluded = [json.loads(line) for line in first.exclusions_path.read_text().splitlines()]
    assert excluded[0]["source_model_year"] == "9999"
    assert {row["reason"] for row in excluded} == {
        "invalid_model_year",
        "unsupported_product_type",
    }
    metadata = json.loads(first.provenance_path.read_text())
    assert metadata["input_count"] == 4
    assert metadata["source_provenance"]["source_artifact_sha256"] == "a" * 64
    assert metadata["cleaning_timestamp_utc"] == "2026-09-12T15:00:00Z"
    assert metadata["cleaning_version"] == "nhtsa-complaints-1.0"
    assert list(clean_rows[0]) == [
        "event_id",
        "source_type",
        "source_record_id",
        "source_reference_id",
        "broad_vehicle_id",
        "known_configuration_id",
        "source_make",
        "normalized_make",
        "source_model",
        "normalized_model",
        "source_model_year",
        "model_year",
        "component",
        "source_component",
        "severity",
        "death_count",
        "injury_count",
        "crash",
        "fire",
        "mileage",
        "mileage_unit",
        "occurrence_date",
        "report_date",
        "narrative",
        "quality_flags",
    ]


def test_cleaning_refuses_overwrite_and_preserves_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "interim.jsonl"
    write_interim(source, [source_record()])
    output = tmp_path / "clean.jsonl"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(ArtifactExistsError, match="refusing to overwrite"):
        clean_structured_artifact(source, output)

    assert output.read_text(encoding="utf-8") == "keep"


def test_input_schema_and_provenance_count_mismatches_fail_without_partial_outputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "interim.jsonl"
    write_interim(source, [source_record()])
    source.write_text('{"not":"the schema"}\n', encoding="utf-8")
    output = tmp_path / "clean.jsonl"

    with pytest.raises(CleaningError, match="ordered 51-field schema"):
        clean_structured_artifact(source, output)

    assert not output.exists()
    assert not output.with_suffix(".jsonl.excluded.jsonl").exists()
    assert not output.with_suffix(".jsonl.provenance.json").exists()
