"""Environment-backed settings for the project foundation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

DEFAULT_ENVIRONMENT: Final = "development"
DEFAULT_LOG_LEVEL: Final = "INFO"
VALID_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime settings with deterministic defaults."""

    environment: str = DEFAULT_ENVIRONMENT
    log_level: str = DEFAULT_LOG_LEVEL

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Create settings from a supplied mapping or the process environment."""
        values = os.environ if environ is None else environ
        environment = values.get("HOWRELIABLE_ENVIRONMENT", DEFAULT_ENVIRONMENT).strip()
        log_level = values.get("HOWRELIABLE_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()

        if not environment:
            raise ValueError("HOWRELIABLE_ENVIRONMENT must not be empty")
        if log_level not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"HOWRELIABLE_LOG_LEVEL must be one of: {allowed}")

        return cls(environment=environment, log_level=log_level)

