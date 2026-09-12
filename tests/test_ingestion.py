"""Tests for the NHTSA ODI complaint ingestion boundary."""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import pytest

from howreliable.data.ingestion.nhtsa_complaints import (
    DATASET_NAME,
    SOURCE_COLUMNS,
    ArchiveError,
    ArtifactExistsError,
    DownloadError,
    HttpResponse,
    SchemaError,
    VehicleMappingError,
    download_artifact,
    extract_source_file,
    ingest_local_artifact,
    iter_complaints,
    map_record_to_vehicle,
    sha256_file,
)
from howreliable.domain import Drivetrain, Transmission

FIXED_RETRIEVAL = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
FIXED_INGESTION = datetime(2026, 9, 12, 14, 5, tzinfo=UTC)


def source_row(**overrides: str) -> list[str]:
    """Build one synthetic row in the documented 51-column source format."""
    values = dict.fromkeys(SOURCE_COLUMNS, "")
    values.update(
        {
            "CMPLID": "1234567",
            "ODINO": "11223344",
            "MFR_NAME": "Mazda North American Operations",
            "MAKETXT": "MAZDA",
            "MODELTXT": "MAZDA3",
            "YEARTXT": "2017",
            "CRASH": "N",
            "FIRE": "N",
            "INJURED": "0",
            "DEATHS": "0",
            "COMPDESC": "SERVICE BRAKES",
            "DATEA": "20200103",
            "LDATE": "20200102",
            "CDESCR": "Brake pedal felt unusual; source text is preserved.",
            "CMPL_TYPE": "IVOQ",
            "DRIVE_TRAIN": "FWD",
            "TRANS_TYPE": "AUTO",
            "PROD_TYPE": "V",
            "REPAIRED_YN": "N",
            "MEDICAL_ATTN": "N",
            "VEHICLES_TOWED_YN": "N",
        }
    )
    values.update(overrides)
    return [values[name] for name in SOURCE_COLUMNS]


def make_archive(
    directory: Path,
    rows: list[list[str]],
    *,
    member_name: str = "COMPLAINTS_RECEIVED_2020-2024.txt",
) -> Path:
    artifact = directory / "COMPLAINTS_RECEIVED_2020-2024.zip"
    payload = "".join(f"{'\t'.join(row)}\n" for row in rows).encode("latin-1")
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, payload)
    return artifact


def test_valid_official_format_fixture_parses_and_preserves_source_fields(tmp_path: Path) -> None:
    artifact = make_archive(
        tmp_path,
        [source_row(MAKETXT="  MAZDA  ", MILES="0000123", CDESCR="Mixed CASE narrative")],
    )

    record = next(iter_complaints(artifact))

    assert record.complaint_id == "1234567"
    assert record.make == "  MAZDA  "
    assert record.field("MILES") == "0000123"
    assert record.narrative == "Mixed CASE narrative"
    assert list(record.as_source_dict()) == list(SOURCE_COLUMNS)


def test_blank_source_fields_are_explicit_none(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row(FAILDATE="", VIN="")])

    record = next(iter_complaints(artifact))

    assert record.field("FAILDATE") is None
    assert record.field("VIN") is None
    assert record.as_source_dict()["FAILDATE"] is None


def test_checksum_is_deterministic(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()])

    assert sha256_file(artifact) == sha256_file(artifact)
    assert len(sha256_file(artifact)) == 64


def test_safe_archive_extraction_and_overwrite_protection(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()])
    destination = tmp_path / "extracted"

    extracted = extract_source_file(artifact, destination)

    assert extracted.name == "COMPLAINTS_RECEIVED_2020-2024.txt"
    assert extracted.read_text(encoding="latin-1").count("\t") == 50
    with pytest.raises(ArtifactExistsError, match="refusing to overwrite"):
        extract_source_file(artifact, destination)


def test_archive_rejects_missing_expected_text_file(tmp_path: Path) -> None:
    artifact = tmp_path / "complaints.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("README.md", "not complaint data")

    with pytest.raises(ArchiveError, match="exactly one complaint text file"):
        list(iter_complaints(artifact))


def test_archive_rejects_unsafe_member_path(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()], member_name="../complaints.txt")

    with pytest.raises(ArchiveError, match="unsafe archive member"):
        extract_source_file(artifact, tmp_path / "out")


def test_invalid_archive_fails_clearly(tmp_path: Path) -> None:
    artifact = tmp_path / "invalid.zip"
    artifact.write_bytes(b"not a ZIP archive")

    with pytest.raises(ArchiveError, match="invalid or unreadable"):
        list(iter_complaints(artifact))


def test_schema_mismatch_reports_row_and_column_count(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()[:-1]])

    with pytest.raises(SchemaError, match=r"row 1: expected 51 columns, received 50"):
        list(iter_complaints(artifact))


def test_malformed_empty_row_fails_clearly(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [[]])

    with pytest.raises(SchemaError, match=r"row 1: expected 51 columns, received 1"):
        list(iter_complaints(artifact))


def test_quotes_are_preserved_as_literal_source_data(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row(CDESCR='Owner wrote "quoted" text')])

    assert next(iter_complaints(artifact)).narrative == 'Owner wrote "quoted" text'


def test_ingestion_writes_deterministic_jsonl_and_complete_provenance(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row(), source_row(CMPLID="7654321", VIN="")])
    first_output = tmp_path / "first" / "complaints.jsonl"
    second_output = tmp_path / "second" / "complaints.jsonl"

    first = ingest_local_artifact(
        artifact,
        first_output,
        retrieval_timestamp=FIXED_RETRIEVAL,
        clock=lambda: FIXED_INGESTION,
    )
    second = ingest_local_artifact(
        artifact,
        second_output,
        retrieval_timestamp=FIXED_RETRIEVAL,
        clock=lambda: FIXED_INGESTION,
    )

    assert first_output.read_bytes() == second_output.read_bytes()
    assert first.provenance_path.read_bytes() == second.provenance_path.read_bytes()
    assert first.provenance.dataset_name == DATASET_NAME
    assert first.provenance.record_count == 2
    assert first.provenance.source_artifact_sha256 == sha256_file(artifact)
    assert first.provenance.retrieval_timestamp_utc == "2026-09-12T14:00:00Z"
    assert first.provenance.ingestion_timestamp_utc == "2026-09-12T14:05:00Z"
    assert first.provenance.upstream_version is None
    assert first.provenance.record_limit is None
    assert first.provenance.complete_artifact is True

    lines = first_output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["VIN"] is None


def test_bounded_ingestion_is_explicitly_marked_incomplete(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row(), source_row(CMPLID="7654321")])

    result = ingest_local_artifact(
        artifact,
        tmp_path / "sample.jsonl",
        retrieval_timestamp=FIXED_RETRIEVAL,
        record_limit=1,
    )

    assert result.provenance.record_count == 1
    assert result.provenance.record_limit == 1
    assert result.provenance.complete_artifact is False


def test_ingestion_refuses_to_overwrite_outputs(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()])
    output = tmp_path / "complaints.jsonl"
    output.write_text("keep me", encoding="utf-8")

    with pytest.raises(ArtifactExistsError, match="refusing to overwrite"):
        ingest_local_artifact(artifact, output, retrieval_timestamp=FIXED_RETRIEVAL)

    assert output.read_text(encoding="utf-8") == "keep me"


class FakeResponse:
    """Minimal context-managed HTTP response used by download tests."""

    def __init__(self, content: bytes, status: int = 200) -> None:
        self.status: int | None = status
        self._content = io.BytesIO(content)

    def read(self, size: int = -1) -> bytes:
        return self._content.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._content.close()


def test_download_success_and_raw_overwrite_protection(tmp_path: Path) -> None:
    content = b"official artifact bytes"

    def opener(url: str, *, timeout: float) -> AbstractContextManager[HttpResponse]:
        assert url == "https://example.test/complaints.zip"
        assert timeout == 10.0
        return FakeResponse(content)

    destination = tmp_path / "raw" / "complaints.zip"
    receipt = download_artifact(
        "https://example.test/complaints.zip",
        destination,
        opener=opener,
        timeout=10.0,
        clock=lambda: FIXED_RETRIEVAL,
    )

    assert destination.read_bytes() == content
    assert receipt.sha256 == sha256_file(destination)
    assert receipt.retrieval_timestamp_utc == "2026-09-12T14:00:00Z"
    with pytest.raises(ArtifactExistsError):
        download_artifact("https://example.test/complaints.zip", destination, opener=opener)
    assert destination.read_bytes() == content


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), OSError("disk full")])
def test_download_failure_is_explicit_and_removes_partial_file(
    tmp_path: Path, failure: Exception
) -> None:
    def failing_opener(url: str, *, timeout: float) -> AbstractContextManager[HttpResponse]:
        del url, timeout
        raise failure

    destination = tmp_path / "complaints.zip"
    with pytest.raises(DownloadError, match="failed to download"):
        download_artifact(
            "https://example.test/complaints.zip",
            destination,
            opener=failing_opener,
        )
    assert not destination.exists()


def test_non_success_http_response_fails_without_artifact(tmp_path: Path) -> None:
    def opener(url: str, *, timeout: float) -> AbstractContextManager[HttpResponse]:
        del url, timeout
        return FakeResponse(b"error body", status=503)

    destination = tmp_path / "complaints.zip"
    with pytest.raises(DownloadError, match="HTTP status 503"):
        download_artifact("https://example.test/complaints.zip", destination, opener=opener)
    assert not destination.exists()


def test_vehicle_mapping_is_explicit_and_does_not_infer_optional_fields(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()])

    vehicle = map_record_to_vehicle(next(iter_complaints(artifact)))

    assert vehicle.make == "mazda"
    assert vehicle.model == "mazda3"
    assert vehicle.year == 2017
    assert vehicle.generation is None
    assert vehicle.trim is None
    assert vehicle.engine is None
    assert vehicle.transmission is Transmission.AUTOMATIC
    assert vehicle.drivetrain is Drivetrain.FWD


@pytest.mark.parametrize(
    "overrides",
    [
        {"TRANS_TYPE": ""},
        {"YEARTXT": "9999"},
        {"PROD_TYPE": "T"},
        {"TRANS_TYPE": "CVT"},
    ],
)
def test_vehicle_mapping_failure_is_observable(tmp_path: Path, overrides: dict[str, str]) -> None:
    artifact = make_archive(tmp_path, [source_row(**overrides)])

    with pytest.raises(VehicleMappingError):
        map_record_to_vehicle(next(iter_complaints(artifact)))


def test_naive_provenance_timestamp_is_rejected(tmp_path: Path) -> None:
    artifact = make_archive(tmp_path, [source_row()])

    with pytest.raises(ValueError, match="timezone-aware"):
        ingest_local_artifact(
            artifact,
            tmp_path / "output.jsonl",
            retrieval_timestamp=datetime(2026, 9, 12),
        )
    assert not (tmp_path / "output.jsonl").exists()
