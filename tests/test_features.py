"""Tests for target-agnostic Phase 2C feature engineering."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from howreliable.data.cleaning import CleanComplaintRecord, QualityFlag
from howreliable.data.features import (
    COHORT_FEATURE_SCHEMA,
    EVENT_FEATURE_SCHEMA,
    FEATURE_VERSION,
    FeatureArtifactExistsError,
    FeatureGenerationError,
    event_feature_record,
    generate_feature_artifacts,
)

FIXED_TIME = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)


def clean_record(**overrides: object) -> CleanComplaintRecord:
    values: dict[str, object] = {
        "event_id": "event_1",
        "source_type": "nhtsa_odi_complaint",
        "source_record_id": "1",
        "source_reference_id": "900",
        "broad_vehicle_id": "vehicle_mazda3_2020",
        "known_configuration_id": None,
        "source_make": "MAZDA",
        "normalized_make": "mazda",
        "source_model": "MAZDA3",
        "normalized_model": "mazda3",
        "source_model_year": "2020",
        "model_year": 2020,
        "component": "engine",
        "source_component": "ENGINE",
        "severity": "low",
        "death_count": 0,
        "injury_count": 0,
        "crash": False,
        "fire": False,
        "mileage": 100,
        "mileage_unit": "miles",
        "occurrence_date": date(2021, 1, 2),
        "report_date": date(2022, 1, 2),
        "narrative": "Preserved only in the clean layer.",
        "quality_flags": (QualityFlag.MISSING_CONFIGURATION_DETAIL,),
    }
    values.update(overrides)
    return CleanComplaintRecord(**values)  # type: ignore[arg-type]


def write_clean(path: Path, records: list[CleanComplaintRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record.as_dict(), separators=(",", ":")) + "\n")
    provenance = {
        "cleaning_timestamp_utc": "2026-09-12T15:00:00Z",
        "cleaning_version": "nhtsa-complaints-1.0",
        "mapping_version": "nhtsa-reliability-event-1.0",
        "cleaned_count": len(records),
    }
    path.with_suffix(f"{path.suffix}.provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )


def feature_fixture() -> list[CleanComplaintRecord]:
    return [
        clean_record(),
        clean_record(
            event_id="event_2",
            source_record_id="2",
            component="brakes",
            source_component="SERVICE BRAKES",
            severity="high",
            injury_count=2,
            crash=True,
            mileage=500_001,
            occurrence_date=date(2021, 6, 1),
            report_date=date(2022, 6, 11),
            quality_flags=(
                QualityFlag.EXTREME_MILEAGE,
                QualityFlag.MISSING_CONFIGURATION_DETAIL,
            ),
        ),
        clean_record(
            event_id="event_3",
            source_record_id="3",
            source_reference_id="901",
            component="other",
            source_component="POWER TRAIN",
            severity="unknown",
            death_count=None,
            injury_count=None,
            crash=None,
            fire=None,
            mileage=None,
            occurrence_date=None,
            report_date=None,
            quality_flags=(
                QualityFlag.BROAD_OTHER_COMPONENT,
                QualityFlag.MISSING_CONFIGURATION_DETAIL,
                QualityFlag.MISSING_MILEAGE,
            ),
        ),
        clean_record(
            event_id="event_4",
            source_record_id="4",
            source_reference_id="902",
            broad_vehicle_id="vehicle_ford_focus_2025",
            source_make="FORD",
            normalized_make="ford",
            source_model="FOCUS",
            normalized_model="focus",
            source_model_year="2025",
            model_year=2025,
            occurrence_date=date(2023, 1, 1),
            report_date=date(2024, 1, 1),
            quality_flags=(
                QualityFlag.FUTURE_MODEL_YEAR,
                QualityFlag.MISSING_CONFIGURATION_DETAIL,
            ),
        ),
    ]


def test_event_features_have_stable_schema_identity_and_dates() -> None:
    clean = clean_record()

    row = event_feature_record(clean.as_dict())

    assert tuple(row) == EVENT_FEATURE_SCHEMA
    assert row["event_id"] == clean.event_id
    assert row["source_record_id"] == clean.source_record_id
    assert row["broad_vehicle_id"] == clean.broad_vehicle_id
    assert row["report_year"] == 2022
    assert row["report_month"] == 1
    assert row["occurrence_year"] == 2021
    assert row["vehicle_age_at_report_years"] == 2
    assert row["vehicle_age_at_occurrence_years"] == 1
    assert row["occurrence_to_report_delay_days"] == 365
    assert row["mileage"] == 100
    assert row["mileage_observed"] is True
    assert row["quality_missing_configuration_detail"] is True
    assert "narrative" not in row


def test_negative_ages_become_null_with_explicit_status() -> None:
    row = event_feature_record(feature_fixture()[-1].as_dict())

    assert row["vehicle_age_at_report_years"] is None
    assert row["report_before_model_year"] is True
    assert row["vehicle_age_at_occurrence_years"] is None
    assert row["occurrence_before_model_year"] is True
    assert row["report_date"] == "2024-01-01"
    assert row["occurrence_date"] == "2023-01-01"


def test_missing_dates_and_mileage_propagate_without_manufactured_values() -> None:
    row = event_feature_record(feature_fixture()[2].as_dict())

    assert row["report_year"] is None
    assert row["report_month"] is None
    assert row["occurrence_year"] is None
    assert row["vehicle_age_at_report_years"] is None
    assert row["report_before_model_year"] is None
    assert row["occurrence_to_report_delay_days"] is None
    assert row["mileage"] is None
    assert row["mileage_observed"] is False


def test_evidence_features_preserve_observed_and_missing_semantics() -> None:
    positive = event_feature_record(feature_fixture()[1].as_dict())
    missing = event_feature_record(feature_fixture()[2].as_dict())

    assert positive["crash_positive"] is True
    assert positive["injury_positive"] is True
    assert positive["death_positive"] is False
    assert missing["crash_positive"] is None
    assert missing["injury_positive"] is None
    assert missing["death_positive"] is None


def test_feature_generation_is_deterministic_and_cohorts_are_stably_ordered(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clean.jsonl"
    write_clean(source, feature_fixture())

    def run(directory: str):  # type: ignore[no-untyped-def]
        root = tmp_path / directory
        return generate_feature_artifacts(
            source,
            root / "events.jsonl",
            root / "cohorts.jsonl",
            root / "features.provenance.json",
            clock=lambda: FIXED_TIME,
        )

    first = run("one")
    second = run("two")

    assert first.input_clean_record_count == first.event_feature_row_count == 4
    assert first.cohort_feature_row_count == 2
    assert first.event_output_path.read_bytes() == second.event_output_path.read_bytes()
    assert first.cohort_output_path.read_bytes() == second.cohort_output_path.read_bytes()
    assert first.provenance_path.read_bytes() == second.provenance_path.read_bytes()
    events = [json.loads(line) for line in first.event_output_path.read_text().splitlines()]
    cohorts = [json.loads(line) for line in first.cohort_output_path.read_text().splitlines()]
    assert [row["event_id"] for row in events] == ["event_1", "event_2", "event_3", "event_4"]
    assert [row["normalized_make"] for row in cohorts] == ["ford", "mazda"]
    assert all(tuple(row) == EVENT_FEATURE_SCHEMA for row in events)
    assert all(tuple(row) == COHORT_FEATURE_SCHEMA for row in cohorts)


def test_cohort_counts_shares_mileage_and_repeated_odino_are_correct(tmp_path: Path) -> None:
    source = tmp_path / "clean.jsonl"
    write_clean(source, feature_fixture())
    result = generate_feature_artifacts(
        source,
        tmp_path / "events.jsonl",
        tmp_path / "cohorts.jsonl",
        tmp_path / "provenance.json",
        clock=lambda: FIXED_TIME,
    )
    rows = [json.loads(line) for line in result.cohort_output_path.read_text().splitlines()]
    mazda = next(row for row in rows if row["normalized_make"] == "mazda")

    assert mazda["complaint_event_count"] == 3
    assert mazda["unique_event_count"] == 3
    assert mazda["unique_source_reference_count"] == 2
    assert mazda["mileage_observed_count"] == 2
    assert mazda["mileage_observed_share"] == pytest.approx(2 / 3)
    assert mazda["mileage_all_observed_median_miles"] == 250_050.5
    assert mazda["quality_extreme_mileage_count"] == 1
    assert mazda["component_engine_complaint_count"] == 1
    assert mazda["component_brakes_complaint_count"] == 1
    assert mazda["component_other_complaint_count"] == 1
    component_shares = [
        mazda[f"component_{component}_complaint_share"]
        for component in (
            "engine",
            "transmission",
            "electrical",
            "cooling",
            "suspension",
            "brakes",
            "steering",
            "fuel_system",
            "hvac",
            "exhaust",
            "body",
            "airbags_restraints",
            "tires_wheels",
            "other",
            "unknown",
        )
    ]
    assert sum(component_shares) == pytest.approx(1.0)
    assert sum(
        mazda[f"severity_{severity}_complaint_share"]
        for severity in ("unknown", "low", "moderate", "high", "critical")
    ) == pytest.approx(1.0)
    assert mazda["crash_evidence_observed_count"] == 2
    assert mazda["crash_positive_count"] == 1
    assert mazda["crash_positive_share_of_observed"] == 0.5
    assert sum(row["complaint_event_count"] for row in rows) == 4


def test_schemas_contain_no_targets_or_unavailable_source_families() -> None:
    columns = EVENT_FEATURE_SCHEMA + COHORT_FEATURE_SCHEMA

    assert not any("target" in column or "label" in column for column in columns)
    assert not any("risk" in column or "reliability" in column for column in columns)
    assert not any("recall" in column or "population" in column for column in columns)


def test_provenance_records_versions_schemas_and_artifact_metadata(tmp_path: Path) -> None:
    source = tmp_path / "clean.jsonl"
    write_clean(source, feature_fixture())
    result = generate_feature_artifacts(
        source,
        tmp_path / "events.jsonl",
        tmp_path / "cohorts.jsonl",
        tmp_path / "provenance.json",
        clock=lambda: FIXED_TIME,
    )

    metadata = json.loads(result.provenance_path.read_text())
    assert metadata["feature_version"] == FEATURE_VERSION
    assert metadata["cleaning_version"] == "nhtsa-complaints-1.0"
    assert metadata["mapping_version"] == "nhtsa-reliability-event-1.0"
    assert metadata["feature_generation_timestamp_utc"] == "2026-09-12T16:00:00Z"
    assert metadata["event_feature_schema"] == list(EVENT_FEATURE_SCHEMA)
    assert metadata["cohort_feature_schema"] == list(COHORT_FEATURE_SCHEMA)
    assert len(metadata["input_clean_artifact_sha256"]) == 64
    assert metadata["policies"]["temporal_scope"].startswith("whole-corpus")


def test_overwrite_protection_preserves_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "clean.jsonl"
    write_clean(source, feature_fixture())
    event_output = tmp_path / "events.jsonl"
    event_output.write_text("keep", encoding="utf-8")

    with pytest.raises(FeatureArtifactExistsError, match="refusing to overwrite"):
        generate_feature_artifacts(
            source,
            event_output,
            tmp_path / "cohorts.jsonl",
            tmp_path / "provenance.json",
        )
    assert event_output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    "content",
    ['{"wrong":"schema"}\n', "not json\n"],
)
def test_malformed_clean_input_fails_without_partial_outputs(
    tmp_path: Path, content: str
) -> None:
    source = tmp_path / "clean.jsonl"
    write_clean(source, feature_fixture())
    source.write_text(content, encoding="utf-8")
    targets = (tmp_path / "events.jsonl", tmp_path / "cohorts.jsonl", tmp_path / "prov.json")

    with pytest.raises(FeatureGenerationError, match="clean input line 1"):
        generate_feature_artifacts(source, *targets)
    assert not any(path.exists() for path in targets)
