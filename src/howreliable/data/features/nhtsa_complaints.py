"""Deterministic event and vehicle-cohort feature generation."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.cleaning import CleanComplaintRecord
from howreliable.data.features.schema import (
    COHORT_FEATURE_SCHEMA,
    COMPONENT_VALUES,
    EVENT_FEATURE_SCHEMA,
    FEATURE_VERSION,
    QUALITY_FLAG_VALUES,
    SEVERITY_VALUES,
    event_feature_record,
)

CLEAN_RECORD_SCHEMA: Final = tuple(item.name for item in fields(CleanComplaintRecord))


class FeatureGenerationError(Exception):
    """Raised when clean input or a feature invariant is invalid."""


class FeatureArtifactExistsError(FeatureGenerationError):
    """Raised when feature generation would overwrite an artifact."""


@dataclass(frozen=True, slots=True)
class FeatureGenerationResult:
    """Paths and row counts produced by one feature-generation run."""

    event_output_path: Path
    cohort_output_path: Path
    provenance_path: Path
    input_clean_record_count: int
    event_feature_row_count: int
    cohort_feature_row_count: int


@dataclass(slots=True)
class EvidenceAccumulator:
    """Observed and positive evidence counts for one cohort."""

    observed: int = 0
    positive: int = 0

    def add(self, value: bool | None) -> None:
        if value is not None:
            self.observed += 1
            self.positive += int(value)


@dataclass(slots=True)
class CohortAccumulator:
    """Minimal sufficient statistics for one broad vehicle cohort."""

    broad_vehicle_id: str
    normalized_make: str
    normalized_model: str
    model_year: int
    complaint_event_count: int = 0
    event_ids: set[str] = field(default_factory=set)
    source_reference_ids: set[str] = field(default_factory=set)
    report_dates: list[date] = field(default_factory=list)
    known_configuration_count: int = 0
    component_counts: Counter[str] = field(default_factory=Counter)
    severity_counts: Counter[str] = field(default_factory=Counter)
    crash: EvidenceAccumulator = field(default_factory=EvidenceAccumulator)
    fire: EvidenceAccumulator = field(default_factory=EvidenceAccumulator)
    injury: EvidenceAccumulator = field(default_factory=EvidenceAccumulator)
    death: EvidenceAccumulator = field(default_factory=EvidenceAccumulator)
    mileages: list[int] = field(default_factory=list)
    report_delays: list[int] = field(default_factory=list)
    quality_counts: Counter[str] = field(default_factory=Counter)

    def add(self, row: dict[str, Any]) -> None:
        self.complaint_event_count += 1
        self.event_ids.add(cast(str, row["event_id"]))
        source_reference_id = cast(str | None, row["source_reference_id"])
        if source_reference_id is not None:
            self.source_reference_ids.add(source_reference_id)
        if row["report_date"] is not None:
            self.report_dates.append(date.fromisoformat(cast(str, row["report_date"])))
        self.known_configuration_count += int(row["known_configuration_id"] is not None)
        self.component_counts[cast(str, row["component"])] += 1
        self.severity_counts[cast(str, row["severity"])] += 1
        self.crash.add(cast(bool | None, row["crash_positive"]))
        self.fire.add(cast(bool | None, row["fire_positive"]))
        self.injury.add(cast(bool | None, row["injury_positive"]))
        self.death.add(cast(bool | None, row["death_positive"]))
        if row["mileage"] is not None:
            self.mileages.append(cast(int, row["mileage"]))
        if row["occurrence_to_report_delay_days"] is not None:
            self.report_delays.append(cast(int, row["occurrence_to_report_delay_days"]))
        for flag in QUALITY_FLAG_VALUES:
            self.quality_counts[flag] += int(cast(bool, row[f"quality_{flag}"]))

    @staticmethod
    def _share(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    @staticmethod
    def _median(values: list[int]) -> int | float | None:
        return statistics.median(values) if values else None

    def as_feature_record(self) -> dict[str, Any]:
        total = self.complaint_event_count
        row: dict[str, Any] = {
            "broad_vehicle_id": self.broad_vehicle_id,
            "normalized_make": self.normalized_make,
            "normalized_model": self.normalized_model,
            "model_year": self.model_year,
            "complaint_event_count": total,
            "unique_event_count": len(self.event_ids),
            "unique_source_reference_count": len(self.source_reference_ids),
            "first_observed_report_date": min(self.report_dates).isoformat()
            if self.report_dates
            else None,
            "last_observed_report_date": max(self.report_dates).isoformat()
            if self.report_dates
            else None,
            "known_configuration_count": self.known_configuration_count,
            "known_configuration_share": self._share(self.known_configuration_count, total),
        }
        for component in COMPONENT_VALUES:
            count = self.component_counts[component]
            row[f"component_{component}_complaint_count"] = count
            row[f"component_{component}_complaint_share"] = count / total
        for severity in SEVERITY_VALUES:
            count = self.severity_counts[severity]
            row[f"severity_{severity}_complaint_count"] = count
            row[f"severity_{severity}_complaint_share"] = count / total
        for name in ("crash", "fire", "injury", "death"):
            evidence = cast(EvidenceAccumulator, getattr(self, name))
            row[f"{name}_evidence_observed_count"] = evidence.observed
            row[f"{name}_positive_count"] = evidence.positive
            row[f"{name}_positive_share_of_observed"] = self._share(
                evidence.positive, evidence.observed
            )
        row.update(
            {
                "mileage_observed_count": len(self.mileages),
                "mileage_observed_share": len(self.mileages) / total,
                "mileage_all_observed_median_miles": self._median(self.mileages),
                "report_delay_observed_count": len(self.report_delays),
                "report_delay_observed_share": len(self.report_delays) / total,
                "report_delay_median_days": self._median(self.report_delays),
            }
        )
        for flag in QUALITY_FLAG_VALUES:
            count = self.quality_counts[flag]
            row[f"quality_{flag}_count"] = count
            row[f"quality_{flag}_share"] = count / total
        if tuple(row) != COHORT_FEATURE_SCHEMA:
            raise AssertionError("cohort feature schema construction drifted")
        return row


def _expect_type(value: object, kind: type[object], field_name: str, line_number: int) -> None:
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise FeatureGenerationError(
            f"clean input line {line_number} has invalid {field_name}"
        )


def _validate_clean_record(value: object, line_number: int) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != CLEAN_RECORD_SCHEMA:
        raise FeatureGenerationError(
            f"clean input line {line_number} does not match the Phase 2B schema"
        )
    row = cast(dict[str, Any], value)
    for name in (
        "event_id",
        "source_type",
        "source_record_id",
        "broad_vehicle_id",
        "source_make",
        "normalized_make",
        "source_model",
        "normalized_model",
        "source_model_year",
        "component",
        "severity",
        "mileage_unit",
    ):
        _expect_type(row[name], str, name, line_number)
    for name in ("source_reference_id", "known_configuration_id", "source_component", "narrative"):
        if row[name] is not None:
            _expect_type(row[name], str, name, line_number)
    _expect_type(row["model_year"], int, "model_year", line_number)
    for name in ("death_count", "injury_count", "mileage"):
        if row[name] is not None:
            _expect_type(row[name], int, name, line_number)
    for name in ("crash", "fire"):
        if row[name] is not None:
            _expect_type(row[name], bool, name, line_number)
    for name in ("occurrence_date", "report_date"):
        if row[name] is not None:
            _expect_type(row[name], str, name, line_number)
            try:
                date.fromisoformat(cast(str, row[name]))
            except ValueError as error:
                raise FeatureGenerationError(
                    f"clean input line {line_number} has invalid {name}"
                ) from error
    quality_flags = row["quality_flags"]
    if (
        not isinstance(quality_flags, list)
        or any(not isinstance(flag, str) for flag in quality_flags)
        or quality_flags != sorted(set(quality_flags))
        or not set(quality_flags).issubset(set(QUALITY_FLAG_VALUES))
    ):
        raise FeatureGenerationError(
            f"clean input line {line_number} has invalid quality_flags"
        )
    if row["component"] not in COMPONENT_VALUES or row["severity"] not in SEVERITY_VALUES:
        raise FeatureGenerationError(
            f"clean input line {line_number} has unsupported categorical values"
        )
    if row["mileage_unit"] != "miles":
        raise FeatureGenerationError(
            f"clean input line {line_number} has unsupported mileage_unit"
        )
    return row


def iter_clean_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream strictly validated Phase 2B clean JSON Lines records."""
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise FeatureGenerationError(
                        f"clean input line {line_number} is not valid JSON"
                    ) from error
                yield _validate_clean_record(value, line_number)
    except OSError as error:
        raise FeatureGenerationError(f"cannot read clean input: {path}") from error


def _read_cleaning_provenance(input_path: Path) -> dict[str, Any]:
    path = input_path.with_suffix(f"{input_path.suffix}.provenance.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FeatureGenerationError(f"cannot read valid cleaning provenance: {path}") from error
    if not isinstance(value, dict):
        raise FeatureGenerationError(f"cleaning provenance must be a JSON object: {path}")
    for name, kind in (
        ("cleaning_version", str),
        ("mapping_version", str),
        ("cleaned_count", int),
    ):
        if not isinstance(value.get(name), kind):
            raise FeatureGenerationError(f"cleaning provenance lacks typed field {name}")
    return cast(dict[str, Any], value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def generate_feature_artifacts(
    input_path: Path,
    event_output_path: Path,
    cohort_output_path: Path,
    provenance_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FeatureGenerationResult:
    """Generate deterministic event/cohort JSONL plus feature provenance."""
    targets = (event_output_path, cohort_output_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing is not None:
        raise FeatureArtifactExistsError(f"refusing to overwrite feature output: {existing}")
    cleaning_provenance = _read_cleaning_provenance(input_path)
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)

    cohorts: dict[tuple[str, str, int], CohortAccumulator] = {}
    event_ids: set[str] = set()
    input_count = 0
    try:
        with event_output_path.open("x", encoding="utf-8", newline="\n") as event_output:
            for clean in iter_clean_records(input_path):
                event = event_feature_record(clean)
                event_id = cast(str, event["event_id"])
                if event_id in event_ids:
                    raise FeatureGenerationError(f"duplicate clean event_id: {event_id}")
                event_ids.add(event_id)
                event_output.write(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                input_count += 1
                key = (
                    cast(str, event["normalized_make"]),
                    cast(str, event["normalized_model"]),
                    cast(int, event["model_year"]),
                )
                accumulator = cohorts.get(key)
                if accumulator is None:
                    accumulator = CohortAccumulator(
                        broad_vehicle_id=cast(str, event["broad_vehicle_id"]),
                        normalized_make=key[0],
                        normalized_model=key[1],
                        model_year=key[2],
                    )
                    cohorts[key] = accumulator
                elif accumulator.broad_vehicle_id != event["broad_vehicle_id"]:
                    raise FeatureGenerationError("broad vehicle identity is inconsistent")
                accumulator.add(event)

        expected_count = cast(int, cleaning_provenance["cleaned_count"])
        if input_count != expected_count:
            raise FeatureGenerationError(
                f"clean record count mismatch: provenance={expected_count}, observed={input_count}"
            )

        cohort_count = 0
        accounted_rows = 0
        with cohort_output_path.open("x", encoding="utf-8", newline="\n") as cohort_output:
            for key in sorted(cohorts):
                row = cohorts[key].as_feature_record()
                cohort_output.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                cohort_count += 1
                accounted_rows += cast(int, row["complaint_event_count"])
        if accounted_rows != input_count:
            raise FeatureGenerationError("cohort record accounting invariant failed")

        metadata = {
            "feature_generation_timestamp_utc": _utc_timestamp(clock()),
            "feature_version": FEATURE_VERSION,
            "input_clean_artifact_filename": input_path.name,
            "input_clean_artifact_sha256": _sha256_file(input_path),
            "source_cleaning_provenance_filename": input_path.with_suffix(
                f"{input_path.suffix}.provenance.json"
            ).name,
            "source_cleaning_provenance": cleaning_provenance,
            "mapping_version": cleaning_provenance["mapping_version"],
            "cleaning_version": cleaning_provenance["cleaning_version"],
            "input_clean_record_count": input_count,
            "event_feature_row_count": input_count,
            "cohort_feature_row_count": cohort_count,
            "event_feature_schema": list(EVENT_FEATURE_SCHEMA),
            "cohort_feature_schema": list(COHORT_FEATURE_SCHEMA),
            "event_feature_artifact": {
                "filename": event_output_path.name,
                "sha256": _sha256_file(event_output_path),
                "size_bytes": event_output_path.stat().st_size,
            },
            "cohort_feature_artifact": {
                "filename": cohort_output_path.name,
                "sha256": _sha256_file(cohort_output_path),
                "size_bytes": cohort_output_path.stat().st_size,
            },
            "policies": {
                "event_order": "Phase 2B clean input order",
                "cohort_order": "normalized_make, normalized_model, model_year",
                "cohort_grain": "normalized make + normalized model + model year",
                "negative_vehicle_age": "null age with explicit before-model-year status",
                "mileage": "no imputation; all observed values included in median",
                "temporal_scope": "whole-corpus descriptive; not leakage-safe for training",
            },
        }
        with provenance_path.open("x", encoding="utf-8", newline="\n") as provenance_output:
            json.dump(metadata, provenance_output, ensure_ascii=False, indent=2)
            provenance_output.write("\n")
    except Exception:
        for path in targets:
            path.unlink(missing_ok=True)
        raise

    return FeatureGenerationResult(
        event_output_path=event_output_path,
        cohort_output_path=cohort_output_path,
        provenance_path=provenance_path,
        input_clean_record_count=input_count,
        event_feature_row_count=input_count,
        cohort_feature_row_count=cohort_count,
    )
