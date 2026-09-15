"""Typed HTTP response contracts for the Phase 5A API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    service: Literal["howreliable"] = "howreliable"


class ModelMetadataResponse(APIModel):
    api_contract_version: str
    registry_contract_version: str
    bundle_id: str
    result_contract_version: str
    model_identifier: str
    model_status: Literal["PREFERRED"]
    prediction_grain: Literal["make_model_model_year_cohort"]
    target: Literal["future_12m_complaint_activity"]
    threshold: float = Field(ge=0.5, le=0.5)
    evaluation_version: str
    explainability_version: str
    supported_semantics: str
    general_limitation: str


class CohortSummary(APIModel):
    cohort_id: str
    make: str
    model: str
    model_year: int


class CohortPage(APIModel):
    items: tuple[CohortSummary, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ErrorDetail(APIModel):
    code: str
    message: str


class ErrorResponse(APIModel):
    error: ErrorDetail
