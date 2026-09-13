"""Phase 3A target-definition tests using processed-artifact fixtures only."""

from __future__ import annotations

import json
import socket
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from howreliable.data.features.schema import EVENT_FEATURE_SCHEMA, FEATURE_VERSION
from howreliable.data.ingestion.nhtsa_complaints import ArtifactExistsError, sha256_file
from howreliable.data.integration.cohorts import INTEGRATED_SCHEMA, INTEGRATION_VERSION
from howreliable.data.targets import (
    TARGET_DEFINITION_VERSION,
    TARGET_SCHEMA,
    TargetDefinitionError,
    generate_future_complaint_targets,
)
from howreliable.data.targets.future_complaints import (
    ELIGIBLE,
    INCOMPLETE_FUTURE_WINDOW,
    INVALID_COHORT_IDENTITY,
    MODEL_YEAR_AFTER_CUTOFF,
    NO_HISTORICAL_COMPLAINT,
    add_months,
    eligibility_reason,
)

FIXED_TIME = datetime(2026, 9, 13, 16, 0, tzinfo=UTC)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _event(
    cohort_id: str,
    report_date: str,
    *,
    component: str = "other",
    severity: str = "low",
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name in EVENT_FEATURE_SCHEMA:
        if name in {"event_id", "source_record_id", "source_reference_id"}:
            value: Any = f"{name}-{cohort_id}-{report_date}-{component}"
        elif name == "source_type":
            value = "nhtsa_odi_complaint"
        elif name == "broad_vehicle_id":
            value = cohort_id
        elif name == "normalized_make":
            value = "ford"
        elif name == "normalized_model":
            value = cohort_id
        elif name == "model_year":
            value = 2020
        elif name == "component":
            value = component
        elif name == "severity":
            value = severity
        elif name == "report_date":
            value = report_date
        elif name == "report_year":
            value = int(report_date[:4])
        elif name == "report_month":
            value = int(report_date[5:7])
        elif name.startswith("quality_") or name.endswith("_positive"):
            value = False
        else:
            value = None
        row[name] = value
    return row


def _cohort(
    cohort_id: str, model_year: int = 2020, production: int | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name in INTEGRATED_SCHEMA:
        if name == "broad_vehicle_id":
            value: Any = cohort_id
        elif name == "normalized_make":
            value = "ford"
        elif name == "normalized_model":
            value = cohort_id
        elif name == "model_year":
            value = model_year
        elif name == "production_total_units":
            value = production
        elif name.startswith("has_"):
            value = False
        elif name == "source_count_available":
            value = 1
        elif (
            name.endswith("_ids")
            or name.endswith("_periods")
            or name.endswith("_status")
            or name.endswith("_method")
            or name.endswith("_category")
        ):
            value = None
        elif "count" in name or "units" in name:
            value = 0
        else:
            value = None
        row[name] = value
    row["complaint_event_count"] = 1
    row["has_production_match"] = production is not None
    return row


def _fixture_data(root: Path) -> Path:
    data = root / "processed"
    events_path = data / "features/nhtsa-complaint-events.jsonl"
    integrated_path = data / "integrated/howreliable-cohorts.jsonl"
    events = [
        _event("c1", "2022-12-31"),
        _event("c1", "2023-01-01", component="engine"),
        _event("c1", "2023-12-31", component="brakes", severity="critical"),
        _event("c1", "2024-01-01", component="electrical"),
        _event("c1", "2024-12-31", component="steering"),
        _event("c2", "2022-01-01"),
        _event("c3", "2022-06-01"),
        _event("c4", "2023-06-01", component="transmission"),
    ]
    cohorts = [
        _cohort("c2"),
        _cohort("c1", production=1_000),
        _cohort("c4"),
        _cohort("c3", model_year=2023),
    ]
    _write_jsonl(events_path, events)
    _write_jsonl(integrated_path, cohorts)
    _write_json(
        data / "features/nhtsa-complaint-features.provenance.json",
        {
            "feature_version": FEATURE_VERSION,
            "cleaning_version": "fixture-cleaning-1.0",
            "mapping_version": "fixture-mapping-1.0",
            "input_clean_artifact_sha256": "a" * 64,
            "event_feature_schema": list(EVENT_FEATURE_SCHEMA),
            "event_feature_row_count": len(events),
            "event_feature_artifact": {"sha256": sha256_file(events_path)},
        },
    )
    _write_json(
        data / "integrated/integration.provenance.json",
        {
            "integration_version": INTEGRATION_VERSION,
            "integrated_schema": list(INTEGRATED_SCHEMA),
            "integrated_row_count": len(cohorts),
            "output_sha256": sha256_file(integrated_path),
            "input_versions": {"phase_2c": FEATURE_VERSION},
        },
    )
    return data


def _generate(root: Path, *, horizon: int = 12, name: str = "first") -> tuple[Path, Path]:
    data = _fixture_data(root)
    output = root / name / "targets.jsonl"
    provenance = root / name / "targets.provenance.json"
    generate_future_complaint_targets(
        data,
        output,
        provenance,
        cutoff=date(2022, 12, 31),
        horizon_months=horizon,
        clock=lambda: FIXED_TIME,
    )
    return output, provenance


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_calendar_horizon_computation_and_validation() -> None:
    assert add_months(date(2022, 12, 31), 12) == date(2023, 12, 31)
    assert add_months(date(2022, 12, 31), 24) == date(2024, 12, 31)
    assert add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    with pytest.raises(TargetDefinitionError, match="positive"):
        add_months(date(2022, 12, 31), 0)


def test_eligibility_reasons_are_explicit() -> None:
    cohort = {
        "broad_vehicle_id": "c1",
        "normalized_make": "ford",
        "normalized_model": "f-150",
        "model_year": 2020,
    }
    def reason(
        value: dict[str, object] = cohort,
        historical: int = 1,
        complete: bool = True,
    ) -> str:
        return eligibility_reason(
            value,
            cutoff=date(2022, 12, 31),
            historical_complaint_count=historical,
            future_window_complete=complete,
        )

    assert reason() == ELIGIBLE
    assert (
        reason(historical=0) == NO_HISTORICAL_COMPLAINT
    )
    assert reason({**cohort, "model_year": 2023}) == MODEL_YEAR_AFTER_CUTOFF
    assert reason({**cohort, "broad_vehicle_id": ""}) == INVALID_COHORT_IDENTITY
    assert reason(complete=False) == INCOMPLETE_FUTURE_WINDOW


def test_cutoff_and_window_boundaries_counts_severity_and_components(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    rows = {row["broad_vehicle_id"]: row for row in _rows(output)}
    assert set(rows) == {"c1", "c2"}
    c1 = rows["c1"]
    assert c1["historical_complaint_count"] == 1
    assert c1["future_window_start"] == "2023-01-01"
    assert c1["future_window_end"] == "2023-12-31"
    assert c1["future_complaint_count"] == 2
    assert c1["future_any_complaint"] is True
    assert c1["future_complaint_at_least_2"] is True
    assert c1["future_complaint_at_least_3"] is False
    assert c1["future_severe_complaint_count"] == 1
    assert c1["future_severe_complaint_activity"] is True
    assert c1["future_component_engine_complaint_count"] == 1
    assert c1["future_component_brakes_complaint_count"] == 1
    assert c1["future_component_electrical_complaint_count"] == 0


def test_eligible_zero_means_no_observed_future_report(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    c2 = next(row for row in _rows(output) if row["broad_vehicle_id"] == "c2")
    assert c2["target_eligible"] is True
    assert c2["eligibility_reason"] == ELIGIBLE
    assert c2["future_complaint_count"] == 0
    assert c2["future_any_complaint"] is False


def test_twenty_four_month_window_includes_2024_end_boundary(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path, horizon=24)
    c1 = next(row for row in _rows(output) if row["broad_vehicle_id"] == "c1")
    assert c1["future_window_end"] == "2024-12-31"
    assert c1["future_complaint_count"] == 4
    assert c1["future_component_electrical_complaint_count"] == 1
    assert c1["future_component_steering_complaint_count"] == 1


def test_incomplete_window_emits_no_labels(tmp_path: Path) -> None:
    data = _fixture_data(tmp_path)
    output = tmp_path / "invalid/targets.jsonl"
    provenance = tmp_path / "invalid/provenance.json"
    with pytest.raises(TargetDefinitionError, match="no labels emitted"):
        generate_future_complaint_targets(
            data,
            output,
            provenance,
            cutoff=date(2023, 12, 31),
            horizon_months=24,
        )
    assert not output.exists()
    assert not provenance.exists()


def test_schema_is_target_only_and_neutrally_named(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    row = _rows(output)[0]
    assert tuple(row) == TARGET_SCHEMA
    assert not set(row) & {
        "production_total_units",
        "communication_unique_count",
        "recall_unique_campaign_count",
    }
    assert not [
        name
        for name in row
        if any(term in name for term in ("repair", "failure", "reliability", "healthy"))
    ]


def test_provenance_accounts_for_eligibility_and_target_distribution(tmp_path: Path) -> None:
    output, provenance = _generate(tmp_path)
    metadata = json.loads(provenance.read_text(encoding="utf-8"))
    assert metadata["target_definition_version"] == TARGET_DEFINITION_VERSION
    assert metadata["total_integrated_cohort_count"] == 4
    assert metadata["eligible_cohort_count"] == 2
    assert metadata["ineligible_cohort_count"] == 2
    assert metadata["eligibility_reason_counts"] == {
        ELIGIBLE: 2,
        MODEL_YEAR_AFTER_CUTOFF: 1,
        NO_HISTORICAL_COMPLAINT: 1,
    }
    assert metadata["diagnostics"]["threshold_comparison"]["1"]["positive_count"] == 1
    assert metadata["diagnostics"]["zero_future_complaint_count"] == 1
    assert metadata["output_sha256"] == sha256_file(output)
    assert metadata["target_definition"]["event_date_field"] == "report_date"


def test_serialization_and_ordering_are_deterministic(tmp_path: Path) -> None:
    first_output, first_provenance = _generate(tmp_path / "a", name="run")
    second_output, second_provenance = _generate(tmp_path / "b", name="run")
    assert first_output.read_bytes() == second_output.read_bytes()
    assert first_provenance.read_bytes() == second_provenance.read_bytes()
    rows = _rows(first_output)
    assert [row["broad_vehicle_id"] for row in rows] == ["c1", "c2"]


def test_overwrite_is_rejected(tmp_path: Path) -> None:
    output, provenance = _generate(tmp_path)
    with pytest.raises(ArtifactExistsError, match="refusing to overwrite"):
        generate_future_complaint_targets(
            tmp_path / "processed",
            output,
            provenance,
            cutoff=date(2022, 12, 31),
            horizon_months=12,
        )


def test_mismatched_source_checksum_fails(tmp_path: Path) -> None:
    data = _fixture_data(tmp_path)
    event_path = data / "features/nhtsa-complaint-events.jsonl"
    event_path.write_text(event_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(TargetDefinitionError, match="checksum mismatch"):
        generate_future_complaint_targets(
            data,
            tmp_path / "output.jsonl",
            tmp_path / "provenance.json",
            cutoff=date(2022, 12, 31),
            horizon_months=12,
        )


def test_generation_has_no_network_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    output, _ = _generate(tmp_path)
    assert output.exists()
