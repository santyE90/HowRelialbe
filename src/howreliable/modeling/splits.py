"""Deterministic target-stratified cohort split assignments."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]

from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.features import (
    FEATURE_CUTOFF,
    FEATURE_MATRIX_VERSION,
    FEATURE_SCHEMA,
    ModelingDataError,
    _json,
    _jsonl,
    _write_json,
    _write_jsonl,
)

SPLIT_VERSION: Final = "cohort-split-1.0"
SPLIT_SEED: Final = 20220913
SPLIT_SCHEMA: Final = ("broad_vehicle_id", "split")


def generate_split(
    data_directory: Path,
    output_path: Path,
    provenance_path: Path,
    *,
    seed: int = SPLIT_SEED,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Create one fixed 70/15/15 stratified assignment for eligible cohorts."""
    existing = next((path for path in (output_path, provenance_path) if path.exists()), None)
    if existing:
        raise ArtifactExistsError(f"refusing to overwrite split output: {existing}")
    feature_path = data_directory / "modeling/cohort-features-asof-2022-12-31.jsonl"
    feature_provenance = _json(
        data_directory / "modeling/cohort-features-asof-2022-12-31.provenance.json"
    )
    target_path = data_directory / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    target_provenance = _json(
        data_directory / "targets/future-complaint-activity-2022-12-31-12m.provenance.json"
    )
    if (
        feature_provenance.get("feature_matrix_version") != FEATURE_MATRIX_VERSION
        or feature_provenance.get("cutoff") != FEATURE_CUTOFF.isoformat()
    ):
        raise ModelingDataError("incompatible feature provenance")
    if (
        target_provenance.get("target_definition_version") != TARGET_DEFINITION_VERSION
        or target_provenance.get("target_definition", {}).get("cutoff")
        != FEATURE_CUTOFF.isoformat()
    ):
        raise ModelingDataError("incompatible target provenance")
    if sha256_file(feature_path) != feature_provenance.get("output_sha256"):
        raise ModelingDataError("feature checksum mismatch")
    if sha256_file(target_path) != target_provenance.get("output_sha256"):
        raise ModelingDataError("target checksum mismatch")
    features = _jsonl(feature_path, FEATURE_SCHEMA)
    targets = _jsonl(target_path, TARGET_SCHEMA)
    feature_ids = [cast(str, row["broad_vehicle_id"]) for row in features]
    target_by_id = {cast(str, row["broad_vehicle_id"]): row for row in targets}
    if len(target_by_id) != len(targets) or set(feature_ids) != set(target_by_id):
        raise ModelingDataError("feature/target identities do not align")
    labels = [int(cast(bool, target_by_id[item]["future_any_complaint"])) for item in feature_ids]
    train_ids, holdout_ids, train_y, holdout_y = train_test_split(
        feature_ids,
        labels,
        test_size=0.30,
        random_state=seed,
        stratify=labels,
    )
    validation_ids, test_ids, validation_y, test_y = train_test_split(
        holdout_ids,
        holdout_y,
        test_size=0.50,
        random_state=seed,
        stratify=holdout_y,
    )
    assignment = {
        **dict.fromkeys(train_ids, "TRAIN"),
        **dict.fromkeys(validation_ids, "VALIDATION"),
        **dict.fromkeys(test_ids, "TEST"),
    }
    if len(assignment) != len(feature_ids):
        raise ModelingDataError("split assignment does not cover each cohort exactly once")
    rows = [{"broad_vehicle_id": item, "split": assignment[item]} for item in feature_ids]
    counts = Counter(row["split"] for row in rows)
    positives = {
        "TRAIN": sum(train_y),
        "VALIDATION": sum(validation_y),
        "TEST": sum(test_y),
    }
    try:
        _write_jsonl(output_path, rows)
        metadata = {
            "split_version": SPLIT_VERSION,
            "generation_timestamp_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "seed": seed,
            "strategy": "70/15/15 stratified random cohort split at one shared cutoff",
            "limitation": "Does not measure generalization across calendar eras.",
            "schema": list(SPLIT_SCHEMA),
            "input_checksums": {
                "features": sha256_file(feature_path),
                "target": sha256_file(target_path),
            },
            "counts": dict(sorted(counts.items())),
            "positive_counts": positives,
            "positive_prevalence": {
                key: positives[key] / counts[key] for key in ("TRAIN", "VALIDATION", "TEST")
            },
            "output_filename": output_path.name,
            "output_size_bytes": output_path.stat().st_size,
            "output_sha256": sha256_file(output_path),
            "processing_complete": True,
        }
        _write_json(provenance_path, metadata)
    except Exception:
        output_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        raise
    return metadata
