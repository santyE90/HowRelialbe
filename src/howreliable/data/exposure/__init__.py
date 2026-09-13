"""NHTSA EWR production exposure ingestion and complaint-cohort matching."""

from howreliable.data.exposure.nhtsa_ewr import (
    AggregatedProductionCohort,
    CanonicalProductionRecord,
    ExposureError,
    MatchStatus,
    NhtsaEwrProductionRecord,
    aggregate_production,
    ingest_snapshot,
    match_complaint_cohorts,
    normalize_identity,
    parse_production_payload,
    retrieve_snapshot,
)

__all__ = [
    "AggregatedProductionCohort",
    "CanonicalProductionRecord",
    "ExposureError",
    "MatchStatus",
    "NhtsaEwrProductionRecord",
    "aggregate_production",
    "ingest_snapshot",
    "match_complaint_cohorts",
    "normalize_identity",
    "parse_production_payload",
    "retrieve_snapshot",
]
