"""Phase 4A frozen-model explanation tests."""

import joblib  # type: ignore[import-untyped]
import numpy as np

from howreliable.modeling.explainability import feature_manifest, tree_path_contributions


def test_manifest_mapping_and_local_reconstruction() -> None:
    model = joblib.load(
        "artifacts/models/baselines/complaints_communications_recalls--random_forest.joblib"
    )
    manifest = feature_manifest(model)
    assert len(manifest) == 105 and [x["index"] for x in manifest] == list(range(105))
    assert all(
        x["raw_feature"]
        and x["feature_family"]
        and x["source_system"]
        and x["human_readable_label"]
        for x in manifest
    )
    matrix = np.zeros((2, 105))
    classifier = model.named_steps["classifier"]
    baseline, contributions = tree_path_contributions(classifier, matrix)
    probability = classifier.predict_proba(matrix)[:, 1]
    assert np.allclose(baseline + contributions.sum(axis=1), probability, atol=1e-12)


def test_explanation_artifact_contract() -> None:
    import json
    from pathlib import Path

    report = json.loads(Path("artifacts/explainability/explainability-report.json").read_text())
    assert (
        report["preferred_model_checksum"]
        == "e1b9b382409d8edff43a2c7edd63576c3aeaaaedb00e334db9ba78cf061b40df"
    )
    assert report["maximum_reconstruction_absolute_error"] < 1e-12
    assert "SHAP unavailable" in report["method"]
    text = json.dumps(report).lower()
    assert "risk factor" in text  # present only in the prohibited-language contract
    assert len(report["representative_local_explanations"]) == 6
    assert any(x["evaluation_warnings"] for x in report["representative_local_explanations"])
