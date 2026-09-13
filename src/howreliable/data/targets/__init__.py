"""Phase 3A future complaint-activity target definition."""

from howreliable.data.targets.future_complaints import (
    TARGET_DEFINITION_VERSION,
    TARGET_SCHEMA,
    TargetDefinitionError,
    TargetGenerationResult,
    generate_future_complaint_targets,
)

__all__ = [
    "TARGET_DEFINITION_VERSION",
    "TARGET_SCHEMA",
    "TargetDefinitionError",
    "TargetGenerationResult",
    "generate_future_complaint_targets",
]
