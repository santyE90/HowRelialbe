"""Canonical vehicle identity model."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

MIN_MODEL_YEAR: Final = 1886
MAX_MODEL_YEAR: Final = 2100


def _normalize_text(value: str) -> str:
    """Normalize descriptive identity text without fuzzy matching."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _normalize_alias(value: str) -> str:
    """Normalize separators used by known categorical aliases."""
    return " ".join(_normalize_text(value).replace("-", " ").replace("_", " ").split())


class Transmission(StrEnum):
    """Supported high-level transmission categories."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    CVT = "cvt"
    DCT = "dct"
    OTHER = "other"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> Transmission:
        """Resolve a supported alias without guessing unfamiliar marketing names."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("transmission must be a string or Transmission value")

        aliases = {
            "automatic": cls.AUTOMATIC,
            "auto": cls.AUTOMATIC,
            "manual": cls.MANUAL,
            "cvt": cls.CVT,
            "continuously variable transmission": cls.CVT,
            "dct": cls.DCT,
            "dual clutch": cls.DCT,
            "dual clutch transmission": cls.DCT,
            "other": cls.OTHER,
            "unknown": cls.UNKNOWN,
        }
        normalized = _normalize_alias(value)
        try:
            return aliases[normalized]
        except KeyError as error:
            raise ValueError(f"unsupported transmission value: {value!r}") from error


class Drivetrain(StrEnum):
    """Supported high-level drivetrain categories."""

    FWD = "fwd"
    RWD = "rwd"
    AWD = "awd"
    FOUR_WD = "four_wd"
    OTHER = "other"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> Drivetrain:
        """Resolve a supported alias without inferring an unknown drivetrain."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("drivetrain must be a string or Drivetrain value")

        aliases = {
            "fwd": cls.FWD,
            "front wheel drive": cls.FWD,
            "rwd": cls.RWD,
            "rear wheel drive": cls.RWD,
            "awd": cls.AWD,
            "all wheel drive": cls.AWD,
            "4wd": cls.FOUR_WD,
            "4x4": cls.FOUR_WD,
            "four wheel drive": cls.FOUR_WD,
            "other": cls.OTHER,
            "unknown": cls.UNKNOWN,
        }
        normalized = _normalize_alias(value)
        try:
            return aliases[normalized]
        except KeyError as error:
            raise ValueError(f"unsupported drivetrain value: {value!r}") from error


class Vehicle(BaseModel):
    """Immutable, normalized representation of a known vehicle configuration."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    make: str
    model: str
    year: int = Field(ge=MIN_MODEL_YEAR, le=MAX_MODEL_YEAR)
    generation: str | None = None
    trim: str | None = None
    engine: str | None = None
    transmission: Transmission
    drivetrain: Drivetrain

    @field_validator("make", "model", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        """Normalize required identity fields and reject missing text."""
        if not isinstance(value, str):
            raise ValueError("value must be text")
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("generation", "trim", "engine", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        """Normalize optional identity text while preserving explicit absence."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("value must be text or None")
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("optional text must be None rather than empty")
        return normalized

    @field_validator("transmission", mode="before")
    @classmethod
    def normalize_transmission(cls, value: object) -> Transmission:
        return Transmission.from_value(value)

    @field_validator("drivetrain", mode="before")
    @classmethod
    def normalize_drivetrain(cls, value: object) -> Drivetrain:
        return Drivetrain.from_value(value)

    @property
    def broad_identity_key(self) -> str:
        """Return canonical JSON for make/model/year/generation identity."""
        return self._canonical_json(("make", "model", "year", "generation"))

    @property
    def configuration_key(self) -> str:
        """Return canonical JSON containing every configuration identity field."""
        return self._canonical_json(tuple(type(self).model_fields))

    @property
    def broad_identity_id(self) -> str:
        """Return a stable digest for the broad vehicle identity."""
        return f"vehicle_{self._digest(self.broad_identity_key)}"

    @property
    def configuration_id(self) -> str:
        """Return a stable digest for the complete known configuration."""
        return f"configuration_{self._digest(self.configuration_key)}"

    def _canonical_json(self, fields: tuple[str, ...]) -> str:
        values = self.model_dump(mode="json", include=set(fields))
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
