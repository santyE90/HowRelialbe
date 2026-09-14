"""Portable deterministic DataLoaders for Phase 3C datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import torch
from torch.utils.data import DataLoader

from howreliable.modeling.pytorch.dataset import HowReliableCohortDataset

DEFAULT_BATCH_SIZE: Final = 64
DEFAULT_LOADER_SEED: Final = 20220913
DEFAULT_NUM_WORKERS: Final = 0


@dataclass(frozen=True)
class CohortDataLoaders:
    """The three loaders whose cohorts remain separated by the frozen split."""

    train: DataLoader[dict[str, Any]]
    validation: DataLoader[dict[str, Any]]
    test: DataLoader[dict[str, Any]]


def create_dataloaders(
    datasets: dict[str, HowReliableCohortDataset],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    seed: int = DEFAULT_LOADER_SEED,
    num_workers: int = DEFAULT_NUM_WORKERS,
) -> CohortDataLoaders:
    """Create shuffled train and stable validation/test loaders."""
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    if num_workers != 0:
        raise ValueError("Phase 3C requires num_workers=0 for portable determinism")
    generator = torch.Generator()
    generator.manual_seed(seed)
    return CohortDataLoaders(
        train=DataLoader(
            datasets["TRAIN"],
            batch_size=batch_size,
            shuffle=True,
            generator=generator,
            num_workers=num_workers,
            drop_last=False,
        ),
        validation=DataLoader(
            datasets["VALIDATION"],
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            drop_last=False,
        ),
        test=DataLoader(
            datasets["TEST"],
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            drop_last=False,
        ),
    )
