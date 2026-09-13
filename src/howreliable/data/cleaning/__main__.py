"""Command-line entry point for deterministic Phase 2B cleaning."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.data.cleaning import clean_structured_artifact

DEFAULT_INPUT = Path("data/interim/nhtsa/complaints/complaints-2020-2024.jsonl")
DEFAULT_OUTPUT = Path("data/processed/nhtsa/complaints/complaints-clean.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean structured NHTSA complaint records")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--exclusions", type=Path)
    parser.add_argument("--provenance", type=Path)
    arguments = parser.parse_args()
    result = clean_structured_artifact(
        arguments.input,
        arguments.output,
        exclusions_path=arguments.exclusions,
        provenance_path=arguments.provenance,
    )
    print(
        f"cleaned {result.cleaned_count} of {result.input_count} records; "
        f"excluded {result.excluded_count}; output={result.output_path}"
    )


if __name__ == "__main__":
    main()
