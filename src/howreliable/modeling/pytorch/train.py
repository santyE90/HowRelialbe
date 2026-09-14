"""CLI for the bounded Phase 3D first-MLP experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.modeling.pytorch.training import run_first_mlp_experiment


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train and evaluate the bounded Phase 3D first-MLP experiment."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pipeline-artifacts",
        type=Path,
        default=Path("artifacts/modeling/pytorch"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/models/pytorch"),
    )
    arguments = parser.parse_args()
    results = run_first_mlp_experiment(
        arguments.repository_root,
        arguments.pipeline_artifacts,
        arguments.output,
        progress=print,
    )
    selected = results["selected_configuration"]
    print(f"selected {selected['configuration']['name']} at epoch {selected['best_epoch']}")
    print(f"validation: {selected['validation_metrics_at_best_checkpoint']}")
    print(f"final test: {results['final_test_metrics']}")
    print(f"result SHA-256: {results['result_artifact_sha256']}")


if __name__ == "__main__":
    main()
