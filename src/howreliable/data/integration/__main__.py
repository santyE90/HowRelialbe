"""CLI for deterministic Phase 2G cohort integration."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.data.integration.cohorts import integrate_cohorts


def main() -> None:
    parser = argparse.ArgumentParser(description="Integrate validated HowReliable? cohorts")
    parser.add_argument("--data-directory", type=Path, default=Path("data/processed"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/integrated/howreliable-cohorts.jsonl"),
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=Path("data/processed/integrated/integration.provenance.json"),
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("data/processed/integrated/dataset-review.json"),
    )
    args = parser.parse_args()
    print(integrate_cohorts(args.data_directory, args.output, args.provenance, args.review))


if __name__ == "__main__":
    main()
