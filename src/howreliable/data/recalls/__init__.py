"""Official NHTSA recall ingestion and cohort matching."""

from howreliable.data.recalls.nhtsa import (
    CanonicalRecallCampaign,
    NhtsaRecallRecord,
    RecallApplicability,
    ingest_archives,
    match_cohorts,
    retrieve_artifacts,
)

__all__ = [
    "CanonicalRecallCampaign",
    "NhtsaRecallRecord",
    "RecallApplicability",
    "ingest_archives",
    "match_cohorts",
    "retrieve_artifacts",
]
