"""Phase 3B leakage-safe feature, split, and baseline tests."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
import pytest

from howreliable.data.features.schema import EVENT_FEATURE_SCHEMA
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.manufacturer_communications.nhtsa import (
    APPLICABILITY_MATCH_SCHEMA as COMMUNICATION_APPLICATION_SCHEMA,
)
from howreliable.data.manufacturer_communications.nhtsa import COMMUNICATION_SCHEMA
from howreliable.data.recalls.nhtsa import APPLICABILITY_MATCH_SCHEMA as RECALL_APPLICATION_SCHEMA
from howreliable.data.recalls.nhtsa import CAMPAIGN_SCHEMA
from howreliable.data.targets import TARGET_DEFINITION_VERSION, TARGET_SCHEMA
from howreliable.modeling.baselines import classification_metrics, run_baseline_experiments
from howreliable.modeling.features import (
    FEATURE_MATRIX_VERSION,
    FEATURE_SCHEMA,
    ModelingDataError,
    _window_start,
    generate_asof_features,
)
from howreliable.modeling.splits import SPLIT_SCHEMA, generate_split

FIXED_TIME = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def schema_row(schema: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    return {name: values.get(name) for name in schema}


def event(identifier: str, report_date: str, component: str = "engine") -> dict[str, Any]:
    values: dict[str, Any] = {
        "event_id": f"e-{identifier}-{report_date}",
        "source_type": "nhtsa_odi_complaint",
        "source_record_id": f"r-{identifier}-{report_date}",
        "source_reference_id": f"o-{identifier}-{report_date}",
        "broad_vehicle_id": identifier,
        "normalized_make": "ford",
        "normalized_model": identifier,
        "model_year": 2020,
        "component": component,
        "severity": "moderate" if component == "brakes" else "low",
        "report_date": report_date,
        "report_year": int(report_date[:4]),
        "report_month": int(report_date[5:7]),
        "mileage": 10_000,
        "mileage_unit": "miles",
        "mileage_observed": True,
        "crash_positive": component == "brakes",
        "fire_positive": False,
        "injury_positive": False,
        "death_positive": False,
    }
    for name in EVENT_FEATURE_SCHEMA:
        if name.startswith("quality_"):
            values[name] = False
    return schema_row(EVENT_FEATURE_SCHEMA, values)


def target(identifier: str, historical: int, positive: bool, index: int = 0) -> dict[str, Any]:
    values: dict[str, Any] = {
        "broad_vehicle_id": identifier,
        "normalized_make": f"make-{index % 5}",
        "normalized_model": f"model-{index % 20}",
        "model_year": 2000 + index % 23,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "cutoff_date": "2022-12-31",
        "horizon_months": 12,
        "future_window_start": "2023-01-01",
        "future_window_end": "2023-12-31",
        "target_eligible": True,
        "eligibility_reason": "ELIGIBLE",
        "cohort_age_at_cutoff": 22 - index % 23,
        "historical_complaint_count": historical,
        "future_complaint_count": int(positive),
        "future_any_complaint": positive,
        "future_complaint_at_least_2": False,
        "future_complaint_at_least_3": False,
        "future_complaint_at_least_5": False,
        "future_complaint_at_least_10": False,
        "future_severe_complaint_count": 0,
        "future_severe_complaint_activity": False,
    }
    for name in TARGET_SCHEMA:
        if name.startswith("future_component_"):
            values[name] = 0
    return schema_row(TARGET_SCHEMA, values)


def source_fixture(tmp_path: Path) -> Path:
    data = tmp_path / "processed"
    targets = [
        target("c1", 3, True),
        target("c2", 1, False, 1),
        target("c3", 1, False, 2),
    ]
    target_path = data / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    write_jsonl(target_path, targets)
    write_json(
        target_path.with_name(f"{target_path.stem}.provenance.json"),
        {
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "target_definition": {"cutoff": "2022-12-31"},
            "output_sha256": sha256_file(target_path),
        },
    )
    events = [
        event("c1", "2021-01-01"),
        event("c1", "2022-01-01"),
        event("c1", "2022-12-31", "brakes"),
        event("c1", "2023-01-01", "steering"),
        event("c2", "2020-01-01"),
        event("c3", "2020-01-01"),
    ]
    event_path = data / "features/nhtsa-complaint-events.jsonl"
    write_jsonl(event_path, events)
    write_json(
        data / "features/nhtsa-complaint-features.provenance.json",
        {"event_feature_artifact": {"sha256": sha256_file(event_path)}},
    )
    comm_path = data / "manufacturer_communications/communications.jsonl"
    comm_rows = [
        schema_row(
            COMMUNICATION_SCHEMA,
            {
                "communication_id": "m-before",
                "date_added": "2022-12-31",
                "communication_type": "SERVICE_BULLETIN",
                "canonical_components": ["engine"],
            },
        ),
        schema_row(
            COMMUNICATION_SCHEMA,
            {
                "communication_id": "m-after",
                "date_added": "2023-01-01",
                "communication_type": "SERVICE_BULLETIN",
                "canonical_components": ["brakes"],
            },
        ),
    ]
    write_jsonl(comm_path, comm_rows)
    comm_apps = data / "manufacturer_communications/applicability-matches.jsonl"
    write_jsonl(
        comm_apps,
        [
            schema_row(
                COMMUNICATION_APPLICATION_SCHEMA,
                {
                    "applicability_id": "a1",
                    "communication_id": "m-before",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
            schema_row(
                COMMUNICATION_APPLICATION_SCHEMA,
                {
                    "applicability_id": "a4",
                    "communication_id": "m-after",
                    "matched_broad_vehicle_id": "c2",
                },
            ),
            schema_row(
                COMMUNICATION_APPLICATION_SCHEMA,
                {
                    "applicability_id": "a2",
                    "communication_id": "m-before",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
            schema_row(
                COMMUNICATION_APPLICATION_SCHEMA,
                {
                    "applicability_id": "a3",
                    "communication_id": "m-after",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
        ],
    )
    write_json(
        data / "manufacturer_communications/matching.provenance.json",
        {
            "input_checksums": {"communications": sha256_file(comm_path)},
            "output_checksums": {"applicability_matches": sha256_file(comm_apps)},
        },
    )
    campaign_path = data / "recalls/campaigns.jsonl"
    campaigns = [
        schema_row(
            CAMPAIGN_SCHEMA,
            {
                "campaign_id": "r-before",
                "report_received_dates": ["2022-12-31"],
                "canonical_components": ["brakes"],
                "fmvss_numbers": ["135"],
                "regulation_part_numbers": [],
                "do_not_drive_values": ["Yes"],
                "park_outside_values": ["No"],
            },
        ),
        schema_row(
            CAMPAIGN_SCHEMA,
            {
                "campaign_id": "r-after",
                "report_received_dates": ["2023-01-01"],
                "canonical_components": ["steering"],
                "fmvss_numbers": [],
                "regulation_part_numbers": [],
                "do_not_drive_values": [],
                "park_outside_values": ["Yes"],
            },
        ),
    ]
    write_jsonl(campaign_path, campaigns)
    recall_apps = data / "recalls/applicability-matches.jsonl"
    write_jsonl(
        recall_apps,
        [
            schema_row(
                RECALL_APPLICATION_SCHEMA,
                {
                    "applicability_id": "ra1",
                    "campaign_id": "r-before",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
            schema_row(
                RECALL_APPLICATION_SCHEMA,
                {
                    "applicability_id": "ra4",
                    "campaign_id": "r-after",
                    "matched_broad_vehicle_id": "c2",
                },
            ),
            schema_row(
                RECALL_APPLICATION_SCHEMA,
                {
                    "applicability_id": "ra2",
                    "campaign_id": "r-before",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
            schema_row(
                RECALL_APPLICATION_SCHEMA,
                {
                    "applicability_id": "ra3",
                    "campaign_id": "r-after",
                    "matched_broad_vehicle_id": "c1",
                },
            ),
        ],
    )
    write_json(
        data / "recalls/ingestion.provenance.json", {"campaigns_sha256": sha256_file(campaign_path)}
    )
    write_json(
        data / "recalls/matching.provenance.json",
        {"outputs": {"applicability-matches.jsonl": sha256_file(recall_apps)}},
    )
    return data


def generate_fixture_features(tmp_path: Path) -> tuple[Path, Path]:
    data = source_fixture(tmp_path)
    output = data / "modeling/cohort-features-asof-2022-12-31.jsonl"
    provenance = data / "modeling/cohort-features-asof-2022-12-31.provenance.json"
    generate_asof_features(data, output, provenance, clock=lambda: FIXED_TIME)
    return output, provenance


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_calendar_recency_boundaries() -> None:
    assert _window_start(datetime(2022, 12, 31).date(), 12).isoformat() == "2022-01-01"
    assert _window_start(datetime(2022, 12, 31).date(), 24).isoformat() == "2021-01-01"
    assert _window_start(datetime(2022, 12, 31).date(), 36).isoformat() == "2020-01-01"


def test_asof_features_exclude_future_events_and_count_recency(tmp_path: Path) -> None:
    output, _ = generate_fixture_features(tmp_path)
    c1 = next(row for row in read_rows(output) if row["broad_vehicle_id"] == "c1")
    assert c1["historical_complaint_count"] == 3
    assert c1["complaints_last_12m"] == 2
    assert c1["complaints_last_24m"] == 3
    assert c1["historical_complaint_component_steering_count"] == 0
    assert c1["historical_complaint_component_brakes_count"] == 1
    assert c1["cohort_age_at_cutoff"] == 22


def test_asof_sources_deduplicate_and_exclude_post_cutoff(tmp_path: Path) -> None:
    output, _ = generate_fixture_features(tmp_path)
    rows = {row["broad_vehicle_id"]: row for row in read_rows(output)}
    c1, c2 = rows["c1"], rows["c2"]
    assert c1["historical_unique_communication_count"] == 1
    assert c1["historical_communication_component_brakes_count"] == 0
    assert c1["historical_unique_recall_campaign_count"] == 1
    assert c1["historical_recall_component_steering_count"] == 0
    assert c1["historical_recall_do_not_drive_count"] == 1
    assert c2["communication_asof_status"] == "MATCHED_RECORDS_AFTER_CUTOFF_ONLY"
    assert c2["historical_unique_communication_count"] == 0
    assert c2["recall_asof_status"] == "MATCHED_RECORDS_AFTER_CUTOFF_ONLY"
    assert c2["historical_unique_recall_campaign_count"] == 0
    assert rows["c3"]["communication_asof_status"] == "NO_MATCHED_RECORD"
    assert rows["c3"]["recall_asof_status"] == "NO_MATCHED_RECORD"


def test_feature_schema_identity_alignment_neutrality_and_provenance(tmp_path: Path) -> None:
    output, provenance = generate_fixture_features(tmp_path)
    duplicate, _ = generate_fixture_features(tmp_path / "duplicate")
    rows = read_rows(output)
    assert len(rows) == 3
    assert all(tuple(row) == FEATURE_SCHEMA for row in rows)
    assert {row["broad_vehicle_id"] for row in rows} == {"c1", "c2", "c3"}
    assert not [name for name in FEATURE_SCHEMA if name.startswith("future_") or "target" in name]
    assert not [name for name in FEATURE_SCHEMA if name.startswith("production_")]
    metadata = json.loads(provenance.read_text(encoding="utf-8"))
    assert metadata["output_sha256"] == sha256_file(output)
    assert metadata["policies"]["production"].startswith("excluded")
    assert output.read_bytes() == duplicate.read_bytes()


def modeling_fixture(tmp_path: Path, count: int = 100) -> Path:
    data = tmp_path / "processed"
    feature_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    for index in range(count):
        identifier = f"c{index:03d}"
        positive = index % 2 == 0
        history = index % 20 + 1
        values: dict[str, Any] = {
            "broad_vehicle_id": identifier,
            "normalized_make": f"make-{index % 5}",
            "normalized_model": f"model-{index % 20}",
            "model_year": 2000 + index % 23,
            "cohort_age_at_cutoff": 22 - index % 23,
            "feature_cutoff_date": "2022-12-31",
            "historical_first_report_date": "2020-01-01",
            "historical_last_report_date": "2022-12-31",
            "communication_asof_status": "OBSERVED_RECORDS"
            if index % 3
            else "NO_MATCHED_RECORD_BY_CUTOFF",
            "recall_asof_status": "OBSERVED_RECORDS"
            if index % 4
            else "NO_MATCHED_RECORD_BY_CUTOFF",
        }
        for name in FEATURE_SCHEMA:
            if name not in values:
                if (name == "historical_mileage_median" and index % 7 == 0) or name.endswith(
                    "_date"
                ):
                    values[name] = None
                elif name.endswith("_status"):
                    values[name] = "NO_MATCHED_RECORD_BY_CUTOFF"
                elif name.endswith("_cutoff"):
                    values[name] = False
                elif "share" in name or "years" in name:
                    values[name] = 0.5
                else:
                    values[name] = history if "complaint" in name else index % 4
        values["historical_complaint_count"] = history
        feature_rows.append(schema_row(FEATURE_SCHEMA, values))
        target_rows.append(target(identifier, history, positive, index))
    feature_path = data / "modeling/cohort-features-asof-2022-12-31.jsonl"
    target_path = data / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    write_jsonl(feature_path, feature_rows)
    write_jsonl(target_path, target_rows)
    write_json(
        feature_path.with_name(f"{feature_path.stem}.provenance.json"),
        {
            "feature_matrix_version": FEATURE_MATRIX_VERSION,
            "cutoff": "2022-12-31",
            "output_sha256": sha256_file(feature_path),
        },
    )
    write_json(
        target_path.with_name(f"{target_path.stem}.provenance.json"),
        {
            "output_sha256": sha256_file(target_path),
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "target_definition": {"cutoff": "2022-12-31"},
        },
    )
    return data


def test_split_is_stratified_reproducible_and_exhaustive(tmp_path: Path) -> None:
    first = modeling_fixture(tmp_path / "a")
    second = modeling_fixture(tmp_path / "b")
    paths = []
    for data in (first, second):
        output = data / "modeling/cohort-split-2022-12-31.jsonl"
        provenance = data / "modeling/cohort-split-2022-12-31.provenance.json"
        result = generate_split(data, output, provenance, clock=lambda: FIXED_TIME)
        assert result["counts"] == {"TEST": 15, "TRAIN": 70, "VALIDATION": 15}
        with pytest.raises(ArtifactExistsError, match="overwrite"):
            generate_split(data, output, provenance)
        paths.append(output)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    rows = read_rows(paths[0])
    assert len(rows) == len({row["broad_vehicle_id"] for row in rows}) == 100
    assert all(tuple(row) == SPLIT_SCHEMA for row in rows)


def test_metrics_accounting_and_probability_validation() -> None:
    metrics = classification_metrics([0, 0, 1, 1], [0.1, 0.8, 0.7, 0.9])
    assert metrics["confusion_matrix"] == [[1, 1], [0, 2]]
    assert sum(sum(row) for row in metrics["confusion_matrix"]) == 4
    assert 0 <= metrics["roc_auc"] <= 1
    with pytest.raises(ModelingDataError, match="within"):
        classification_metrics([0, 1], [-0.1, 1.1])


def test_baseline_models_results_checksums_and_train_only_preprocessing(tmp_path: Path) -> None:
    data = modeling_fixture(tmp_path)
    split_path = data / "modeling/cohort-split-2022-12-31.jsonl"
    split_provenance = data / "modeling/cohort-split-2022-12-31.provenance.json"
    generate_split(data, split_path, split_provenance, clock=lambda: FIXED_TIME)
    split_rows = {row["broad_vehicle_id"]: row["split"] for row in read_rows(split_path)}
    feature_path = data / "modeling/cohort-features-asof-2022-12-31.jsonl"
    feature_rows = read_rows(feature_path)
    test_identifier = next(
        identifier for identifier, split in split_rows.items() if split == "TEST"
    )
    next(row for row in feature_rows if row["broad_vehicle_id"] == test_identifier)[
        "normalized_make"
    ] = "test-only-make"
    write_jsonl(feature_path, feature_rows)
    write_json(
        feature_path.with_name(f"{feature_path.stem}.provenance.json"),
        {
            "feature_matrix_version": FEATURE_MATRIX_VERSION,
            "cutoff": "2022-12-31",
            "output_sha256": sha256_file(feature_path),
        },
    )
    artifact_directory = tmp_path / "artifacts/baselines"
    results_path = tmp_path / "artifacts/baseline-results.json"
    result = run_baseline_experiments(
        data, artifact_directory, results_path, clock=lambda: FIXED_TIME
    )
    assert results_path.exists()
    assert {item["model"] for item in result["model_results"]} == {
        "logistic_regression",
        "random_forest",
        "hist_gradient_boosting",
    }
    assert len(result["model_results"]) == 9
    for item in result["model_results"]:
        path = artifact_directory / item["model_artifact"]
        assert item["model_artifact_sha256"] == sha256_file(path)
        assert 0 <= item["metrics"]["TEST"]["predicted_positive_prevalence"] <= 1
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        run_baseline_experiments(data, artifact_directory, results_path)
    count_model = joblib.load(
        artifact_directory / "historical_count_only--logistic_regression.joblib"
    )
    train_values = [
        row["historical_complaint_count"]
        for row in feature_rows
        if split_rows[row["broad_vehicle_id"]] == "TRAIN"
    ]
    assert count_model.named_steps["preprocess"].named_transformers_["numeric"].named_steps[
        "imputer"
    ].statistics_[0] == np.median(train_values)
    assert count_model.named_steps["preprocess"].named_transformers_["numeric"].named_steps[
        "scaler"
    ].mean_[0] == np.mean(train_values)
    identity_model = joblib.load(
        artifact_directory / "all_evidence_make_model_identity--logistic_regression.joblib"
    )
    make_categories = (
        identity_model.named_steps["preprocess"].named_transformers_["categorical"].categories_[0]
    )
    assert "test-only-make" not in make_categories


def test_overwrite_checksum_network_and_no_torch_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, provenance = generate_fixture_features(tmp_path)
    with pytest.raises(ArtifactExistsError, match="overwrite"):
        generate_asof_features(tmp_path / "processed", output, provenance)
    data = source_fixture(tmp_path / "bad")
    target_path = data / "targets/future-complaint-activity-2022-12-31-12m.jsonl"
    target_path.write_text(target_path.read_text() + "{}\n", encoding="utf-8")
    with pytest.raises(ModelingDataError, match="target checksum"):
        generate_asof_features(data, tmp_path / "bad-output.jsonl", tmp_path / "bad-prov.json")

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    modeling_directory = Path(__file__).parents[1] / "src/howreliable/modeling"
    for module in modeling_directory.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "import torch" not in source
        assert "from torch" not in source
