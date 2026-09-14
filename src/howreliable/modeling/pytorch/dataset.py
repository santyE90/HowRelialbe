"""PyTorch Dataset for preprocessed make/model/model-year cohorts."""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
from numpy.typing import NDArray
from torch.utils.data import Dataset

from howreliable.modeling.features import ModelingDataError


class HowReliableCohortDataset(Dataset[dict[str, Any]]):
    """Map a cohort index to precomputed features, target, identity, and diagnostics."""

    def __init__(
        self,
        features: NDArray[np.float32],
        targets: NDArray[np.float32],
        cohort_ids: list[str],
        metadata: list[dict[str, Any]],
    ) -> None:
        if features.ndim != 2:
            raise ModelingDataError("dataset features must be a two-dimensional matrix")
        if targets.ndim != 2 or targets.shape[1] != 1:
            raise ModelingDataError("dataset targets must have shape [rows, 1]")
        if not (len(features) == len(targets) == len(cohort_ids) == len(metadata)):
            raise ModelingDataError("dataset inputs have inconsistent row counts")
        if features.dtype != np.float32 or targets.dtype != np.float32:
            raise ModelingDataError("dataset arrays must use float32")
        if not np.all(np.isfinite(features)) or not np.all(np.isfinite(targets)):
            raise ModelingDataError("dataset tensors cannot contain non-finite values")
        self.features = torch.from_numpy(features.copy())
        self.targets = torch.from_numpy(targets.copy())
        self.cohort_ids = tuple(cohort_ids)
        self.metadata = tuple(dict(item) for item in metadata)

    def __len__(self) -> int:
        return len(self.cohort_ids)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return {
            "features": self.features[index],
            "target": self.targets[index],
            "cohort_id": self.cohort_ids[index],
            "metadata": self.metadata[index],
        }
