"""FastAPI application factory for the frozen complaint-activity service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from howreliable.api.schemas import (
    CohortPage,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ModelMetadataResponse,
)
from howreliable.api.service import (
    ArtifactContractError,
    CohortNotFoundError,
    PredictionService,
)
from howreliable.modeling.presentation import ComplaintActivityResult

API_VERSION: Final = "1.0.0"
DESCRIPTION: Final = (
    "Typed access to frozen make/model/model-year cohort predictions of accepted observed "
    "NHTSA complaint activity during the defined future window."
)


def create_app(*, root: Path | None = None, service: PredictionService | None = None) -> FastAPI:
    """Create an application and initialize its validated service once."""
    logger = logging.getLogger("howreliable.api")
    resolved_root = root or Path.cwd()
    try:
        prediction_service = service or PredictionService.load(resolved_root)
    except ArtifactContractError:
        logger.exception("API initialization failed artifact contract validation")
        raise
    logger.info("API service initialized; frozen model ready")

    app = FastAPI(
        title="HowReliable? API",
        version=API_VERSION,
        description=DESCRIPTION,
    )
    app.state.prediction_service = prediction_service

    @app.exception_handler(CohortNotFoundError)
    def cohort_not_found(_request: Request, _error: CohortNotFoundError) -> JSONResponse:
        response = ErrorResponse(
            error=ErrorDetail(
                code="COHORT_NOT_FOUND",
                message="The requested cohort is not supported by the frozen model.",
            )
        )
        return JSONResponse(status_code=404, content=response.model_dump(mode="json"))

    @app.exception_handler(Exception)
    def internal_error(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unexpected API request failure", exc_info=error)
        response = ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="The request could not be completed.",
            )
        )
        return JSONResponse(status_code=500, content=response.model_dump(mode="json"))

    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Process health",
        description="Reports process liveness without loading or invoking inference resources.",
    )
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/api/v1/model",
        response_model=ModelMetadataResponse,
        summary="Frozen model contract",
        description="Returns safe metadata for the cohort complaint-activity model.",
    )
    def model_metadata() -> ModelMetadataResponse:
        return prediction_service.metadata()

    @app.get(
        "/api/v1/cohorts",
        response_model=CohortPage,
        summary="List supported cohorts",
        description=("Lists frozen supported cohorts in make, model, model-year, cohort-ID order."),
    )
    def list_cohorts(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> CohortPage:
        return prediction_service.list_cohorts(limit=limit, offset=offset)

    @app.get(
        "/api/v1/cohorts/{cohort_id}",
        response_model=ComplaintActivityResult,
        response_model_exclude_none=True,
        responses={404: {"model": ErrorResponse}},
        summary="Get cohort complaint-activity prediction",
        description=(
            "Returns the canonical complaint-activity result for one existing frozen cohort; "
            "future observed evaluation targets are excluded."
        ),
    )
    def cohort_prediction(cohort_id: str) -> ComplaintActivityResult:
        return prediction_service.predict(cohort_id)

    return app
