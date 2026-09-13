"""Phase 2E manufacturer-communication tests; no live network is used."""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.manufacturer_communications.nhtsa import (
    TSB_COLUMNS,
    ApplicabilityStatus,
    CommunicationMatchStatus,
    CommunicationType,
    ManufacturerCommunicationError,
    MfrCommsCsvRecord,
    NhtsaTsbRecord,
    _applicability,
    _communication_type,
    _parse_date,
    ingest_archives,
    iter_mfr_records,
    iter_tsb_records,
    match_cohorts,
    parse_mfr_comms_row,
    parse_tsb_row,
)


def tsb_row(**changes: str) -> list[str]:
    values = {
        "NHTSA ID Number": "10000001",
        "Replacement Service Bulletin Number": "",
        "Date Added to File": "20210102",
        "TSB/Document ID": "TSB-1",
        "Mfr Communication Date": "20201231",
        "Mfr Internal Campaign ID/Software Version": "",
        "Communication Type": "Service Bulletin/Repair Instructions",
        "Make": " FÖRD ",
        "Model": " F-150 ",
        "Model Year": "2020",
        "NHTSA Components": "STEERING",
        "Mfr Component System": "",
        "Mfr Component Subsystem": "",
        "Summary": "Structured fixture summary",
    }
    values.update(changes)
    return [values[name] for name in TSB_COLUMNS]


def write_zip(path: Path, member: str, content: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, content.encode("utf-8"))


def fixture_snapshot(base: Path) -> Path:
    base.mkdir()
    rows = [
        tsb_row(),
        tsb_row(**{"Model Year": "2021"}),
        tsb_row(**{"Model Year": "2020", "NHTSA Components": "SUSPENSION"}),
        tsb_row(
            **{
                "NHTSA ID Number": "10000002",
                "TSB/Document ID": "TSB-1",
                "Communication Type": "Other",
                "Model Year": "9999",
                "Summary": "warranty words must not classify narrative",
                "Mfr Component System": "Body",
            }
        ),
    ]
    tsb = base / "TSBS_RECEIVED_2020-2024.zip"
    write_zip(tsb, "TSBS_RECEIVED_2020-2024.txt", "\n".join("\t".join(row) for row in rows) + "\n")
    mfr = base / "MFR_COMMS_RECEIVED_2020-2024.zip"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["TSB/Document ID", "Make", "Model", "Model Year", "Concise Summary"])
    writer.writerow(["10000001", " FÖRD ", " F-150 ", "2020,2021", "summary"])
    writer.writerow(["10000002", " FÖRD ", " F-150 ", "9999", "summary"])
    write_zip(mfr, "MFR_COMMS_RECEIVED_2020-2024.csv", buffer.getvalue())
    dictionary = base / "TSBS.txt"
    dictionary.write_text("official fixture dictionary", encoding="utf-8")
    artifacts = []
    for path, url in (
        (mfr, "https://static.nhtsa.gov/mfr.zip"),
        (tsb, "https://static.nhtsa.gov/tsb.zip"),
        (dictionary, "https://static.nhtsa.gov/TSBS.txt"),
    ):
        artifacts.append(
            {
                "filename": path.name,
                "official_source_url": url,
                "retrieval_timestamp_utc": "2025-01-01T00:00:00Z",
                "sha256": sha256_file(path),
            }
        )
    manifest = base / "acquisition-manifest.json"
    manifest.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    return manifest


def test_official_format_source_models_are_immutable_and_preserve_blanks() -> None:
    record = parse_tsb_row(tsb_row())
    assert isinstance(record, NhtsaTsbRecord)
    assert record.field("Replacement Service Bulletin Number") is None
    assert record.field("Make") == " FÖRD "
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.values = ()  # type: ignore[misc]
    compact = parse_mfr_comms_row(["1", "MAKE", "MODEL", "2019,2020", "summary"])
    assert isinstance(compact, MfrCommsCsvRecord)
    assert compact.expanded_model_years == ("2019", "2020")


@pytest.mark.parametrize("row", [[], ["x"] * 13, ["x"] * 15])
def test_tsb_field_count_is_strict(row: list[str]) -> None:
    with pytest.raises(ManufacturerCommunicationError, match="fields"):
        parse_tsb_row(row)


def test_malformed_required_field_and_identifier_are_rejected() -> None:
    with pytest.raises(ManufacturerCommunicationError, match="Make"):
        parse_tsb_row(tsb_row(Make=""))
    with pytest.raises(ManufacturerCommunicationError, match="NHTSA ID"):
        parse_tsb_row(tsb_row(**{"NHTSA ID Number": "not-id"}))


def test_communication_and_applicability_identity_are_distinct_and_deterministic() -> None:
    first = _applicability("10000001", "Ford", "F-150", "2020", 2)
    repeated = _applicability("10000001", "Ford", "F-150", "2020", 2)
    another_year = _applicability("10000001", "Ford", "F-150", "2021", 1)
    assert first == repeated
    assert first.communication_id == another_year.communication_id
    assert first.applicability_id != another_year.applicability_id
    assert first.normalized_make == "ford"
    assert first.original_make == "Ford"


def test_unknown_model_year_is_insufficient_not_zero_or_guessed() -> None:
    row = _applicability("10000001", "Ford", "F-150", "9999", 1)
    assert row.model_year is None
    assert row.applicability_status == ApplicabilityStatus.INSUFFICIENT_VEHICLE_IDENTITY


def test_equipment_like_label_is_preserved_not_inferred_as_non_vehicle() -> None:
    row = _applicability("10000001", "EQUIPMENT", "BRAKE KIT", "2020", 1)
    assert row.original_make == "EQUIPMENT"
    assert row.applicability_status == ApplicabilityStatus.ELIGIBLE


def test_out_of_range_date_is_flagged_without_imputation() -> None:
    assert _parse_date("29130401", "communication_date") == (
        None,
        "out_of_range_communication_date",
    )


def test_structured_type_mapping_never_reads_summary() -> None:
    assert _communication_type("Other") is CommunicationType.OTHER
    assert _communication_type("Warranty Program/Extension") is CommunicationType.WARRANTY_PROGRAM
    assert _communication_type("unpublished value") is CommunicationType.UNKNOWN


def test_archive_iterators_parse_both_real_shapes(tmp_path: Path) -> None:
    manifest = fixture_snapshot(tmp_path / "raw")
    tsb = manifest.parent / "TSBS_RECEIVED_2020-2024.zip"
    mfr = manifest.parent / "MFR_COMMS_RECEIVED_2020-2024.zip"
    assert len(tuple(iter_tsb_records(tsb))) == 4
    assert len(tuple(iter_mfr_records(mfr))) == 2


def run_fixture_ingestion(
    tmp_path: Path, name: str = "out"
) -> tuple[Path, Path, Path, dict[str, Any]]:
    manifest = fixture_snapshot(tmp_path / f"raw-{name}")
    directory = tmp_path / name
    communications = directory / "communications.jsonl"
    applicability = directory / "applicability.jsonl"
    provenance = directory / "provenance.json"
    result = ingest_archives(
        manifest,
        communications,
        applicability,
        provenance,
        clock=lambda: datetime(2025, 1, 2, tzinfo=UTC),
    )
    return communications, applicability, provenance, result


def test_fixture_ingestion_counts_components_overlap_dates_and_checksums(tmp_path: Path) -> None:
    communications, applicability, provenance, result = run_fixture_ingestion(tmp_path)
    communication_rows = [json.loads(line) for line in communications.read_text().splitlines()]
    application_rows = [json.loads(line) for line in applicability.read_text().splitlines()]
    assert result["tsv_source_row_count"] == 4
    assert result["unique_communication_count"] == 2
    assert result["applicability_count"] == 3
    assert result["compact_view_audit"]["identifier_overlap_count"] == 2
    assert result["repeated_manufacturer_document_id_count"] == 1
    assert result["communications_sha256"] == sha256_file(communications)
    assert json.loads(provenance.read_text())["processing_complete"] is True
    assert communication_rows[0]["canonical_components"] == ["steering", "suspension"]
    assert communication_rows[0]["communication_id"] == "nhtsa_mc_10000001"
    assert communication_rows[0]["nhtsa_components"] == ["STEERING", "SUSPENSION"]
    assert communication_rows[1]["communication_type"] == "OTHER"
    assert communication_rows[1]["manufacturer_component_systems"] == ["Body"]
    assert application_rows[0]["source_expanded_row_count"] == 2


def test_ingestion_refuses_overwrite_and_checksum_tampering(tmp_path: Path) -> None:
    communications, applicability, provenance, _ = run_fixture_ingestion(tmp_path)
    manifest = tmp_path / "raw-out" / "acquisition-manifest.json"
    with pytest.raises(ArtifactExistsError):
        ingest_archives(manifest, communications, applicability, provenance)
    manifest_data = json.loads(manifest.read_text())
    manifest_data["artifacts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    with pytest.raises(ManufacturerCommunicationError, match="checksum"):
        ingest_archives(
            manifest,
            tmp_path / "bad-c.jsonl",
            tmp_path / "bad-a.jsonl",
            tmp_path / "bad-p.json",
        )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_cohort_matching_aggregates_documents_without_multiplying_years(tmp_path: Path) -> None:
    communications, applicability, _, _ = run_fixture_ingestion(tmp_path)
    complaints = tmp_path / "complaints.jsonl"
    write_jsonl(
        complaints,
        [
            {
                "broad_vehicle_id": "v20",
                "normalized_make": "förd",
                "normalized_model": "f-150",
                "model_year": 2020,
                "complaint_event_count": 5,
            },
            {
                "broad_vehicle_id": "v21",
                "normalized_make": "förd",
                "normalized_model": "f-150",
                "model_year": 2021,
                "complaint_event_count": 3,
            },
            {
                "broad_vehicle_id": "none",
                "normalized_make": "other",
                "normalized_model": "none",
                "model_year": 2020,
                "complaint_event_count": 2,
            },
        ],
    )
    production = tmp_path / "production.jsonl"
    write_jsonl(
        production,
        [
            {"broad_vehicle_id": "v20", "production_count": 100},
            {"broad_vehicle_id": "v21", "production_count": None},
            {"broad_vehicle_id": "none", "production_count": None},
        ],
    )
    paths = [tmp_path / f"match-{name}" for name in ("apps", "cohorts", "unmatched", "cross", "p")]
    result = match_cohorts(
        complaints,
        communications,
        applicability,
        production,
        *paths,
    )
    rows = [json.loads(line) for line in paths[1].read_text().splitlines()]
    assert result["matched_cohort_count"] == 2
    assert result["matched_unique_communication_count"] == 1
    assert result["matched_applicability_count"] == 2
    assert rows[0]["unique_communication_count"] == 1
    assert rows[0]["communication_applicability_row_count"] == 1
    assert rows[0]["source_expanded_row_count"] == 2
    assert rows[2]["manufacturer_communication_match_status"] == CommunicationMatchStatus.NO_MATCH
    assert result["cross_source_cohort_counts"] == {
        "complaint_manufacturer_communications": 1,
        "complaint_only": 1,
        "complaint_production_manufacturer_communications": 1,
    }
    application_matches = [json.loads(line) for line in paths[0].read_text().splitlines()]
    assert {row["complaint_match_status"] for row in application_matches} == {
        "EXACT",
        "INSUFFICIENT_VEHICLE_IDENTITY",
    }
    serialized = paths[1].read_text()
    for prohibited in ("reliability_score", "risk_rate", "failure_count"):
        assert prohibited not in serialized
    with pytest.raises(ArtifactExistsError):
        match_cohorts(
            complaints,
            communications,
            applicability,
            production,
            *paths,
        )
    repeated_paths = [
        tmp_path / f"repeat-{name}" for name in ("apps", "cohorts", "unmatched", "cross", "p")
    ]
    match_cohorts(
        complaints,
        communications,
        applicability,
        production,
        *repeated_paths,
    )
    assert all(
        paths[index].read_bytes() == repeated_paths[index].read_bytes()
        for index in range(4)
    )


def test_ambiguous_complaint_identity_is_not_silently_selected(tmp_path: Path) -> None:
    communications, applicability, _, _ = run_fixture_ingestion(tmp_path)
    base = {
        "normalized_make": "förd",
        "normalized_model": "f-150",
        "model_year": 2020,
        "complaint_event_count": 1,
    }
    complaints = tmp_path / "complaints.jsonl"
    write_jsonl(
        complaints,
        [{"broad_vehicle_id": "one", **base}, {"broad_vehicle_id": "two", **base}],
    )
    production = tmp_path / "production.jsonl"
    write_jsonl(
        production,
        [
            {"broad_vehicle_id": "one", "production_count": None},
            {"broad_vehicle_id": "two", "production_count": None},
        ],
    )
    paths = [tmp_path / f"amb-{name}" for name in ("apps", "cohorts", "u", "cross", "p")]
    result = match_cohorts(complaints, communications, applicability, production, *paths)
    assert result["applicability_match_status_counts"]["AMBIGUOUS"] == 1


def test_deterministic_ingestion_outputs(tmp_path: Path) -> None:
    first = run_fixture_ingestion(tmp_path, "first")
    second = run_fixture_ingestion(tmp_path, "second")
    assert first[0].read_bytes() == second[0].read_bytes()
    assert first[1].read_bytes() == second[1].read_bytes()
