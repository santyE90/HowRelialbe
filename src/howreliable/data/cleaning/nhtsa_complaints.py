"""Deterministic, non-destructive cleaning for structured NHTSA complaints."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.cleaning.models import (
    CleanComplaintRecord,
    ExcludedComplaintRecord,
    QualityFlag,
)
from howreliable.data.ingestion.nhtsa_complaints import (
    SOURCE_COLUMNS,
    ArtifactExistsError,
    NhtsaComplaintRecord,
)
from howreliable.data.mapping import EventMappingError, NhtsaSourceContext, map_nhtsa_complaint
from howreliable.domain import Component

CLEANING_VERSION: Final = "nhtsa-complaints-1.0"
MAPPING_VERSION: Final = "nhtsa-reliability-event-1.0"
EXTREME_MILEAGE_THRESHOLD: Final = 500_000
EXTREME_REPORT_DELAY_DAYS: Final = 3_652
SHORT_NARRATIVE_MAX_CHARACTERS: Final = 10
NARRATIVE_DOCUMENTED_MAX_CHARACTERS: Final = 2_048
MILEAGE_UNIT: Final = "miles"


class CleaningError(Exception):
    """Raised when an input or cleaning invariant is violated."""


@dataclass(frozen=True, slots=True)
class CleaningResult:
    """Paths and counts produced by a complete cleaning run."""

    output_path: Path
    exclusions_path: Path
    provenance_path: Path
    input_count: int
    cleaned_count: int
    excluded_count: int
    quality_flag_counts: Mapping[str, int]
    exclusion_reason_counts: Mapping[str, int]


def _has_encoding_control(value: str | None) -> bool:
    return value is not None and any("\u0080" <= character <= "\u009f" for character in value)


def clean_nhtsa_complaint(
    record: NhtsaComplaintRecord,
    *,
    source_context: NhtsaSourceContext | None = None,
) -> CleanComplaintRecord:
    """Clean one mappable complaint while retaining original values and anomalies."""
    event = map_nhtsa_complaint(record, source_context=source_context)
    flags: set[QualityFlag] = set()

    if event.mileage is None:
        flags.add(QualityFlag.MISSING_MILEAGE)
    elif event.mileage == 0:
        flags.add(QualityFlag.ZERO_MILEAGE)
    elif event.mileage > EXTREME_MILEAGE_THRESHOLD:
        flags.add(QualityFlag.EXTREME_MILEAGE)

    if event.vehicle.known_configuration_id is None:
        flags.add(QualityFlag.MISSING_CONFIGURATION_DETAIL)
    if event.component is Component.OTHER:
        flags.add(QualityFlag.BROAD_OTHER_COMPONENT)
    if event.report_date is not None and event.vehicle.year > event.report_date.year:
        flags.add(QualityFlag.FUTURE_MODEL_YEAR)
    if event.occurrence_date is not None and event.report_date is not None:
        delay_days = (event.report_date - event.occurrence_date).days
        if delay_days < 0:
            flags.add(QualityFlag.NEGATIVE_REPORT_DELAY)
        elif delay_days > EXTREME_REPORT_DELAY_DAYS:
            flags.add(QualityFlag.EXTREME_REPORT_DELAY)

    narrative = event.description
    if narrative is None:
        flags.add(QualityFlag.MISSING_NARRATIVE)
    else:
        if len(narrative) <= SHORT_NARRATIVE_MAX_CHARACTERS:
            flags.add(QualityFlag.SHORT_NARRATIVE)
        if len(narrative) > NARRATIVE_DOCUMENTED_MAX_CHARACTERS:
            flags.add(QualityFlag.OVERSIZED_NARRATIVE)

    source_text = (record.make, record.model, record.component, record.narrative)
    if any(_has_encoding_control(value) for value in source_text):
        flags.add(QualityFlag.ENCODING_CONTROL_CHARACTER)

    evidence = event.severity_evidence
    if record.make is None or record.model is None or record.model_year is None:
        raise CleaningError("mapped event unexpectedly lacks original vehicle identity")
    return CleanComplaintRecord(
        event_id=event.event_id,
        source_type=event.source.source_type.value,
        source_record_id=event.source.source_record_id,
        source_reference_id=event.source.source_reference_id,
        broad_vehicle_id=event.vehicle.broad_identity_id,
        known_configuration_id=event.vehicle.known_configuration_id,
        source_make=record.make,
        normalized_make=event.vehicle.make,
        source_model=record.model,
        normalized_model=event.vehicle.model,
        source_model_year=record.model_year,
        model_year=event.vehicle.year,
        component=event.component.value,
        source_component=record.component,
        severity=event.severity.value,
        death_count=evidence.death_count,
        injury_count=evidence.injury_count,
        crash=evidence.crash,
        fire=evidence.fire,
        mileage=event.mileage,
        mileage_unit=MILEAGE_UNIT,
        occurrence_date=event.occurrence_date,
        report_date=event.report_date,
        narrative=narrative,
        quality_flags=tuple(sorted(flags, key=lambda flag: flag.value)),
    )


def excluded_nhtsa_complaint(
    record: NhtsaComplaintRecord, error: EventMappingError
) -> ExcludedComplaintRecord:
    """Represent one rejected row without discarding its relevant original evidence."""
    fields = record.as_source_dict()
    return ExcludedComplaintRecord(
        source_record_id=fields["CMPLID"],
        source_reference_id=fields["ODINO"],
        source_product_type=fields["PROD_TYPE"],
        source_make=fields["MAKETXT"],
        source_model=fields["MODELTXT"],
        source_model_year=fields["YEARTXT"],
        source_component=fields["COMPDESC"],
        reason=error.reason.value,
        message=str(error),
    )


def iter_structured_complaints(path: Path) -> Iterator[NhtsaComplaintRecord]:
    """Stream and validate Phase 1B JSON Lines without coercing source values."""
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise CleaningError(f"input line {line_number} is not valid JSON") from error
                if not isinstance(value, dict) or list(value) != list(SOURCE_COLUMNS):
                    raise CleaningError(
                        f"input line {line_number} does not match the ordered 51-field schema"
                    )
                fields = cast(dict[str, object], value)
                if any(item is not None and not isinstance(item, str) for item in fields.values()):
                    raise CleaningError(
                        f"input line {line_number} contains a non-string source value"
                    )
                yield NhtsaComplaintRecord(
                    tuple(cast(str | None, fields[name]) for name in SOURCE_COLUMNS)
                )
    except OSError as error:
        raise CleaningError(f"cannot read structured input: {path}") from error


def _read_source_provenance(path: Path) -> dict[str, Any]:
    provenance_path = path.with_suffix(f"{path.suffix}.provenance.json")
    try:
        value = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CleaningError(f"cannot read valid source provenance: {provenance_path}") from error
    if not isinstance(value, dict):
        raise CleaningError(f"source provenance must be a JSON object: {provenance_path}")
    required = {
        "source_artifact_filename": str,
        "source_artifact_sha256": str,
        "record_count": int,
    }
    if any(not isinstance(value.get(name), kind) for name, kind in required.items()):
        raise CleaningError(f"source provenance lacks required typed fields: {provenance_path}")
    return cast(dict[str, Any], value)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def clean_structured_artifact(
    input_path: Path,
    output_path: Path,
    *,
    exclusions_path: Path | None = None,
    provenance_path: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CleaningResult:
    """Stream an interim artifact to immutable clean, exclusion, and metadata files."""
    exclusions_path = exclusions_path or output_path.with_suffix(
        f"{output_path.suffix}.excluded.jsonl"
    )
    provenance_path = provenance_path or output_path.with_suffix(
        f"{output_path.suffix}.provenance.json"
    )
    targets = (output_path, exclusions_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing is not None:
        raise ArtifactExistsError(f"refusing to overwrite cleaning output: {existing}")

    source_provenance = _read_source_provenance(input_path)
    context = NhtsaSourceContext(
        artifact_filename=source_provenance["source_artifact_filename"],
        artifact_sha256=source_provenance["source_artifact_sha256"],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exclusions_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    input_count = cleaned_count = excluded_count = 0
    flag_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    try:
        with (
            output_path.open("x", encoding="utf-8", newline="\n") as clean_output,
            exclusions_path.open("x", encoding="utf-8", newline="\n") as excluded_output,
        ):
            for record in iter_structured_complaints(input_path):
                input_count += 1
                try:
                    clean = clean_nhtsa_complaint(record, source_context=context)
                except EventMappingError as error:
                    excluded = excluded_nhtsa_complaint(record, error)
                    excluded_output.write(
                        json.dumps(excluded.as_dict(), ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    excluded_count += 1
                    reason_counts[excluded.reason] += 1
                else:
                    clean_output.write(
                        json.dumps(clean.as_dict(), ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    cleaned_count += 1
                    flag_counts.update(flag.value for flag in clean.quality_flags)

        expected_count = cast(int, source_provenance["record_count"])
        if input_count != expected_count:
            raise CleaningError(
                f"source record count mismatch: provenance={expected_count}, observed={input_count}"
            )
        if input_count != cleaned_count + excluded_count:
            raise CleaningError("record conservation invariant failed")

        metadata = {
            "cleaning_timestamp_utc": _utc_timestamp(clock()),
            "cleaning_version": CLEANING_VERSION,
            "mapping_version": MAPPING_VERSION,
            "input_filename": input_path.name,
            "output_filename": output_path.name,
            "exclusions_filename": exclusions_path.name,
            "source_provenance_filename": input_path.with_suffix(
                f"{input_path.suffix}.provenance.json"
            ).name,
            "source_provenance": source_provenance,
            "policy": {
                "mileage_unit": MILEAGE_UNIT,
                "extreme_mileage_above": EXTREME_MILEAGE_THRESHOLD,
                "extreme_report_delay_days_above": EXTREME_REPORT_DELAY_DAYS,
                "short_narrative_characters_at_most": SHORT_NARRATIVE_MAX_CHARACTERS,
                "oversized_narrative_characters_above": NARRATIVE_DOCUMENTED_MAX_CHARACTERS,
                "normalization": "Unicode NFKC, whitespace collapse/trim, casefold",
                "repeated_source_references": "retained as distinct CMPLID rows",
            },
            "input_count": input_count,
            "cleaned_count": cleaned_count,
            "excluded_count": excluded_count,
            "quality_flag_counts": dict(sorted(flag_counts.items())),
            "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        }
        with provenance_path.open("x", encoding="utf-8", newline="\n") as metadata_output:
            json.dump(metadata, metadata_output, ensure_ascii=False, indent=2)
            metadata_output.write("\n")
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise

    return CleaningResult(
        output_path=output_path,
        exclusions_path=exclusions_path,
        provenance_path=provenance_path,
        input_count=input_count,
        cleaned_count=cleaned_count,
        excluded_count=excluded_count,
        quality_flag_counts=dict(sorted(flag_counts.items())),
        exclusion_reason_counts=dict(sorted(reason_counts.items())),
    )
