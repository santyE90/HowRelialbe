"""Structured source ingestion."""

from howreliable.data.ingestion.nhtsa_complaints import (
    DEFAULT_SOURCE_URL,
    NhtsaComplaintRecord,
    Provenance,
    download_artifact,
    ingest_local_artifact,
    iter_complaints,
    map_record_to_vehicle,
    sha256_file,
)

__all__ = [
    "DEFAULT_SOURCE_URL",
    "NhtsaComplaintRecord",
    "Provenance",
    "download_artifact",
    "ingest_local_artifact",
    "iter_complaints",
    "map_record_to_vehicle",
    "sha256_file",
]

