"""Generate and validate the Phase 3C PyTorch data-pipeline artifacts."""

from __future__ import annotations

from pathlib import Path

from howreliable.modeling.pytorch.pipeline import generate_pytorch_pipeline


def main() -> None:
    bundle = generate_pytorch_pipeline(Path.cwd(), Path("artifacts/modeling/pytorch"))
    metadata = bundle.metadata
    print(
        "generated PyTorch datasets "
        f"{metadata['split_counts']} with tensor dimension "
        f"{metadata['final_tensor_dimension']}"
    )
    print(f"example batches: {metadata['example_batches']}")
    print(f"artifact checksums: {bundle.artifact_checksums}")


if __name__ == "__main__":
    main()
