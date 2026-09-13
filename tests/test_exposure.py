"""Phase 2D NHTSA EWR production exposure tests (all network-free)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from howreliable.data.exposure.nhtsa_ewr import (
    AggregatedProductionCohort,
    CanonicalProductionRecord,
    ExposureError,
    MatchStatus,
    NhtsaEwrProductionRecord,
    aggregate_production,
    canonicalize,
    ingest_snapshot,
    match_complaint_cohorts,
    normalize_identity,
    parse_production_payload,
    run_matching,
)
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file


def source_row(
    *, production: object = 123, make: object = "FÖRD", model: object = "  F-150 "
) -> dict[str, object]:
    return {
        "fuelPropulsionSystem": "SIF",
        "make": make,
        "model": model,
        "modelYear": "2020",
        "platform": "P702",
        "reportCategory": "L",
        "totalProduction": production,
        "typeCode": "PU",
    }


def parsed(**changes: Any) -> NhtsaEwrProductionRecord:
    context: dict[str, Any] = {
        "manufacturer_id": 2,
        "manufacturer_name": "Ford Motor Company",
        "report_id": 10,
        "reporting_year": 2024,
        "reporting_quarter": 4,
    }
    context.update(changes)
    return parse_production_payload(
        {"meta": {}, "results": [source_row()]},
        manufacturer_id=context["manufacturer_id"],
        manufacturer_name=context["manufacturer_name"],
        report_id=context["report_id"],
        reporting_year=context["reporting_year"],
        reporting_quarter=context["reporting_quarter"],
    )[0]


def canonical(**changes: Any) -> CanonicalProductionRecord:
    values = dataclasses.asdict(canonicalize(parsed()))
    values.update(changes)
    return CanonicalProductionRecord(**values)


def cohort(**changes: Any) -> AggregatedProductionCohort:
    values: dict[str, Any] = {
        "normalized_make": "förd",
        "normalized_model": "f-150",
        "model_year": 2020,
        "production_count": 123,
        "source_record_count": 1,
        "source_record_ids": ("source-1",),
        "reporting_periods": ("2024Q4",),
        "reporting_entity_ids": (2,),
    }
    values.update(changes)
    return AggregatedProductionCohort(**values)


def complaint(**changes: Any) -> dict[str, Any]:
    values = {
        "broad_vehicle_id": "vehicle-1",
        "normalized_make": "förd",
        "normalized_model": "f-150",
        "model_year": 2020,
        "complaint_event_count": 4,
    }
    values.update(changes)
    return values


def test_official_schema_parsing_and_canonical_preservation() -> None:
    record = parsed()
    result = canonicalize(record)
    assert record.total_production == 123
    assert result.original_make == "FÖRD"
    assert result.original_model == "  F-150 "
    assert result.normalized_make == "förd"
    assert result.normalized_model == "f-150"
    assert result.model_year == 2020
    assert result.production_count == 123
    assert result.source_record_id.startswith("nhtsa_ewr_")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.production_count = 9  # type: ignore[misc]


def test_historical_schema_without_optional_fields_parses_as_null() -> None:
    row = source_row()
    del row["fuelPropulsionSystem"]
    del row["platform"]
    record = parse_production_payload(
        {"results": [row]},
        manufacturer_id=2,
        manufacturer_name="Ford",
        report_id=10,
        reporting_year=2003,
        reporting_quarter=4,
    )[0]
    assert record.fuel_propulsion_system is None
    assert record.platform is None


@pytest.mark.parametrize("payload", [{}, {"results": [{}]}, {"results": "bad"}])
def test_malformed_payload_rejected(payload: object) -> None:
    with pytest.raises(ExposureError):
        parse_production_payload(
            payload,
            manufacturer_id=2,
            manufacturer_name="Ford",
            report_id=10,
            reporting_year=2024,
            reporting_quarter=4,
        )


@pytest.mark.parametrize("production", [-1, 1.5, "12", None, True])
def test_malformed_or_negative_production_rejected(production: object) -> None:
    with pytest.raises(ExposureError, match="totalProduction"):
        parse_production_payload(
            {"results": [source_row(production=production)]},
            manufacturer_id=2,
            manufacturer_name="Ford",
            report_id=10,
            reporting_year=2024,
            reporting_quarter=4,
        )


def test_zero_production_is_preserved_without_rate() -> None:
    record = parse_production_payload(
        {"results": [source_row(production=0)]},
        manufacturer_id=2,
        manufacturer_name="Ford",
        report_id=10,
        reporting_year=2024,
        reporting_quarter=4,
    )[0]
    assert canonicalize(record).production_count == 0
    match = match_complaint_cohorts([complaint()], [cohort(production_count=0)])[0]
    assert match["production_count"] == 0
    assert match["complaints_per_10k_produced"] is None


def test_normalization_is_only_nfkc_whitespace_and_case() -> None:
    assert normalize_identity("  \uff26\uff2f\uff32\uff24\tMOTOR ") == "ford motor"
    assert normalize_identity("Mercedes-Benz") == "mercedes-benz"
    assert normalize_identity("VW") != normalize_identity("Volkswagen")


def test_model_year_is_strictly_validated() -> None:
    bad = dataclasses.replace(parsed(), model_year="9999")
    with pytest.raises(ExposureError, match="model year"):
        canonicalize(bad)


def test_latest_cumulative_record_wins_and_configurations_sum() -> None:
    old = canonical(source_record_id="old", reporting_year=2014, production_count=50)
    newest = dataclasses.replace(
        old, source_record_id="new", reporting_year=2024, production_count=80
    )
    other_configuration = dataclasses.replace(
        newest,
        source_record_id="electric",
        fuel_propulsion_system="ELE",
        production_count=20,
    )
    result = aggregate_production([other_configuration, newest, old])
    assert len(result) == 1
    assert result[0].production_count == 100
    assert result[0].source_record_count == 2
    assert result == aggregate_production([old, newest, other_configuration])


def test_exact_unmatched_alias_and_ambiguous_matching() -> None:
    exact = match_complaint_cohorts([complaint()], [cohort()])[0]
    assert exact["production_match_status"] == MatchStatus.EXACT
    assert exact["complaint_event_count"] == 4
    assert exact["broad_vehicle_id"] == "vehicle-1"
    assert exact["complaints_per_10k_produced"] == pytest.approx(325.203252)
    unmatched = match_complaint_cohorts(
        [complaint(normalized_model="other")], [cohort()]
    )[0]
    assert unmatched["production_match_status"] == MatchStatus.NO_PRODUCTION_RECORD
    assert unmatched["production_count"] is None
    alias = match_complaint_cohorts(
        [complaint(normalized_make="ford")],
        [cohort()],
        aliases={("ford", "f-150"): ("förd", "f-150")},
    )[0]
    assert alias["production_match_status"] == MatchStatus.EXPLICIT_ALIAS
    ambiguous = match_complaint_cohorts(
        [complaint()], [cohort(), cohort(source_record_ids=("two",))]
    )[0]
    assert ambiguous["production_match_status"] == MatchStatus.AMBIGUOUS
    assert ambiguous["production_count"] is None


def test_repeated_conflicting_rows_rejected() -> None:
    first = canonical(source_record_id="one")
    conflict = dataclasses.replace(first, source_record_id="two", production_count=999)
    with pytest.raises(ExposureError, match="conflicting"):
        aggregate_production([first, conflict])


def test_identical_repeated_source_row_is_not_double_counted() -> None:
    first = canonical(source_record_id="one")
    repeated = dataclasses.replace(first, source_record_id="two")
    result = aggregate_production([repeated, first])
    assert result[0].production_count == 123
    assert result[0].source_record_count == 1


def _raw_snapshot(directory: Path) -> Path:
    directory.mkdir()
    payload = {"meta": {}, "results": [source_row()]}
    report = directory / "production-report-2-10.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    manufacturer = directory / "manufacturer-2.json"
    manufacturer.write_text("{}", encoding="utf-8")
    artifacts = [
        {
            "filename": report.name,
            "official_source_url": "https://api.nhtsa.gov/ewr/productions?manufacturerId=2",
            "sha256": sha256_file(report),
            "manufacturer_id": 2,
            "manufacturer_name": "Ford Motor Company",
            "reporting_year": 2024,
            "reporting_quarter": 4,
            "reportId": 10,
            "categoryCode": "L",
        },
        {
            "filename": manufacturer.name,
            "official_source_url": "https://api.nhtsa.gov/ewr/manufacturers/2",
            "sha256": sha256_file(manufacturer),
        },
    ]
    manifest = {
        "source_organization": "National Highway Traffic Safety Administration (NHTSA)",
        "dataset_name": "Early Warning Reporting Light Vehicle Production",
        "official_source_location": "https://api.nhtsa.gov/ewr",
        "source_reporting_category": "L",
        "requested_report_years": [2024],
        "requested_quarter": 4,
        "retrieval_timestamp_utc": "2025-01-01T00:00:00Z",
        "source_schema_version": None,
        "adapter_version": "ewr-production-ingestion-1.0",
        "complete_for_selected_scope": True,
        "artifacts": artifacts,
    }
    path = directory / "snapshot-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_local_ingestion_provenance_checksums_and_overwrite(tmp_path: Path) -> None:
    manifest = _raw_snapshot(tmp_path / "raw")
    records = tmp_path / "processed" / "records.jsonl"
    cohorts = tmp_path / "processed" / "cohorts.jsonl"
    provenance = tmp_path / "processed" / "provenance.json"
    assert ingest_snapshot(
        manifest,
        records,
        cohorts,
        provenance,
        clock=lambda: datetime(2025, 1, 2, tzinfo=UTC),
    ) == (1, 1)
    metadata = json.loads(provenance.read_text(encoding="utf-8"))
    assert metadata["production_records_sha256"] == sha256_file(records)
    assert metadata["retrieval_timestamp_utc"] == "2025-01-01T00:00:00Z"
    assert metadata["source_schema_version"] is None
    with pytest.raises(ArtifactExistsError):
        ingest_snapshot(manifest, records, cohorts, provenance)


def test_checksum_tampering_is_rejected_and_partial_outputs_removed(tmp_path: Path) -> None:
    manifest = _raw_snapshot(tmp_path / "raw")
    (manifest.parent / "production-report-2-10.json").write_text("{}", encoding="utf-8")
    records = tmp_path / "records.jsonl"
    with pytest.raises(ExposureError, match="checksum"):
        ingest_snapshot(manifest, records, tmp_path / "cohorts.jsonl", tmp_path / "p.json")
    assert not records.exists()


def test_match_artifacts_are_deterministic_and_contain_no_prohibited_fields(tmp_path: Path) -> None:
    complaints = tmp_path / "complaints.jsonl"
    complaints.write_text(json.dumps(complaint()) + "\n", encoding="utf-8")
    productions = tmp_path / "production.jsonl"
    productions.write_text(json.dumps(dataclasses.asdict(cohort())) + "\n", encoding="utf-8")
    output = tmp_path / "match.jsonl"
    diagnostics = tmp_path / "unmatched.jsonl"
    provenance = tmp_path / "match.provenance.json"
    summary = run_matching(complaints, productions, output, diagnostics, provenance)
    assert summary["status_counts"] == {"EXACT": 1}
    serialized = output.read_text(encoding="utf-8")
    for prohibited in ("reliability_score", "risk_rate", "failure_probability"):
        assert prohibited not in serialized
    assert summary["match_output_sha256"] == sha256_file(output)
