"""Explicit source-to-domain mapping boundaries."""

from howreliable.data.mapping.nhtsa_reliability import (
    EventMappingError,
    MappingFailureReason,
    NhtsaSourceContext,
    map_nhtsa_complaint,
    map_nhtsa_component,
)

__all__ = [
    "EventMappingError",
    "MappingFailureReason",
    "NhtsaSourceContext",
    "map_nhtsa_complaint",
    "map_nhtsa_component",
]

