"""CLI for authoritative Phase 3F frozen-model evaluation."""

import argparse
from pathlib import Path

from howreliable.modeling.evaluation_report import generate_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the two frozen Phase 3F models")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="deliberately replace an existing complete evaluation",
    )
    arguments = parser.parse_args()
    result = generate_evaluation(
        Path.cwd(), Path("artifacts/evaluation"), regenerate=arguments.regenerate
    )
    print(f"preferred model: {result['preferred_model']}")
    print(f"evaluation report SHA-256: {result['evaluation_report_sha256']}")


if __name__ == "__main__":
    main()
