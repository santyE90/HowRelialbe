"""Define observable cohort-level future complaint activity without model features."""

from __future__ import annotations

import calendar
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.features.schema import (
    COMPONENT_VALUES,
    EVENT_FEATURE_SCHEMA,
    FEATURE_VERSION,
)
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.integration.cohorts import (
    INTEGRATED_SCHEMA,
    INTEGRATION_VERSION,
)

TARGET_DEFINITION_VERSION: Final = "future-complaint-activity-1.0"
SELECTED_COMPONENTS: Final = ("engine", "transmission", "electrical", "brakes", "steering")
SEVERE_VALUES: Final = frozenset(("moderate", "high", "critical"))
THRESHOLDS: Final = (1, 2, 3, 5, 10)
CORPUS_END_DATE: Final = date(2024, 12, 31)
ELIGIBLE: Final = "ELIGIBLE"
INCOMPLETE_FUTURE_WINDOW: Final = "INCOMPLETE_FUTURE_WINDOW"
MODEL_YEAR_AFTER_CUTOFF: Final = "MODEL_YEAR_AFTER_CUTOFF"
NO_HISTORICAL_COMPLAINT: Final = "NO_HISTORICAL_COMPLAINT"
INVALID_COHORT_IDENTITY: Final = "INVALID_COHORT_IDENTITY"

TARGET_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "target_definition_version",
    "cutoff_date",
    "horizon_months",
    "future_window_start",
    "future_window_end",
    "target_eligible",
    "eligibility_reason",
    "cohort_age_at_cutoff",
    "historical_complaint_count",
    "future_complaint_count",
    "future_any_complaint",
    "future_complaint_at_least_2",
    "future_complaint_at_least_3",
    "future_complaint_at_least_5",
    "future_complaint_at_least_10",
    "future_severe_complaint_count",
    "future_severe_complaint_activity",
    *(f"future_component_{component}_complaint_count" for component in SELECTED_COMPONENTS),
)


class TargetDefinitionError(Exception):
    """Raised when a target cannot be constructed without ambiguity or leakage."""


@dataclass(frozen=True, slots=True)
class TargetGenerationResult:
    """Paths and accounting from one target-generation run."""

    output_path: Path
    provenance_path: Path
    eligible_cohort_count: int
    ineligible_cohort_count: int
    future_complaint_total: int
    output_sha256: str


@dataclass(slots=True)
class EventCounts:
    """Historical and future accepted complaint counts for one cohort."""

    historical: int = 0
    future: int = 0
    future_severe: int = 0
    first_historical_date: date | None = None
    components: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.components is None:
            self.components = Counter()


def add_months(value: date, months: int) -> date:
    """Add whole calendar months while preserving month-end semantics."""
    if months <= 0:
        raise TargetDefinitionError("horizon months must be positive")
    absolute_month = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def eligibility_reason(
    cohort: Mapping[str, Any],
    *,
    cutoff: date,
    historical_complaint_count: int,
    future_window_complete: bool,
) -> str:
    """Return the single deterministic eligibility status for one cohort/window."""
    if not future_window_complete:
        return INCOMPLETE_FUTURE_WINDOW
    identity = cohort.get("broad_vehicle_id")
    make = cohort.get("normalized_make")
    model = cohort.get("normalized_model")
    model_year = cohort.get("model_year")
    if (
        not isinstance(identity, str)
        or not identity
        or not isinstance(make, str)
        or not make
        or not isinstance(model, str)
        or not model
        or not isinstance(model_year, int)
    ):
        return INVALID_COHORT_IDENTITY
    if model_year > cutoff.year:
        return MODEL_YEAR_AFTER_CUTOFF
    if historical_complaint_count < 1:
        return NO_HISTORICAL_COMPLAINT
    return ELIGIBLE


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetDefinitionError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise TargetDefinitionError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _read_jsonl(path: Path, schema: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                value = json.loads(line)
                if not isinstance(value, dict) or tuple(value) != tuple(schema):
                    raise TargetDefinitionError(
                        f"unexpected schema in {path} line {line_number}"
                    )
                rows.append(cast(dict[str, Any], value))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetDefinitionError(f"cannot read JSON Lines: {path}") from error
    return rows


def _validated_inputs(
    data_directory: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    feature_directory = data_directory / "features"
    event_path = feature_directory / "nhtsa-complaint-events.jsonl"
    feature_provenance_path = feature_directory / "nhtsa-complaint-features.provenance.json"
    integrated_path = data_directory / "integrated/howreliable-cohorts.jsonl"
    integration_provenance_path = data_directory / "integrated/integration.provenance.json"
    feature = _read_json(feature_provenance_path)
    integration = _read_json(integration_provenance_path)
    if feature.get("feature_version") != FEATURE_VERSION:
        raise TargetDefinitionError("unsupported complaint event feature version")
    if tuple(feature.get("event_feature_schema", ())) != EVENT_FEATURE_SCHEMA:
        raise TargetDefinitionError("complaint event schema provenance mismatch")
    if integration.get("integration_version") != INTEGRATION_VERSION:
        raise TargetDefinitionError("unsupported integrated cohort version")
    if tuple(integration.get("integrated_schema", ())) != INTEGRATED_SCHEMA:
        raise TargetDefinitionError("integrated cohort schema provenance mismatch")
    expected_event_sha = feature.get("event_feature_artifact", {}).get("sha256")
    expected_integrated_sha = integration.get("output_sha256")
    if not isinstance(expected_event_sha, str) or sha256_file(event_path) != expected_event_sha:
        raise TargetDefinitionError("complaint event checksum mismatch")
    if (
        not isinstance(expected_integrated_sha, str)
        or sha256_file(integrated_path) != expected_integrated_sha
    ):
        raise TargetDefinitionError("integrated cohort checksum mismatch")
    events = _read_jsonl(event_path, EVENT_FEATURE_SCHEMA)
    cohorts = _read_jsonl(integrated_path, INTEGRATED_SCHEMA)
    if len(events) != feature.get("event_feature_row_count"):
        raise TargetDefinitionError("complaint event row count mismatch")
    if len(cohorts) != integration.get("integrated_row_count"):
        raise TargetDefinitionError("integrated cohort row count mismatch")
    identities = [row["broad_vehicle_id"] for row in cohorts]
    if len(identities) != len(set(identities)):
        raise TargetDefinitionError("duplicate integrated cohort identity")
    cohort_set = set(identities)
    if any(event["broad_vehicle_id"] not in cohort_set for event in events):
        raise TargetDefinitionError("complaint event references an unknown cohort")
    return events, cohorts, {
        "feature": feature,
        "integration": integration,
        "event_path": event_path,
        "integrated_path": integrated_path,
    }


def _quantile(values: Sequence[int | float], probability: float) -> int | float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Sequence[int | float]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "minimum": min(values) if values else None,
        "p50": _quantile(values, 0.50),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "p95": _quantile(values, 0.95),
        "p99": _quantile(values, 0.99),
        "maximum": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "population_variance": statistics.pvariance(values) if values else None,
        "population_standard_deviation": statistics.pstdev(values) if values else None,
    }


def _ranks(values: Sequence[int | float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for index, _ in indexed[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def _correlation(
    left: Sequence[int | float], right: Sequence[int | float]
) -> dict[str, float | int | None]:
    if len(left) != len(right):
        raise TargetDefinitionError("correlation inputs have different lengths")
    if len(left) < 2 or len(set(left)) < 2 or len(set(right)) < 2:
        return {"pair_count": len(left), "pearson": None, "spearman": None}
    return {
        "pair_count": len(left),
        "pearson": statistics.correlation(left, right),
        "spearman": statistics.correlation(_ranks(left), _ranks(right)),
    }


def _age_bucket(age: int) -> str:
    if age == 0:
        return "0"
    if age <= 2:
        return "1-2"
    if age <= 5:
        return "3-5"
    if age <= 10:
        return "6-10"
    if age <= 20:
        return "11-20"
    return "21+"


def _group_diagnostics(
    rows: Sequence[Mapping[str, Any]], key: str
) -> dict[str, dict[str, int | float]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    result: dict[str, dict[str, int | float]] = {}
    for value, group in sorted(grouped.items()):
        positive = sum(cast(bool, row["future_any_complaint"]) for row in group)
        result[value] = {
            "cohort_count": len(group),
            "future_complaint_total": sum(
                cast(int, row["future_complaint_count"]) for row in group
            ),
            "future_any_complaint_count": positive,
            "future_any_complaint_rate": positive / len(group),
        }
    return result


def _diagnostics(
    rows: Sequence[dict[str, Any]], cohorts_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    future = [cast(int, row["future_complaint_count"]) for row in rows]
    historical = [cast(int, row["historical_complaint_count"]) for row in rows]
    observation_days = [cast(int, row["historical_observation_days"]) for row in rows]
    thresholds = {
        str(threshold): {
            "positive_count": (positive := sum(value >= threshold for value in future)),
            "negative_count": len(future) - positive,
            "positive_rate": positive / len(future),
            "negative_rate": (len(future) - positive) / len(future),
        }
        for threshold in THRESHOLDS
    }
    production_pairs = [
        (row, cohorts_by_id[cast(str, row["broad_vehicle_id"])])
        for row in rows
        if cohorts_by_id[cast(str, row["broad_vehicle_id"])]["production_total_units"]
        is not None
    ]
    production_units = [
        cast(int, cohort["production_total_units"]) for _, cohort in production_pairs
    ]
    production_future = [cast(int, row["future_complaint_count"]) for row, _ in production_pairs]
    per_10k = [
        count / units * 10_000
        for count, units in zip(production_future, production_units, strict=True)
    ]
    ranked = sorted(future, reverse=True)
    total = sum(ranked)
    top_one_count = max(1, math.ceil(len(ranked) * 0.01))
    top_ten_count = max(1, math.ceil(len(ranked) * 0.10))
    age_buckets = _group_diagnostics(rows, "age_bucket")
    return {
        "future_complaint_count_distribution": _distribution(future),
        "historical_complaint_count_distribution": _distribution(historical),
        "historical_observation_days_distribution": _distribution(observation_days),
        "threshold_comparison": thresholds,
        "future_complaint_total": total,
        "zero_future_complaint_count": sum(value == 0 for value in future),
        "future_severe_complaint_total": sum(
            cast(int, row["future_severe_complaint_count"]) for row in rows
        ),
        "future_severe_activity_cohort_count": sum(
            cast(bool, row["future_severe_complaint_activity"]) for row in rows
        ),
        "component_support": {
            component: {
                "future_event_count": sum(
                    cast(int, row[f"future_component_{component}_complaint_count"])
                    for row in rows
                ),
                "active_cohort_count": sum(
                    cast(int, row[f"future_component_{component}_complaint_count"]) > 0
                    for row in rows
                ),
            }
            for component in SELECTED_COMPONENTS
        },
        "historical_vs_future_complaint_relationship": _correlation(historical, future),
        "future_event_concentration": {
            "top_1_percent_cohort_count": top_one_count,
            "top_1_percent_future_event_share": (
                sum(ranked[:top_one_count]) / total if total else None
            ),
            "top_10_percent_cohort_count": top_ten_count,
            "top_10_percent_future_event_share": (
                sum(ranked[:top_ten_count]) / total if total else None
            ),
        },
        "by_model_year": _group_diagnostics(rows, "model_year"),
        "by_cohort_age_at_cutoff": _group_diagnostics(rows, "cohort_age_at_cutoff"),
        "by_age_bucket": {
            key: age_buckets[key]
            for key in ("0", "1-2", "3-5", "6-10", "11-20", "21+")
            if key in age_buckets
        },
        "production_normalization_sensitivity": {
            "cohort_count": len(production_pairs),
            "raw_future_complaint_count_distribution": _distribution(production_future),
            "future_complaints_per_10k_produced_distribution": _distribution(per_10k),
            "production_units_vs_raw_future_complaints": _correlation(
                production_units, production_future
            ),
            "interpretation": (
                "Sensitivity analysis only: production is incomplete and is not active "
                "fleet exposure."
            ),
        },
    }


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def generate_future_complaint_targets(
    data_directory: Path,
    output_path: Path,
    provenance_path: Path,
    *,
    cutoff: date,
    horizon_months: int,
    corpus_end_date: date = CORPUS_END_DATE,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> TargetGenerationResult:
    """Generate an eligible-cohort target artifact from accepted complaint events."""
    targets = (output_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing is not None:
        raise ArtifactExistsError(f"refusing to overwrite target output: {existing}")
    window_end = add_months(cutoff, horizon_months)
    window_start = cutoff + timedelta(days=1)
    events, cohorts, validated = _validated_inputs(data_directory)
    if window_end > corpus_end_date:
        raise TargetDefinitionError(
            f"future window ends {window_end.isoformat()} after corpus end "
            f"{corpus_end_date.isoformat()}; no labels emitted"
        )
    counts: defaultdict[str, EventCounts] = defaultdict(EventCounts)
    for event in events:
        report_value = event["report_date"]
        if not isinstance(report_value, str):
            raise TargetDefinitionError("accepted complaint event lacks report_date")
        report_date = date.fromisoformat(report_value)
        identifier = cast(str, event["broad_vehicle_id"])
        counter = counts[identifier]
        if report_date <= cutoff:
            counter.historical += 1
            if counter.first_historical_date is None or report_date < counter.first_historical_date:
                counter.first_historical_date = report_date
        elif report_date <= window_end:
            counter.future += 1
            counter.future_severe += int(cast(str, event["severity"]) in SEVERE_VALUES)
            component = cast(str, event["component"])
            if component in SELECTED_COMPONENTS:
                assert counter.components is not None
                counter.components[component] += 1
    reason_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        identifier = cast(str, cohort["broad_vehicle_id"])
        counter = counts[identifier]
        reason = eligibility_reason(
            cohort,
            cutoff=cutoff,
            historical_complaint_count=counter.historical,
            future_window_complete=True,
        )
        reason_counts[reason] += 1
        if reason != ELIGIBLE:
            continue
        future = counter.future
        components = counter.components or Counter()
        row = {
            "broad_vehicle_id": identifier,
            "normalized_make": cohort["normalized_make"],
            "normalized_model": cohort["normalized_model"],
            "model_year": cohort["model_year"],
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "cutoff_date": cutoff.isoformat(),
            "horizon_months": horizon_months,
            "future_window_start": window_start.isoformat(),
            "future_window_end": window_end.isoformat(),
            "target_eligible": True,
            "eligibility_reason": ELIGIBLE,
            "cohort_age_at_cutoff": cutoff.year - cast(int, cohort["model_year"]),
            "historical_complaint_count": counter.historical,
            "future_complaint_count": future,
            "future_any_complaint": future >= 1,
            "future_complaint_at_least_2": future >= 2,
            "future_complaint_at_least_3": future >= 3,
            "future_complaint_at_least_5": future >= 5,
            "future_complaint_at_least_10": future >= 10,
            "future_severe_complaint_count": counter.future_severe,
            "future_severe_complaint_activity": counter.future_severe >= 1,
            **{
                f"future_component_{component}_complaint_count": components[component]
                for component in SELECTED_COMPONENTS
            },
        }
        if tuple(row) != TARGET_SCHEMA:
            raise AssertionError("target schema construction drifted")
        row["age_bucket"] = _age_bucket(cast(int, row["cohort_age_at_cutoff"]))
        assert counter.first_historical_date is not None
        row["historical_observation_days"] = (cutoff - counter.first_historical_date).days
        rows.append(row)
    rows.sort(
        key=lambda row: (
            cast(str, row["normalized_make"]),
            cast(str, row["normalized_model"]),
            cast(int, row["model_year"]),
            cast(str, row["broad_vehicle_id"]),
        )
    )
    diagnostic_rows = [{**row} for row in rows]
    for row in rows:
        del row["age_bucket"]
        del row["historical_observation_days"]
    try:
        _write_jsonl(output_path, rows)
        cohort_lookup = {cast(str, row["broad_vehicle_id"]): row for row in cohorts}
        diagnostics = _diagnostics(diagnostic_rows, cohort_lookup)
        feature = cast(dict[str, Any], validated["feature"])
        integration = cast(dict[str, Any], validated["integration"])
        metadata = {
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "generation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "target_definition": {
                "prediction_unit": "normalized make + normalized model + model year cohort",
                "source": "accepted Phase 2C NHTSA ODI complaint event features",
                "event_date_field": "report_date",
                "cutoff_semantics": (
                    "history report_date <= cutoff; future cutoff < report_date <= window end"
                ),
                "cutoff": cutoff.isoformat(),
                "horizon_months": horizon_months,
                "future_window_start": window_start.isoformat(),
                "future_window_end": window_end.isoformat(),
                "corpus_end_date": corpus_end_date.isoformat(),
                "eligibility_rules": [
                    "valid cohort identity",
                    "model year is not after cutoff year",
                    "at least one accepted complaint report on or before cutoff",
                    "complete future window lies inside complaint corpus",
                ],
                "binary_zero_semantics": (
                    "No complaint event was observed in the future NHTSA complaint corpus "
                    "during the defined window; this is not a healthy or no-failure claim."
                ),
                "severe_values": sorted(SEVERE_VALUES),
                "severity_rule_version": feature["mapping_version"],
                "component_taxonomy": list(COMPONENT_VALUES),
                "component_taxonomy_version": feature["mapping_version"],
                "selected_component_diagnostics": list(SELECTED_COMPONENTS),
            },
            "input_versions": {
                "feature_version": feature["feature_version"],
                "cleaning_version": feature["cleaning_version"],
                "mapping_version": feature["mapping_version"],
                "integration_version": integration["integration_version"],
            },
            "input_checksums": {
                "accepted_clean_complaints": feature["input_clean_artifact_sha256"],
                "complaint_event_features": sha256_file(cast(Path, validated["event_path"])),
                "integrated_cohorts": sha256_file(cast(Path, validated["integrated_path"])),
            },
            "target_schema": list(TARGET_SCHEMA),
            "total_integrated_cohort_count": len(cohorts),
            "eligible_cohort_count": len(rows),
            "ineligible_cohort_count": len(cohorts) - len(rows),
            "eligibility_reason_counts": dict(sorted(reason_counts.items())),
            "diagnostics": diagnostics,
            "output_filename": output_path.name,
            "output_row_count": len(rows),
            "output_size_bytes": output_path.stat().st_size,
            "output_sha256": sha256_file(output_path),
            "processing_complete": True,
        }
        _write_json(provenance_path, metadata)
    except Exception:
        for path in targets:
            path.unlink(missing_ok=True)
        raise
    return TargetGenerationResult(
        output_path=output_path,
        provenance_path=provenance_path,
        eligible_cohort_count=len(rows),
        ineligible_cohort_count=len(cohorts) - len(rows),
        future_complaint_total=cast(int, diagnostics["future_complaint_total"]),
        output_sha256=sha256_file(output_path),
    )
