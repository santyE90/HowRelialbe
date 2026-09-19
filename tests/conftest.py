"""Repository-wide pytest policy for explicit full-artifact test classification."""

from pathlib import Path

import pytest

CANONICAL_MANIFEST = Path("artifacts/registry/bundles/howreliable-rf-2022-cutoff-v1/manifest.json")
FULL_ARTIFACT_MODULES = frozenset(
    {
        "test_api.py",
        "test_deployment.py",
        "test_explainability.py",
        "test_monitoring.py",
        "test_presentation.py",
        "test_registry.py",
        "test_s3.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Label and explicitly skip tests whose validated inputs are intentionally ignored."""
    bundle_available = CANONICAL_MANIFEST.is_file()
    for item in items:
        if Path(str(item.fspath)).name not in FULL_ARTIFACT_MODULES:
            continue
        item.add_marker(pytest.mark.full_artifacts)
        if not bundle_available:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "requires ignored canonical Phase 3B-6C artifacts; run the full-artifact "
                        "gate where howreliable-rf-2022-cutoff-v1 is provisioned"
                    )
                )
            )
