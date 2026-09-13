"""Source-faithful adapter for NHTSA's official flat recall corpus."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

from howreliable.data.exposure.nhtsa_ewr import normalize_identity
from howreliable.data.ingestion.nhtsa_complaints import (
    ArtifactExistsError,
    download_artifact,
    sha256_file,
)
from howreliable.data.mapping.nhtsa_reliability import map_nhtsa_component
from howreliable.domain import Component

SOURCE_ORGANIZATION: Final = "National Highway Traffic Safety Administration (NHTSA)"
SOURCE_DATASET: Final = "NHTSA Recalls flat file"
PRE_URL: Final = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_PRE_2010.zip"
POST_URL: Final = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip"
DICTIONARY_URL: Final = "https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt"
ADAPTER_VERSION: Final = "nhtsa-recalls-1.0"
MATCHING_VERSION: Final = "nhtsa-recall-matching-1.0"
SOURCE_SCHEMA_VERSION: Final = "RCL.txt May 2025 (29 fields)"
SOURCE_ENCODING: Final = "latin-1"

RCL_COLUMNS: Final = (
    "RECORD_ID",
    "CAMPNO",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "MFGCAMPNO",
    "COMPNAME",
    "MFGNAME",
    "BGMAN",
    "ENDMAN",
    "RCLTYPECD",
    "POTAFF",
    "ODATE",
    "INFLUENCED_BY",
    "MFGTXT",
    "RCDATE",
    "DATEA",
    "RPNO",
    "FMVSS",
    "DESC_DEFECT",
    "CONEQUENCE_DEFECT",
    "CORRECTIVE_ACTION",
    "NOTES",
    "RCL_CMPT_ID",
    "MFR_COMP_NAME",
    "MFR_COMP_DESC",
    "MFR_COMP_PTNO",
    "DO_NOT_DRIVE",
    "PARK_OUTSIDE",
)
ID_PATTERN: Final = re.compile(r"^[0-9]{1,9}$")
CAMPAIGN_PATTERN: Final = re.compile(r"^[0-9]{2}[A-Z0-9]{1,10}$")
DATE_PATTERN: Final = re.compile(r"^[0-9]{8}$")
YEAR_PATTERN: Final = re.compile(r"^[0-9]{4}$")
EXPLICIT_ALIASES: Final[Mapping[tuple[str, str], tuple[str, str]]] = {}


class RecallError(Exception):
    """Raised for malformed recall data or failed processing invariants."""


class RecallType(StrEnum):
    VEHICLE = "VEHICLE"
    EQUIPMENT = "EQUIPMENT"
    CHILD_RESTRAINT = "CHILD_RESTRAINT"
    TIRE = "TIRE"
    OTHER = "OTHER"


TYPE_MAP: Final = {
    "V": RecallType.VEHICLE,
    "E": RecallType.EQUIPMENT,
    "C": RecallType.CHILD_RESTRAINT,
    "T": RecallType.TIRE,
}


class RecallMatchStatus(StrEnum):
    EXACT = "EXACT"
    EXPLICIT_ALIAS = "EXPLICIT_ALIAS"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    INSUFFICIENT_VEHICLE_IDENTITY = "INSUFFICIENT_VEHICLE_IDENTITY"


@dataclass(frozen=True, slots=True)
class NhtsaRecallRecord:
    """One immutable, source-specific 29-field flat-file row."""

    values: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(RCL_COLUMNS):
            raise RecallError(f"expected {len(RCL_COLUMNS)} fields, received {len(self.values)}")

    def field(self, name: str) -> str | None:
        try:
            return self.values[RCL_COLUMNS.index(name)]
        except ValueError as error:
            raise KeyError(name) from error

    def as_source_dict(self) -> dict[str, str | None]:
        return dict(zip(RCL_COLUMNS, self.values, strict=True))

    @property
    def source_record_id(self) -> str:
        return f"nhtsa_recall_row_{cast(str, self.field('RECORD_ID'))}"


@dataclass(frozen=True, slots=True)
class CanonicalRecallCampaign:
    """One official NHTSA campaign, independent of vehicle applicability."""

    campaign_id: str
    source_organization: str
    source_dataset: str
    nhtsa_campaign_number: str
    manufacturer_campaign_numbers: tuple[str, ...]
    filing_manufacturers: tuple[str, ...]
    recalled_manufacturers: tuple[str, ...]
    original_recall_type_codes: tuple[str, ...]
    recall_types: tuple[str, ...]
    report_received_dates: tuple[str, ...]
    owner_notification_dates: tuple[str, ...]
    record_creation_dates: tuple[str, ...]
    initiators: tuple[str, ...]
    regulation_part_numbers: tuple[str, ...]
    fmvss_numbers: tuple[str, ...]
    potential_units_affected: int | None
    potential_units_affected_values: tuple[int, ...]
    original_components: tuple[str, ...]
    canonical_components: tuple[str, ...]
    defect_descriptions: tuple[str, ...]
    consequence_descriptions: tuple[str, ...]
    corrective_actions: tuple[str, ...]
    notes: tuple[str, ...]
    do_not_drive_values: tuple[str, ...]
    park_outside_values: tuple[str, ...]
    quality_flags: tuple[str, ...]
    source_artifact_filenames: tuple[str, ...]
    source_row_count: int


@dataclass(frozen=True, slots=True)
class RecallApplicability:
    """One official vehicle recall row, retaining its source-row identity."""

    applicability_id: str
    campaign_id: str
    nhtsa_campaign_number: str
    source_record_id: str
    source_record_number: str
    original_make: str
    original_model: str
    original_model_year: str
    normalized_make: str
    normalized_model: str
    model_year: int | None
    original_product_type_code: str
    product_type: str
    original_component: str | None
    canonical_component: str
    manufacturing_begin_date_raw: str | None
    manufacturing_begin_date: str | None
    manufacturing_end_date_raw: str | None
    manufacturing_end_date: str | None
    recalled_component_id: str | None
    manufacturer_component_name: str | None
    manufacturer_component_description: str | None
    manufacturer_component_part_number: str | None
    source_artifact_filename: str


CAMPAIGN_SCHEMA: Final = tuple(CanonicalRecallCampaign.__dataclass_fields__)
APPLICABILITY_SCHEMA: Final = tuple(RecallApplicability.__dataclass_fields__)
APPLICABILITY_MATCH_SCHEMA: Final = (
    *APPLICABILITY_SCHEMA,
    "complaint_match_status",
    "match_method",
    "matched_broad_vehicle_id",
    "ambiguity_candidate_count",
)
COHORT_RECALL_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "recall_match_status",
    "match_method",
    "unique_recall_campaign_count",
    "recall_applicability_row_count",
    "first_recall_date",
    "last_recall_date",
    "noncompliance_campaign_count",
    "known_affected_population_campaign_count",
    "remedy_observed_campaign_count",
    *(f"recall_type_{item.value.lower()}_campaign_count" for item in RecallType),
    *(f"component_{item.value}_recall_campaign_count" for item in Component),
)
CROSS_SOURCE_SCHEMA: Final = (
    "broad_vehicle_id",
    "normalized_make",
    "normalized_model",
    "model_year",
    "complaint_event_count",
    "has_production",
    "has_manufacturer_communications",
    "has_recalls",
    "coverage_category",
)


def _required(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise RecallError(f"{name} must not be blank")
    return value


def parse_recall_row(row: Sequence[str]) -> NhtsaRecallRecord:
    """Validate field count/identity while preserving raw text and explicit nulls."""
    if len(row) != len(RCL_COLUMNS):
        raise RecallError(f"expected {len(RCL_COLUMNS)} fields, received {len(row)}")
    values = tuple(value if value != "" else None for value in row)
    for index in (0, 1, 2, 3, 4, 10):
        _required(values[index], RCL_COLUMNS[index])
    record_id = cast(str, values[0])
    campaign = cast(str, values[1])
    if ID_PATTERN.fullmatch(record_id) is None:
        raise RecallError(f"invalid RECORD_ID: {record_id!r}")
    if CAMPAIGN_PATTERN.fullmatch(campaign) is None:
        raise RecallError(f"invalid CAMPNO: {campaign!r}")
    return NhtsaRecallRecord(values)


def _archive_member(path: Path) -> zipfile.ZipInfo:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            selected = [item for item in members if item.filename.lower().endswith(".txt")]
            if len(selected) != 1:
                raise RecallError(f"expected one TXT member in {path.name}")
            member_path = PurePosixPath(selected[0].filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RecallError(f"unsafe archive member: {member_path}")
            return selected[0]
    except (OSError, zipfile.BadZipFile) as error:
        raise RecallError(f"invalid ZIP artifact: {path}") from error


def iter_recall_records(path: Path) -> Iterator[NhtsaRecallRecord]:
    """Stream headerless Latin-1 TSV; quote characters have no structural meaning."""
    member = _archive_member(path)
    csv.field_size_limit(20_000_000)
    try:
        with zipfile.ZipFile(path) as archive, archive.open(member) as binary:
            source = io.TextIOWrapper(binary, encoding=SOURCE_ENCODING, newline="")
            reader = csv.reader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
            for number, row in enumerate(reader, 1):
                if not row:
                    continue
                try:
                    yield parse_recall_row(row)
                except RecallError as error:
                    raise RecallError(f"{path.name} row {number}: {error}") from error
    except (OSError, UnicodeError, csv.Error, zipfile.BadZipFile) as error:
        raise RecallError(f"cannot read recall artifact: {path}") from error


def _parse_date(value: str | None) -> str | None:
    if value is None or DATE_PATTERN.fullmatch(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _parse_year(value: str) -> int | None:
    if YEAR_PATTERN.fullmatch(value) is None or value == "9999":
        return None
    year = int(value)
    return year if 1886 <= year <= 2100 else None


def _integer(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    return int(value)


def _recall_type(code: str) -> RecallType:
    return TYPE_MAP.get(code, RecallType.OTHER)


def _applicability(record: NhtsaRecallRecord, artifact: str) -> RecallApplicability:
    fields = record.as_source_dict()
    campaign = cast(str, fields["CAMPNO"])
    make = cast(str, fields["MAKETXT"])
    model = cast(str, fields["MODELTXT"])
    year_text = cast(str, fields["YEARTXT"])
    type_code = cast(str, fields["RCLTYPECD"])
    component = fields["COMPNAME"]
    identity = json.dumps(
        [campaign, cast(str, fields["RECORD_ID"]), make, model, year_text],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return RecallApplicability(
        applicability_id=f"nhtsa_recall_app_{hashlib.sha256(identity.encode()).hexdigest()}",
        campaign_id=f"nhtsa_recall_{campaign}",
        nhtsa_campaign_number=campaign,
        source_record_id=record.source_record_id,
        source_record_number=cast(str, fields["RECORD_ID"]),
        original_make=make,
        original_model=model,
        original_model_year=year_text,
        normalized_make=normalize_identity(make),
        normalized_model=normalize_identity(model),
        model_year=_parse_year(year_text),
        original_product_type_code=type_code,
        product_type=_recall_type(type_code).value,
        original_component=component,
        canonical_component=(
            map_nhtsa_component(component).value if component else Component.UNKNOWN.value
        ),
        manufacturing_begin_date_raw=fields["BGMAN"],
        manufacturing_begin_date=_parse_date(fields["BGMAN"]),
        manufacturing_end_date_raw=fields["ENDMAN"],
        manufacturing_end_date=_parse_date(fields["ENDMAN"]),
        recalled_component_id=fields["RCL_CMPT_ID"],
        manufacturer_component_name=fields["MFR_COMP_NAME"],
        manufacturer_component_description=fields["MFR_COMP_DESC"],
        manufacturer_component_part_number=fields["MFR_COMP_PTNO"],
        source_artifact_filename=artifact,
    )


@dataclass(slots=True)
class _CampaignAccumulator:
    values: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    artifacts: set[str] = field(default_factory=set)
    source_rows: int = 0


def _sorted_values(acc: _CampaignAccumulator, field_name: str) -> tuple[str, ...]:
    return tuple(sorted(acc.values[field_name]))


def _campaign(number: str, acc: _CampaignAccumulator) -> CanonicalRecallCampaign:
    population_values = tuple(
        sorted(int(value) for value in acc.values["POTAFF"] if value.isdecimal())
    )
    flags: list[str] = []
    if len(population_values) > 1:
        flags.append("multiple_potential_units_affected_values")
    date_fields = ("RCDATE", "ODATE", "DATEA")
    if any(any(_parse_date(value) is None for value in acc.values[name]) for name in date_fields):
        flags.append("unparseable_date_value")
    components = _sorted_values(acc, "COMPNAME")
    return CanonicalRecallCampaign(
        campaign_id=f"nhtsa_recall_{number}",
        source_organization=SOURCE_ORGANIZATION,
        source_dataset=SOURCE_DATASET,
        nhtsa_campaign_number=number,
        manufacturer_campaign_numbers=_sorted_values(acc, "MFGCAMPNO"),
        filing_manufacturers=_sorted_values(acc, "MFGNAME"),
        recalled_manufacturers=_sorted_values(acc, "MFGTXT"),
        original_recall_type_codes=_sorted_values(acc, "RCLTYPECD"),
        recall_types=tuple(
            sorted({_recall_type(value).value for value in acc.values["RCLTYPECD"]})
        ),
        report_received_dates=tuple(
            sorted(filter(None, (_parse_date(v) for v in acc.values["RCDATE"])))
        ),
        owner_notification_dates=tuple(
            sorted(filter(None, (_parse_date(v) for v in acc.values["ODATE"])))
        ),
        record_creation_dates=tuple(
            sorted(filter(None, (_parse_date(v) for v in acc.values["DATEA"])))
        ),
        initiators=_sorted_values(acc, "INFLUENCED_BY"),
        regulation_part_numbers=_sorted_values(acc, "RPNO"),
        fmvss_numbers=_sorted_values(acc, "FMVSS"),
        potential_units_affected=(population_values[0] if len(population_values) == 1 else None),
        potential_units_affected_values=population_values,
        original_components=components,
        canonical_components=tuple(sorted({map_nhtsa_component(v).value for v in components})),
        defect_descriptions=_sorted_values(acc, "DESC_DEFECT"),
        consequence_descriptions=_sorted_values(acc, "CONEQUENCE_DEFECT"),
        corrective_actions=_sorted_values(acc, "CORRECTIVE_ACTION"),
        notes=_sorted_values(acc, "NOTES"),
        do_not_drive_values=_sorted_values(acc, "DO_NOT_DRIVE"),
        park_outside_values=_sorted_values(acc, "PARK_OUTSIDE"),
        quality_flags=tuple(flags),
        source_artifact_filenames=tuple(sorted(acc.artifacts)),
        source_row_count=acc.source_rows,
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


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise RecallError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def retrieve_artifacts(
    raw_directory: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> Path:
    """Download complete PRE/POST flat files and the dictionary immutably."""
    if raw_directory.exists():
        raise ArtifactExistsError(f"refusing to overwrite raw snapshot: {raw_directory}")
    raw_directory.mkdir(parents=True)
    artifacts = []
    for filename, url in (
        ("FLAT_RCL_PRE_2010.zip", PRE_URL),
        ("FLAT_RCL_POST_2010.zip", POST_URL),
        ("RCL.txt", DICTIONARY_URL),
    ):
        receipt = download_artifact(url, raw_directory / filename, clock=clock, timeout=180)
        artifacts.append(
            {
                "filename": filename,
                "official_source_url": receipt.source_url,
                "retrieval_timestamp_utc": receipt.retrieval_timestamp_utc,
                "sha256": receipt.sha256,
            }
        )
    manifest = raw_directory / "acquisition-manifest.json"
    _write_json(
        manifest,
        {
            "source_organization": SOURCE_ORGANIZATION,
            "source_scope": "complete PRE_2010 + POST_2010 flat corpus",
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "artifacts": artifacts,
        },
    )
    return manifest


def create_manifest(
    raw_directory: Path,
    retrieved_at: datetime,
) -> Path:
    """Create a checksummed manifest for already acquired official named artifacts."""
    path = raw_directory / "acquisition-manifest.json"
    if path.exists():
        raise ArtifactExistsError(f"refusing to overwrite raw manifest: {path}")
    items = []
    for filename, url in (
        ("FLAT_RCL_PRE_2010.zip", PRE_URL),
        ("FLAT_RCL_POST_2010.zip", POST_URL),
        ("RCL.txt", DICTIONARY_URL),
    ):
        artifact = raw_directory / filename
        if not artifact.is_file():
            raise RecallError(f"missing official artifact: {artifact}")
        items.append(
            {
                "filename": filename,
                "official_source_url": url,
                "retrieval_timestamp_utc": _utc(retrieved_at),
                "sha256": sha256_file(artifact),
            }
        )
    _write_json(
        path,
        {
            "source_organization": SOURCE_ORGANIZATION,
            "source_scope": "complete PRE_2010 + POST_2010 flat corpus",
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "artifacts": items,
        },
    )
    return path


def _check_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecallError("cannot read acquisition manifest") from error
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        raise RecallError("invalid acquisition manifest schema")
    result = cast(dict[str, Any], value)
    for item in result["artifacts"]:
        artifact = path.parent / item["filename"]
        if sha256_file(artifact) != item["sha256"]:
            raise RecallError(f"checksum mismatch: {artifact.name}")
    return result


def ingest_archives(
    manifest_path: Path,
    campaigns_path: Path,
    applicability_path: Path,
    provenance_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Canonicalize complete recall campaign and vehicle-application records."""
    targets = (campaigns_path, applicability_path, provenance_path)
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite recall output: {existing}")
    manifest = _check_manifest(manifest_path)
    accumulators: dict[str, _CampaignAccumulator] = {}
    applications: list[RecallApplicability] = []
    source_counts: Counter[str] = Counter()
    years: list[int] = []
    dates: list[str] = []
    try:
        for filename in ("FLAT_RCL_PRE_2010.zip", "FLAT_RCL_POST_2010.zip"):
            artifact = manifest_path.parent / filename
            for record in iter_recall_records(artifact):
                fields = record.as_source_dict()
                campaign_number = cast(str, fields["CAMPNO"])
                acc = accumulators.setdefault(campaign_number, _CampaignAccumulator())
                acc.source_rows += 1
                acc.artifacts.add(filename)
                for name, value in fields.items():
                    if value is not None and name != "RECORD_ID":
                        acc.values[name].add(value)
                recall_type = _recall_type(cast(str, fields["RCLTYPECD"]))
                source_counts[f"source_type_{recall_type.value.lower()}_row_count"] += 1
                parsed_date = _parse_date(fields["RCDATE"])
                if parsed_date:
                    dates.append(parsed_date)
                if recall_type is RecallType.VEHICLE:
                    application = _applicability(record, filename)
                    applications.append(application)
                    if application.model_year is not None:
                        years.append(application.model_year)
        campaigns = [_campaign(number, accumulators[number]) for number in sorted(accumulators)]
        applications.sort(
            key=lambda row: (
                row.normalized_make,
                row.normalized_model,
                row.model_year or 9999,
                row.nhtsa_campaign_number,
                int(row.source_record_number),
            )
        )
        _write_jsonl(campaigns_path, (asdict(row) for row in campaigns))
        _write_jsonl(applicability_path, (asdict(row) for row in applications))
        population_known = sum(row.potential_units_affected is not None for row in campaigns)
        population_conflicts = sum(
            "multiple_potential_units_affected_values" in row.quality_flags for row in campaigns
        )
        type_campaigns: Counter[str] = Counter()
        component_campaigns: Counter[str] = Counter()
        for campaign in campaigns:
            type_campaigns.update(campaign.recall_types)
            component_campaigns.update(campaign.canonical_components)
        source_rows_per_campaign = [row.source_row_count for row in campaigns]
        summary: dict[str, Any] = {
            "source_organization": SOURCE_ORGANIZATION,
            "source_dataset": SOURCE_DATASET,
            "source_scope": "complete PRE_2010 + POST_2010 flat corpus",
            "source_completeness": "official daily flat corpus across both published partitions",
            "ingestion_timestamp_utc": _utc(clock()),
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "source_delimiter": "tab",
            "source_encoding": SOURCE_ENCODING,
            "source_has_header": False,
            "artifacts": manifest["artifacts"],
            "archive_members": {
                name: _archive_member(manifest_path.parent / name).filename
                for name in ("FLAT_RCL_PRE_2010.zip", "FLAT_RCL_POST_2010.zip")
            },
            "source_row_count": sum(acc.source_rows for acc in accumulators.values()),
            "parse_success_count": sum(acc.source_rows for acc in accumulators.values()),
            "parse_failure_count": 0,
            "unique_campaign_count": len(campaigns),
            "unique_manufacturer_campaign_number_count": len(
                {value for row in campaigns for value in row.manufacturer_campaign_numbers}
            ),
            "manufacturer_campaign_numbers_reused_across_nhtsa_campaigns": sum(
                count > 1
                for count in Counter(
                    value for row in campaigns for value in row.manufacturer_campaign_numbers
                ).values()
            ),
            "vehicle_applicability_count": len(applications),
            "vehicle_applicability_unknown_year_count": sum(
                row.model_year is None for row in applications
            ),
            "nonvehicle_source_row_count": sum(
                value
                for key, value in source_counts.items()
                if key != "source_type_vehicle_row_count"
            ),
            **dict(sorted(source_counts.items())),
            "recall_type_campaign_counts": dict(sorted(type_campaigns.items())),
            "canonical_component_campaign_counts": dict(sorted(component_campaigns.items())),
            "campaigns_with_known_affected_population": population_known,
            "campaigns_with_conflicting_affected_population_values": population_conflicts,
            "campaigns_with_multiple_source_rows": sum(
                row.source_row_count > 1 for row in campaigns
            ),
            "maximum_source_rows_for_one_campaign": max(source_rows_per_campaign),
            "campaigns_with_multiple_original_components": sum(
                len(row.original_components) > 1 for row in campaigns
            ),
            "campaigns_with_multiple_defect_descriptions": sum(
                len(row.defect_descriptions) > 1 for row in campaigns
            ),
            "campaigns_with_multiple_report_received_dates": sum(
                len(row.report_received_dates) > 1 for row in campaigns
            ),
            "vehicle_unique_make_count": len({row.normalized_make for row in applications}),
            "vehicle_unique_make_model_count": len(
                {(row.normalized_make, row.normalized_model) for row in applications}
            ),
            "campaigns_with_defect_description": sum(
                bool(row.defect_descriptions) for row in campaigns
            ),
            "campaigns_with_consequence_description": sum(
                bool(row.consequence_descriptions) for row in campaigns
            ),
            "campaigns_with_corrective_action": sum(
                bool(row.corrective_actions) for row in campaigns
            ),
            "recall_date_range": [min(dates), max(dates)] if dates else [None, None],
            "vehicle_model_year_range": [min(years), max(years)] if years else [None, None],
            "campaign_schema": list(CAMPAIGN_SCHEMA),
            "applicability_schema": list(APPLICABILITY_SCHEMA),
            "campaigns_sha256": sha256_file(campaigns_path),
            "applicability_sha256": sha256_file(applicability_path),
            "processing_complete": True,
        }
        _write_json(provenance_path, summary)
        return summary
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RecallError(f"{path} line {number} is not an object")
                yield cast(dict[str, Any], value)
    except (OSError, json.JSONDecodeError) as error:
        raise RecallError(f"cannot read JSON Lines: {path}") from error


@dataclass(slots=True)
class _CohortAccumulator:
    campaigns: set[str] = field(default_factory=set)
    applications: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    types: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    components: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    noncompliance: set[str] = field(default_factory=set)
    population: set[str] = field(default_factory=set)
    remedy: set[str] = field(default_factory=set)


def _percentile(values: Sequence[int], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Sequence[int]) -> dict[str, float | int]:
    return {
        "minimum": min(values) if values else 0,
        "median": _percentile(values, 0.5),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
        "maximum": max(values) if values else 0,
        "one_recall_cohort_count": sum(value == 1 for value in values),
    }


def match_cohorts(
    complaint_cohorts_path: Path,
    campaigns_path: Path,
    applicability_path: Path,
    production_matches_path: Path,
    communication_evidence_path: Path,
    applicability_matches_path: Path,
    cohort_recall_path: Path,
    unmatched_path: Path,
    cross_source_path: Path,
    provenance_path: Path,
) -> dict[str, Any]:
    """Match vehicle recall applications and emit descriptive four-source coverage."""
    targets = (
        applicability_matches_path,
        cohort_recall_path,
        unmatched_path,
        cross_source_path,
        provenance_path,
    )
    existing = next((path for path in targets if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite recall match output: {existing}")
    complaints = tuple(_iter_jsonl(complaint_cohorts_path))
    complaint_index: defaultdict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in complaints:
        complaint_index[
            (row["normalized_make"], row["normalized_model"], row["model_year"])
        ].append(row)
    campaigns = {row["campaign_id"]: row for row in _iter_jsonl(campaigns_path)}
    aggregates: defaultdict[str, _CohortAccumulator] = defaultdict(_CohortAccumulator)
    status_counts: Counter[str] = Counter()
    matched_campaigns: set[str] = set()
    matched_applications = 0
    source_makes: set[str] = set()
    source_models: set[tuple[str, str]] = set()
    source_years: set[int] = set()
    try:
        applicability_matches_path.parent.mkdir(parents=True, exist_ok=True)
        with applicability_matches_path.open("x", encoding="utf-8", newline="\n") as output:
            for application in _iter_jsonl(applicability_path):
                candidates: list[dict[str, Any]] = []
                method: str | None = None
                year = application["model_year"]
                if year is not None:
                    key = (application["normalized_make"], application["normalized_model"], year)
                    source_makes.add(key[0])
                    source_models.add((key[0], key[1]))
                    source_years.add(year)
                    candidates = complaint_index.get(key, [])
                    method = "exact_normalized" if candidates else None
                    if not candidates and (key[0], key[1]) in EXPLICIT_ALIASES:
                        alias = EXPLICIT_ALIASES[(key[0], key[1])]
                        candidates = complaint_index.get((*alias, year), [])
                        method = "explicit_alias" if candidates else None
                if year is None:
                    status = RecallMatchStatus.INSUFFICIENT_VEHICLE_IDENTITY
                elif len(candidates) == 1:
                    status = (
                        RecallMatchStatus.EXPLICIT_ALIAS
                        if method == "explicit_alias"
                        else RecallMatchStatus.EXACT
                    )
                elif len(candidates) > 1:
                    status = RecallMatchStatus.AMBIGUOUS
                else:
                    status = RecallMatchStatus.NO_MATCH
                cohort_id = candidates[0]["broad_vehicle_id"] if len(candidates) == 1 else None
                status_counts[status.value] += 1
                if cohort_id:
                    matched_applications += 1
                    campaign = campaigns[application["campaign_id"]]
                    campaign_id = application["campaign_id"]
                    matched_campaigns.add(campaign_id)
                    acc = aggregates[cohort_id]
                    acc.campaigns.add(campaign_id)
                    acc.applications.add(application["applicability_id"])
                    acc.dates.update(campaign["report_received_dates"])
                    for value in campaign["recall_types"]:
                        acc.types[value].add(campaign_id)
                    for value in campaign["canonical_components"]:
                        acc.components[value].add(campaign_id)
                    if campaign["fmvss_numbers"]:
                        acc.noncompliance.add(campaign_id)
                    if campaign["potential_units_affected"] is not None:
                        acc.population.add(campaign_id)
                    if campaign["corrective_actions"]:
                        acc.remedy.add(campaign_id)
                match_row = {
                    **application,
                    "complaint_match_status": status.value,
                    "match_method": method,
                    "matched_broad_vehicle_id": cohort_id,
                    "ambiguity_candidate_count": len(candidates) if len(candidates) > 1 else 0,
                }
                if tuple(match_row) != APPLICABILITY_MATCH_SCHEMA:
                    raise AssertionError("recall applicability match schema drifted")
                output.write(
                    json.dumps(match_row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
        evidence_rows: list[dict[str, Any]] = []
        unmatched_rows: list[dict[str, Any]] = []
        recall_counts: list[int] = []
        for complaint in complaints:
            cohort_acc = aggregates.get(complaint["broad_vehicle_id"])
            dates = sorted(cohort_acc.dates) if cohort_acc else []
            count = len(cohort_acc.campaigns) if cohort_acc else 0
            if count:
                recall_counts.append(count)
            evidence_row: dict[str, Any] = {
                "broad_vehicle_id": complaint["broad_vehicle_id"],
                "normalized_make": complaint["normalized_make"],
                "normalized_model": complaint["normalized_model"],
                "model_year": complaint["model_year"],
                "complaint_event_count": complaint["complaint_event_count"],
                "recall_match_status": RecallMatchStatus.EXACT.value
                if cohort_acc
                else RecallMatchStatus.NO_MATCH.value,
                "match_method": "exact_normalized" if cohort_acc else None,
                "unique_recall_campaign_count": count,
                "recall_applicability_row_count": (
                    len(cohort_acc.applications) if cohort_acc else 0
                ),
                "first_recall_date": dates[0] if dates else None,
                "last_recall_date": dates[-1] if dates else None,
                "noncompliance_campaign_count": (
                    len(cohort_acc.noncompliance) if cohort_acc else 0
                ),
                "known_affected_population_campaign_count": (
                    len(cohort_acc.population) if cohort_acc else 0
                ),
                "remedy_observed_campaign_count": (len(cohort_acc.remedy) if cohort_acc else 0),
            }
            evidence_row.update(
                {
                    f"recall_type_{item.value.lower()}_campaign_count": len(
                        cohort_acc.types[item.value]
                    )
                    if cohort_acc
                    else 0
                    for item in RecallType
                }
            )
            evidence_row.update(
                {
                    f"component_{item.value}_recall_campaign_count": len(
                        cohort_acc.components[item.value]
                    )
                    if cohort_acc
                    else 0
                    for item in Component
                }
            )
            if tuple(evidence_row) != COHORT_RECALL_SCHEMA:
                raise AssertionError("cohort recall schema drifted")
            evidence_rows.append(evidence_row)
            if not cohort_acc:
                model_key = (complaint["normalized_make"], complaint["normalized_model"])
                if model_key[0] not in source_makes:
                    mismatch = "make_absent_from_vehicle_applicability"
                elif model_key not in source_models:
                    mismatch = "model_absent_for_make"
                elif complaint["model_year"] not in source_years:
                    mismatch = "model_year_absent_from_vehicle_applicability"
                else:
                    mismatch = "make_model_year_combination_absent"
                unmatched_rows.append({**evidence_row, "mismatch_class": mismatch})
        _write_jsonl(cohort_recall_path, evidence_rows)
        _write_jsonl(unmatched_path, unmatched_rows)
        production = {
            row["broad_vehicle_id"]: row["production_count"] is not None
            for row in _iter_jsonl(production_matches_path)
        }
        communications = {
            row["broad_vehicle_id"]: row["unique_communication_count"] > 0
            for row in _iter_jsonl(communication_evidence_path)
        }
        cross_counts: Counter[str] = Counter()
        cross_events: Counter[str] = Counter()
        cross_rows = []
        for row in evidence_rows:
            p = production[row["broad_vehicle_id"]]
            c = communications[row["broad_vehicle_id"]]
            r = row["unique_recall_campaign_count"] > 0
            parts = (
                ["complaint"]
                + (["production"] if p else [])
                + (["manufacturer_communications"] if c else [])
                + (["recalls"] if r else [])
            )
            category = "complaint_only" if len(parts) == 1 else "_".join(parts)
            cross_counts[category] += 1
            cross_events[category] += row["complaint_event_count"]
            cross = {
                "broad_vehicle_id": row["broad_vehicle_id"],
                "normalized_make": row["normalized_make"],
                "normalized_model": row["normalized_model"],
                "model_year": row["model_year"],
                "complaint_event_count": row["complaint_event_count"],
                "has_production": p,
                "has_manufacturer_communications": c,
                "has_recalls": r,
                "coverage_category": category,
            }
            if tuple(cross) != CROSS_SOURCE_SCHEMA:
                raise AssertionError("cross-source schema drifted")
            cross_rows.append(cross)
        _write_jsonl(cross_source_path, cross_rows)
        total_events = sum(row["complaint_event_count"] for row in complaints)
        matched_events = sum(
            row["complaint_event_count"]
            for row in evidence_rows
            if row["unique_recall_campaign_count"] > 0
        )
        mismatch_counts = Counter(row["mismatch_class"] for row in unmatched_rows)
        by_year: defaultdict[int, list[int]] = defaultdict(lambda: [0, 0])
        by_make: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in evidence_rows:
            matched = int(row["unique_recall_campaign_count"] > 0)
            by_year[row["model_year"]][0] += matched
            by_year[row["model_year"]][1] += 1
            by_make[row["normalized_make"]][0] += matched
            by_make[row["normalized_make"]][1] += 1
        summary: dict[str, Any] = {
            "matching_version": MATCHING_VERSION,
            "aliases": dict(EXPLICIT_ALIASES),
            "applicability_match_schema": list(APPLICABILITY_MATCH_SCHEMA),
            "cohort_recall_schema": list(COHORT_RECALL_SCHEMA),
            "cross_source_schema": list(CROSS_SOURCE_SCHEMA),
            "complaint_cohort_count": len(complaints),
            "complaint_event_count": total_events,
            "matched_cohort_count": len(recall_counts),
            "matched_cohort_percentage": 100 * len(recall_counts) / len(complaints),
            "matched_event_count": matched_events,
            "matched_event_percentage": 100 * matched_events / total_events,
            "matched_unique_campaign_count": len(matched_campaigns),
            "matched_applicability_count": matched_applications,
            "cohort_match_status_counts": {
                RecallMatchStatus.EXACT.value: len(recall_counts),
                RecallMatchStatus.EXPLICIT_ALIAS.value: 0,
                RecallMatchStatus.AMBIGUOUS.value: 0,
                RecallMatchStatus.NO_MATCH.value: len(complaints) - len(recall_counts),
            },
            "applicability_match_status_counts": dict(sorted(status_counts.items())),
            "recalls_per_matched_cohort": _distribution(recall_counts),
            "mismatch_class_counts": dict(sorted(mismatch_counts.items())),
            "top_unmatched_makes": Counter(
                row["normalized_make"] for row in unmatched_rows
            ).most_common(20),
            "top_unmatched_models": Counter(
                f"{row['normalized_make']}|{row['normalized_model']}" for row in unmatched_rows
            ).most_common(20),
            "coverage_by_model_year": {
                str(k): {"matched": v[0], "total": v[1]} for k, v in sorted(by_year.items())
            },
            "coverage_by_make": {
                k: {"matched": v[0], "total": v[1]} for k, v in sorted(by_make.items())
            },
            "cross_source_cohort_counts": dict(sorted(cross_counts.items())),
            "cross_source_event_counts": dict(sorted(cross_events.items())),
            "recall_beyond_communications_cohort_count": sum(
                row["has_recalls"] and not row["has_manufacturer_communications"]
                for row in cross_rows
            ),
            "recall_and_communications_cohort_count": sum(
                row["has_recalls"] and row["has_manufacturer_communications"] for row in cross_rows
            ),
            "outputs": {
                path.name: sha256_file(path)
                for path in (
                    applicability_matches_path,
                    cohort_recall_path,
                    unmatched_path,
                    cross_source_path,
                )
            },
            "temporal_leakage_warning": (
                "Whole-history recall diagnostics are not training-ready; future features "
                "must apply prediction-time cutoffs."
            ),
            "processing_complete": True,
        }
        _write_json(provenance_path, summary)
        return summary
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise
