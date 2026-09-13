"""Phase 2F recall tests; all fixtures are offline."""

from __future__ import annotations

import dataclasses
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.recalls.nhtsa import (
    RCL_COLUMNS,
    CanonicalRecallCampaign,
    NhtsaRecallRecord,
    RecallError,
    RecallType,
    _applicability,
    _campaign,
    _CampaignAccumulator,
    create_manifest,
    ingest_archives,
    iter_recall_records,
    match_cohorts,
    parse_recall_row,
)


def recall_row(**changes: str) -> list[str]:
    values = {
        "RECORD_ID": "123456",
        "CAMPNO": "22V176000",
        "MAKETXT": " FÖRD ",
        "MODELTXT": " F-150 ",
        "YEARTXT": "2022",
        "MFGCAMPNO": "42L8",
        "COMPNAME": "SUSPENSION",
        "MFGNAME": "Ford Motor Company",
        "BGMAN": "20210101",
        "ENDMAN": "20211231",
        "RCLTYPECD": "V",
        "POTAFF": "100",
        "ODATE": "20220613",
        "INFLUENCED_BY": "MFR",
        "MFGTXT": "Ford Motor Company",
        "RCDATE": "20220323",
        "DATEA": "20220324",
        "RPNO": "573",
        "FMVSS": "",
        "DESC_DEFECT": 'Defect text with a "quote".',
        "CONEQUENCE_DEFECT": "Consequence text",
        "CORRECTIVE_ACTION": "Remedy text",
        "NOTES": "Notes text",
        "RCL_CMPT_ID": "component-id",
        "MFR_COMP_NAME": "Rear knuckle",
        "MFR_COMP_DESC": "Rear suspension part",
        "MFR_COMP_PTNO": "ABC-123",
        "DO_NOT_DRIVE": "No",
        "PARK_OUTSIDE": "No",
    }
    values.update(changes)
    return [values[name] for name in RCL_COLUMNS]


def write_zip(path: Path, rows: list[list[str]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        lines = ["\t".join(row) for row in rows]
        archive.writestr(path.stem + ".txt", ("\n".join(lines) + "\n").encode("latin-1"))


def fixture_snapshot(base: Path) -> Path:
    base.mkdir(parents=True)
    write_zip(
        base / "FLAT_RCL_PRE_2010.zip",
        [recall_row(RECORD_ID="1", CAMPNO="09V001000", YEARTXT="2009", POTAFF="20")],
    )
    write_zip(
        base / "FLAT_RCL_POST_2010.zip",
        [
            recall_row(),
            recall_row(RECORD_ID="123457", YEARTXT="2021"),
            recall_row(RECORD_ID="123458", POTAFF="100", COMPNAME="STEERING"),
            recall_row(
                RECORD_ID="123459",
                CAMPNO="22E177000",
                MAKETXT="EQUIPMENT",
                MODELTXT="BRAKE KIT",
                YEARTXT="9999",
                RCLTYPECD="E",
                MFGCAMPNO="42L8",
                POTAFF="7",
                DESC_DEFECT="warranty and severe words stay text only",
            ),
        ],
    )
    (base / "RCL.txt").write_text("fixture official dictionary", encoding="utf-8")
    return create_manifest(base, datetime(2025, 1, 1, tzinfo=UTC))


def run_ingest(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    manifest = fixture_snapshot(tmp_path / "raw")
    output = tmp_path / "processed"
    campaigns = output / "campaigns.jsonl"
    applicability = output / "applicability.jsonl"
    provenance = output / "ingestion.json"
    result = ingest_archives(
        manifest,
        campaigns,
        applicability,
        provenance,
        clock=lambda: datetime(2025, 1, 2, tzinfo=UTC),
    )
    return campaigns, applicability, provenance, result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_source_record_is_immutable_and_preserves_all_fields() -> None:
    record = parse_recall_row(recall_row(FMVSS="208", NOTES=" raw text "))
    assert isinstance(record, NhtsaRecallRecord)
    assert record.field("FMVSS") == "208"
    assert record.field("NOTES") == " raw text "
    assert record.field("RPNO") == "573"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.values = ()  # type: ignore[misc]


@pytest.mark.parametrize("row", [[], ["x"] * 28, ["x"] * 30])
def test_exact_field_count_is_required(row: list[str]) -> None:
    with pytest.raises(RecallError, match="fields"):
        parse_recall_row(row)


@pytest.mark.parametrize(
    ("change", "message"),
    [({"RECORD_ID": ""}, "RECORD_ID"), ({"CAMPNO": "bad"}, "CAMPNO")],
)
def test_required_identity_is_strict(change: dict[str, str], message: str) -> None:
    with pytest.raises(RecallError, match=message):
        parse_recall_row(recall_row(**change))


def test_official_alphanumeric_campaign_serial_is_preserved() -> None:
    record = parse_recall_row(recall_row(CAMPNO="21V00J000"))
    assert record.field("CAMPNO") == "21V00J000"


def test_quote_characters_do_not_merge_real_format_rows(tmp_path: Path) -> None:
    artifact = tmp_path / "rows.zip"
    write_zip(artifact, [recall_row(), recall_row(RECORD_ID="123457")])
    records = tuple(iter_recall_records(artifact))
    assert len(records) == 2
    assert records[0].field("DESC_DEFECT") == 'Defect text with a "quote".'


def test_applicability_identity_normalization_year_and_component() -> None:
    record = parse_recall_row(recall_row())
    application = _applicability(record, "fixture.zip")
    repeated = _applicability(record, "fixture.zip")
    another = _applicability(
        parse_recall_row(recall_row(RECORD_ID="123457", YEARTXT="2021")), "fixture.zip"
    )
    assert application == repeated
    assert application.applicability_id != another.applicability_id
    assert application.campaign_id == another.campaign_id
    assert application.normalized_make == "förd"
    assert application.normalized_model == "f-150"
    assert application.model_year == 2022
    assert application.canonical_component == "suspension"


def test_unknown_year_and_nonvehicle_type_are_preserved() -> None:
    record = parse_recall_row(recall_row(YEARTXT="9999", RCLTYPECD="E", MAKETXT="EQUIPMENT"))
    application = _applicability(record, "fixture.zip")
    assert application.model_year is None
    assert application.product_type == RecallType.EQUIPMENT
    assert application.original_make == "EQUIPMENT"


def test_campaign_population_is_not_summed_and_repetition_is_preserved() -> None:
    acc = _CampaignAccumulator()
    for row in (recall_row(), recall_row(RECORD_ID="123457", YEARTXT="2021")):
        record = parse_recall_row(row)
        acc.source_rows += 1
        acc.artifacts.add("fixture.zip")
        for name, value in record.as_source_dict().items():
            if value is not None and name != "RECORD_ID":
                acc.values[name].add(value)
    campaign = _campaign("22V176000", acc)
    assert isinstance(campaign, CanonicalRecallCampaign)
    assert campaign.potential_units_affected == 100
    assert campaign.potential_units_affected_values == (100,)
    assert campaign.source_row_count == 2


def test_conflicting_campaign_population_is_not_arbitrarily_aggregated() -> None:
    acc = _CampaignAccumulator()
    acc.source_rows = 2
    acc.artifacts.add("fixture.zip")
    for row in (recall_row(POTAFF="100"), recall_row(POTAFF="200")):
        for name, value in parse_recall_row(row).as_source_dict().items():
            if value is not None and name != "RECORD_ID":
                acc.values[name].add(value)
    campaign = _campaign("22V176000", acc)
    assert campaign.potential_units_affected is None
    assert campaign.potential_units_affected_values == (100, 200)
    assert "multiple_potential_units_affected_values" in campaign.quality_flags


def test_ingestion_separates_campaigns_applications_and_nonvehicles(tmp_path: Path) -> None:
    campaigns_path, applications_path, provenance_path, result = run_ingest(tmp_path)
    campaigns = read_jsonl(campaigns_path)
    applications = read_jsonl(applications_path)
    assert result["source_row_count"] == 5
    assert result["parse_failure_count"] == 0
    assert result["unique_campaign_count"] == 3
    assert result["vehicle_applicability_count"] == 4
    assert result["nonvehicle_source_row_count"] == 1
    assert len(campaigns) == 3
    assert len(applications) == 4
    assert json.loads(provenance_path.read_text())["processing_complete"] is True
    assert result["campaigns_sha256"] == sha256_file(campaigns_path)
    assert result["applicability_sha256"] == sha256_file(applications_path)
    assert result["campaigns_with_multiple_source_rows"] == 1


def test_manufacturer_campaign_repetition_does_not_merge_nhtsa_campaigns(
    tmp_path: Path,
) -> None:
    campaigns_path, _, _, result = run_ingest(tmp_path)
    campaigns = read_jsonl(campaigns_path)
    assert {row["nhtsa_campaign_number"] for row in campaigns} == {
        "09V001000",
        "22E177000",
        "22V176000",
    }
    assert result["manufacturer_campaign_numbers_reused_across_nhtsa_campaigns"] == 1


def test_text_and_structured_classification_are_independent(tmp_path: Path) -> None:
    campaigns_path, _, _, _ = run_ingest(tmp_path)
    campaign = next(
        row for row in read_jsonl(campaigns_path) if row["nhtsa_campaign_number"] == "22E177000"
    )
    assert campaign["recall_types"] == ["EQUIPMENT"]
    assert campaign["defect_descriptions"] == ["warranty and severe words stay text only"]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_exact_matching_aggregation_cross_source_and_unmatched(tmp_path: Path) -> None:
    campaigns, applications, _, _ = run_ingest(tmp_path)
    complaint = tmp_path / "complaints.jsonl"
    production = tmp_path / "production.jsonl"
    communications = tmp_path / "communications.jsonl"
    complaint_rows = [
        {
            "broad_vehicle_id": "vehicle-1",
            "normalized_make": "förd",
            "normalized_model": "f-150",
            "model_year": 2022,
            "complaint_event_count": 10,
        },
        {
            "broad_vehicle_id": "vehicle-2",
            "normalized_make": "unknown",
            "normalized_model": "model",
            "model_year": 2022,
            "complaint_event_count": 2,
        },
    ]
    write_jsonl(complaint, complaint_rows)
    write_jsonl(
        production,
        [
            {"broad_vehicle_id": "vehicle-1", "production_count": 100},
            {"broad_vehicle_id": "vehicle-2", "production_count": None},
        ],
    )
    write_jsonl(
        communications,
        [
            {"broad_vehicle_id": "vehicle-1", "unique_communication_count": 1},
            {"broad_vehicle_id": "vehicle-2", "unique_communication_count": 0},
        ],
    )
    out = tmp_path / "match"
    result = match_cohorts(
        complaint,
        campaigns,
        applications,
        production,
        communications,
        out / "applications.jsonl",
        out / "cohorts.jsonl",
        out / "unmatched.jsonl",
        out / "cross.jsonl",
        out / "provenance.json",
    )
    cohorts = read_jsonl(out / "cohorts.jsonl")
    assert result["matched_cohort_count"] == 1
    assert result["matched_event_count"] == 10
    assert cohorts[0]["unique_recall_campaign_count"] == 1
    assert cohorts[0]["recall_applicability_row_count"] == 2
    assert cohorts[1]["unique_recall_campaign_count"] == 0
    assert result["cross_source_cohort_counts"] == {
        "complaint_only": 1,
        "complaint_production_manufacturer_communications_recalls": 1,
    }
    prohibited = {"failure_count", "repair_count", "risk_count", "reliability_score"}
    assert prohibited.isdisjoint(cohorts[0])


def test_outputs_are_deterministic_and_never_overwritten(tmp_path: Path) -> None:
    first, apps_first, _, _ = run_ingest(tmp_path / "a")
    second, apps_second, _, _ = run_ingest(tmp_path / "b")
    assert sha256_file(first) == sha256_file(second)
    assert sha256_file(apps_first) == sha256_file(apps_second)
    manifest = tmp_path / "a" / "raw" / "acquisition-manifest.json"
    with pytest.raises(ArtifactExistsError):
        ingest_archives(manifest, first, apps_first, tmp_path / "new.json")


def test_malformed_archive_fails_without_partial_outputs(tmp_path: Path) -> None:
    manifest = fixture_snapshot(tmp_path / "raw")
    artifact = manifest.parent / "FLAT_RCL_POST_2010.zip"
    write_zip(artifact, [["bad"]])
    data = json.loads(manifest.read_text())
    for item in data["artifacts"]:
        if item["filename"] == artifact.name:
            item["sha256"] = sha256_file(artifact)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "out"
    with pytest.raises(RecallError, match="fields"):
        ingest_archives(
            manifest, output / "campaigns.jsonl", output / "apps.jsonl", output / "p.json"
        )
    assert not output.exists() or not any(output.iterdir())
