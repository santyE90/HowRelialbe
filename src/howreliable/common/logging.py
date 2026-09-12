"""Reusable standard-library logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import Final, TextIO

from howreliable.config import Settings

LOGGER_NAME: Final = "howreliable"
LOG_FORMAT: Final = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(
    settings: Settings | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the project logger without modifying the root logger."""
    resolved_settings = settings or Settings.from_env()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_settings.log_level)
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    logger.handlers.clear()
    logger.addHandler(handler)
    return logger

