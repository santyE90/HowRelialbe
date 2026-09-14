"""Rebuild cohort features strictly from evidence observable by a cutoff."""

from __future__ import annotations

import calendar
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.features.schema import COMPONENT_VALUES, EVENT_FEATURE_SCHEMA, SEVERITY_VALUES
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.manufacturer_communications.nhtsa import (
    APPLICABILITY_MATCH_SCHEMA as COMMUNICATION_APPLICATION_SCHEMA,
)
from howreliable.data.manufacturer_communications.nhtsa import (
    COMMUNICATION_SCHEMA,
    CommunicationType,
)
from howreliable.data.recalls.nhtsa import APPLICABILITY_MATCH_SCHEMA as RECALL_APPLICATION_SCHEMA
from howreliable.data.recalls.nhtsa import CAMPAIGN_SCHEMA
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA

FEATURE_MATRIX_VERSION: Final = "cohort-features-asof-1.0"
FEATURE_CUTOFF: Final = date(2022, 12, 31)
COMMUNICATION_TYPES: Final = tuple(item.value.lower() for item in CommunicationType)
EVIDENCE_NAMES: Final = ("crash", "fire", "injury", "death")

IDENTITY_COLUMNS: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "cohort_age_at_cutoff",
    "feature_cutoff_date",
)
VOLUME_COLUMNS: Final = (
    "historical_complaint_count",
    "historical_unique_odino_count",
    "historical_years_observed",
    "historical_first_report_date",
    "historical_last_report_date",
    "days_since_last_historical_complaint",
)
RECENCY_COLUMNS: Final = (
    "complaints_last_12m",
    "complaints_last_24m",
    "complaints_last_36m",
)
COMPONENT_COLUMNS: Final = tuple(
    name
    for component in COMPONENT_VALUES
    for name in (
        f"historical_complaint_component_{component}_count",
        f"historical_complaint_component_{component}_share",
    )
)
SEVERITY_COLUMNS: Final = tuple(
    name
    for severity in SEVERITY_VALUES
    for name in (
        f"historical_complaint_severity_{severity}_count",
        f"historical_complaint_severity_{severity}_share",
    )
)
EVIDENCE_COLUMNS: Final = tuple(f"historical_{name}_positive_count" for name in EVIDENCE_NAMES)
MILEAGE_COLUMNS: Final = (
    "historical_mileage_observed_count",
    "historical_mileage_observed_share",
    "historical_mileage_median",
)
COMMUNICATION_COLUMNS: Final = (
    "communication_observed_by_cutoff",
    "communication_asof_status",
    "historical_unique_communication_count",
    "historical_first_communication_date",
    "historical_last_communication_date",
    "days_since_last_communication",
    *(f"historical_communication_type_{item}_count" for item in COMMUNICATION_TYPES),
    *(f"historical_communication_component_{item}_count" for item in COMPONENT_VALUES),
)
RECALL_COLUMNS: Final = (
    "recall_observed_by_cutoff",
    "recall_asof_status",
    "historical_unique_recall_campaign_count",
    "historical_first_recall_date",
    "historical_last_recall_date",
    "days_since_last_recall",
    "historical_recall_noncompliance_count",
    "historical_recall_do_not_drive_count",
    "historical_recall_park_outside_count",
    *(f"historical_recall_component_{item}_count" for item in COMPONENT_VALUES),
)
FEATURE_SCHEMA: Final = (
    *IDENTITY_COLUMNS,
    *VOLUME_COLUMNS,
    *RECENCY_COLUMNS,
    *COMPONENT_COLUMNS,
    *SEVERITY_COLUMNS,
    *EVIDENCE_COLUMNS,
    *MILEAGE_COLUMNS,
    *COMMUNICATION_COLUMNS,
    *RECALL_COLUMNS,
)
FEATURE_FAMILIES: Final = {
    "STATIC": IDENTITY_COLUMNS,
    "HISTORICAL_COMPLAINT_VOLUME": VOLUME_COLUMNS,
    "HISTORICAL_COMPLAINT_RECENCY": RECENCY_COLUMNS,
    "HISTORICAL_COMPLAINT_COMPONENTS": COMPONENT_COLUMNS,
    "HISTORICAL_COMPLAINT_SEVERITY": (*SEVERITY_COLUMNS, *EVIDENCE_COLUMNS, *MILEAGE_COLUMNS),
    "COMMUNICATIONS": COMMUNICATION_COLUMNS,
    "RECALLS": RECALL_COLUMNS,
    "OPTIONAL_PRODUCTION": (),
}


class ModelingDataError(Exception):
    """Raised when leakage-safe modeling inputs fail validation."""


@dataclass(slots=True)
class ComplaintAccumulator:
    count: int = 0
    odinos: set[str] = field(default_factory=set)
    dates: list[date] = field(default_factory=list)
    components: Counter[str] = field(default_factory=Counter)
    severities: Counter[str] = field(default_factory=Counter)
    evidence: Counter[str] = field(default_factory=Counter)
    mileages: list[int] = field(default_factory=list)
    recency: Counter[int] = field(default_factory=Counter)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelingDataError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise ModelingDataError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _jsonl(path: Path, schema: Sequence[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                row = json.loads(line)
                if not isinstance(row, dict) or tuple(row) != tuple(schema):
                    raise ModelingDataError(f"unexpected schema: {path} line {number}")
                result.append(cast(dict[str, Any], row))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelingDataError(f"cannot read JSON Lines: {path}") from error
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")


def _days_since(cutoff: date, dates: Sequence[date]) -> int | None:
    return (cutoff - max(dates)).days if dates else None


def _share(value: int, total: int) -> float:
    return value / total


def _window_start(cutoff: date, months: int) -> date:
    absolute_month = cutoff.year * 12 + cutoff.month - 1 - months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(cutoff.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) + timedelta(days=1)


def generate_asof_features(
    data_directory: Path,
    output_path: Path,
    provenance_path: Path,
    *,
    cutoff: date = FEATURE_CUTOFF,
    target_path: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Generate target-aligned, human-readable as-of-cutoff features."""
    existing = next((path for path in (output_path, provenance_path) if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite modeling output: {existing}")
    target_path = (
        target_path or data_directory / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    )
    target_provenance_path = target_path.with_name(f"{target_path.stem}.provenance.json")
    target_provenance = _json(target_provenance_path)
    if target_provenance.get("target_definition_version") != TARGET_DEFINITION_VERSION:
        raise ModelingDataError("incompatible target definition")
    if target_provenance.get("target_definition", {}).get("cutoff") != cutoff.isoformat():
        raise ModelingDataError("target cutoff mismatch")
    if sha256_file(target_path) != target_provenance.get("output_sha256"):
        raise ModelingDataError("target checksum mismatch")
    targets = _jsonl(target_path, TARGET_SCHEMA)
    target_ids = [cast(str, row["broad_vehicle_id"]) for row in targets]
    if len(target_ids) != len(set(target_ids)):
        raise ModelingDataError("duplicate target cohort identity")
    target_set = set(target_ids)

    feature_dir = data_directory / "features"
    feature_prov = _json(feature_dir / "nhtsa-complaint-features.provenance.json")
    event_path = feature_dir / "nhtsa-complaint-events.jsonl"
    if sha256_file(event_path) != feature_prov.get("event_feature_artifact", {}).get("sha256"):
        raise ModelingDataError("complaint event checksum mismatch")
    events = _jsonl(event_path, EVENT_FEATURE_SCHEMA)
    complaints: defaultdict[str, ComplaintAccumulator] = defaultdict(ComplaintAccumulator)
    for event in events:
        report_value = event["report_date"]
        if not isinstance(report_value, str):
            raise ModelingDataError("accepted complaint lacks report date")
        report_date = date.fromisoformat(report_value)
        if report_date > cutoff:
            continue
        identifier = cast(str, event["broad_vehicle_id"])
        if identifier not in target_set:
            continue
        acc = complaints[identifier]
        acc.count += 1
        if event["source_reference_id"] is not None:
            acc.odinos.add(cast(str, event["source_reference_id"]))
        acc.dates.append(report_date)
        acc.components[cast(str, event["component"])] += 1
        acc.severities[cast(str, event["severity"])] += 1
        for evidence in EVIDENCE_NAMES:
            acc.evidence[evidence] += int(event[f"{evidence}_positive"] is True)
        if event["mileage"] is not None:
            acc.mileages.append(cast(int, event["mileage"]))
        for months in (12, 24, 36):
            acc.recency[months] += int(report_date >= _window_start(cutoff, months))

    comm_dir = data_directory / "manufacturer_communications"
    communication_provenance = _json(comm_dir / "matching.provenance.json")
    communication_path = comm_dir / "communications.jsonl"
    communication_application_path = comm_dir / "applicability-matches.jsonl"
    if sha256_file(communication_path) != communication_provenance.get("input_checksums", {}).get(
        "communications"
    ):
        raise ModelingDataError("communication checksum mismatch")
    if sha256_file(communication_application_path) != communication_provenance.get(
        "output_checksums", {}
    ).get("applicability_matches"):
        raise ModelingDataError("communication applicability checksum mismatch")
    communications = {
        row["communication_id"]: row for row in _jsonl(communication_path, COMMUNICATION_SCHEMA)
    }
    comm_matched_sets: defaultdict[str, set[str]] = defaultdict(set)
    comm_sets: defaultdict[str, set[str]] = defaultdict(set)
    for application in _jsonl(communication_application_path, COMMUNICATION_APPLICATION_SCHEMA):
        identifier = application["matched_broad_vehicle_id"]
        communication = communications[application["communication_id"]]
        added = communication["date_added"]
        if identifier in target_set:
            comm_matched_sets[cast(str, identifier)].add(cast(str, application["communication_id"]))
        if (
            identifier in target_set
            and isinstance(added, str)
            and date.fromisoformat(added) <= cutoff
        ):
            comm_sets[cast(str, identifier)].add(cast(str, application["communication_id"]))

    recall_dir = data_directory / "recalls"
    recall_provenance = _json(recall_dir / "matching.provenance.json")
    recall_ingestion_provenance = _json(recall_dir / "ingestion.provenance.json")
    campaign_path = recall_dir / "campaigns.jsonl"
    recall_application_path = recall_dir / "applicability-matches.jsonl"
    if sha256_file(campaign_path) != recall_ingestion_provenance.get("campaigns_sha256"):
        raise ModelingDataError("recall campaign checksum mismatch")
    if sha256_file(recall_application_path) != recall_provenance.get("outputs", {}).get(
        "applicability-matches.jsonl"
    ):
        raise ModelingDataError("recall applicability checksum mismatch")
    campaigns = {row["campaign_id"]: row for row in _jsonl(campaign_path, CAMPAIGN_SCHEMA)}
    recall_matched_sets: defaultdict[str, set[str]] = defaultdict(set)
    recall_sets: defaultdict[str, set[str]] = defaultdict(set)
    for application in _jsonl(recall_application_path, RECALL_APPLICATION_SCHEMA):
        identifier = application["matched_broad_vehicle_id"]
        campaign = campaigns[application["campaign_id"]]
        dates = [date.fromisoformat(item) for item in campaign["report_received_dates"]]
        if identifier in target_set:
            recall_matched_sets[cast(str, identifier)].add(cast(str, application["campaign_id"]))
        if identifier in target_set and dates and min(dates) <= cutoff:
            recall_sets[cast(str, identifier)].add(cast(str, application["campaign_id"]))

    target_by_id = {cast(str, row["broad_vehicle_id"]): row for row in targets}
    rows: list[dict[str, Any]] = []
    for identifier in target_ids:
        target = target_by_id[identifier]
        complaint = complaints[identifier]
        if complaint.count != target["historical_complaint_count"]:
            raise ModelingDataError(f"historical complaint mismatch: {identifier}")
        comm_docs = [communications[item] for item in sorted(comm_sets[identifier])]
        comm_matched_docs = [communications[item] for item in sorted(comm_matched_sets[identifier])]
        comm_dates = [date.fromisoformat(cast(str, item["date_added"])) for item in comm_docs]
        recall_docs = [campaigns[item] for item in sorted(recall_sets[identifier])]
        recall_matched_docs = [campaigns[item] for item in sorted(recall_matched_sets[identifier])]
        recall_dates = [
            min(date.fromisoformat(value) for value in item["report_received_dates"])
            for item in recall_docs
        ]
        row: dict[str, Any] = {
            "broad_vehicle_id": identifier,
            "normalized_make": target["normalized_make"],
            "normalized_model": target["normalized_model"],
            "model_year": target["model_year"],
            "cohort_age_at_cutoff": cutoff.year - cast(int, target["model_year"]),
            "feature_cutoff_date": cutoff.isoformat(),
            "historical_complaint_count": complaint.count,
            "historical_unique_odino_count": len(complaint.odinos),
            "historical_years_observed": (max(complaint.dates) - min(complaint.dates)).days
            / 365.25,
            "historical_first_report_date": min(complaint.dates).isoformat(),
            "historical_last_report_date": max(complaint.dates).isoformat(),
            "days_since_last_historical_complaint": _days_since(cutoff, complaint.dates),
            "complaints_last_12m": complaint.recency[12],
            "complaints_last_24m": complaint.recency[24],
            "complaints_last_36m": complaint.recency[36],
        }
        for component in COMPONENT_VALUES:
            count = complaint.components[component]
            row[f"historical_complaint_component_{component}_count"] = count
            row[f"historical_complaint_component_{component}_share"] = _share(
                count, complaint.count
            )
        for severity in SEVERITY_VALUES:
            count = complaint.severities[severity]
            row[f"historical_complaint_severity_{severity}_count"] = count
            row[f"historical_complaint_severity_{severity}_share"] = _share(count, complaint.count)
        for evidence in EVIDENCE_NAMES:
            row[f"historical_{evidence}_positive_count"] = complaint.evidence[evidence]
        row.update(
            {
                "historical_mileage_observed_count": len(complaint.mileages),
                "historical_mileage_observed_share": len(complaint.mileages) / complaint.count,
                "historical_mileage_median": statistics.median(complaint.mileages)
                if complaint.mileages
                else None,
                "communication_observed_by_cutoff": bool(comm_docs),
                "communication_asof_status": (
                    "OBSERVED_RECORDS"
                    if comm_docs
                    else (
                        "MATCHED_RECORDS_AFTER_CUTOFF_ONLY"
                        if comm_matched_docs
                        else "NO_MATCHED_RECORD"
                    )
                ),
                "historical_unique_communication_count": len(comm_docs),
                "historical_first_communication_date": min(comm_dates).isoformat()
                if comm_dates
                else None,
                "historical_last_communication_date": max(comm_dates).isoformat()
                if comm_dates
                else None,
                "days_since_last_communication": _days_since(cutoff, comm_dates),
            }
        )
        for item in COMMUNICATION_TYPES:
            row[f"historical_communication_type_{item}_count"] = sum(
                doc["communication_type"].lower() == item for doc in comm_docs
            )
        for component in COMPONENT_VALUES:
            row[f"historical_communication_component_{component}_count"] = sum(
                component in doc["canonical_components"] for doc in comm_docs
            )
        row.update(
            {
                "recall_observed_by_cutoff": bool(recall_docs),
                "recall_asof_status": (
                    "OBSERVED_RECORDS"
                    if recall_docs
                    else (
                        "MATCHED_RECORDS_AFTER_CUTOFF_ONLY"
                        if recall_matched_docs
                        else "NO_MATCHED_RECORD"
                    )
                ),
                "historical_unique_recall_campaign_count": len(recall_docs),
                "historical_first_recall_date": min(recall_dates).isoformat()
                if recall_dates
                else None,
                "historical_last_recall_date": max(recall_dates).isoformat()
                if recall_dates
                else None,
                "days_since_last_recall": _days_since(cutoff, recall_dates),
                "historical_recall_noncompliance_count": sum(
                    bool(doc["fmvss_numbers"] or doc["regulation_part_numbers"])
                    for doc in recall_docs
                ),
                "historical_recall_do_not_drive_count": sum(
                    "Yes" in doc["do_not_drive_values"] for doc in recall_docs
                ),
                "historical_recall_park_outside_count": sum(
                    "Yes" in doc["park_outside_values"] for doc in recall_docs
                ),
            }
        )
        for component in COMPONENT_VALUES:
            row[f"historical_recall_component_{component}_count"] = sum(
                component in doc["canonical_components"] for doc in recall_docs
            )
        if tuple(row) != FEATURE_SCHEMA:
            raise AssertionError("feature schema construction drifted")
        rows.append(row)
    rows.sort(
        key=lambda row: (
            row["normalized_make"],
            row["normalized_model"],
            row["model_year"],
            row["broad_vehicle_id"],
        )
    )
    if {row["broad_vehicle_id"] for row in rows} != target_set:
        raise ModelingDataError("feature/target identity mismatch")
    try:
        _write_jsonl(output_path, rows)
        metadata = {
            "feature_matrix_version": FEATURE_MATRIX_VERSION,
            "generation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "cutoff": cutoff.isoformat(),
            "row_count": len(rows),
            "column_count": len(FEATURE_SCHEMA),
            "schema": list(FEATURE_SCHEMA),
            "feature_families": {key: list(value) for key, value in FEATURE_FAMILIES.items()},
            "input_checksums": {
                "target": sha256_file(target_path),
                "complaint_events": sha256_file(event_path),
                "communications": sha256_file(communication_path),
                "communication_applications": sha256_file(communication_application_path),
                "recall_campaigns": sha256_file(campaign_path),
                "recall_applications": sha256_file(recall_application_path),
            },
            "policies": {
                "complaints": "report_date <= cutoff",
                "communications": "date_added <= cutoff; unique communication identity",
                "recalls": "Part 573 report received <= cutoff; unique campaign identity",
                "production": "excluded: historical filing/revision state is not reconstructable",
                "whole_history_integration": "not used except target-provided static identity",
            },
            "source_availability": {
                "communications_observed": sum(
                    row["communication_observed_by_cutoff"] for row in rows
                ),
                "communications_matched_after_cutoff_only": sum(
                    row["communication_asof_status"] == "MATCHED_RECORDS_AFTER_CUTOFF_ONLY"
                    for row in rows
                ),
                "communications_unmatched": sum(
                    row["communication_asof_status"] == "NO_MATCHED_RECORD" for row in rows
                ),
                "recalls_observed": sum(row["recall_observed_by_cutoff"] for row in rows),
                "recalls_matched_after_cutoff_only": sum(
                    row["recall_asof_status"] == "MATCHED_RECORDS_AFTER_CUTOFF_ONLY" for row in rows
                ),
                "recalls_unmatched": sum(
                    row["recall_asof_status"] == "NO_MATCHED_RECORD" for row in rows
                ),
            },
            "output_filename": output_path.name,
            "output_size_bytes": output_path.stat().st_size,
            "output_sha256": sha256_file(output_path),
            "processing_complete": True,
        }
        _write_json(provenance_path, metadata)
    except Exception:
        output_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        raise
    return metadata
