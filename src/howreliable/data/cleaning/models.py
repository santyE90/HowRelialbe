"""Auditable records produced by the Phase 2B cleaning boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class QualityFlag(StrEnum):
    """Stable, non-destructive data-quality observations."""

    BROAD_OTHER_COMPONENT = "broad_other_component"
    ENCODING_CONTROL_CHARACTER = "encoding_control_character"
    EXTREME_MILEAGE = "extreme_mileage"
    EXTREME_REPORT_DELAY = "extreme_report_delay"
    FUTURE_MODEL_YEAR = "future_model_year"
    MISSING_CONFIGURATION_DETAIL = "missing_configuration_detail"
    MISSING_MILEAGE = "missing_mileage"
    MISSING_NARRATIVE = "missing_narrative"
    NEGATIVE_REPORT_DELAY = "negative_report_delay"
    OVERSIZED_NARRATIVE = "oversized_narrative"
    SHORT_NARRATIVE = "short_narrative"
    ZERO_MILEAGE = "zero_mileage"


@dataclass(frozen=True, slots=True)
class CleanComplaintRecord:
    """One accepted source row with canonical values and retained evidence."""

    event_id: str
    source_type: str
    source_record_id: str
    source_reference_id: str | None
    broad_vehicle_id: str
    known_configuration_id: str | None
    source_make: str
    normalized_make: str
    source_model: str
    normalized_model: str
    source_model_year: str
    model_year: int
    component: str
    source_component: str | None
    severity: str
    death_count: int | None
    injury_count: int | None
    crash: bool | None
    fire: bool | None
    mileage: int | None
    mileage_unit: str
    occurrence_date: date | None
    report_date: date | None
    narrative: str | None
    quality_flags: tuple[QualityFlag, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return fields in declared order with JSON-compatible values."""
        result = asdict(self)
        result["occurrence_date"] = (
            self.occurrence_date.isoformat() if self.occurrence_date is not None else None
        )
        result["report_date"] = (
            self.report_date.isoformat() if self.report_date is not None else None
        )
        result["quality_flags"] = [flag.value for flag in self.quality_flags]
        return result


@dataclass(frozen=True, slots=True)
class ExcludedComplaintRecord:
    """Minimal original evidence and the explicit reason a row was excluded."""

    source_record_id: str | None
    source_reference_id: str | None
    source_product_type: str | None
    source_make: str | None
    source_model: str | None
    source_model_year: str | None
    source_component: str | None
    reason: str
    message: str

    def as_dict(self) -> dict[str, str | None]:
        """Return fields in declared deterministic order."""
        return asdict(self)
