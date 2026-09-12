"""Foundation-level behavior tests."""

from __future__ import annotations

import io
import logging

import howreliable
from howreliable.common import configure_logging
from howreliable.config import Settings


def test_package_imports() -> None:
    assert howreliable.__version__ == "0.1.0"


def test_settings_have_deterministic_defaults() -> None:
    assert Settings.from_env({}) == Settings(environment="development", log_level="INFO")


def test_settings_read_supplied_environment() -> None:
    settings = Settings.from_env(
        {"HOWRELIABLE_ENVIRONMENT": "test", "HOWRELIABLE_LOG_LEVEL": "debug"}
    )

    assert settings == Settings(environment="test", log_level="DEBUG")


def test_logging_initializes_with_expected_format() -> None:
    output = io.StringIO()
    logger = configure_logging(Settings(environment="test", log_level="WARNING"), stream=output)

    logger.warning("foundation ready")

    assert logger.name == "howreliable"
    assert logger.level == logging.WARNING
    assert logger.propagate is False
    assert "| WARNING | howreliable | foundation ready" in output.getvalue()

