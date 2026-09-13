"""Official NHTSA manufacturer-communications/TSB archive adapter."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, TextIO, cast

from howreliable.data.exposure.nhtsa_ewr import normalize_identity
from howreliable.data.ingestion.nhtsa_complaints import (
    ArtifactExistsError,
    download_artifact,
    sha256_file,
)
from howreliable.data.mapping.nhtsa_reliability import map_nhtsa_component
from howreliable.domain import Component

SOURCE_ORGANIZATION: Final = "National Highway Traffic Safety Administration (NHTSA)"
MFR_DATASET: Final = "Manufacturer Communications compact CSV"
TSB_DATASET: Final = "Manufacturer Communications flat TSV"
MFR_URL: Final = (
    "https://static.nhtsa.gov/odi/ffdd/tsbs/MFR_COMMS_RECEIVED_2020-2024.zip"
)
TSB_URL: Final = "https://static.nhtsa.gov/odi/ffdd/tsbs/TSBS_RECEIVED_2020-2024.zip"
DICTIONARY_URL: Final = "https://static.nhtsa.gov/odi/ffdd/tsbs/TSBS.txt"
ADAPTER_VERSION: Final = "nhtsa-manufacturer-communications-1.0"
MATCHING_VERSION: Final = "nhtsa-manufacturer-communication-matching-1.0"
SOURCE_SCHEMA_VERSION: Final = "TSBS.txt change log 2024-05-13 (14 fields)"
SOURCE_ENCODING: Final = "utf-8"
ID_PATTERN: Final = re.compile(r"^[0-9]{1,9}$")
YEAR_PATTERN: Final = re.compile(r"^[0-9]{4}$")
DATE_PATTERN: Final = re.compile(r"^[0-9]{8}$")

MFR_COLUMNS: Final = (
    "TSB/Document ID",
    "Make",
    "Model",
    "Model Year",
    "Concise Summary",
)
TSB_COLUMNS: Final = (
    "NHTSA ID Number",
    "Replacement Service Bulletin Number",
    "Date Added to File",
    "TSB/Document ID",
    "Mfr Communication Date",
    "Mfr Internal Campaign ID/Software Version",
    "Communication Type",
    "Make",
    "Model",
    "Model Year",
    "NHTSA Components",
    "Mfr Component System",
    "Mfr Component Subsystem",
    "Summary",
)


class ManufacturerCommunicationError(Exception):
    """Raised for invalid communication source data or processing invariants."""


class CommunicationType(StrEnum):
    """Direct mapping of NHTSA's structured communication-type field."""

    SERVICE_BULLETIN = "SERVICE_BULLETIN"
    SERVICE_CAMPAIGN = "SERVICE_CAMPAIGN"
    WARRANTY_PROGRAM = "WARRANTY_PROGRAM"
    OVER_THE_AIR = "OVER_THE_AIR"
    EMISSIONS = "EMISSIONS"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


TYPE_MAP: Final = {
    "Service Bulletin/Repair Instructions": CommunicationType.SERVICE_BULLETIN,
    "Service Campaign": CommunicationType.SERVICE_CAMPAIGN,
    "Warranty Program/Extension": CommunicationType.WARRANTY_PROGRAM,
    "Over The Air": CommunicationType.OVER_THE_AIR,
    "Emissions": CommunicationType.EMISSIONS,
    "Other": CommunicationType.OTHER,
}


class ApplicabilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INSUFFICIENT_VEHICLE_IDENTITY = "INSUFFICIENT_VEHICLE_IDENTITY"


class CommunicationMatchStatus(StrEnum):
    EXACT = "EXACT"
    EXPLICIT_ALIAS = "EXPLICIT_ALIAS"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    INSUFFICIENT_VEHICLE_IDENTITY = "INSUFFICIENT_VEHICLE_IDENTITY"


EXPLICIT_ALIASES: Final[Mapping[tuple[str, str], tuple[str, str]]] = {}


@dataclass(frozen=True, slots=True)
class MfrCommsCsvRecord:
    """One five-field row from the compact official CSV view."""

    source_identifier: str
    make: str
    model: str
    model_years: str
    concise_summary: str

    @property
    def expanded_model_years(self) -> tuple[str, ...]:
        return tuple(self.model_years.split(","))


@dataclass(frozen=True, slots=True)
class NhtsaTsbRecord:
    """One source-faithful row from the richer headerless 14-field TSV."""

    values: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(TSB_COLUMNS):
            raise ManufacturerCommunicationError(
                f"expected {len(TSB_COLUMNS)} fields, received {len(self.values)}"
            )

    def field(self, name: str) -> str | None:
        try:
            return self.values[TSB_COLUMNS.index(name)]
        except ValueError as error:
            raise KeyError(name) from error

    def as_source_dict(self) -> dict[str, str | None]:
        return dict(zip(TSB_COLUMNS, self.values, strict=True))

    @property
    def nhtsa_id(self) -> str:
        return cast(str, self.field("NHTSA ID Number"))


@dataclass(frozen=True, slots=True)
class CanonicalCommunication:
    """One NHTSA communication/document, independent of vehicle applicability."""

    communication_id: str
    source_organization: str
    source_dataset: str
    source_nhtsa_id: str
    manufacturer_document_id: str
    replacement_document_id: str | None
    manufacturer_campaign_or_software_id: str | None
    original_communication_type: str
    communication_type: str
    original_date_added: str
    date_added: str | None
    original_communication_date: str
    communication_date: str | None
    date_quality_flags: tuple[str, ...]
    summary: str
    nhtsa_components: tuple[str, ...]
    canonical_components: tuple[str, ...]
    manufacturer_component_systems: tuple[str, ...]
    manufacturer_component_subsystems: tuple[str, ...]
    source_artifact_filename: str
    source_row_count: int


@dataclass(frozen=True, slots=True)
class CommunicationApplicability:
    """A communication-to-source-product relationship, distinct from the document."""

    applicability_id: str
    communication_id: str
    source_nhtsa_id: str
    original_make: str
    original_model: str
    original_model_year: str
    normalized_make: str
    normalized_model: str
    model_year: int | None
    applicability_status: str
    source_expanded_row_count: int


COMMUNICATION_SCHEMA: Final = tuple(CanonicalCommunication.__dataclass_fields__)
APPLICABILITY_SCHEMA: Final = tuple(CommunicationApplicability.__dataclass_fields__)
APPLICABILITY_MATCH_SCHEMA: Final = (
    *APPLICABILITY_SCHEMA,
    "complaint_match_status",
    "match_method",
    "matched_broad_vehicle_id",
    "ambiguity_candidate_count",
)
COHORT_EVIDENCE_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "manufacturer_communication_match_status",
    "match_method",
    "unique_communication_count",
    "communication_applicability_row_count",
    "source_expanded_row_count",
    "unique_manufacturer_document_id_count",
    "first_communication_date",
    "last_communication_date",
    "summary_observed_communication_count",
    "manufacturer_component_observed_communication_count",
    *(f"communication_type_{item.value.lower()}_count" for item in CommunicationType),
    *(f"component_{item.value}_communication_count" for item in Component),
)
CROSS_SOURCE_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "has_production",
    "has_manufacturer_communications",
    "coverage_category",
)


@dataclass(slots=True)
class _CommunicationAccumulator:
    metadata: tuple[str, ...]
    nhtsa_components: set[str] = field(default_factory=set)
    systems: set[str] = field(default_factory=set)
    subsystems: set[str] = field(default_factory=set)
    source_row_count: int = 0


def _required_text(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise ManufacturerCommunicationError(f"{name} must not be blank")
    return value


def parse_mfr_comms_row(row: Sequence[str]) -> MfrCommsCsvRecord:
    """Parse one compact CSV row without interpreting its misleading first header."""
    if len(row) != len(MFR_COLUMNS):
        raise ManufacturerCommunicationError(
            f"expected {len(MFR_COLUMNS)} CSV fields, received {len(row)}"
        )
    values = tuple(_required_text(value, MFR_COLUMNS[i]) for i, value in enumerate(row))
    return MfrCommsCsvRecord(*values)


def parse_tsb_row(row: Sequence[str]) -> NhtsaTsbRecord:
    """Parse one TSV row, preserving blanks as explicit nulls."""
    if len(row) != len(TSB_COLUMNS):
        raise ManufacturerCommunicationError(
            f"expected {len(TSB_COLUMNS)} TSV fields, received {len(row)}"
        )
    values = tuple(value if value != "" else None for value in row)
    for index in (0, 2, 3, 4, 6, 7, 8, 9, 10, 13):
        _required_text(values[index], TSB_COLUMNS[index])
    identifier = cast(str, values[0])
    if ID_PATTERN.fullmatch(identifier) is None:
        raise ManufacturerCommunicationError(f"invalid NHTSA ID Number: {identifier!r}")
    return NhtsaTsbRecord(values)


def _archive_member(artifact: Path, suffix: str) -> zipfile.ZipInfo:
    try:
        with zipfile.ZipFile(artifact) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            selected = [item for item in members if item.filename.lower().endswith(suffix)]
            if len(selected) != 1:
                raise ManufacturerCommunicationError(
                    f"expected one {suffix} member in {artifact.name}, found {len(selected)}"
                )
            path = PurePosixPath(selected[0].filename)
            if path.is_absolute() or ".." in path.parts:
                raise ManufacturerCommunicationError(f"unsafe archive member: {path}")
            return selected[0]
    except (OSError, zipfile.BadZipFile) as error:
        raise ManufacturerCommunicationError(f"invalid ZIP artifact: {artifact}") from error


def _open_member(artifact: Path, suffix: str) -> tuple[zipfile.ZipFile, TextIO]:
    archive = zipfile.ZipFile(artifact)
    try:
        members = [item for item in archive.infolist() if not item.is_dir()]
        selected = [item for item in members if item.filename.lower().endswith(suffix)]
        if len(selected) != 1:
            raise ManufacturerCommunicationError(
                f"expected one {suffix} member in {artifact.name}, found {len(selected)}"
            )
        binary = archive.open(selected[0])
        return archive, io.TextIOWrapper(binary, encoding=SOURCE_ENCODING, newline="")
    except Exception:
        archive.close()
        raise


def iter_tsb_records(artifact: Path) -> Iterator[NhtsaTsbRecord]:
    """Stream all headerless TSV records with strict field-count validation."""
    _archive_member(artifact, ".txt")
    try:
        archive, source = _open_member(artifact, ".txt")
        with archive, source:
            for number, line in enumerate(source, 1):
                try:
                    yield parse_tsb_row(line.rstrip("\r\n").split("\t"))
                except ManufacturerCommunicationError as error:
                    raise ManufacturerCommunicationError(f"TSV row {number}: {error}") from error
    except (OSError, UnicodeError, zipfile.BadZipFile) as error:
        raise ManufacturerCommunicationError(f"cannot read TSV artifact: {artifact}") from error


def iter_mfr_records(artifact: Path) -> Iterator[MfrCommsCsvRecord]:
    """Stream compact CSV records, explicitly ignoring repeated header lines."""
    _archive_member(artifact, ".csv")
    csv.field_size_limit(10_000_000)
    try:
        archive, source = _open_member(artifact, ".csv")
        with archive, source:
            reader = csv.reader(source)
            header = next(reader, None)
            if tuple(header or ()) != MFR_COLUMNS:
                raise ManufacturerCommunicationError("compact CSV header does not match schema")
            for number, row in enumerate(reader, 2):
                if tuple(row) == MFR_COLUMNS:
                    continue
                try:
                    yield parse_mfr_comms_row(row)
                except ManufacturerCommunicationError as error:
                    raise ManufacturerCommunicationError(f"CSV row {number}: {error}") from error
    except (OSError, UnicodeError, csv.Error, zipfile.BadZipFile) as error:
        raise ManufacturerCommunicationError(f"cannot read CSV artifact: {artifact}") from error


def _parse_date(value: str, name: str) -> tuple[str | None, str | None]:
    if DATE_PATTERN.fullmatch(value) is None:
        return None, f"malformed_{name}"
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None, f"invalid_{name}"
    if not 1900 <= parsed.year <= 2100:
        return None, f"out_of_range_{name}"
    return parsed.isoformat(), None


def _communication_type(value: str) -> CommunicationType:
    return TYPE_MAP.get(value, CommunicationType.UNKNOWN)


def _applicability(
    identifier: str,
    make: str,
    model: str,
    year_text: str,
    source_row_count: int,
) -> CommunicationApplicability:
    model_year = None
    status = ApplicabilityStatus.INSUFFICIENT_VEHICLE_IDENTITY
    if YEAR_PATTERN.fullmatch(year_text) and year_text != "9999":
        candidate = int(year_text)
        if 1886 <= candidate <= 2100:
            model_year = candidate
            status = ApplicabilityStatus.ELIGIBLE
    normalized_make = normalize_identity(make)
    normalized_model = normalize_identity(model)
    identity = json.dumps(
        [identifier, make, model, year_text], ensure_ascii=False, separators=(",", ":")
    )
    return CommunicationApplicability(
        applicability_id=f"nhtsa_mc_app_{hashlib.sha256(identity.encode()).hexdigest()}",
        communication_id=f"nhtsa_mc_{identifier}",
        source_nhtsa_id=identifier,
        original_make=make,
        original_model=model,
        original_model_year=year_text,
        normalized_make=normalized_make,
        normalized_model=normalized_model,
        model_year=model_year,
        applicability_status=status.value,
        source_expanded_row_count=source_row_count,
    )


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


def retrieve_artifacts(
    raw_directory: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Download both official views and their shared dictionary without overwrite."""
    if raw_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite raw snapshot: {raw_directory}")
    raw_directory.mkdir(parents=True)
    artifacts = []
    for filename, url in (
        ("MFR_COMMS_RECEIVED_2020-2024.zip", MFR_URL),
        ("TSBS_RECEIVED_2020-2024.zip", TSB_URL),
        ("TSBS.txt", DICTIONARY_URL),
    ):
        receipt = download_artifact(url, raw_directory / filename, clock=clock, timeout=180)
        artifacts.append(
            {
                "filename": filename,
                "official_source_url": url,
                "retrieval_timestamp_utc": receipt.retrieval_timestamp_utc,
                "sha256": receipt.sha256,
            }
        )
    manifest = {
        "source_organization": SOURCE_ORGANIZATION,
        "requested_received_date_range": "2020-2024",
        "source_schema_version": SOURCE_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "artifacts": artifacts,
    }
    path = raw_directory / "acquisition-manifest.json"
    _write_json(path, manifest)
    return path


def _check_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManufacturerCommunicationError("cannot read acquisition manifest") from error
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        raise ManufacturerCommunicationError("invalid acquisition manifest schema")
    manifest = cast(dict[str, Any], value)
    for artifact in manifest["artifacts"]:
        path = manifest_path.parent / artifact["filename"]
        if sha256_file(path) != artifact["sha256"]:
            raise ManufacturerCommunicationError(f"checksum mismatch: {path.name}")
    return manifest


def _audit_compact_view(
    artifact: Path,
    tsv_ids: set[str],
    tsv_products: set[tuple[str, str, str, str]],
) -> dict[str, Any]:
    identifiers: set[str] = set()
    products: set[tuple[str, str, str, str]] = set()
    rows = 0
    repeated_headers = 0
    csv.field_size_limit(10_000_000)
    archive, source = _open_member(artifact, ".csv")
    with archive, source:
        reader = csv.reader(source)
        header = next(reader)
        if tuple(header) != MFR_COLUMNS:
            raise ManufacturerCommunicationError("compact CSV header does not match schema")
        for row in reader:
            if tuple(row) == MFR_COLUMNS:
                repeated_headers += 1
                continue
            record = parse_mfr_comms_row(row)
            rows += 1
            identifiers.add(record.source_identifier)
            for year in record.expanded_model_years:
                products.add((record.source_identifier, record.make, record.model, year))
    return {
        "source_row_count": rows,
        "repeated_header_row_count": repeated_headers,
        "unique_source_identifier_count": len(identifiers),
        "expanded_product_count": len(products),
        "identifier_overlap_count": len(identifiers & tsv_ids),
        "mfr_only_identifier_count": len(identifiers - tsv_ids),
        "tsv_only_identifier_count": len(tsv_ids - identifiers),
        "product_overlap_count": len(products & tsv_products),
        "mfr_only_product_count": len(products - tsv_products),
        "tsv_only_product_count": len(tsv_products - products),
        "mfr_only_identifiers": sorted(identifiers - tsv_ids),
        "tsv_only_identifiers": sorted(tsv_ids - identifiers),
    }


def ingest_archives(
    manifest_path: Path,
    communications_path: Path,
    applicability_path: Path,
    provenance_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Canonicalize rich TSV data and audit, but never union, the compact CSV view."""
    targets = (communications_path, applicability_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite communication output: {existing}")
    manifest = _check_manifest(manifest_path)
    tsb_path = manifest_path.parent / "TSBS_RECEIVED_2020-2024.zip"
    mfr_path = manifest_path.parent / "MFR_COMMS_RECEIVED_2020-2024.zip"
    database_path = applicability_path.parent / ".manufacturer-communications.tmp.sqlite"
    if database_path.exists():
        raise ArtifactExistsError(f"refusing to overwrite temporary database: {database_path}")
    communications: dict[str, _CommunicationAccumulator] = {}
    type_counts: Counter[str] = Counter()
    invalid_date_counts: Counter[str] = Counter()
    product_keys: set[tuple[str, str, str, str]] = set()
    source_rows = 0
    try:
        applicability_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.execute(
            "CREATE TABLE applicability (nhtsa_id TEXT, make TEXT, model TEXT, year TEXT, "
            "source_rows INTEGER, PRIMARY KEY (nhtsa_id, make, model, year))"
        )
        batch: list[tuple[str, str, str, str]] = []
        for record in iter_tsb_records(tsb_path):
            source_rows += 1
            fields = record.as_source_dict()
            identifier = record.nhtsa_id
            metadata = tuple(
                cast(str, fields[name]) if fields[name] is not None else ""
                for name in (
                    "Replacement Service Bulletin Number",
                    "Date Added to File",
                    "TSB/Document ID",
                    "Mfr Communication Date",
                    "Mfr Internal Campaign ID/Software Version",
                    "Communication Type",
                    "Summary",
                )
            )
            accumulator = communications.setdefault(identifier, _CommunicationAccumulator(metadata))
            if accumulator.metadata != metadata:
                raise ManufacturerCommunicationError(
                    f"communication metadata conflicts for NHTSA ID {identifier}"
                )
            accumulator.source_row_count += 1
            accumulator.nhtsa_components.add(cast(str, fields["NHTSA Components"]))
            system = fields["Mfr Component System"]
            subsystem = fields["Mfr Component Subsystem"]
            if system:
                accumulator.systems.add(system)
            if subsystem:
                accumulator.subsystems.add(subsystem)
            type_counts[cast(str, fields["Communication Type"])] += 1
            key = (
                identifier,
                cast(str, fields["Make"]),
                cast(str, fields["Model"]),
                cast(str, fields["Model Year"]),
            )
            product_keys.add(key)
            batch.append(key)
            if len(batch) >= 10_000:
                _upsert_applicability(connection, batch)
                batch.clear()
        _upsert_applicability(connection, batch)
        connection.commit()

        canonical_rows: list[CanonicalCommunication] = []
        manufacturer_document_ids: Counter[str] = Counter()
        for identifier, accumulator in sorted(communications.items()):
            replacement, added, document, communication_date, campaign, raw_type, summary = (
                accumulator.metadata
            )
            parsed_added, added_flag = _parse_date(added, "date_added")
            parsed_communication, communication_flag = _parse_date(
                communication_date, "communication_date"
            )
            flags = tuple(sorted(flag for flag in (added_flag, communication_flag) if flag))
            invalid_date_counts.update(flags)
            manufacturer_document_ids[document] += 1
            canonical_components = tuple(
                sorted({map_nhtsa_component(value).value for value in accumulator.nhtsa_components})
            )
            canonical_rows.append(
                CanonicalCommunication(
                    communication_id=f"nhtsa_mc_{identifier}",
                    source_organization=SOURCE_ORGANIZATION,
                    source_dataset=TSB_DATASET,
                    source_nhtsa_id=identifier,
                    manufacturer_document_id=document,
                    replacement_document_id=replacement or None,
                    manufacturer_campaign_or_software_id=campaign or None,
                    original_communication_type=raw_type,
                    communication_type=_communication_type(raw_type).value,
                    original_date_added=added,
                    date_added=parsed_added,
                    original_communication_date=communication_date,
                    communication_date=parsed_communication,
                    date_quality_flags=flags,
                    summary=summary,
                    nhtsa_components=tuple(sorted(accumulator.nhtsa_components)),
                    canonical_components=canonical_components,
                    manufacturer_component_systems=tuple(sorted(accumulator.systems)),
                    manufacturer_component_subsystems=tuple(sorted(accumulator.subsystems)),
                    source_artifact_filename=tsb_path.name,
                    source_row_count=accumulator.source_row_count,
                )
            )
        _write_jsonl(communications_path, (asdict(row) for row in canonical_rows))

        def applicability_rows() -> Iterator[dict[str, Any]]:
            query = (
                "SELECT nhtsa_id, make, model, year, source_rows FROM applicability "
                "ORDER BY lower(make), lower(model), year, nhtsa_id, make, model"
            )
            for identifier, make, model, year, count in connection.execute(query):
                yield asdict(_applicability(identifier, make, model, year, count))

        applicability_count = _write_jsonl(applicability_path, applicability_rows())
        connection.close()
        database_path.unlink()
        audit = _audit_compact_view(mfr_path, set(communications), product_keys)
        types_by_communication = Counter(row.communication_type for row in canonical_rows)
        valid_years = [
            int(year)
            for _, _, _, year in product_keys
            if YEAR_PATTERN.fullmatch(year) and year != "9999" and 1886 <= int(year) <= 2100
        ]
        added_dates = sorted(row.date_added for row in canonical_rows if row.date_added)
        communication_dates = sorted(
            row.communication_date for row in canonical_rows if row.communication_date
        )
        component_document_counts: Counter[str] = Counter()
        for row in canonical_rows:
            component_document_counts.update(row.canonical_components)
        provenance = {
            "source_organization": SOURCE_ORGANIZATION,
            "canonical_source_dataset": TSB_DATASET,
            "audited_source_dataset": MFR_DATASET,
            "requested_received_date_range": "2020-2024",
            "ingestion_timestamp_utc": _utc(clock()),
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "artifacts": manifest["artifacts"],
            "archive_members": {
                tsb_path.name: _archive_member(tsb_path, ".txt").filename,
                mfr_path.name: _archive_member(mfr_path, ".csv").filename,
            },
            "tsv_source_row_count": source_rows,
            "tsv_parse_success_count": source_rows,
            "tsv_parse_failure_count": 0,
            "unique_communication_count": len(canonical_rows),
            "unique_manufacturer_document_id_count": len(manufacturer_document_ids),
            "repeated_manufacturer_document_id_count": sum(
                count > 1 for document, count in manufacturer_document_ids.items() if document
            ),
            "applicability_count": applicability_count,
            "unknown_model_year_applicability_count": sum(
                year == "9999" for _, _, _, year in product_keys
            ),
            "model_year_range": [min(valid_years), max(valid_years)],
            "date_added_range": [added_dates[0], added_dates[-1]],
            "communication_date_range": [communication_dates[0], communication_dates[-1]],
            "unique_make_count": len({make for _, make, _, _ in product_keys}),
            "unique_make_model_count": len({(make, model) for _, make, model, _ in product_keys}),
            "communication_type_source_row_counts": dict(sorted(type_counts.items())),
            "communication_type_document_counts": dict(sorted(types_by_communication.items())),
            "invalid_date_counts": dict(sorted(invalid_date_counts.items())),
            "valid_communication_date_document_count": sum(
                row.communication_date is not None for row in canonical_rows
            ),
            "summary_document_count": sum(bool(row.summary) for row in canonical_rows),
            "nhtsa_component_document_count": sum(
                bool(row.nhtsa_components) for row in canonical_rows
            ),
            "canonical_component_document_counts": dict(
                sorted(component_document_counts.items())
            ),
            "manufacturer_component_document_count": sum(
                bool(row.manufacturer_component_systems or row.manufacturer_component_subsystems)
                for row in canonical_rows
            ),
            "campaign_or_software_document_count": sum(
                row.manufacturer_campaign_or_software_id is not None for row in canonical_rows
            ),
            "replacement_document_reference_count": sum(
                row.replacement_document_id is not None for row in canonical_rows
            ),
            "compact_view_audit": audit,
            "processing_complete": True,
            "communication_schema": list(COMMUNICATION_SCHEMA),
            "applicability_schema": list(APPLICABILITY_SCHEMA),
            "communications_sha256": sha256_file(communications_path),
            "applicability_sha256": sha256_file(applicability_path),
        }
        _write_json(provenance_path, provenance)
        return provenance
    except Exception:
        if "connection" in locals():
            with suppress(sqlite3.Error):
                connection.close()
        database_path.unlink(missing_ok=True)
        for target in targets:
            target.unlink(missing_ok=True)
        raise


def _upsert_applicability(
    connection: sqlite3.Connection, rows: Sequence[tuple[str, str, str, str]]
) -> None:
    connection.executemany(
        "INSERT INTO applicability VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(nhtsa_id, make, model, year) DO UPDATE SET source_rows=source_rows+1",
        rows,
    )


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ManufacturerCommunicationError(f"{path} line {number} is not an object")
                yield cast(dict[str, Any], value)
    except (OSError, json.JSONDecodeError) as error:
        raise ManufacturerCommunicationError(f"cannot read JSON Lines: {path}") from error


@dataclass(slots=True)
class _CohortAccumulator:
    communication_ids: set[str] = field(default_factory=set)
    applicability_ids: set[str] = field(default_factory=set)
    document_ids: set[str] = field(default_factory=set)
    types: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    components: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    dates: list[str] = field(default_factory=list)
    manufacturer_component_ids: set[str] = field(default_factory=set)
    summary_ids: set[str] = field(default_factory=set)
    source_expanded_row_count: int = 0


def match_cohorts(
    complaint_cohorts_path: Path,
    communications_path: Path,
    applicability_path: Path,
    production_matches_path: Path,
    applicability_matches_path: Path,
    cohort_evidence_path: Path,
    unmatched_path: Path,
    cross_source_path: Path,
    provenance_path: Path,
) -> dict[str, Any]:
    """Match application rows and produce cohort and cross-source coverage diagnostics."""
    targets = (
        applicability_matches_path,
        cohort_evidence_path,
        unmatched_path,
        cross_source_path,
        provenance_path,
    )
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite match output: {existing}")
    complaints = tuple(_iter_jsonl(complaint_cohorts_path))
    complaint_index: defaultdict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in complaints:
        complaint_index[
            (row["normalized_make"], row["normalized_model"], row["model_year"])
        ].append(row)
    communications = {row["communication_id"]: row for row in _iter_jsonl(communications_path)}
    accumulators: defaultdict[str, _CohortAccumulator] = defaultdict(_CohortAccumulator)
    match_statuses: Counter[str] = Counter()
    matched_communication_ids: set[str] = set()
    matched_document_ids: set[str] = set()
    matched_applicability_count = 0
    applicability_matches_path.parent.mkdir(parents=True, exist_ok=True)
    match_output = applicability_matches_path.open("x", encoding="utf-8", newline="\n")
    valid_source_makes: set[str] = set()
    valid_source_models: set[tuple[str, str]] = set()
    valid_source_years: set[int] = set()
    for application in _iter_jsonl(applicability_path):
        status = CommunicationMatchStatus.INSUFFICIENT_VEHICLE_IDENTITY
        candidates: list[dict[str, Any]] = []
        method: str | None = None
        year = application["model_year"]
        if year is not None:
            key = (
                application["normalized_make"],
                application["normalized_model"],
                year,
            )
            valid_source_makes.add(key[0])
            valid_source_models.add((key[0], key[1]))
            valid_source_years.add(year)
            candidates = complaint_index.get(key, [])
            method = "exact_normalized" if candidates else None
            if not candidates:
                alias = EXPLICIT_ALIASES.get((key[0], key[1]))
                if alias:
                    candidates = complaint_index.get((*alias, year), [])
                    method = "explicit_alias" if candidates else None
            if len(candidates) == 1:
                status = (
                    CommunicationMatchStatus.EXPLICIT_ALIAS
                    if method == "explicit_alias"
                    else CommunicationMatchStatus.EXACT
                )
            elif len(candidates) > 1:
                status = CommunicationMatchStatus.AMBIGUOUS
            else:
                status = CommunicationMatchStatus.NO_MATCH
        cohort_id = candidates[0]["broad_vehicle_id"] if len(candidates) == 1 else None
        match_statuses[status.value] += 1
        if cohort_id is not None:
            matched_applicability_count += 1
            communication = communications[application["communication_id"]]
            matched_communication_ids.add(application["communication_id"])
            matched_document_ids.add(communication["manufacturer_document_id"])
            aggregate = accumulators[cohort_id]
            aggregate.communication_ids.add(application["communication_id"])
            aggregate.applicability_ids.add(application["applicability_id"])
            aggregate.document_ids.add(communication["manufacturer_document_id"])
            aggregate.types[communication["communication_type"]].add(
                application["communication_id"]
            )
            for component in communication["canonical_components"]:
                aggregate.components[component].add(application["communication_id"])
            if communication["communication_date"] is not None:
                aggregate.dates.append(communication["communication_date"])
            if communication["manufacturer_component_systems"] or communication[
                "manufacturer_component_subsystems"
            ]:
                aggregate.manufacturer_component_ids.add(application["communication_id"])
            if communication["summary"]:
                aggregate.summary_ids.add(application["communication_id"])
            aggregate.source_expanded_row_count += application["source_expanded_row_count"]
        match_row = {
            **application,
            "complaint_match_status": status.value,
            "match_method": method,
            "matched_broad_vehicle_id": cohort_id,
            "ambiguity_candidate_count": len(candidates) if len(candidates) > 1 else 0,
        }
        if tuple(match_row) != APPLICABILITY_MATCH_SCHEMA:
            raise AssertionError("applicability match schema construction drifted")
        match_output.write(
            json.dumps(match_row, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    match_output.close()

    unmatched_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    communication_counts: list[int] = []
    for complaint in complaints:
        cohort_acc = accumulators.get(complaint["broad_vehicle_id"])
        if cohort_acc:
            status = CommunicationMatchStatus.EXACT
            communication_count = len(cohort_acc.communication_ids)
            communication_counts.append(communication_count)
            dates = sorted(cohort_acc.dates)
        else:
            status = CommunicationMatchStatus.NO_MATCH
            communication_count = 0
            dates = []
        evidence_row: dict[str, Any] = {
            "broad_vehicle_id": complaint["broad_vehicle_id"],
            "normalized_make": complaint["normalized_make"],
            "normalized_model": complaint["normalized_model"],
            "model_year": complaint["model_year"],
            "complaint_event_count": complaint["complaint_event_count"],
            "manufacturer_communication_match_status": status.value,
            "match_method": "exact_normalized" if cohort_acc else None,
            "unique_communication_count": communication_count,
            "communication_applicability_row_count": (
                len(cohort_acc.applicability_ids) if cohort_acc else 0
            ),
            "source_expanded_row_count": (
                cohort_acc.source_expanded_row_count if cohort_acc else 0
            ),
            "unique_manufacturer_document_id_count": (
                len(cohort_acc.document_ids) if cohort_acc else 0
            ),
            "first_communication_date": dates[0] if dates else None,
            "last_communication_date": dates[-1] if dates else None,
            "summary_observed_communication_count": (
                len(cohort_acc.summary_ids) if cohort_acc else 0
            ),
            "manufacturer_component_observed_communication_count": (
                len(cohort_acc.manufacturer_component_ids) if cohort_acc else 0
            ),
        }
        evidence_row.update(
            {
                f"communication_type_{item.value.lower()}_count": (
                    len(cohort_acc.types[item.value]) if cohort_acc else 0
                )
                for item in CommunicationType
            }
        )
        evidence_row.update(
            {
                f"component_{item.value}_communication_count": (
                    len(cohort_acc.components[item.value]) if cohort_acc else 0
                )
                for item in Component
            }
        )
        if tuple(evidence_row) != COHORT_EVIDENCE_SCHEMA:
            raise AssertionError("cohort evidence schema construction drifted")
        evidence_rows.append(evidence_row)
        if not cohort_acc:
            mismatch_key = (complaint["normalized_make"], complaint["normalized_model"])
            if mismatch_key[0] not in valid_source_makes:
                mismatch = "make_absent_from_vehicle_applicability"
            elif mismatch_key not in valid_source_models:
                mismatch = "model_absent_for_make"
            elif complaint["model_year"] not in valid_source_years:
                mismatch = "model_year_absent_from_vehicle_applicability"
            else:
                mismatch = "make_model_year_combination_absent"
            unmatched_rows.append({**evidence_row, "mismatch_class": mismatch})
    _write_jsonl(cohort_evidence_path, evidence_rows)
    _write_jsonl(unmatched_path, unmatched_rows)

    production = {
        row["broad_vehicle_id"]: row["production_count"] is not None
        for row in _iter_jsonl(production_matches_path)
    }
    cross_counts: Counter[str] = Counter()
    cross_events: Counter[str] = Counter()
    cross_rows = []
    for row in evidence_rows:
        has_production = production[row["broad_vehicle_id"]]
        has_communication = row["unique_communication_count"] > 0
        if has_production and has_communication:
            category = "complaint_production_manufacturer_communications"
        elif has_production:
            category = "complaint_production"
        elif has_communication:
            category = "complaint_manufacturer_communications"
        else:
            category = "complaint_only"
        cross_counts[category] += 1
        cross_events[category] += row["complaint_event_count"]
        cross_row = {
                "broad_vehicle_id": row["broad_vehicle_id"],
                "normalized_make": row["normalized_make"],
                "normalized_model": row["normalized_model"],
                "model_year": row["model_year"],
                "complaint_event_count": row["complaint_event_count"],
                "has_production": has_production,
                "has_manufacturer_communications": has_communication,
                "coverage_category": category,
            }
        if tuple(cross_row) != CROSS_SOURCE_SCHEMA:
            raise AssertionError("cross-source schema construction drifted")
        cross_rows.append(cross_row)
    _write_jsonl(cross_source_path, cross_rows)
    total_events = sum(row["complaint_event_count"] for row in complaints)
    mismatch_counts = Counter(row["mismatch_class"] for row in unmatched_rows)
    cohort_status_counts = Counter(
        row["manufacturer_communication_match_status"] for row in evidence_rows
    )
    summary = {
        "matching_version": MATCHING_VERSION,
        "aliases": dict(EXPLICIT_ALIASES),
        "applicability_match_schema": list(APPLICABILITY_MATCH_SCHEMA),
        "cohort_evidence_schema": list(COHORT_EVIDENCE_SCHEMA),
        "cross_source_schema": list(CROSS_SOURCE_SCHEMA),
        "complaint_cohort_count": len(complaints),
        "complaint_event_count": total_events,
        "matched_cohort_count": len(communication_counts),
        "matched_cohort_percentage": 100 * len(communication_counts) / len(complaints),
        "matched_event_count": sum(
            row["complaint_event_count"]
            for row in evidence_rows
            if row["unique_communication_count"] > 0
        ),
        "matched_event_percentage": 100
        * sum(
            row["complaint_event_count"]
            for row in evidence_rows
            if row["unique_communication_count"] > 0
        )
        / total_events,
        "matched_unique_communication_count": len(matched_communication_ids),
        "matched_unique_manufacturer_document_id_count": len(matched_document_ids),
        "matched_applicability_count": matched_applicability_count,
        "cohort_match_status_counts": dict(sorted(cohort_status_counts.items())),
        "applicability_match_status_counts": dict(sorted(match_statuses.items())),
        "communications_per_matched_cohort": _distribution(communication_counts),
        "mismatch_class_counts": dict(sorted(mismatch_counts.items())),
        "top_unmatched_makes": Counter(
            row["normalized_make"] for row in unmatched_rows
        ).most_common(20),
        "top_unmatched_make_models": Counter(
            f"{row['normalized_make']} / {row['normalized_model']}" for row in unmatched_rows
        ).most_common(20),
        "coverage_by_model_year": _coverage_by(evidence_rows, "model_year"),
        "coverage_by_make": _coverage_by(evidence_rows, "normalized_make"),
        "cross_source_cohort_counts": dict(sorted(cross_counts.items())),
        "cross_source_event_counts": dict(sorted(cross_events.items())),
        "cross_source_cohort_percentages": {
            key: 100 * value / len(complaints) for key, value in sorted(cross_counts.items())
        },
        "cross_source_event_percentages": {
            key: 100 * value / total_events for key, value in sorted(cross_events.items())
        },
        "input_checksums": {
            "complaints": sha256_file(complaint_cohorts_path),
            "communications": sha256_file(communications_path),
            "applicability": sha256_file(applicability_path),
            "production_matches": sha256_file(production_matches_path),
        },
        "output_checksums": {
            "applicability_matches": sha256_file(applicability_matches_path),
            "cohort_evidence": sha256_file(cohort_evidence_path),
            "unmatched": sha256_file(unmatched_path),
            "cross_source": sha256_file(cross_source_path),
        },
    }
    _write_json(provenance_path, summary)
    return summary


def _coverage_by(rows: Sequence[dict[str, Any]], field_name: str) -> dict[str, dict[str, Any]]:
    groups: defaultdict[object, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[field_name]].append(row)
    result = {}
    for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
        matched = [row for row in group if row["unique_communication_count"] > 0]
        events = sum(row["complaint_event_count"] for row in group)
        matched_events = sum(row["complaint_event_count"] for row in matched)
        result[str(key)] = {
            "cohorts": len(group),
            "matched_cohorts": len(matched),
            "matched_cohort_percentage": 100 * len(matched) / len(group),
            "events": events,
            "matched_events": matched_events,
            "matched_event_percentage": 100 * matched_events / events,
        }
    return result


def _distribution(values: Sequence[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "median": None, "p75": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": _percentile(ordered, 0.5),
        "p75": _percentile(ordered, 0.75),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def _percentile(values: Sequence[int], quantile: float) -> float:
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
