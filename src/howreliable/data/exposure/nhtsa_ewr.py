"""Adapter for the official public NHTSA EWR light-vehicle production API."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file

SOURCE_ORGANIZATION: Final = "National Highway Traffic Safety Administration (NHTSA)"
DATASET_NAME: Final = "Early Warning Reporting Light Vehicle Production"
API_ROOT: Final = "https://api.nhtsa.gov/ewr"
SOURCE_CATEGORY: Final = "L"
INGESTION_VERSION: Final = "ewr-production-ingestion-1.0"
MATCHING_VERSION: Final = "ewr-production-matching-1.0"
SOURCE_SCHEMA_VERSION: Final = None
DEFAULT_REPORT_YEARS: Final = (2003, 2014, 2025)
EXPLICIT_ALIASES: Final[Mapping[tuple[str, str], tuple[str, str]]] = {}
MATCH_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "production_match_status",
    "match_method",
    "production_count",
    "production_source_record_count",
    "production_source_record_ids",
    "production_reporting_periods",
    "ambiguity_candidate_count",
    "complaints_per_10k_produced",
)
PRODUCTION_FIELDS: Final = (
    "fuelPropulsionSystem",
    "make",
    "model",
    "modelYear",
    "platform",
    "reportCategory",
    "totalProduction",
    "typeCode",
)
REQUIRED_PRODUCTION_FIELDS: Final = frozenset(PRODUCTION_FIELDS) - {
    "fuelPropulsionSystem",
    "platform",
}


class ExposureError(Exception):
    """Raised for invalid EWR input or an exposure-processing invariant."""


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    EXPLICIT_ALIAS = "EXPLICIT_ALIAS"
    AMBIGUOUS = "AMBIGUOUS"
    NO_PRODUCTION_RECORD = "NO_PRODUCTION_RECORD"


def normalize_identity(value: str) -> str:
    """Apply the same conservative NFKC/whitespace/case normalization as vehicles."""
    if not isinstance(value, str):
        raise ExposureError("identity value must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized:
        raise ExposureError("identity value must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class NhtsaEwrProductionRecord:
    """One source-faithful API row with report context supplied by its endpoint."""

    manufacturer_id: int
    manufacturer_name: str
    report_id: int
    reporting_year: int
    reporting_quarter: int
    fuel_propulsion_system: str | None
    make: str
    model: str
    model_year: str
    platform: str | None
    report_category: str
    total_production: int
    type_code: str
    source_row_number: int

    @property
    def source_record_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return f"nhtsa_ewr_{hashlib.sha256(payload.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CanonicalProductionRecord:
    """Immutable production exposure record; intentionally not a reliability event."""

    source_record_id: str
    manufacturer_id: int
    reporting_entity: str
    report_id: int
    reporting_year: int
    reporting_quarter: int
    original_make: str
    original_model: str
    normalized_make: str
    normalized_model: str
    model_year: int
    platform: str | None
    vehicle_type_code: str
    fuel_propulsion_system: str | None
    production_count: int


@dataclass(frozen=True, slots=True)
class AggregatedProductionCohort:
    """Latest authoritative source configurations summed to complaint cohort grain."""

    normalized_make: str
    normalized_model: str
    model_year: int
    production_count: int
    source_record_count: int
    source_record_ids: tuple[str, ...]
    reporting_periods: tuple[str, ...]
    reporting_entity_ids: tuple[int, ...]


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ExposureError(f"{field} must be an integer >= {minimum}")
    return value


def parse_production_payload(
    payload: object,
    *,
    manufacturer_id: int,
    manufacturer_name: str,
    report_id: int,
    reporting_year: int,
    reporting_quarter: int,
) -> tuple[NhtsaEwrProductionRecord, ...]:
    """Validate the actual current public API schema without numeric coercion."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ExposureError("production response must contain a results array")
    results = cast(list[object], payload["results"])
    meta = payload.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("pagination"), dict):
        pagination = cast(dict[str, object], meta["pagination"])
        if pagination.get("nextUrl") is not None or pagination.get("total") != len(results):
            raise ExposureError("production response is not a complete report")
    if not 1 <= reporting_quarter <= 4:
        raise ExposureError("reporting_quarter must be between 1 and 4")
    records: list[NhtsaEwrProductionRecord] = []
    for row_number, value in enumerate(results, 1):
        if not isinstance(value, dict):
            raise ExposureError(f"production row {row_number} must be an object")
        row = cast(dict[str, object], value)
        fields = set(row)
        if not REQUIRED_PRODUCTION_FIELDS <= fields <= set(PRODUCTION_FIELDS):
            raise ExposureError(
                f"production row {row_number} does not match a supported official schema"
            )
        for name in ("make", "model", "modelYear", "reportCategory", "typeCode"):
            if not isinstance(row[name], str) or not row[name]:
                raise ExposureError(f"production row {row_number} has invalid {name}")
        for name in ("fuelPropulsionSystem", "platform"):
            if row.get(name) is not None and not isinstance(row[name], str):
                raise ExposureError(f"production row {row_number} has invalid {name}")
        production = _integer(row["totalProduction"], "totalProduction")
        if row["reportCategory"] != SOURCE_CATEGORY:
            raise ExposureError(f"production row {row_number} is not light-vehicle category L")
        records.append(
            NhtsaEwrProductionRecord(
                manufacturer_id=_integer(manufacturer_id, "manufacturer_id", minimum=1),
                manufacturer_name=manufacturer_name,
                report_id=_integer(report_id, "report_id", minimum=1),
                reporting_year=_integer(reporting_year, "reporting_year", minimum=2000),
                reporting_quarter=_integer(reporting_quarter, "reporting_quarter", minimum=1),
                fuel_propulsion_system=cast(str | None, row.get("fuelPropulsionSystem")),
                make=cast(str, row["make"]),
                model=cast(str, row["model"]),
                model_year=cast(str, row["modelYear"]),
                platform=cast(str | None, row.get("platform")),
                report_category=cast(str, row["reportCategory"]),
                total_production=production,
                type_code=cast(str, row["typeCode"]),
                source_row_number=row_number,
            )
        )
    return tuple(records)


def canonicalize(record: NhtsaEwrProductionRecord) -> CanonicalProductionRecord:
    try:
        year = int(record.model_year)
    except ValueError as error:
        raise ExposureError(f"invalid model year: {record.model_year!r}") from error
    if not 1886 <= year <= 2100 or str(year) != record.model_year:
        raise ExposureError(f"invalid model year: {record.model_year!r}")
    return CanonicalProductionRecord(
        source_record_id=record.source_record_id,
        manufacturer_id=record.manufacturer_id,
        reporting_entity=record.manufacturer_name,
        report_id=record.report_id,
        reporting_year=record.reporting_year,
        reporting_quarter=record.reporting_quarter,
        original_make=record.make,
        original_model=record.model,
        normalized_make=normalize_identity(record.make),
        normalized_model=normalize_identity(record.model),
        model_year=year,
        platform=record.platform,
        vehicle_type_code=record.type_code,
        fuel_propulsion_system=record.fuel_propulsion_system,
        production_count=record.total_production,
    )


def aggregate_production(
    records: Iterable[CanonicalProductionRecord],
) -> tuple[AggregatedProductionCohort, ...]:
    """Select latest cumulative/final value per configuration, then sum configurations."""
    latest: dict[tuple[object, ...], CanonicalProductionRecord] = {}
    for record in records:
        configuration = (
            record.manufacturer_id,
            record.normalized_make,
            record.normalized_model,
            record.model_year,
            record.platform,
            record.vehicle_type_code,
            record.fuel_propulsion_system,
        )
        incumbent = latest.get(configuration)
        ordering = (record.reporting_year, record.reporting_quarter, record.report_id)
        if incumbent is None or ordering > (
            incumbent.reporting_year,
            incumbent.reporting_quarter,
            incumbent.report_id,
        ):
            latest[configuration] = record
        elif ordering == (
            incumbent.reporting_year,
            incumbent.reporting_quarter,
            incumbent.report_id,
        ):
            if dataclass_payload(record, exclude={"source_record_id"}) != dataclass_payload(
                incumbent, exclude={"source_record_id"}
            ):
                raise ExposureError("conflicting repeated source configuration in one report")
            if record.source_record_id < incumbent.source_record_id:
                latest[configuration] = record

    groups: dict[tuple[str, str, int], list[CanonicalProductionRecord]] = defaultdict(list)
    for record in latest.values():
        groups[(record.normalized_make, record.normalized_model, record.model_year)].append(record)
    cohorts = []
    for key, members in sorted(groups.items()):
        ordered = sorted(members, key=lambda item: item.source_record_id)
        cohorts.append(
            AggregatedProductionCohort(
                *key,
                production_count=sum(item.production_count for item in ordered),
                source_record_count=len(ordered),
                source_record_ids=tuple(item.source_record_id for item in ordered),
                reporting_periods=tuple(
                    sorted({f"{item.reporting_year}Q{item.reporting_quarter}" for item in ordered})
                ),
                reporting_entity_ids=tuple(sorted({item.manufacturer_id for item in ordered})),
            )
        )
    return tuple(cohorts)


def dataclass_payload(
    value: CanonicalProductionRecord, *, exclude: set[str]
) -> dict[str, object]:
    """Return dataclass fields except lineage-only identities for duplicate comparison."""
    return {key: item for key, item in asdict(value).items() if key not in exclude}


def _json_rows(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ExposureError(f"{path} line {number} must be an object")
                yield cast(dict[str, Any], value)
    except (OSError, json.JSONDecodeError) as error:
        raise ExposureError(f"cannot read valid JSON Lines from {path}") from error


def match_complaint_cohorts(
    complaints: Iterable[Mapping[str, Any]],
    production: Sequence[AggregatedProductionCohort],
    *,
    aliases: Mapping[tuple[str, str], tuple[str, str]] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Conservatively match exact identities, then optional inspectable aliases."""
    index: dict[tuple[str, str, int], list[AggregatedProductionCohort]] = defaultdict(list)
    for item in production:
        index[(item.normalized_make, item.normalized_model, item.model_year)].append(item)
    aliases = EXPLICIT_ALIASES if aliases is None else aliases
    output = []
    for complaint in complaints:
        make = cast(str, complaint["normalized_make"])
        model = cast(str, complaint["normalized_model"])
        year = cast(int, complaint["model_year"])
        candidates = index.get((make, model, year), [])
        method: str | None = "exact_normalized" if candidates else None
        alias = aliases.get((make, model))
        if not candidates and alias is not None:
            candidates = index.get((*alias, year), [])
            method = "explicit_alias" if candidates else None
        if len(candidates) > 1:
            status = MatchStatus.AMBIGUOUS
        elif candidates:
            status = MatchStatus.EXPLICIT_ALIAS if method == "explicit_alias" else MatchStatus.EXACT
        else:
            status = MatchStatus.NO_PRODUCTION_RECORD
        matched = candidates[0] if len(candidates) == 1 else None
        count = matched.production_count if matched else None
        rate = None
        if count is not None and count > 0:
            rate = cast(int, complaint["complaint_event_count"]) * 10_000 / count
        output.append(
            {
                "broad_vehicle_id": complaint["broad_vehicle_id"],
                "normalized_make": make,
                "normalized_model": model,
                "model_year": year,
                "complaint_event_count": complaint["complaint_event_count"],
                "production_match_status": status.value,
                "match_method": method,
                "production_count": count,
                "production_source_record_count": matched.source_record_count if matched else 0,
                "production_source_record_ids": list(matched.source_record_ids) if matched else [],
                "production_reporting_periods": list(matched.reporting_periods) if matched else [],
                "ambiguity_candidate_count": len(candidates) if len(candidates) > 1 else 0,
                "complaints_per_10k_produced": rate,
            }
        )
        if tuple(output[-1]) != MATCH_SCHEMA:
            raise AssertionError("production match schema construction drifted")
    return tuple(output)


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


def _fetch(url: str, *, timeout: float = 120.0) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return cast(bytes, response.read())
    except (OSError, urllib.error.URLError) as error:
        raise ExposureError(f"official API request failed: {url}: {error}") from error


def _artifact_name(prefix: str, identifier: int) -> str:
    return f"{prefix}-{identifier}.json"


def retrieve_snapshot(
    raw_directory: Path,
    *,
    report_years: Sequence[int] = DEFAULT_REPORT_YEARS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    workers: int = 8,
) -> Path:
    """Acquire immutable API response files for selected Q4 light-vehicle filings."""
    manifest_path = raw_directory / "snapshot-manifest.json"
    if raw_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite raw snapshot: {raw_directory}")
    raw_directory.mkdir(parents=True)
    retrieval = clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
    artifacts: list[dict[str, object]] = []
    try:
        manufacturers_url = f"{API_ROOT}/manufacturers?max=1000"
        manufacturers_bytes = _fetch(manufacturers_url)
        manufacturers_path = raw_directory / "manufacturers.json"
        manufacturers_path.write_bytes(manufacturers_bytes)
        listing = json.loads(manufacturers_bytes)
        manufacturers = cast(list[dict[str, Any]], listing["results"])
        production_makers = [
            item
            for item in manufacturers
            if any(r["type"] == "productions" for r in item["reportTypes"])
        ]

        def detail(item: dict[str, Any]) -> tuple[dict[str, Any], str, bytes]:
            url = f"{API_ROOT}/manufacturers/{item['id']}"
            return item, url, _fetch(url)

        details: list[tuple[dict[str, Any], str, bytes]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            details.extend(pool.map(detail, production_makers))
        reports: list[dict[str, Any]] = []
        for item, url, content in details:
            path = raw_directory / _artifact_name("manufacturer", cast(int, item["id"]))
            path.write_bytes(content)
            artifacts.append(_artifact_metadata(path, url))
            result = json.loads(content)["results"][0]
            periods = result.get("productions", [])
            for wanted_year in report_years:
                matching = [p for p in periods if p["year"] == wanted_year and p["quarter"] == 4]
                for period in matching:
                    for report in period["reports"]:
                        if report["categoryCode"] == SOURCE_CATEGORY:
                            reports.append(
                                {
                                    "manufacturer_id": item["id"],
                                    "manufacturer_name": item["name"],
                                    "reporting_year": wanted_year,
                                    "reporting_quarter": 4,
                                    **report,
                                }
                            )

        def report_data(item: dict[str, Any]) -> tuple[dict[str, Any], str, bytes]:
            url = f"{item['reportUrl']}&max=10000"
            content = _fetch(url)
            response = json.loads(content)
            if response["meta"]["pagination"]["nextUrl"] is not None:
                raise ExposureError(f"production response remains paginated: {url}")
            return item, url, content

        payloads: list[tuple[dict[str, Any], str, bytes]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            payloads.extend(pool.map(report_data, reports))
        for item, url, content in payloads:
            path = raw_directory / (
                f"production-report-{item['manufacturer_id']}-{item['reportId']}.json"
            )
            path.write_bytes(content)
            entry = _artifact_metadata(path, url)
            report_keys = (
                "manufacturer_id",
                "manufacturer_name",
                "reporting_year",
                "reporting_quarter",
                "reportId",
                "categoryCode",
            )
            entry.update({key: item[key] for key in report_keys})
            artifacts.append(entry)
        artifacts.insert(0, _artifact_metadata(manufacturers_path, manufacturers_url))
        manifest = {
            "source_organization": SOURCE_ORGANIZATION,
            "dataset_name": DATASET_NAME,
            "official_source_location": API_ROOT,
            "source_reporting_category": SOURCE_CATEGORY,
            "requested_report_years": list(report_years),
            "requested_quarter": 4,
            "retrieval_timestamp_utc": retrieval,
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "adapter_version": INGESTION_VERSION,
            "selected_report_count": len(reports),
            "complete_for_selected_scope": True,
            "artifacts": sorted(artifacts, key=lambda item: cast(str, item["filename"])),
        }
        _write_json(manifest_path, manifest)
        return manifest_path
    except Exception:
        # Retain partial raw responses for forensic recovery; never silently overwrite them.
        raise


def _artifact_metadata(path: Path, url: str) -> dict[str, object]:
    return {"filename": path.name, "official_source_url": url, "sha256": sha256_file(path)}


def ingest_snapshot(
    manifest_path: Path,
    production_records_path: Path,
    production_cohorts_path: Path,
    provenance_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[int, int]:
    """Validate a raw snapshot and emit canonical records/cohorts with lineage."""
    targets = (production_records_path, production_cohorts_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite exposure output: {existing}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical: list[CanonicalProductionRecord] = []
    try:
        for artifact in manifest["artifacts"]:
            filename = artifact["filename"]
            path = manifest_path.parent / filename
            if sha256_file(path) != artifact["sha256"]:
                raise ExposureError(f"raw artifact checksum mismatch: {filename}")
            if not filename.startswith("production-report-"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = parse_production_payload(
                payload,
                manufacturer_id=artifact["manufacturer_id"],
                manufacturer_name=artifact["manufacturer_name"],
                report_id=artifact["reportId"],
                reporting_year=artifact["reporting_year"],
                reporting_quarter=artifact["reporting_quarter"],
            )
            canonical.extend(canonicalize(record) for record in records)
        canonical.sort(key=lambda item: item.source_record_id)
        cohorts = aggregate_production(canonical)
        _write_jsonl(production_records_path, (asdict(row) for row in canonical))
        _write_jsonl(production_cohorts_path, (asdict(row) for row in cohorts))
        manifest_keys = (
            "source_organization",
            "dataset_name",
            "official_source_location",
            "source_reporting_category",
            "requested_report_years",
            "requested_quarter",
            "retrieval_timestamp_utc",
            "source_schema_version",
            "adapter_version",
            "complete_for_selected_scope",
        )
        provenance = {
            **{key: manifest[key] for key in manifest_keys},
            "ingestion_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "production_semantics": (
                "cumulative current-model-year or final ceased-model-year total"
            ),
            "aggregation_rule": (
                "latest selected reporting period per source configuration, then sum "
                "configurations at make/model/model-year grain"
            ),
            "raw_artifact_count": len(manifest["artifacts"]),
            "source_record_count": len(canonical),
            "production_cohort_count": len(cohorts),
            "production_records_sha256": sha256_file(production_records_path),
            "production_cohorts_sha256": sha256_file(production_cohorts_path),
        }
        _write_json(provenance_path, provenance)
        return len(canonical), len(cohorts)
    except Exception:
        for path in targets:
            path.unlink(missing_ok=True)
        raise


def run_matching(
    complaint_cohorts_path: Path,
    production_cohorts_path: Path,
    output_path: Path,
    diagnostics_path: Path,
    provenance_path: Path,
) -> dict[str, Any]:
    """Match complete Phase 2C cohorts and write deterministic diagnostics."""
    targets = (output_path, diagnostics_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite match output: {existing}")
    complaint_rows = tuple(_json_rows(complaint_cohorts_path))
    production = tuple(
        AggregatedProductionCohort(**row) for row in _json_rows(production_cohorts_path)
    )
    matches = match_complaint_cohorts(complaint_rows, production)
    unmatched = [
        row
        for row in matches
        if row["production_match_status"] == MatchStatus.NO_PRODUCTION_RECORD
    ]
    production_makes = {row.normalized_make for row in production}
    production_make_models = {(row.normalized_make, row.normalized_model) for row in production}
    production_years = {row.model_year for row in production}
    diagnostics = []
    classes: Counter[str] = Counter()
    for row in unmatched:
        key = (cast(str, row["normalized_make"]), cast(str, row["normalized_model"]))
        if row["normalized_make"] not in production_makes:
            mismatch = "make_absent_from_selected_source"
        elif key not in production_make_models:
            mismatch = "model_absent_for_make"
        elif row["model_year"] not in production_years:
            mismatch = "model_year_outside_selected_source"
        else:
            mismatch = "make_model_year_combination_absent"
        classes[mismatch] += 1
        diagnostics.append({**row, "mismatch_class": mismatch})
    _write_jsonl(output_path, matches)
    _write_jsonl(diagnostics_path, diagnostics)
    status_counts = Counter(cast(str, row["production_match_status"]) for row in matches)
    events = sum(cast(int, row["complaint_event_count"]) for row in matches)
    matched_events = sum(
        cast(int, row["complaint_event_count"])
        for row in matches
        if row["production_count"] is not None
    )
    rates = sorted(
        cast(float, row["complaints_per_10k_produced"])
        for row in matches
        if row["complaints_per_10k_produced"] is not None
    )
    coverage_by_year: dict[str, dict[str, int | float]] = {}
    for year in sorted({cast(int, row["model_year"]) for row in matches}):
        year_rows = [row for row in matches if row["model_year"] == year]
        year_matched = [row for row in year_rows if row["production_count"] is not None]
        year_events = sum(cast(int, row["complaint_event_count"]) for row in year_rows)
        year_matched_events = sum(
            cast(int, row["complaint_event_count"]) for row in year_matched
        )
        coverage_by_year[str(year)] = {
            "complaint_cohorts": len(year_rows),
            "matched_cohorts": len(year_matched),
            "matched_cohort_percentage": 100 * len(year_matched) / len(year_rows),
            "complaint_events": year_events,
            "matched_events": year_matched_events,
            "matched_event_percentage": 100 * year_matched_events / year_events,
        }
    unmatched_make_counts = Counter(cast(str, row["normalized_make"]) for row in unmatched)
    unmatched_model_counts = Counter(
        f"{row['normalized_make']} / {row['normalized_model']}" for row in unmatched
    )
    unmatched_year_counts = Counter(cast(int, row["model_year"]) for row in unmatched)
    summary: dict[str, Any] = {
        "matching_version": MATCHING_VERSION,
        "match_schema": list(MATCH_SCHEMA),
        "complaint_cohort_count": len(matches),
        "complaint_event_count": events,
        "status_counts": dict(sorted(status_counts.items())),
        "matched_cohort_percentage": 100 * (len(matches) - len(unmatched)) / len(matches),
        "matched_event_percentage": 100 * matched_events / events,
        "zero_production_cohort_count": sum(row["production_count"] == 0 for row in matches),
        "missing_production_cohort_count": len(unmatched),
        "diagnostic_rate_cohort_count": len(rates),
        "diagnostic_rate_summary": _rate_summary(rates),
        "matched_production_count_sum": sum(
            cast(int, row["production_count"])
            for row in matches
            if row["production_count"] is not None
        ),
        "coverage_by_model_year": coverage_by_year,
        "mismatch_class_counts": dict(sorted(classes.items())),
        "top_unmatched_makes": unmatched_make_counts.most_common(20),
        "top_unmatched_make_models": unmatched_model_counts.most_common(20),
        "top_unmatched_model_years": unmatched_year_counts.most_common(20),
        "complaint_input_sha256": sha256_file(complaint_cohorts_path),
        "production_input_sha256": sha256_file(production_cohorts_path),
        "match_output_sha256": sha256_file(output_path),
        "diagnostics_output_sha256": sha256_file(diagnostics_path),
        "aliases": {},
    }
    _write_json(provenance_path, summary)
    return summary


def _rate_summary(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {"min": values[0], "median": median, "max": values[-1]}
