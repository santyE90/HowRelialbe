"""FastAPI application factory for the frozen complaint-activity service."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, TextIO

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from howreliable.api.schemas import (
    CohortPage,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ModelMetadataResponse,
)
from howreliable.api.service import (
    SUPPORTED_COHORT_COUNT,
    ArtifactContractError,
    CohortNotFoundError,
    PredictionService,
)
from howreliable.cloud.deployment import DEPLOYMENT_CONTRACT_VERSION
from howreliable.common import configure_logging
from howreliable.config.settings import Settings
from howreliable.modeling.presentation import ComplaintActivityResult
from howreliable.modeling.registry import REGISTRY_CONTRACT_VERSION

API_VERSION: Final = "1.0.0"
DESCRIPTION: Final = (
    "Typed access to frozen make/model/model-year cohort predictions of accepted observed "
    "NHTSA complaint activity during the defined future window."
)
REQUEST_ID_HEADER: Final = "X-Request-ID"
REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def _request_id(value: str | None) -> str:
    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    value = getattr(route, "path", None)
    return value if isinstance(value, str) else "unmatched"


def create_app(
    *,
    root: Path | None = None,
    service: PredictionService | None = None,
    settings: Settings | None = None,
    log_stream: TextIO | None = None,
) -> FastAPI:
    """Create an application and initialize its validated service once."""
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings, stream=log_stream)
    logger = logging.getLogger("howreliable.api")
    resolved_root = root or Path.cwd()
    logger.info(
        "API service startup began",
        extra={
            "event": "service_starting",
            "api_version": API_VERSION,
            "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
            "artifact_backend": resolved_settings.artifact_backend,
            "bundle_id": resolved_settings.model_bundle_id,
        },
    )
    try:
        prediction_service = service or PredictionService.load(
            resolved_root, settings=resolved_settings
        )
    except ArtifactContractError:
        raise
    metadata = prediction_service.metadata()
    logger.info(
        "API service initialized; frozen model ready",
        extra={
            "event": "service_ready",
            "api_version": metadata.api_contract_version,
            "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
            "bundle_id": metadata.bundle_id,
            "registry_version": REGISTRY_CONTRACT_VERSION,
            "model_identifier": metadata.model_identifier,
            "supported_cohort_count": SUPPORTED_COHORT_COUNT,
        },
    )

    app = FastAPI(
        title="HowReliable? API",
        version=API_VERSION,
        description=DESCRIPTION,
    )
    app.state.prediction_service = prediction_service

    @app.middleware("http")
    async def request_logging(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            request.state.failure_logged = True
            logger.exception(
                "API request failed",
                extra={
                    "event": "request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": _route_template(request),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_category": type(error).__name__,
                },
            )
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        level = (
            logging.DEBUG
            if request.method == "GET" and request.url.path == "/health"
            else logging.INFO
        )
        logger.log(
            level,
            "API request completed",
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": _route_template(request),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return response

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
    def internal_error(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", _request_id(None))
        if not getattr(request.state, "failure_logged", False):
            logger.exception(
                "Unexpected API request failure",
                exc_info=error,
                extra={
                    "event": "request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": _route_template(request),
                    "status_code": 500,
                    "error_category": type(error).__name__,
                },
            )
        response = ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="The request could not be completed.",
            )
        )
        return JSONResponse(
            status_code=500,
            content=response.model_dump(mode="json"),
            headers={REQUEST_ID_HEADER: request_id},
        )

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
