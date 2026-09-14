"""The first intentionally small neural network for HowReliable?."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast

import torch
from torch import nn


@dataclass(frozen=True)
class MLPConfig:
    """A compact, serializable MLP architecture and optimizer configuration."""

    name: str
    hidden_dimensions: tuple[int, ...]
    dropout: float
    learning_rate: float
    weight_decay: float = 1e-4

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["hidden_dimensions"] = list(self.hidden_dimensions)
        return value


class HowReliableMLP(nn.Module):
    """Return one raw complaint-activity logit for each cohort row."""

    def __init__(
        self,
        input_dimension: int,
        hidden_dimensions: tuple[int, ...],
        dropout: float,
    ) -> None:
        super().__init__()
        if input_dimension <= 0:
            raise ValueError("input dimension must be positive")
        if not hidden_dimensions or len(hidden_dimensions) > 2:
            raise ValueError("the first MLP requires one or two hidden layers")
        if any(width <= 0 or width > 128 for width in hidden_dimensions):
            raise ValueError("hidden dimensions must be within [1, 128]")
        if not 0 <= dropout < 1:
            raise ValueError("dropout must be within [0, 1)")
        layers: list[nn.Module] = []
        previous = input_dimension
        for width in hidden_dimensions:
            layers.extend((nn.Linear(previous, width), nn.ReLU(), nn.Dropout(dropout)))
            previous = width
        layers.append(nn.Linear(previous, 1))
        self.network = nn.Sequential(*layers)
        self.input_dimension = input_dimension
        self.hidden_dimensions = hidden_dimensions
        self.dropout = dropout

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        """Produce raw logits; BCEWithLogitsLoss supplies the stable sigmoid internally."""
        if features.ndim != 2 or features.shape[1] != self.input_dimension:
            raise ValueError(
                f"expected [batch, {self.input_dimension}] features, got {list(features.shape)}"
            )
        return cast(torch.Tensor, self.network(features))

    @property
    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())
