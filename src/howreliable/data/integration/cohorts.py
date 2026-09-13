"""Join validated processed evidence and review target feasibility without labels."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.exposure.nhtsa_ewr import MATCH_SCHEMA as PRODUCTION_SCHEMA
from howreliable.data.exposure.nhtsa_ewr import (
    MATCHING_VERSION as PRODUCTION_MATCHING_VERSION,
)
from howreliable.data.features.schema import (
    COHORT_FEATURE_SCHEMA,
    EVENT_FEATURE_SCHEMA,
    FEATURE_VERSION,
)
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.manufacturer_communications.nhtsa import (
    COHORT_EVIDENCE_SCHEMA as COMMUNICATION_SCHEMA,
)
from howreliable.data.manufacturer_communications.nhtsa import (
    MATCHING_VERSION as COMMUNICATION_MATCHING_VERSION,
)
from howreliable.data.recalls.nhtsa import COHORT_RECALL_SCHEMA as RECALL_SCHEMA
from howreliable.data.recalls.nhtsa import MATCHING_VERSION as RECALL_MATCHING_VERSION

INTEGRATION_VERSION: Final = "howreliable-integrated-cohorts-1.0"
REVIEW_VERSION: Final = "howreliable-dataset-review-1.0"
CUTOFFS: Final = (
    date(2021, 12, 31),
    date(2022, 12, 31),
    date(2023, 12, 31),
)
COMPONENTS: Final = (
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


class IntegrationError(Exception):
    """Raised when validated inputs cannot be integrated safely."""


def _complaint_name(name: str) -> str:
    if name in {"broad_vehicle_id", "normalized_make", "normalized_model", "model_year"}:
        return name
    if name == "complaint_event_count":
        return name
    if name.endswith("_complaint_count"):
        return f"complaint_{name.removesuffix('_complaint_count')}_count"
    if name.endswith("_complaint_share"):
        return f"complaint_{name.removesuffix('_complaint_share')}_share"
    return f"complaint_{name}"


COMPLAINT_COLUMNS: Final = tuple(_complaint_name(name) for name in COHORT_FEATURE_SCHEMA)
PRODUCTION_COLUMNS: Final = (
    "has_production_match",
    "production_match_status",
    "production_match_method",
    "production_total_units",
    "production_source_record_count",
    "production_source_record_ids",
    "production_reporting_periods",
    "production_ambiguity_candidate_count",
    "production_complaints_per_10k_units",
)
COMMUNICATION_VALUE_COLUMNS: Final = (
    "communication_unique_count",
    "communication_applicability_row_count",
    "communication_source_expanded_row_count",
    "communication_unique_manufacturer_document_id_count",
    "communication_first_date",
    "communication_last_date",
    "communication_summary_observed_count",
    "communication_manufacturer_component_observed_count",
    "communication_type_service_bulletin_count",
    "communication_type_service_campaign_count",
    "communication_type_warranty_program_count",
    "communication_type_over_the_air_count",
    "communication_type_emissions_count",
    "communication_type_other_count",
    "communication_type_unknown_count",
    *(f"communication_component_{item}_count" for item in COMPONENTS),
)
COMMUNICATION_COLUMNS: Final = (
    "has_manufacturer_communications",
    "communication_match_status",
    "communication_match_method",
    *COMMUNICATION_VALUE_COLUMNS,
)
RECALL_VALUE_COLUMNS: Final = (
    "recall_unique_campaign_count",
    "recall_applicability_row_count",
    "recall_first_report_date",
    "recall_last_report_date",
    "recall_noncompliance_campaign_count",
    "recall_known_affected_population_campaign_count",
    "recall_remedy_observed_campaign_count",
    "recall_type_vehicle_campaign_count",
    "recall_type_equipment_campaign_count",
    "recall_type_child_restraint_campaign_count",
    "recall_type_tire_campaign_count",
    "recall_type_other_campaign_count",
    *(f"recall_component_{item}_campaign_count" for item in COMPONENTS),
)
RECALL_COLUMNS: Final = (
    "has_recall",
    "recall_match_status",
    "recall_match_method",
    *RECALL_VALUE_COLUMNS,
)
SUPPORT_COLUMNS: Final = (
    "source_count_available",
    "has_all_four_sources",
    "source_coverage_category",
)
INTEGRATED_SCHEMA: Final = (
    *COMPLAINT_COLUMNS,
    *PRODUCTION_COLUMNS,
    *COMMUNICATION_COLUMNS,
    *RECALL_COLUMNS,
    *SUPPORT_COLUMNS,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrationError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise IntegrationError(f"expected a JSON object: {path}")
    return cast(dict[str, Any], value)


def _iter_jsonl(path: Path, schema: Sequence[str] | None = None) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise IntegrationError(f"{path} line {number} is not an object")
                row = cast(dict[str, Any], value)
                if schema is not None and tuple(row) != tuple(schema):
                    raise IntegrationError(f"{path} line {number} has unexpected schema")
                yield row
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrationError(f"cannot read JSON Lines: {path}") from error


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise IntegrationError("generation timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _assert_checksum(path: Path, expected: object, label: str) -> str:
    if not isinstance(expected, str):
        raise IntegrationError(f"missing expected checksum for {label}")
    actual = sha256_file(path)
    if actual != expected:
        raise IntegrationError(f"checksum mismatch for {label}: {path}")
    return actual


def _paths(data: Path) -> dict[str, Path]:
    return {
        "cohorts": data / "features/nhtsa-vehicle-cohorts.jsonl",
        "events": data / "features/nhtsa-complaint-events.jsonl",
        "feature_provenance": data / "features/nhtsa-complaint-features.provenance.json",
        "production": data / "exposure/complaint-production-matches.jsonl",
        "production_provenance": data / "exposure/complaint-production-matches.provenance.json",
        "communications": data / "manufacturer_communications/cohort-evidence.jsonl",
        "communication_records": data / "manufacturer_communications/communications.jsonl",
        "communication_applications": data
        / "manufacturer_communications/applicability-matches.jsonl",
        "communication_provenance": data / "manufacturer_communications/matching.provenance.json",
        "recalls": data / "recalls/cohort-recalls.jsonl",
        "recall_campaigns": data / "recalls/campaigns.jsonl",
        "recall_applications": data / "recalls/applicability-matches.jsonl",
        "recall_provenance": data / "recalls/matching.provenance.json",
        "recall_ingestion_provenance": data / "recalls/ingestion.provenance.json",
    }


def _validate_inputs(paths: Mapping[str, Path]) -> dict[str, Any]:
    feature = _read_json(paths["feature_provenance"])
    production = _read_json(paths["production_provenance"])
    communication = _read_json(paths["communication_provenance"])
    recall = _read_json(paths["recall_provenance"])
    recall_ingestion = _read_json(paths["recall_ingestion_provenance"])
    if feature.get("feature_version") != FEATURE_VERSION:
        raise IntegrationError("unsupported Phase 2C feature version")
    if tuple(feature.get("cohort_feature_schema", ())) != COHORT_FEATURE_SCHEMA:
        raise IntegrationError("Phase 2C cohort schema provenance mismatch")
    if tuple(feature.get("event_feature_schema", ())) != EVENT_FEATURE_SCHEMA:
        raise IntegrationError("Phase 2C event schema provenance mismatch")
    if production.get("matching_version") != PRODUCTION_MATCHING_VERSION:
        raise IntegrationError("unsupported Phase 2D matching version")
    if tuple(production.get("match_schema", ())) != PRODUCTION_SCHEMA:
        raise IntegrationError("Phase 2D schema provenance mismatch")
    if communication.get("matching_version") != COMMUNICATION_MATCHING_VERSION:
        raise IntegrationError("unsupported Phase 2E matching version")
    if tuple(communication.get("cohort_evidence_schema", ())) != COMMUNICATION_SCHEMA:
        raise IntegrationError("Phase 2E schema provenance mismatch")
    if recall.get("matching_version") != RECALL_MATCHING_VERSION:
        raise IntegrationError("unsupported Phase 2F matching version")
    if tuple(recall.get("cohort_recall_schema", ())) != RECALL_SCHEMA:
        raise IntegrationError("Phase 2F schema provenance mismatch")
    checksums = {
        "phase_2c_cohorts": _assert_checksum(
            paths["cohorts"],
            feature.get("cohort_feature_artifact", {}).get("sha256"),
            "Phase 2C cohorts",
        ),
        "phase_2c_events": _assert_checksum(
            paths["events"],
            feature.get("event_feature_artifact", {}).get("sha256"),
            "Phase 2C events",
        ),
        "phase_2d_production": _assert_checksum(
            paths["production"], production.get("match_output_sha256"), "Phase 2D matches"
        ),
        "phase_2e_communications": _assert_checksum(
            paths["communications"],
            communication.get("output_checksums", {}).get("cohort_evidence"),
            "Phase 2E cohort evidence",
        ),
        "phase_2e_communication_records": _assert_checksum(
            paths["communication_records"],
            communication.get("input_checksums", {}).get("communications"),
            "Phase 2E communication records",
        ),
        "phase_2e_communication_applications": _assert_checksum(
            paths["communication_applications"],
            communication.get("output_checksums", {}).get("applicability_matches"),
            "Phase 2E application matches",
        ),
        "phase_2f_recalls": _assert_checksum(
            paths["recalls"],
            recall.get("outputs", {}).get("cohort-recalls.jsonl"),
            "Phase 2F cohort recalls",
        ),
        "phase_2f_campaigns": _assert_checksum(
            paths["recall_campaigns"],
            recall_ingestion.get("campaigns_sha256"),
            "Phase 2F campaigns",
        ),
        "phase_2f_recall_applications": _assert_checksum(
            paths["recall_applications"],
            recall.get("outputs", {}).get("applicability-matches.jsonl"),
            "Phase 2F application matches",
        ),
    }
    return {
        "feature": feature,
        "production": production,
        "communication": communication,
        "recall": recall,
        "recall_ingestion": recall_ingestion,
        "checksums": checksums,
    }


def _indexed(path: Path, schema: Sequence[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(path, schema):
        identifier = cast(str, row["broad_vehicle_id"])
        if identifier in result:
            raise IntegrationError(f"duplicate broad_vehicle_id in {path}: {identifier}")
        result[identifier] = row
    return result


def _production_values(row: Mapping[str, Any]) -> dict[str, Any]:
    available = row["production_count"] is not None
    return {
        "has_production_match": available,
        "production_match_status": row["production_match_status"],
        "production_match_method": row["match_method"],
        "production_total_units": row["production_count"] if available else None,
        "production_source_record_count": row["production_source_record_count"]
        if available
        else None,
        "production_source_record_ids": row["production_source_record_ids"] if available else None,
        "production_reporting_periods": row["production_reporting_periods"] if available else None,
        "production_ambiguity_candidate_count": row["ambiguity_candidate_count"]
        if available
        else None,
        "production_complaints_per_10k_units": row["complaints_per_10k_produced"]
        if available
        else None,
    }


def _communication_values(row: Mapping[str, Any]) -> dict[str, Any]:
    available = row["unique_communication_count"] > 0
    source_names = (
        "unique_communication_count",
        "communication_applicability_row_count",
        "source_expanded_row_count",
        "unique_manufacturer_document_id_count",
        "first_communication_date",
        "last_communication_date",
        "summary_observed_communication_count",
        "manufacturer_component_observed_communication_count",
        "communication_type_service_bulletin_count",
        "communication_type_service_campaign_count",
        "communication_type_warranty_program_count",
        "communication_type_over_the_air_count",
        "communication_type_emissions_count",
        "communication_type_other_count",
        "communication_type_unknown_count",
        *(f"component_{item}_communication_count" for item in COMPONENTS),
    )
    result = {
        "has_manufacturer_communications": available,
        "communication_match_status": row["manufacturer_communication_match_status"],
        "communication_match_method": row["match_method"],
    }
    result.update(
        {
            target: row[source] if available else None
            for target, source in zip(COMMUNICATION_VALUE_COLUMNS, source_names, strict=True)
        }
    )
    return result


def _recall_values(row: Mapping[str, Any]) -> dict[str, Any]:
    available = row["unique_recall_campaign_count"] > 0
    source_names = (
        "unique_recall_campaign_count",
        "recall_applicability_row_count",
        "first_recall_date",
        "last_recall_date",
        "noncompliance_campaign_count",
        "known_affected_population_campaign_count",
        "remedy_observed_campaign_count",
        "recall_type_vehicle_campaign_count",
        "recall_type_equipment_campaign_count",
        "recall_type_child_restraint_campaign_count",
        "recall_type_tire_campaign_count",
        "recall_type_other_campaign_count",
        *(f"component_{item}_recall_campaign_count" for item in COMPONENTS),
    )
    result = {
        "has_recall": available,
        "recall_match_status": row["recall_match_status"],
        "recall_match_method": row["match_method"],
    }
    result.update(
        {
            target: row[source] if available else None
            for target, source in zip(RECALL_VALUE_COLUMNS, source_names, strict=True)
        }
    )
    return result


def _integrated_rows(
    cohorts: Sequence[dict[str, Any]],
    production: Mapping[str, dict[str, Any]],
    communications: Mapping[str, dict[str, Any]],
    recalls: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    identifiers = {row["broad_vehicle_id"] for row in cohorts}
    for name, source in (
        ("production", production),
        ("communications", communications),
        ("recalls", recalls),
    ):
        if set(source) != identifiers:
            raise IntegrationError(f"{name} cohort identity does not match Phase 2C")
    output = []
    for cohort in cohorts:
        identifier = cast(str, cohort["broad_vehicle_id"])
        row = {_complaint_name(name): value for name, value in cohort.items()}
        row.update(_production_values(production[identifier]))
        row.update(_communication_values(communications[identifier]))
        row.update(_recall_values(recalls[identifier]))
        source_count = (
            1
            + int(row["has_production_match"])
            + int(row["has_manufacturer_communications"])
            + int(row["has_recall"])
        )
        labels = ["complaints"]
        if row["has_production_match"]:
            labels.append("production")
        if row["has_manufacturer_communications"]:
            labels.append("communications")
        if row["has_recall"]:
            labels.append("recalls")
        row.update(
            {
                "source_count_available": source_count,
                "has_all_four_sources": source_count == 4,
                "source_coverage_category": "+".join(labels),
            }
        )
        if tuple(row) != INTEGRATED_SCHEMA:
            raise AssertionError("integrated cohort schema construction drifted")
        output.append(row)
    return output


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Iterable[int | float | None]) -> dict[str, Any]:
    materialized = list(values)
    observed = [float(value) for value in materialized if value is not None]
    return {
        "observed_count": len(observed),
        "missing_count": len(materialized) - len(observed),
        "minimum": min(observed) if observed else None,
        "median": _percentile(observed, 0.5),
        "p75": _percentile(observed, 0.75),
        "p95": _percentile(observed, 0.95),
        "maximum": max(observed) if observed else None,
    }


def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2 + 1
        for index in order[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_sum = sum((value - left_mean) ** 2 for value in left)
    right_sum = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator else None


def _correlation(
    rows: Sequence[Mapping[str, Any]], left_name: str, right_name: str
) -> dict[str, Any]:
    pairs = [
        (float(row[left_name]), float(row[right_name]))
        for row in rows
        if row[left_name] is not None and row[right_name] is not None
    ]
    left = [pair[0] for pair in pairs]
    right = [pair[1] for pair in pairs]
    return {
        "left": left_name,
        "right": right_name,
        "complete_pair_count": len(pairs),
        "pearson": _pearson(left, right),
        "spearman": _pearson(_rank(left), _rank(right)),
        "interpretation": "descriptive association only; not causal",
    }


def _earliest_communication_dates(paths: Mapping[str, Path]) -> dict[str, date]:
    dates = {
        row["communication_id"]: date.fromisoformat(row["date_added"])
        for row in _iter_jsonl(paths["communication_records"])
        if row.get("date_added") is not None
    }
    earliest: dict[str, date] = {}
    for row in _iter_jsonl(paths["communication_applications"]):
        cohort = row.get("matched_broad_vehicle_id")
        observed = dates.get(row["communication_id"])
        if cohort is not None and observed is not None:
            earliest[cohort] = min(earliest.get(cohort, observed), observed)
    return earliest


def _earliest_recall_dates(paths: Mapping[str, Path]) -> dict[str, date]:
    dates = {
        row["campaign_id"]: min(date.fromisoformat(value) for value in row["report_received_dates"])
        for row in _iter_jsonl(paths["recall_campaigns"])
        if row.get("report_received_dates")
    }
    earliest: dict[str, date] = {}
    for row in _iter_jsonl(paths["recall_applications"]):
        cohort = row.get("matched_broad_vehicle_id")
        observed = dates.get(row["campaign_id"])
        if cohort is not None and observed is not None:
            earliest[cohort] = min(earliest.get(cohort, observed), observed)
    return earliest


def _window_end(cutoff: date, months: int) -> date:
    return date(cutoff.year + months // 12, cutoff.month, cutoff.day)


def _temporal_review(paths: Mapping[str, Path], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    prior: dict[date, Counter[str]] = {cutoff: Counter() for cutoff in CUTOFFS}
    future_12: dict[date, Counter[str]] = {cutoff: Counter() for cutoff in CUTOFFS}
    future_24: dict[date, Counter[str]] = {cutoff: Counter() for cutoff in CUTOFFS}
    minimum_date: date | None = None
    maximum_date: date | None = None
    for event in _iter_jsonl(paths["events"], EVENT_FEATURE_SCHEMA):
        report_date = date.fromisoformat(event["report_date"])
        minimum_date = min(minimum_date or report_date, report_date)
        maximum_date = max(maximum_date or report_date, report_date)
        identifier = event["broad_vehicle_id"]
        for cutoff in CUTOFFS:
            if report_date <= cutoff:
                prior[cutoff][identifier] += 1
            elif report_date <= _window_end(cutoff, 12):
                future_12[cutoff][identifier] += 1
                future_24[cutoff][identifier] += 1
            elif report_date <= _window_end(cutoff, 24):
                future_24[cutoff][identifier] += 1
    if minimum_date is None or maximum_date is None:
        raise IntegrationError("complaint event artifact is empty")
    communication_dates = _earliest_communication_dates(paths)
    recall_dates = _earliest_recall_dates(paths)
    by_id = {row["broad_vehicle_id"]: row for row in rows}
    results = []
    for cutoff in CUTOFFS:
        eligible = set(prior[cutoff])
        complete_12 = maximum_date >= _window_end(cutoff, 12)
        complete_24 = maximum_date >= _window_end(cutoff, 24)
        production_before = sum(
            any(
                int(period[:4]) <= cutoff.year
                for period in (by_id[item]["production_reporting_periods"] or [])
            )
            for item in eligible
            if by_id[item]["has_production_match"]
        )
        results.append(
            {
                "cutoff": cutoff.isoformat(),
                "prior_complaint_cohort_count": len(eligible),
                "prior_complaint_event_count": sum(prior[cutoff].values()),
                "cohorts_with_at_least_5_prior_complaints": sum(
                    value >= 5 for value in prior[cutoff].values()
                ),
                "cohorts_with_at_least_10_prior_complaints": sum(
                    value >= 10 for value in prior[cutoff].values()
                ),
                "twelve_month_window_end": _window_end(cutoff, 12).isoformat(),
                "twelve_month_window_complete": complete_12,
                "cohorts_with_full_12_month_future_observation": len(eligible)
                if complete_12
                else 0,
                "cohorts_with_future_complaints_in_12_month_window": sum(
                    future_12[cutoff][item] > 0 for item in eligible
                )
                if complete_12
                else None,
                "future_complaint_events_in_12_month_window": sum(
                    future_12[cutoff][item] for item in eligible
                )
                if complete_12
                else None,
                "twenty_four_month_window_end": _window_end(cutoff, 24).isoformat(),
                "twenty_four_month_window_complete": complete_24,
                "cohorts_with_full_24_month_future_observation": len(eligible)
                if complete_24
                else 0,
                "cohorts_with_future_complaints_in_24_month_window": sum(
                    future_24[cutoff][item] > 0 for item in eligible
                )
                if complete_24
                else None,
                "future_complaint_events_in_24_month_window": sum(
                    future_24[cutoff][item] for item in eligible
                )
                if complete_24
                else None,
                "eligible_cohorts_with_production_period_at_or_before_cutoff": production_before,
                "eligible_cohorts_with_communications_added_by_cutoff": sum(
                    communication_dates.get(item, date.max) <= cutoff for item in eligible
                ),
                "eligible_cohorts_with_recalls_reported_by_cutoff": sum(
                    recall_dates.get(item, date.max) <= cutoff for item in eligible
                ),
            }
        )
    return {
        "complaint_report_date_range": [minimum_date.isoformat(), maximum_date.isoformat()],
        "cutoff_strategy": (
            "calendar-year-end; inclusive history and exclusive-start future windows"
        ),
        "communication_time_field": "NHTSA date_added",
        "recall_time_field": "Part 573 report_received_date",
        "production_time_field": (
            "reporting period only; historical revision-time reconstruction is unavailable"
        ),
        "cutoffs": results,
        "vehicle_age_cutoff_assessment": (
            "Not recommended for the first target: the 2020-2024 received-date complaint "
            "snapshot cannot reconstruct age-three histories for older cohorts."
        ),
        "rolling_cutoff_assessment": (
            "Technically possible for dated complaints/actions, but not preferred initially "
            "because production is quarterly and the complaint observation span is only five years."
        ),
    }


def _review(rows: Sequence[dict[str, Any]], temporal: Mapping[str, Any]) -> dict[str, Any]:
    source_counts = Counter(row["source_count_available"] for row in rows)
    coverage = Counter(row["source_coverage_category"] for row in rows)
    correlations = [
        _correlation(rows, "complaint_event_count", "communication_unique_count"),
        _correlation(rows, "complaint_event_count", "recall_unique_campaign_count"),
        _correlation(rows, "complaint_event_count", "production_total_units"),
        _correlation(rows, "communication_unique_count", "recall_unique_campaign_count"),
        _correlation(rows, "production_total_units", "production_complaints_per_10k_units"),
        _correlation(rows, "model_year", "complaint_event_count"),
        _correlation(rows, "model_year", "source_count_available"),
        _correlation(rows, "complaint_severity_critical_count", "communication_unique_count"),
        _correlation(rows, "complaint_severity_critical_count", "recall_unique_campaign_count"),
    ]
    component_relationships = [
        {
            "component": item,
            "complaint_vs_communication": _correlation(
                rows, f"complaint_component_{item}_share", f"communication_component_{item}_count"
            ),
            "complaint_vs_recall": _correlation(
                rows, f"complaint_component_{item}_share", f"recall_component_{item}_campaign_count"
            ),
        }
        for item in COMPONENTS
    ]
    coverage_events: Counter[str] = Counter()
    by_year: defaultdict[int, Counter[str]] = defaultdict(Counter)
    by_make: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        coverage_events[row["source_coverage_category"]] += row["complaint_event_count"]
        accumulators = (by_year[row["model_year"]], by_make[row["normalized_make"]])
        for accumulator in accumulators:
            accumulator["cohorts"] += 1
            accumulator["complaint_events"] += row["complaint_event_count"]
            accumulator["production"] += int(row["has_production_match"])
            accumulator["communications"] += int(row["has_manufacturer_communications"])
            accumulator["recalls"] += int(row["has_recall"])
            accumulator["all_four"] += int(row["has_all_four_sources"])
    thresholds = {}
    for minimum in (1, 2, 5, 10, 20, 50):
        thresholds[f"minimum_{minimum}_complaints"] = sum(
            row["complaint_event_count"] >= minimum for row in rows
        )
    thresholds.update(
        {
            "at_least_two_sources": sum(row["source_count_available"] >= 2 for row in rows),
            "at_least_three_sources": sum(row["source_count_available"] >= 3 for row in rows),
            "all_four_sources": sum(row["source_count_available"] == 4 for row in rows),
            "at_least_5_complaints_and_3_sources": sum(
                row["complaint_event_count"] >= 5 and row["source_count_available"] >= 3
                for row in rows
            ),
        }
    )
    return {
        "review_version": REVIEW_VERSION,
        "integrated_cohort_count": len(rows),
        "unique_make_count": len({row["normalized_make"] for row in rows}),
        "unique_model_count": len({row["normalized_model"] for row in rows}),
        "model_year_range": [
            min(row["model_year"] for row in rows),
            max(row["model_year"] for row in rows),
        ],
        "coverage_category_counts": dict(sorted(coverage.items())),
        "coverage_category_event_counts": dict(sorted(coverage_events.items())),
        "source_count_available_counts": {str(k): v for k, v in sorted(source_counts.items())},
        "coverage_by_model_year": {
            str(key): dict(sorted(value.items())) for key, value in sorted(by_year.items())
        },
        "coverage_by_make": {
            key: dict(sorted(value.items())) for key, value in sorted(by_make.items())
        },
        "production_available_count": sum(row["has_production_match"] for row in rows),
        "production_missing_count": sum(not row["has_production_match"] for row in rows),
        "communication_available_count": sum(
            row["has_manufacturer_communications"] for row in rows
        ),
        "communication_unmatched_count": sum(
            not row["has_manufacturer_communications"] for row in rows
        ),
        "recall_available_count": sum(row["has_recall"] for row in rows),
        "recall_unmatched_count": sum(not row["has_recall"] for row in rows),
        "complaint_count_distribution": _distribution(row["complaint_event_count"] for row in rows),
        "production_units_distribution": _distribution(
            row["production_total_units"] for row in rows
        ),
        "communication_count_distribution": _distribution(
            row["communication_unique_count"] for row in rows
        ),
        "recall_count_distribution": _distribution(
            row["recall_unique_campaign_count"] for row in rows
        ),
        "mileage_observed_share_distribution": _distribution(
            row["complaint_mileage_observed_share"] for row in rows
        ),
        "cohorts_with_no_observed_mileage": sum(
            row["complaint_mileage_observed_count"] == 0 for row in rows
        ),
        "one_complaint_cohort_count": sum(row["complaint_event_count"] == 1 for row in rows),
        "support_threshold_diagnostics": thresholds,
        "correlations": correlations,
        "component_relationships": component_relationships,
        "temporal_feasibility": dict(temporal),
        "target_family_assessment": {
            "future_complaint_activity": "recommended_for_phase_3a_design",
            "future_severe_complaint_activity": "secondary_only_due_to_safety_scope_and_imbalance",
            "future_component_specific_complaints": (
                "possible_secondary_target_after_component_support_review"
            ),
            "future_regulatory_action": "observable_but_different_safety_regulatory_problem",
            "relative_elevated_issue_activity": (
                "research_candidate_but_exposure_normalization_is_incomplete"
            ),
            "twelve_month_major_repair_risk": "unsupported_without_verified_repair_outcomes",
        },
        "recommended_phase_3a_direction": (
            "Design a cohort-level, calendar-cutoff future complaint-activity target, named "
            "and interpreted strictly as reporting activity rather than repair or failure risk."
        ),
        "negative_example_warning": (
            "No future complaint means no observed report, not a healthy vehicle or "
            "absence of failure."
        ),
        "individual_vehicle_warning": (
            "Individual-vehicle prediction is unsupported because there are no longitudinal "
            "vehicle identities or non-complaint vehicle records."
        ),
        "labels_generated": False,
    }


def integrate_cohorts(
    data_directory: Path,
    output_path: Path,
    provenance_path: Path,
    review_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Integrate all validated cohort evidence and emit descriptive review diagnostics."""
    targets = (output_path, provenance_path, review_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite integration output: {existing}")
    paths = _paths(data_directory)
    validated = _validate_inputs(paths)
    try:
        cohorts = tuple(_iter_jsonl(paths["cohorts"], COHORT_FEATURE_SCHEMA))
        production = _indexed(paths["production"], PRODUCTION_SCHEMA)
        communications = _indexed(paths["communications"], COMMUNICATION_SCHEMA)
        recalls = _indexed(paths["recalls"], RECALL_SCHEMA)
        rows = _integrated_rows(cohorts, production, communications, recalls)
        temporal = _temporal_review(paths, rows)
        review = _review(rows, temporal)
        _write_jsonl(output_path, rows)
        _write_json(review_path, review)
        provenance = {
            "integration_version": INTEGRATION_VERSION,
            "generation_timestamp_utc": _utc(clock()),
            "input_versions": {
                "phase_2c": validated["feature"]["feature_version"],
                "phase_2d": validated["production"]["matching_version"],
                "phase_2e": validated["communication"]["matching_version"],
                "phase_2f": validated["recall"]["matching_version"],
            },
            "input_checksums": validated["checksums"],
            "input_row_counts": {
                "phase_2c_cohorts": len(cohorts),
                "phase_2d_production": len(production),
                "phase_2e_communications": len(communications),
                "phase_2f_recalls": len(recalls),
            },
            "integrated_row_count": len(rows),
            "integrated_column_count": len(INTEGRATED_SCHEMA),
            "integrated_schema": list(INTEGRATED_SCHEMA),
            "policies": {
                "grain": "normalized make + normalized model + model year",
                "base": "Phase 2C complaint cohorts; no cohort dropped",
                "missingness": (
                    "unmatched source values are null; observed zeros require a matched source"
                ),
                "population": "campaign population is not allocated or integrated",
                "temporal": "whole-history rows are descriptive and not training-ready",
                "labels": "no targets or labels generated",
            },
            "source_coverage_counts": review["source_count_available_counts"],
            "output_filename": output_path.name,
            "output_size_bytes": output_path.stat().st_size,
            "output_sha256": sha256_file(output_path),
            "review_filename": review_path.name,
            "review_sha256": sha256_file(review_path),
            "processing_complete": True,
        }
        _write_json(provenance_path, provenance)
        return provenance
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise
