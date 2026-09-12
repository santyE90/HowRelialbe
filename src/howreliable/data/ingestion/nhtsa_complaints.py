"""Adapter for the official NHTSA ODI vehicle-owner complaints flat file."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final, Protocol, cast

from howreliable.domain import Vehicle

SOURCE_ORGANIZATION: Final = "National Highway Traffic Safety Administration (NHTSA)"
DATASET_NAME: Final = "Office of Defects Investigation Vehicle Owner Complaints"
DEFAULT_SOURCE_URL: Final = (
    "https://static.nhtsa.gov/odi/ffdd/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip"
)
DEFAULT_REQUESTED_RANGE: Final = "complaints received 2020-2024"
ADAPTER_VERSION: Final = "1.0"
SOURCE_SCHEMA_VERSION: Final = "CMPL 2026-04-30 (51 fields)"
# NHTSA does not publish an encoding and the real artifact contains bytes undefined in
# Windows-1252. Latin-1 provides a deterministic, lossless one-byte-to-one-code-point mapping.
SOURCE_ENCODING: Final = "latin-1"

SOURCE_COLUMNS: Final = (
    "CMPLID",
    "ODINO",
    "MFR_NAME",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "CRASH",
    "FAILDATE",
    "FIRE",
    "INJURED",
    "DEATHS",
    "COMPDESC",
    "CITY",
    "STATE",
    "VIN",
    "DATEA",
    "LDATE",
    "MILES",
    "OCCURENCES",
    "CDESCR",
    "CMPL_TYPE",
    "POLICE_RPT_YN",
    "PURCH_DT",
    "ORIG_OWNER_YN",
    "ANTI_BRAKES_YN",
    "CRUISE_CONT_YN",
    "NUM_CYLS",
    "DRIVE_TRAIN",
    "FUEL_SYS",
    "FUEL_TYPE",
    "TRANS_TYPE",
    "VEH_SPEED",
    "DOT",
    "TIRE_SIZE",
    "LOC_OF_TIRE",
    "TIRE_FAIL_TYPE",
    "ORIG_EQUIP_YN",
    "MANUF_DT",
    "SEAT_TYPE",
    "RESTRAINT_TYPE",
    "DEALER_NAME",
    "DEALER_TEL",
    "DEALER_CITY",
    "DEALER_STATE",
    "DEALER_ZIP",
    "PROD_TYPE",
    "REPAIRED_YN",
    "MEDICAL_ATTN",
    "VEHICLES_TOWED_YN",
    "STATE_OF_INCIDENT",
    "VEHICLE_OPERATOR",
)


class IngestionError(Exception):
    """Base exception for expected ingestion failures."""


class ArtifactExistsError(IngestionError):
    """Raised when an operation would overwrite an artifact."""


class DownloadError(IngestionError):
    """Raised when the official source cannot be downloaded completely."""


class ArchiveError(IngestionError):
    """Raised when an archive is invalid or has an unsafe/unexpected layout."""


class SchemaError(IngestionError):
    """Raised when a flat-file record does not match the documented schema."""


class VehicleMappingError(IngestionError):
    """Raised when a complaint lacks fields required by the Vehicle model."""


class HttpResponse(Protocol):
    """Small response boundary used to isolate urllib in tests."""

    status: int | None

    def read(self, size: int = -1) -> bytes: ...


class UrlOpener(Protocol):
    """Callable protocol for an HTTPS opener."""

    def __call__(
        self, url: str, *, timeout: float
    ) -> AbstractContextManager[HttpResponse]: ...


@dataclass(frozen=True, slots=True)
class DownloadReceipt:
    """Metadata captured while acquiring a raw artifact."""

    path: Path
    source_url: str
    retrieval_timestamp_utc: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Provenance:
    """Traceability metadata for a structured ingestion output."""

    source_organization: str
    dataset_name: str
    official_source_url: str
    requested_range: str
    upstream_version: None
    retrieval_timestamp_utc: str
    source_artifact_filename: str
    source_artifact_sha256: str
    ingestion_timestamp_utc: str
    adapter_version: str
    source_schema_version: str
    source_member_filename: str
    record_count: int
    record_limit: int | None
    complete_artifact: bool

    def as_dict(self) -> dict[str, str | int | None]:
        """Return provenance in deterministic serialization order."""
        return {
            "source_organization": self.source_organization,
            "dataset_name": self.dataset_name,
            "official_source_url": self.official_source_url,
            "requested_range": self.requested_range,
            "upstream_version": self.upstream_version,
            "retrieval_timestamp_utc": self.retrieval_timestamp_utc,
            "source_artifact_filename": self.source_artifact_filename,
            "source_artifact_sha256": self.source_artifact_sha256,
            "ingestion_timestamp_utc": self.ingestion_timestamp_utc,
            "adapter_version": self.adapter_version,
            "source_schema_version": self.source_schema_version,
            "source_member_filename": self.source_member_filename,
            "record_count": self.record_count,
            "record_limit": self.record_limit,
            "complete_artifact": self.complete_artifact,
        }


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Locations and provenance produced by an ingestion run."""

    output_path: Path
    provenance_path: Path
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class NhtsaComplaintRecord:
    """One source row, preserving all 51 documented columns as strings or None."""

    values: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(SOURCE_COLUMNS):
            raise SchemaError(
                f"expected {len(SOURCE_COLUMNS)} columns, received {len(self.values)}"
            )

    @classmethod
    def from_columns(cls, columns: list[str]) -> NhtsaComplaintRecord:
        """Represent blank source fields explicitly without changing nonblank values."""
        if len(columns) != len(SOURCE_COLUMNS):
            raise SchemaError(f"expected {len(SOURCE_COLUMNS)} columns, received {len(columns)}")
        return cls(tuple(value if value != "" else None for value in columns))

    def as_source_dict(self) -> dict[str, str | None]:
        """Return fields in official data-dictionary order."""
        return dict(zip(SOURCE_COLUMNS, self.values, strict=True))

    def field(self, name: str) -> str | None:
        """Read one official source field by its documented name."""
        try:
            index = SOURCE_COLUMNS.index(name)
        except ValueError as error:
            raise KeyError(name) from error
        return self.values[index]

    @property
    def complaint_id(self) -> str | None:
        return self.field("CMPLID")

    @property
    def odi_number(self) -> str | None:
        return self.field("ODINO")

    @property
    def make(self) -> str | None:
        return self.field("MAKETXT")

    @property
    def model(self) -> str | None:
        return self.field("MODELTXT")

    @property
    def model_year(self) -> str | None:
        return self.field("YEARTXT")

    @property
    def component(self) -> str | None:
        return self.field("COMPDESC")

    @property
    def received_date(self) -> str | None:
        return self.field("LDATE")

    @property
    def narrative(self) -> str | None:
        return self.field("CDESCR")


def sha256_file(path: Path) -> str:
    """Compute a source artifact checksum without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifact(
    url: str,
    destination: Path,
    *,
    opener: UrlOpener | None = None,
    timeout: float = 60.0,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DownloadReceipt:
    """Download an HTTPS artifact once, refusing to overwrite an existing file."""
    if not url.lower().startswith("https://"):
        raise DownloadError("source URL must use HTTPS")
    if destination.exists():
        raise ArtifactExistsError(f"refusing to overwrite raw artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    open_url = opener or cast(UrlOpener, urllib.request.urlopen)

    try:
        with open_url(url, timeout=timeout) as response, destination.open("xb") as target:
            if response.status is not None and not 200 <= response.status < 300:
                raise DownloadError(f"source returned HTTP status {response.status}")
            shutil.copyfileobj(cast(BinaryIO, response), target, length=1024 * 1024)
    except (OSError, urllib.error.URLError, DownloadError) as error:
        destination.unlink(missing_ok=True)
        if isinstance(error, DownloadError):
            raise
        raise DownloadError(f"failed to download {url}: {error}") from error

    return DownloadReceipt(
        path=destination,
        source_url=url,
        retrieval_timestamp_utc=_utc_timestamp(clock()),
        sha256=sha256_file(destination),
    )


def _source_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = [member for member in archive.infolist() if not member.is_dir()]
    text_members = [member for member in members if member.filename.lower().endswith(".txt")]
    if len(text_members) != 1:
        raise ArchiveError(f"expected exactly one complaint text file, found {len(text_members)}")

    member = text_members[0]
    member_path = PurePosixPath(member.filename)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ArchiveError(f"unsafe archive member path: {member.filename}")
    return member


def archive_member_name(artifact: Path) -> str:
    """Validate an archive and return its single complaint text member."""
    try:
        with zipfile.ZipFile(artifact) as archive:
            return _source_member(archive).filename
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveError(f"invalid or unreadable ZIP archive: {artifact}") from error


def extract_source_file(artifact: Path, destination_directory: Path) -> Path:
    """Safely extract the single source text file without overwriting existing data."""
    try:
        with zipfile.ZipFile(artifact) as archive:
            member = _source_member(archive)
            target = destination_directory / PurePosixPath(member.filename).name
            if target.exists():
                raise ArtifactExistsError(f"refusing to overwrite extracted artifact: {target}")
            destination_directory.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return target
    except ArtifactExistsError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ArchiveError(f"invalid or unreadable ZIP archive: {artifact}") from error


def iter_complaints(
    artifact: Path,
    *,
    limit: int | None = None,
) -> Iterator[NhtsaComplaintRecord]:
    """Stream structurally valid source records from the official ZIP artifact."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative or None")
    if limit == 0:
        return

    try:
        with zipfile.ZipFile(artifact) as archive:
            member = _source_member(archive)
            with (
                archive.open(member) as binary_source,
                io.TextIOWrapper(
                    binary_source,
                    encoding=SOURCE_ENCODING,
                    errors="strict",
                    newline="",
                ) as text_source,
            ):
                for row_number, raw_line in enumerate(text_source, start=1):
                    line = raw_line.removesuffix("\n").removesuffix("\r")
                    columns = line.split("\t")
                    try:
                        yield NhtsaComplaintRecord.from_columns(columns)
                    except SchemaError as error:
                        raise SchemaError(f"row {row_number}: {error}") from error
                    if limit is not None and row_number >= limit:
                        break
    except (ArchiveError, SchemaError):
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, UnicodeError) as error:
        raise ArchiveError(f"invalid or unreadable complaint archive: {artifact}") from error


def map_record_to_vehicle(record: NhtsaComplaintRecord) -> Vehicle:
    """Map only sufficiently specified NHTSA vehicle rows to the Phase 1A model."""
    fields = record.as_source_dict()
    if fields["PROD_TYPE"] != "V":
        raise VehicleMappingError("complaint does not describe a vehicle product")

    required = ("MAKETXT", "MODELTXT", "YEARTXT", "TRANS_TYPE", "DRIVE_TRAIN")
    missing = [name for name in required if fields[name] is None]
    if missing:
        raise VehicleMappingError(f"insufficient vehicle fields: {', '.join(missing)}")
    if fields["YEARTXT"] == "9999":
        raise VehicleMappingError("NHTSA model year is explicitly unknown")

    transmission = {"AUTO": "automatic", "MAN": "manual"}.get(
        cast(str, fields["TRANS_TYPE"])
    )
    if transmission is None:
        raise VehicleMappingError(f"unsupported NHTSA transmission: {fields['TRANS_TYPE']!r}")

    try:
        return Vehicle.model_validate(
            {
                "make": cast(str, fields["MAKETXT"]),
                "model": cast(str, fields["MODELTXT"]),
                "year": int(cast(str, fields["YEARTXT"])),
                "generation": None,
                "trim": None,
                "engine": None,
                "transmission": transmission,
                "drivetrain": cast(str, fields["DRIVE_TRAIN"]),
            }
        )
    except (TypeError, ValueError) as error:
        raise VehicleMappingError(f"invalid canonical vehicle fields: {error}") from error


def ingest_local_artifact(
    artifact: Path,
    output_path: Path,
    *,
    retrieval_timestamp: datetime,
    source_url: str = DEFAULT_SOURCE_URL,
    requested_range: str = DEFAULT_REQUESTED_RANGE,
    record_limit: int | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IngestionResult:
    """Convert one immutable ZIP artifact to deterministic JSON Lines plus provenance."""
    provenance_path = output_path.with_suffix(f"{output_path.suffix}.provenance.json")
    existing = [path for path in (output_path, provenance_path) if path.exists()]
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite ingestion output: {existing[0]}")

    source_checksum = sha256_file(artifact)
    member_name = archive_member_name(artifact)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    record_count = 0

    try:
        with output_path.open("x", encoding="utf-8", newline="\n") as output:
            for record in iter_complaints(artifact, limit=record_limit):
                line = json.dumps(
                    record.as_source_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                output.write(f"{line}\n")
                record_count += 1

        provenance = Provenance(
            source_organization=SOURCE_ORGANIZATION,
            dataset_name=DATASET_NAME,
            official_source_url=source_url,
            requested_range=requested_range,
            upstream_version=None,
            retrieval_timestamp_utc=_utc_timestamp(retrieval_timestamp),
            source_artifact_filename=artifact.name,
            source_artifact_sha256=source_checksum,
            ingestion_timestamp_utc=_utc_timestamp(clock()),
            adapter_version=ADAPTER_VERSION,
            source_schema_version=SOURCE_SCHEMA_VERSION,
            source_member_filename=member_name,
            record_count=record_count,
            record_limit=record_limit,
            complete_artifact=record_limit is None,
        )
        with provenance_path.open("x", encoding="utf-8", newline="\n") as metadata:
            json.dump(provenance.as_dict(), metadata, ensure_ascii=False, indent=2)
            metadata.write("\n")
    except Exception:
        output_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        raise

    return IngestionResult(output_path, provenance_path, provenance)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
