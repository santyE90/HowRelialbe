"""CLI for Phase 3B feature, split, and baseline generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.modeling import generate_asof_features, generate_split, run_baseline_experiments


def main() -> None:
    parser = argparse.ArgumentParser(description="Build leakage-safe traditional baselines")
    parser.add_argument("command", choices=("features", "split", "run"))
    arguments = parser.parse_args()
    data = Path("data/processed")
    modeling = data / "modeling"
    if arguments.command == "features":
        result = generate_asof_features(
            data,
            modeling / "cohort-features-asof-2022-12-31.jsonl",
            modeling / "cohort-features-asof-2022-12-31.provenance.json",
        )
        print(f"generated {result['row_count']} as-of-cutoff feature rows")
    elif arguments.command == "split":
        result = generate_split(
            data,
            modeling / "cohort-split-2022-12-31.jsonl",
            modeling / "cohort-split-2022-12-31.provenance.json",
        )
        print(f"generated split counts: {result['counts']}")
    else:
        result = run_baseline_experiments(
            data,
            Path("artifacts/models/baselines"),
            Path("artifacts/models/baseline-results.json"),
        )
        print(f"selected baseline: {result['best_model']}")


if __name__ == "__main__":
    main()
