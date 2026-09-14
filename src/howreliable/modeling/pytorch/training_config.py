"""Validated configuration and deterministic identity for Phase 3E training runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Final

from howreliable.modeling.features import ModelingDataError

TRAINING_INFRASTRUCTURE_VERSION: Final = "pytorch-training-1.0"


@dataclass(frozen=True)
class TrainingConfig:
    """Small immutable configuration for the validated first MLP."""

    architecture_name: str = "mlp_64_32"
    hidden_dimensions: tuple[int, ...] = (64, 32)
    dropout: float = 0.1
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    batch_size: int = 64
    max_epochs: int = 100
    early_stopping_patience: int = 10
    minimum_improvement: float = 1e-4
    seed: int = 20220913
    device: str = "cpu"
    threshold: float = 0.5
    selection_metric: str = "validation_roc_auc"

    def __post_init__(self) -> None:
        if not self.architecture_name:
            raise ValueError("architecture name cannot be empty")
        if not self.hidden_dimensions or len(self.hidden_dimensions) > 2:
            raise ValueError("one or two hidden dimensions are required")
        if any(width <= 0 or width > 128 for width in self.hidden_dimensions):
            raise ValueError("hidden dimensions must be within [1, 128]")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be within [0, 1)")
        if self.learning_rate <= 0 or self.weight_decay < 0:
            raise ValueError("learning rate must be positive and weight decay nonnegative")
        if self.batch_size <= 0 or self.max_epochs <= 0:
            raise ValueError("batch size and max epochs must be positive")
        if self.early_stopping_patience <= 0 or self.minimum_improvement < 0:
            raise ValueError("early-stopping settings are invalid")
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda")
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be within (0, 1)")
        if self.selection_metric != "validation_roc_auc":
            raise ValueError("Phase 3E checkpoint selection requires validation ROC-AUC")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["hidden_dimensions"] = list(self.hidden_dimensions)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrainingConfig:
        expected = set(cls().to_dict())
        if set(value) != expected:
            raise ModelingDataError("training configuration schema mismatch")
        restored = dict(value)
        restored["hidden_dimensions"] = tuple(restored["hidden_dimensions"])
        return cls(**restored)


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used for configuration and run hashes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def config_checksum(config: TrainingConfig) -> str:
    return hashlib.sha256(canonical_json(config.to_dict()).encode()).hexdigest()


def derive_run_id(config: TrainingConfig, lineage: dict[str, str]) -> str:
    """Hash meaningful configuration, lineage, and schema inputs without timestamps."""
    required = {
        "target_version",
        "target_checksum",
        "feature_checksum",
        "feature_manifest_checksum",
        "preprocessor_checksum",
        "dataset_metadata_checksum",
        "split_checksum",
    }
    if set(lineage) != required:
        raise ModelingDataError("run lineage schema mismatch")
    identity = {
        "training_infrastructure_version": TRAINING_INFRASTRUCTURE_VERSION,
        "config": config.to_dict(),
        "lineage": lineage,
    }
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()
