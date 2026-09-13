"""Stable schemas and row-level derivations for Phase 2C features."""

from __future__ import annotations

from datetime import date
from typing import Any, Final, cast

from howreliable.data.cleaning import QualityFlag
from howreliable.domain import Component, Severity

FEATURE_VERSION: Final = "nhtsa-complaint-features-1.0"

QUALITY_FLAG_VALUES: Final = tuple(flag.value for flag in QualityFlag)
COMPONENT_VALUES: Final = tuple(component.value for component in Component)
SEVERITY_VALUES: Final = tuple(severity.value for severity in Severity)

EVENT_FEATURE_SCHEMA: Final = (
    "event_id",
    "source_type",
    "source_record_id",
    "source_reference_id",
    "broad_vehicle_id",
    "known_configuration_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "component",
    "severity",
    "report_date",
    "report_year",
    "report_month",
    "occurrence_date",
    "occurrence_year",
    "vehicle_age_at_report_years",
    "report_before_model_year",
    "vehicle_age_at_occurrence_years",
    "occurrence_before_model_year",
    "occurrence_to_report_delay_days",
    "mileage",
    "mileage_unit",
    "mileage_observed",
    "death_count",
    "injury_count",
    "crash_positive",
    "fire_positive",
    "injury_positive",
    "death_positive",
    *(f"quality_{flag}" for flag in QUALITY_FLAG_VALUES),
)

COHORT_FEATURE_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "unique_event_count",
    "unique_source_reference_count",
    "first_observed_report_date",
    "last_observed_report_date",
    "known_configuration_count",
    "known_configuration_share",
    *(
        column
        for component in COMPONENT_VALUES
        for column in (
            f"component_{component}_complaint_count",
            f"component_{component}_complaint_share",
        )
    ),
    *(
        column
        for severity in SEVERITY_VALUES
        for column in (
            f"severity_{severity}_complaint_count",
            f"severity_{severity}_complaint_share",
        )
    ),
    *(
        column
        for evidence in ("crash", "fire", "injury", "death")
        for column in (
            f"{evidence}_evidence_observed_count",
            f"{evidence}_positive_count",
            f"{evidence}_positive_share_of_observed",
        )
    ),
    "mileage_observed_count",
    "mileage_observed_share",
    "mileage_all_observed_median_miles",
    "report_delay_observed_count",
    "report_delay_observed_share",
    "report_delay_median_days",
    *(
        column
        for flag in QUALITY_FLAG_VALUES
        for column in (f"quality_{flag}_count", f"quality_{flag}_share")
    ),
)


def _year_features(
    value: str | None, model_year: int
) -> tuple[int | None, int | None, bool | None]:
    if value is None:
        return None, None, None
    value_date = date.fromisoformat(value)
    difference = value_date.year - model_year
    return value_date.year, difference if difference >= 0 else None, difference < 0


def event_feature_record(clean: dict[str, Any]) -> dict[str, Any]:
    """Derive one target-agnostic event feature row from one validated clean row."""
    model_year = cast(int, clean["model_year"])
    report_date = cast(str | None, clean["report_date"])
    occurrence_date = cast(str | None, clean["occurrence_date"])
    report_year, age_at_report, report_before_model = _year_features(report_date, model_year)
    occurrence_year, age_at_occurrence, occurrence_before_model = _year_features(
        occurrence_date, model_year
    )
    delay = None
    if report_date is not None and occurrence_date is not None:
        delay = (date.fromisoformat(report_date) - date.fromisoformat(occurrence_date)).days

    death_count = cast(int | None, clean["death_count"])
    injury_count = cast(int | None, clean["injury_count"])
    quality_flags = cast(list[str], clean["quality_flags"])
    flag_set = set(quality_flags)
    row: dict[str, Any] = {
        "event_id": clean["event_id"],
        "source_type": clean["source_type"],
        "source_record_id": clean["source_record_id"],
        "source_reference_id": clean["source_reference_id"],
        "broad_vehicle_id": clean["broad_vehicle_id"],
        "known_configuration_id": clean["known_configuration_id"],
        "normalized_make": clean["normalized_make"],
        "normalized_model": clean["normalized_model"],
        "model_year": model_year,
        "component": clean["component"],
        "severity": clean["severity"],
        "report_date": report_date,
        "report_year": report_year,
        "report_month": date.fromisoformat(report_date).month if report_date else None,
        "occurrence_date": occurrence_date,
        "occurrence_year": occurrence_year,
        "vehicle_age_at_report_years": age_at_report,
        "report_before_model_year": report_before_model,
        "vehicle_age_at_occurrence_years": age_at_occurrence,
        "occurrence_before_model_year": occurrence_before_model,
        "occurrence_to_report_delay_days": delay,
        "mileage": clean["mileage"],
        "mileage_unit": clean["mileage_unit"],
        "mileage_observed": clean["mileage"] is not None,
        "death_count": death_count,
        "injury_count": injury_count,
        "crash_positive": clean["crash"],
        "fire_positive": clean["fire"],
        "injury_positive": injury_count > 0 if injury_count is not None else None,
        "death_positive": death_count > 0 if death_count is not None else None,
    }
    row.update({f"quality_{flag}": flag in flag_set for flag in QUALITY_FLAG_VALUES})
    if tuple(row) != EVENT_FEATURE_SCHEMA:
        raise AssertionError("event feature schema construction drifted")
    return row
