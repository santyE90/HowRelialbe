"""Canonical domain models shared across HowReliable?."""

from howreliable.domain.reliability import (
    Component,
    EventType,
    ReliabilityEvent,
    Severity,
    SeverityEvidence,
    SourceReference,
    SourceType,
    VehicleAssociation,
    derive_severity,
)
from howreliable.domain.vehicle import Drivetrain, Transmission, Vehicle

__all__ = [
    "Component",
    "Drivetrain",
    "EventType",
    "ReliabilityEvent",
    "Severity",
    "SeverityEvidence",
    "SourceReference",
    "SourceType",
    "Transmission",
    "Vehicle",
    "VehicleAssociation",
    "derive_severity",
]
