"""Command-line entry point for Phase 2C feature generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.data.features import generate_feature_artifacts

DEFAULT_INPUT = Path("data/processed/nhtsa/complaints/complaints-clean.jsonl")
DEFAULT_EVENT_OUTPUT = Path("data/processed/features/nhtsa-complaint-events.jsonl")
DEFAULT_COHORT_OUTPUT = Path("data/processed/features/nhtsa-vehicle-cohorts.jsonl")
DEFAULT_PROVENANCE = Path("data/processed/features/nhtsa-complaint-features.provenance.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate target-agnostic NHTSA event and cohort features"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--event-output", type=Path, default=DEFAULT_EVENT_OUTPUT)
    parser.add_argument("--cohort-output", type=Path, default=DEFAULT_COHORT_OUTPUT)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE)
    arguments = parser.parse_args()
    result = generate_feature_artifacts(
        arguments.input,
        arguments.event_output,
        arguments.cohort_output,
        arguments.provenance,
    )
    print(
        f"generated {result.event_feature_row_count} event rows and "
        f"{result.cohort_feature_row_count} cohort rows from "
        f"{result.input_clean_record_count} clean records"
    )


if __name__ == "__main__":
    main()
