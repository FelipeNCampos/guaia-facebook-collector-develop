import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.schemas.request_models import (
    CreateRequestFailureResponse,
    CreateRequestPayload,
    RequestConflictResponse,
    RequestDetailResponse,
)
from app.services.health import run_health_checks
from app.services.metrics import render_metrics
from app.services.request_service import (
    RequestConflictError,
    RequestNotFoundError,
    RequestPublicationError,
    RequestRetryError,
    RequestService,
)
from app_shared.observability import log_event

router = APIRouter()


def get_request_service(request: Request) -> RequestService:
    return RequestService(request.app.state.settings)


def _normalize_create_payload(
    payload: CreateRequestPayload | list[CreateRequestPayload],
) -> CreateRequestPayload:
    if isinstance(payload, list):
        if len(payload) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="POST /requests accepts a single object or a one-item list",
            )
        return payload[0]
    return payload


@router.get("/health")
async def health(request: Request):
    health_report = await run_health_checks(request.app)
    status_code = 200 if health_report["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=health_report)


@router.get("/metrics")
async def metrics(request: Request):
    return await asyncio.to_thread(render_metrics, request.app)


@router.post(
    "/requests",
    response_model=RequestDetailResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": RequestConflictResponse},
        503: {"model": CreateRequestFailureResponse},
    },
)
async def create_request(payload: CreateRequestPayload | list[CreateRequestPayload], request: Request):
    service = get_request_service(request)
    logger = request.app.state.logger
    metrics = request.app.state.api_metrics
    client_ip = request.client.host if request.client else "unknown"
    normalized_payload = _normalize_create_payload(payload)
    request_data = normalized_payload.model_dump(mode="python", exclude_none=True)

    try:
        created_request = await asyncio.to_thread(service.create_request, client_ip, request_data)
    except RequestConflictError as exc:
        log_event(
            logger,
            "warning",
            "request_create_conflict",
            id_query=exc.id_query,
            client_ip=client_ip,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=RequestConflictResponse(
                detail=str(exc),
                id_query=exc.id_query,
            ).model_dump(mode="json"),
        )
    except RequestPublicationError as exc:
        metrics.requests_created_total.labels(status="5").inc()
        log_event(
            logger,
            "error",
            "request_create_failed",
            request_id=str(exc.metadata["request_id"]),
            id_query=exc.metadata["id_query"],
            status="5",
            client_ip=client_ip,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=CreateRequestFailureResponse(
                detail=str(exc),
                metadata=exc.metadata,
            ).model_dump(mode="json"),
        )

    created_status = str(created_request["metadata"]["status_current"])
    metrics.requests_created_total.labels(status=created_status).inc()
    log_event(
        logger,
        "info",
        "request_created",
        request_id=str(created_request["metadata"]["request_id"]),
        id_query=created_request["metadata"]["id_query"],
        user_id=str(created_request["metadata"]["user_id"]),
        status=created_status,
        client_ip=client_ip,
    )
    return RequestDetailResponse.model_validate(created_request)


@router.get("/requests/query/{id_query}", response_model=RequestDetailResponse)
async def get_request_by_query(id_query: int, request: Request):
    service = get_request_service(request)
    try:
        request_summary = await asyncio.to_thread(service.get_request_by_query, id_query)
    except RequestNotFoundError as exc:
        log_event(
            request.app.state.logger,
            "warning",
            "request_lookup_not_found",
            id_query=id_query,
            status="not_found",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    log_event(
        request.app.state.logger,
        "info",
        "request_lookup_succeeded",
        request_id=str(request_summary["metadata"]["request_id"]),
        id_query=request_summary["metadata"]["id_query"],
        user_id=str(request_summary["metadata"]["user_id"]),
        status=str(request_summary["metadata"]["status_current"]),
    )
    return RequestDetailResponse.model_validate(request_summary)


@router.post(
    "/requests/query/{id_query}/retry",
    response_model=RequestDetailResponse,
    responses={
        404: {"model": CreateRequestFailureResponse},
        409: {"model": RequestConflictResponse},
        503: {"model": CreateRequestFailureResponse},
    },
)
async def retry_failed_request_by_query(id_query: int, request: Request):
    service = get_request_service(request)
    try:
        request_summary = await asyncio.to_thread(service.retry_failed_request_by_query, id_query)
    except RequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RequestRetryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RequestPublicationError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=CreateRequestFailureResponse(
                detail=str(exc),
                metadata=exc.metadata,
            ).model_dump(mode="json"),
        )

    log_event(
        request.app.state.logger,
        "info",
        "request_retry_enqueued",
        request_id=str(request_summary["metadata"]["request_id"]),
        id_query=request_summary["metadata"]["id_query"],
        user_id=str(request_summary["metadata"]["user_id"]),
        status=str(request_summary["metadata"]["status_current"]),
    )
    return RequestDetailResponse.model_validate(request_summary)


@router.get("/requests/{request_id}", response_model=RequestDetailResponse)
async def get_request(request_id: UUID, request: Request):
    service = get_request_service(request)
    try:
        request_summary = await asyncio.to_thread(service.get_request, request_id)
    except RequestNotFoundError as exc:
        log_event(
            request.app.state.logger,
            "warning",
            "request_lookup_not_found",
            request_id=str(request_id),
            status="not_found",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    log_event(
        request.app.state.logger,
        "info",
        "request_lookup_succeeded",
        request_id=str(request_summary["metadata"]["request_id"]),
        id_query=request_summary["metadata"]["id_query"],
        user_id=str(request_summary["metadata"]["user_id"]),
        status=str(request_summary["metadata"]["status_current"]),
    )
    return RequestDetailResponse.model_validate(request_summary)
