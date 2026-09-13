"""Phase 2G integration tests; fixtures contain processed artifacts only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from howreliable.data.exposure.nhtsa_ewr import MATCH_SCHEMA as PRODUCTION_SCHEMA
from howreliable.data.exposure.nhtsa_ewr import MATCHING_VERSION as PRODUCTION_VERSION
from howreliable.data.features.schema import (
    COHORT_FEATURE_SCHEMA,
    EVENT_FEATURE_SCHEMA,
    FEATURE_VERSION,
)
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.integration.cohorts import (
    INTEGRATED_SCHEMA,
    IntegrationError,
    integrate_cohorts,
)
from howreliable.data.manufacturer_communications.nhtsa import (
    COHORT_EVIDENCE_SCHEMA as COMMUNICATION_SCHEMA,
)
from howreliable.data.manufacturer_communications.nhtsa import (
    MATCHING_VERSION as COMMUNICATION_VERSION,
)
from howreliable.data.recalls.nhtsa import COHORT_RECALL_SCHEMA as RECALL_SCHEMA
from howreliable.data.recalls.nhtsa import MATCHING_VERSION as RECALL_VERSION


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def complaint_row(identifier: str, count: int, make: str = "ford") -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name in COHORT_FEATURE_SCHEMA:
        if name == "broad_vehicle_id":
            value: Any = identifier
        elif name == "normalized_make":
            value = make
        elif name == "normalized_model":
            value = "f-150"
        elif name == "model_year":
            value = 2020
        elif name == "complaint_event_count":
            value = count
        elif name in {"first_observed_report_date", "last_observed_report_date"}:
            value = "2021-01-01"
        elif name.endswith("_share"):
            value = 0.0
        elif "median" in name:
            value = None
        else:
            value = 0
        row[name] = value
    row["unique_event_count"] = count
    row["unique_source_reference_count"] = count
    row["mileage_observed_count"] = 0
    return row


def event_row(identifier: str, report_date: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name in EVENT_FEATURE_SCHEMA:
        if name in {"event_id", "source_record_id", "source_reference_id"}:
            value: Any = f"{name}-{identifier}-{report_date}"
        elif name == "source_type":
            value = "nhtsa_odi_complaint"
        elif name == "broad_vehicle_id":
            value = identifier
        elif name == "normalized_make":
            value = "ford"
        elif name == "normalized_model":
            value = "f-150"
        elif name == "model_year":
            value = 2020
        elif name == "report_date":
            value = report_date
        elif name == "report_year":
            value = int(report_date[:4])
        elif name.startswith("quality_") or name.endswith("_positive"):
            value = False
        elif name in {"component", "severity", "mileage_unit"}:
            value = "other"
        else:
            value = None
        row[name] = value
    return row


def production_row(identifier: str, matched: bool) -> dict[str, Any]:
    values: dict[str, Any] = {
        "broad_vehicle_id": identifier,
        "normalized_make": "ford",
        "normalized_model": "f-150",
        "model_year": 2020,
        "complaint_event_count": 2 if identifier == "c1" else 1,
        "production_match_status": "EXACT" if matched else "NO_PRODUCTION_RECORD",
        "match_method": "exact_normalized" if matched else None,
        "production_count": 1000 if matched else None,
        "production_source_record_count": 1 if matched else 0,
        "production_source_record_ids": ["p1"] if matched else [],
        "production_reporting_periods": ["2020Q4"] if matched else [],
        "ambiguity_candidate_count": 0,
        "complaints_per_10k_produced": 20.0 if matched else None,
    }
    return {name: values[name] for name in PRODUCTION_SCHEMA}


def communication_row(identifier: str, matched: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in COMMUNICATION_SCHEMA:
        if name == "broad_vehicle_id":
            value: Any = identifier
        elif name == "normalized_make":
            value = "ford"
        elif name == "normalized_model":
            value = "f-150"
        elif name == "model_year":
            value = 2020
        elif name == "complaint_event_count":
            value = 2 if identifier == "c1" else 1
        elif name == "manufacturer_communication_match_status":
            value = "EXACT" if matched else "NO_MATCH"
        elif name == "match_method":
            value = "exact_normalized" if matched else None
        elif name == "unique_communication_count":
            value = 1 if matched else 0
        elif name in {"first_communication_date", "last_communication_date"}:
            value = "2021-06-01" if matched else None
        elif name.endswith("_count"):
            value = 0
        else:
            value = 0
        values[name] = value
    return values


def recall_row(identifier: str, matched: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in RECALL_SCHEMA:
        if name == "broad_vehicle_id":
            value: Any = identifier
        elif name == "normalized_make":
            value = "ford"
        elif name == "normalized_model":
            value = "f-150"
        elif name == "model_year":
            value = 2020
        elif name == "complaint_event_count":
            value = 2 if identifier == "c1" else 1
        elif name == "recall_match_status":
            value = "EXACT" if matched else "NO_MATCH"
        elif name == "match_method":
            value = "exact_normalized" if matched else None
        elif name == "unique_recall_campaign_count":
            value = 1 if matched else 0
        elif name in {"first_recall_date", "last_recall_date"}:
            value = "2021-07-01" if matched else None
        elif name.endswith("_count"):
            value = 0
        else:
            value = 0
        values[name] = value
    return values


def fixture_data(base: Path) -> Path:
    data = base / "processed"
    feature = data / "features"
    exposure = data / "exposure"
    communications = data / "manufacturer_communications"
    recalls = data / "recalls"
    cohorts_path = feature / "nhtsa-vehicle-cohorts.jsonl"
    events_path = feature / "nhtsa-complaint-events.jsonl"
    production_path = exposure / "complaint-production-matches.jsonl"
    communication_path = communications / "cohort-evidence.jsonl"
    communication_records = communications / "communications.jsonl"
    communication_apps = communications / "applicability-matches.jsonl"
    recall_path = recalls / "cohort-recalls.jsonl"
    campaigns = recalls / "campaigns.jsonl"
    recall_apps = recalls / "applicability-matches.jsonl"
    write_jsonl(cohorts_path, [complaint_row("c1", 2), complaint_row("c2", 1, "honda")])
    write_jsonl(
        events_path,
        [
            event_row("c1", "2021-01-01"),
            event_row("c1", "2022-06-01"),
            event_row("c2", "2023-06-01"),
        ],
    )
    write_jsonl(production_path, [production_row("c1", True), production_row("c2", False)])
    write_jsonl(communication_path, [communication_row("c1", True), communication_row("c2", False)])
    write_jsonl(communication_records, [{"communication_id": "m1", "date_added": "2021-06-01"}])
    write_jsonl(communication_apps, [{"communication_id": "m1", "matched_broad_vehicle_id": "c1"}])
    write_jsonl(recall_path, [recall_row("c1", True), recall_row("c2", False)])
    write_jsonl(campaigns, [{"campaign_id": "r1", "report_received_dates": ["2021-07-01"]}])
    write_jsonl(recall_apps, [{"campaign_id": "r1", "matched_broad_vehicle_id": "c1"}])
    write_json(
        feature / "nhtsa-complaint-features.provenance.json",
        {
            "feature_version": FEATURE_VERSION,
            "cohort_feature_schema": list(COHORT_FEATURE_SCHEMA),
            "event_feature_schema": list(EVENT_FEATURE_SCHEMA),
            "cohort_feature_artifact": {"sha256": sha256_file(cohorts_path)},
            "event_feature_artifact": {"sha256": sha256_file(events_path)},
        },
    )
    write_json(
        exposure / "complaint-production-matches.provenance.json",
        {
            "matching_version": PRODUCTION_VERSION,
            "match_schema": list(PRODUCTION_SCHEMA),
            "match_output_sha256": sha256_file(production_path),
        },
    )
    write_json(
        communications / "matching.provenance.json",
        {
            "matching_version": COMMUNICATION_VERSION,
            "cohort_evidence_schema": list(COMMUNICATION_SCHEMA),
            "input_checksums": {"communications": sha256_file(communication_records)},
            "output_checksums": {
                "cohort_evidence": sha256_file(communication_path),
                "applicability_matches": sha256_file(communication_apps),
            },
        },
    )
    write_json(
        recalls / "matching.provenance.json",
        {
            "matching_version": RECALL_VERSION,
            "cohort_recall_schema": list(RECALL_SCHEMA),
            "outputs": {
                "cohort-recalls.jsonl": sha256_file(recall_path),
                "applicability-matches.jsonl": sha256_file(recall_apps),
            },
        },
    )
    write_json(
        recalls / "ingestion.provenance.json",
        {"campaigns_sha256": sha256_file(campaigns)},
    )
    return data


def run_fixture(tmp_path: Path, name: str = "output") -> tuple[Path, Path, Path, dict[str, Any]]:
    data = fixture_data(tmp_path)
    output = tmp_path / name
    cohort_path = output / "cohorts.jsonl"
    provenance_path = output / "provenance.json"
    review_path = output / "review.json"
    result = integrate_cohorts(
        data,
        cohort_path,
        provenance_path,
        review_path,
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC),
    )
    return cohort_path, provenance_path, review_path, result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_join_preserves_every_identity_and_exact_schema(tmp_path: Path) -> None:
    output, _, _, result = run_fixture(tmp_path)
    rows = read_jsonl(output)
    assert result["integrated_row_count"] == 2
    assert [row["broad_vehicle_id"] for row in rows] == ["c1", "c2"]
    assert tuple(rows[0]) == INTEGRATED_SCHEMA
    assert result["integrated_column_count"] == len(INTEGRATED_SCHEMA)


def test_production_missing_is_null_not_zero(tmp_path: Path) -> None:
    rows = read_jsonl(run_fixture(tmp_path)[0])
    assert rows[0]["production_total_units"] == 1000
    assert rows[1]["has_production_match"] is False
    assert rows[1]["production_total_units"] is None
    assert rows[1]["production_source_record_count"] is None


def test_communication_and_recall_unmatched_counts_are_null(tmp_path: Path) -> None:
    rows = read_jsonl(run_fixture(tmp_path)[0])
    assert rows[0]["communication_unique_count"] == 1
    assert rows[0]["communication_type_service_campaign_count"] == 0
    assert rows[1]["communication_unique_count"] is None
    assert rows[0]["recall_unique_campaign_count"] == 1
    assert rows[0]["recall_type_equipment_campaign_count"] == 0
    assert rows[1]["recall_unique_campaign_count"] is None


def test_source_support_indicators_are_descriptive(tmp_path: Path) -> None:
    rows = read_jsonl(run_fixture(tmp_path)[0])
    assert rows[0]["source_count_available"] == 4
    assert rows[0]["has_all_four_sources"] is True
    assert rows[1]["source_count_available"] == 1
    assert rows[1]["source_coverage_category"] == "complaints"


def test_no_target_label_or_score_columns(tmp_path: Path) -> None:
    row = read_jsonl(run_fixture(tmp_path)[0])[0]
    prohibited = {"target", "label", "risk_score", "reliability_score", "confidence_score"}
    assert prohibited.isdisjoint(row)
    assert not any(name.endswith("_target") or name.endswith("_label") for name in row)


def test_temporal_review_is_deterministic_and_generates_no_labels(tmp_path: Path) -> None:
    _, _, review_path, _ = run_fixture(tmp_path)
    review = json.loads(review_path.read_text())
    assert review["labels_generated"] is False
    first = review["temporal_feasibility"]["cutoffs"][0]
    assert first["cutoff"] == "2021-12-31"
    assert first["prior_complaint_cohort_count"] == 1
    assert first["cohorts_with_future_complaints_in_12_month_window"] == 1
    assert first["eligible_cohorts_with_communications_added_by_cutoff"] == 1
    assert first["eligible_cohorts_with_recalls_reported_by_cutoff"] == 1


def test_provenance_records_versions_checksums_and_policy(tmp_path: Path) -> None:
    output, provenance_path, review_path, result = run_fixture(tmp_path)
    provenance = json.loads(provenance_path.read_text())
    assert provenance["input_versions"]["phase_2c"] == FEATURE_VERSION
    assert provenance["output_sha256"] == sha256_file(output)
    assert provenance["review_sha256"] == sha256_file(review_path)
    assert provenance["policies"]["labels"] == "no targets or labels generated"
    assert result == provenance


def test_serialization_is_deterministic(tmp_path: Path) -> None:
    first = run_fixture(tmp_path / "a")[0]
    second = run_fixture(tmp_path / "b")[0]
    assert sha256_file(first) == sha256_file(second)


def test_overwrite_is_refused(tmp_path: Path) -> None:
    output, provenance, review, _ = run_fixture(tmp_path)
    with pytest.raises(ArtifactExistsError):
        integrate_cohorts(tmp_path / "processed", output, provenance, review)


@pytest.mark.parametrize("failure", ["version", "checksum", "schema"])
def test_invalid_provenance_fails_without_outputs(tmp_path: Path, failure: str) -> None:
    data = fixture_data(tmp_path)
    path = data / "features/nhtsa-complaint-features.provenance.json"
    provenance = json.loads(path.read_text())
    if failure == "version":
        provenance["feature_version"] = "unsupported"
    elif failure == "checksum":
        provenance["cohort_feature_artifact"]["sha256"] = "0" * 64
    else:
        provenance["cohort_feature_schema"] = ["wrong"]
    write_json(path, provenance)
    output = tmp_path / "out"
    with pytest.raises(IntegrationError):
        integrate_cohorts(data, output / "c.jsonl", output / "p.json", output / "r.json")
    assert not output.exists()


def test_mismatched_source_identity_fails(tmp_path: Path) -> None:
    data = fixture_data(tmp_path)
    path = data / "recalls/cohort-recalls.jsonl"
    rows = read_jsonl(path)
    rows[1]["broad_vehicle_id"] = "not-c2"
    write_jsonl(path, rows)
    provenance_path = data / "recalls/matching.provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["outputs"]["cohort-recalls.jsonl"] = sha256_file(path)
    write_json(provenance_path, provenance)
    output = tmp_path / "out"
    with pytest.raises(IntegrationError, match="identity"):
        integrate_cohorts(data, output / "c.jsonl", output / "p.json", output / "r.json")
