from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation

from app.schemas.request_models import RequestDataPayload
from app.services.request_repository import RequestRepository
from app_shared.config import Settings
from app_shared.request_status import (
    FACEBOOK_QUERY_SOURCE,
    FACEBOOK_SOURCE_TYPE,
    RequestStatusCode,
)


class RequestPublicationError(Exception):
    def __init__(self, metadata: dict[str, Any]):
        self.metadata = metadata
        super().__init__("Falha ao preparar request para processamento")


class RequestNotFoundError(Exception):
    pass


class RequestConflictError(Exception):
    def __init__(self, id_query: int):
        self.id_query = id_query
        super().__init__(f"id_query {id_query} already exists")


class RequestRetryError(Exception):
    def __init__(self, id_query: int, message: str):
        self.id_query = id_query
        super().__init__(message)


class RequestService:
    def __init__(
        self,
        settings: Settings,
        repository: RequestRepository | None = None,
    ):
        self.settings = settings
        self.repository = repository or RequestRepository(settings.db_dsn)

    def create_request(self, client_ip: str, request_data: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(uuid4())
        persistence_payload = {"source": "api", "trace_id": trace_id}

        try:
            user_row, request_row = self.repository.create_request_for_ip(
                ip_request=client_ip,
                id_query=request_data["id_query"],
                subject=request_data["subject"],
                start_date=request_data.get("start_date"),
                end_date=request_data.get("end_date"),
                status_current=RequestStatusCode.PENDING,
                payload=persistence_payload,
            )
        except UniqueViolation as exc:
            raise RequestConflictError(request_data["id_query"]) from exc

        queued_row = self.repository.update_request_status(
            request_row["id"],
            RequestStatusCode.PENDING,
            legacy_status="queued",
        )
        return {
            "metadata": self._build_metadata(user_row, queued_row),
            "data": RequestDataPayload().model_dump(mode="json"),
        }

    def get_request(self, request_id: UUID) -> dict[str, Any]:
        request_row = self.repository.get_request_metadata(request_id)
        if request_row is None:
            raise RequestNotFoundError(f"Request {request_id} not found")
        return self._build_request_response(request_row)

    def get_request_by_query(self, id_query: int) -> dict[str, Any]:
        request_row = self.repository.get_request_metadata_by_query(id_query)
        if request_row is None:
            raise RequestNotFoundError(f"Request with id_query {id_query} not found")
        return self._build_request_response(request_row)

    def retry_failed_request_by_query(self, id_query: int) -> dict[str, Any]:
        request_row = self.repository.requeue_failed_request_by_query(
            id_query,
            max_retries=self.settings.message_retry_max_attempts,
        )
        if request_row is None:
            raise RequestNotFoundError(f"Request with id_query {id_query} not found")

        if int(request_row["status_current"]) != int(RequestStatusCode.PENDING):
            retry_count = 0
            payload = request_row.get("payload")
            if isinstance(payload, dict):
                retry_count = int(payload.get("request_retry_attempts") or 0)
            if int(request_row["status_current"]) != int(RequestStatusCode.FAILED):
                raise RequestRetryError(id_query, f"id_query {id_query} is not failed")
            if retry_count >= self.settings.message_retry_max_attempts:
                raise RequestRetryError(id_query, f"id_query {id_query} reached retry limit")

        return self.get_request_by_query(id_query)

    def _build_request_response(self, request_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "metadata": {
                **request_row,
                "status_current": RequestStatusCode(int(request_row["status_current"])),
            },
            "data": self.repository.get_request_data(request_row["request_id"]),
        }

    def _build_metadata(self, user_row: dict[str, Any], request_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": request_row["id"],
            "id_query": request_row["id_query"],
            "user_id": request_row["user_id"],
            "ip_request": user_row["ip_request"],
            "numero_requests": user_row["numero_requests"],
            "subject": request_row["subject"],
            "query_source": request_row.get("query_source", FACEBOOK_QUERY_SOURCE),
            "source_type": request_row.get("source_type", FACEBOOK_SOURCE_TYPE),
            "status_current": RequestStatusCode(int(request_row["status_current"])),
            "created_at": request_row["created_at"],
            "updated_at": request_row["updated_at"],
            "start_date": request_row.get("start_date"),
            "end_date": request_row.get("end_date"),
        }

    def _build_request_message(
        self,
        *,
        request_id: UUID,
        id_query: int,
        user_id: UUID,
        subject: str,
        start_date,
        end_date,
        status_current: RequestStatusCode,
        trace_id: str,
    ) -> dict[str, Any]:
        return {
            "request_id": str(request_id),
            "id_query": id_query,
            "user_id": str(user_id),
            "subject": subject,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "status_current": int(status_current),
            "query_source": FACEBOOK_QUERY_SOURCE,
            "source_type": FACEBOOK_SOURCE_TYPE,
            "metadata": {
                "source": "api",
                "trace_id": trace_id,
            },
        }
