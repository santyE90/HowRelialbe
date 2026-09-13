"""CLI for deterministic Phase 3A target generation."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from howreliable.data.targets import generate_future_complaint_targets


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cutoff must be an ISO date (YYYY-MM-DD)") from error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate cohort-level future NHTSA complaint-activity targets"
    )
    parser.add_argument("--data-directory", type=Path, default=Path("data/processed"))
    parser.add_argument("--cutoff", type=_date, default=date(2022, 12, 31))
    parser.add_argument("--horizon-months", type=int, default=12)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provenance", type=Path)
    arguments = parser.parse_args()
    stem = f"future-complaint-activity-{arguments.cutoff.isoformat()}-{arguments.horizon_months}m"
    output = arguments.output or Path("data/processed/targets") / f"{stem}.jsonl"
    provenance = arguments.provenance or Path("data/processed/targets") / f"{stem}.provenance.json"
    result = generate_future_complaint_targets(
        arguments.data_directory,
        output,
        provenance,
        cutoff=arguments.cutoff,
        horizon_months=arguments.horizon_months,
    )
    print(
        f"generated {result.eligible_cohort_count} eligible cohort targets; "
        f"{result.ineligible_cohort_count} cohorts ineligible; "
        f"future complaints={result.future_complaint_total}"
    )


if __name__ == "__main__":
    main()
