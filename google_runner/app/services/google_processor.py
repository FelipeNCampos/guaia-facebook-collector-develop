from datetime import datetime
from uuid import UUID

from app.services.captcha import CaptchaResolver
from app.services.discovery import LEGACY_CATEGORY_TO_TYPE, GoogleCaptchaDetectedError, SeleniumGoogleSearchClient
from app.services.google_repository import GoogleRunnerRepository
from app_shared.config import Settings
from app_shared.observability import log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError
from app_shared.request_status import FACEBOOK_QUERY_SOURCE, FACEBOOK_SOURCE_TYPE, RequestStatusCode

DOWNSTREAM_QUEUES = {
    "perfil": "facebook.perfil",
    "video": "facebook.video",
    "reel": "facebook.reel",
    "post": "facebook.post",
    "storie": "facebook.storie",
    "foto": "facebook.foto",
    "permalink": "facebook.permalink",
}


class GoogleRunnerProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: GoogleRunnerRepository | None = None,
        search_client: SeleniumGoogleSearchClient | None = None,
        downstream_publishers: dict[str, object] | None = None,
        logger=None,
    ):
        self.settings = settings
        self.repository = repository or GoogleRunnerRepository(settings.db_dsn)
        self.captcha_resolver = CaptchaResolver(settings)
        self.search_client = search_client or SeleniumGoogleSearchClient(
            settings,
            captcha_resolver=self.captcha_resolver,
            logger=logger,
        )
        if downstream_publishers is None:
            from app.services.messaging import RabbitMQPublisher

            self.downstream_publishers = {
                queue_name: RabbitMQPublisher(settings.amqp_url, queue_name)
                for queue_name in DOWNSTREAM_QUEUES.values()
            }
        else:
            self.downstream_publishers = downstream_publishers
        self.logger = logger

    def process_message(
        self,
        message: dict,
        *,
        attempt: int = 0,
        max_attempts: int = 3,
        mark_processing: bool = True,
    ) -> None:
        request_id, id_query, user_id, subject, start_date, end_date, trace_id = self._validate_message(message)

        if mark_processing:
            self.repository.update_request_status(request_id, RequestStatusCode.PROCESSING, error=None)
            if self.logger:
                log_event(
                    self.logger,
                    "info",
                    "google_request_marked_processing",
                    request_id=str(request_id),
                    id_query=id_query,
                    user_id=str(user_id),
                    trace_id=trace_id,
                    status=str(int(RequestStatusCode.PROCESSING)),
                    attempt=attempt,
                )

        if self.logger:
            log_event(
                self.logger,
                "info",
                "google_search_started",
                request_id=str(request_id),
                id_query=id_query,
                user_id=str(user_id),
                trace_id=trace_id,
                status=str(int(RequestStatusCode.PROCESSING)),
                attempt=attempt,
                subject=subject,
                start_date=start_date,
                end_date=end_date,
            )

        try:
            search_result = self.search_client.collect_results(
                subject,
                after=self._format_query_date(start_date),
                before=self._format_query_date(end_date),
                request_id=str(request_id),
                id_query=id_query,
                user_id=str(user_id),
                trace_id=trace_id,
                attempt=attempt,
            )
        except GoogleCaptchaDetectedError as exc:
            if attempt >= max_attempts:
                self.repository.update_request_status(
                    request_id,
                    RequestStatusCode.COMPLETED,
                    error="google_captcha_detected",
                    legacy_status="finished",
                )
                if self.logger:
                    log_event(
                        self.logger,
                        "error",
                        "google_search_captcha_failed",
                        request_id=str(request_id),
                        id_query=id_query,
                        user_id=str(user_id),
                        trace_id=trace_id,
                        status=str(int(RequestStatusCode.COMPLETED)),
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error_type="google_captcha_detected",
                        error="google_captcha_detected",
                    )
                raise TerminalProcessingError("google_captcha_detected", "google_captcha_detected") from exc

            self.repository.update_request_status(
                request_id,
                RequestStatusCode.PROCESSING,
                error="google_captcha_detected_retrying",
            )
            if self.logger:
                log_event(
                    self.logger,
                    "warning",
                    "google_search_captcha_retry_scheduled",
                    request_id=str(request_id),
                    id_query=id_query,
                    user_id=str(user_id),
                    trace_id=trace_id,
                    status=str(int(RequestStatusCode.PROCESSING)),
                    attempt=attempt,
                    max_attempts=max_attempts,
                    next_attempt=attempt + 1,
                    error_type="google_captcha_detected",
                    error="google_captcha_detected",
                )
            raise RetryableProcessingError("google_captcha_detected", "google_captcha_detected") from exc
        except Exception as exc:
            if self.logger:
                log_event(
                    self.logger,
                    "warning",
                    "google_search_failed",
                    request_id=str(request_id),
                    id_query=id_query,
                    user_id=str(user_id),
                    trace_id=trace_id,
                    status=str(int(RequestStatusCode.PROCESSING)),
                    attempt=attempt,
                    error_type="processing_error",
                    error=str(exc),
                )
            if attempt >= max_attempts:
                self.repository.update_request_status(
                    request_id,
                    RequestStatusCode.COMPLETED,
                    error=str(exc),
                    legacy_status="finished",
                )
                raise TerminalProcessingError("processing_error", str(exc)) from exc
            raise RetryableProcessingError("processing_error", str(exc)) from exc

        if not search_result.records:
            self.repository.update_request_status(request_id, RequestStatusCode.COMPLETED, error=None)
            if self.logger:
                log_event(
                    self.logger,
                    "info",
                    "google_search_finished_without_results",
                    request_id=str(request_id),
                    id_query=id_query,
                    user_id=str(user_id),
                    trace_id=trace_id,
                    status=str(int(RequestStatusCode.COMPLETED)),
                    attempt=attempt,
                    links_found=0,
                    pages_visited=search_result.pages_visited,
                )
            return

        try:
            for record in search_result.records:
                normalized_type = LEGACY_CATEGORY_TO_TYPE[record["categoria"]]
                self.repository.upsert_request_link(
                    request_id=request_id,
                    tipo=normalized_type,
                    fonte=record["fonte"],
                    perfil_slug=record["perfil"],
                    status="queued",
                    error="",
                )

                downstream_message = {
                    "request_id": str(request_id),
                    "id_query": id_query,
                    "user_id": str(user_id),
                    "tipo": normalized_type,
                    "fonte": record["fonte"],
                    "perfil": record["perfil"],
                    "metadata": {
                        "origem": "google_runner",
                        "trace_id": trace_id,
                        "categoria_original": record["categoria"],
                    },
                }
                queue_name = DOWNSTREAM_QUEUES[normalized_type]
                self.downstream_publishers[queue_name].publish(downstream_message)
        except Exception as exc:
            error_message = str(exc)
            if attempt >= max_attempts:
                self.repository.update_request_status(
                    request_id,
                    RequestStatusCode.COMPLETED,
                    error="downstream_publication_failed",
                    legacy_status="finished",
                )
                raise TerminalProcessingError("downstream_publication_failed", error_message) from exc
            raise RetryableProcessingError("downstream_publication_failed", error_message) from exc

        self.repository.update_request_status(
            request_id,
            RequestStatusCode.PROCESSING,
            error=None,
        )
        if self.logger:
            log_event(
                self.logger,
                "info",
                "google_request_dispatched_to_facebook_runners",
                request_id=str(request_id),
                id_query=id_query,
                user_id=str(user_id),
                trace_id=trace_id,
                status=str(int(RequestStatusCode.PROCESSING)),
                attempt=attempt,
                links_found=len(search_result.records),
                pages_visited=search_result.pages_visited,
            )

    def _validate_message(self, message: dict) -> tuple[UUID, int, UUID, str, str | None, str | None, str]:
        required_fields = (
            "request_id",
            "id_query",
            "user_id",
            "subject",
            "status_current",
            "query_source",
            "source_type",
            "metadata",
        )
        missing_fields = [field_name for field_name in required_fields if field_name not in message]
        if missing_fields:
            raise TerminalProcessingError("invalid_payload", f"Missing required fields: {', '.join(missing_fields)}")

        try:
            metadata = message.get("metadata") or {}
            subject = str(message["subject"]).strip()
            if not subject:
                raise ValueError("subject must not be blank")
            if int(message["query_source"]) != FACEBOOK_QUERY_SOURCE:
                raise ValueError("query_source must be 5 for Facebook")
            if int(message["source_type"]) != FACEBOOK_SOURCE_TYPE:
                raise ValueError("source_type must be 3 for Facebook")
            if int(message["status_current"]) != int(RequestStatusCode.PENDING):
                raise ValueError("status_current must be 1 before Google processing starts")

            return (
                UUID(message["request_id"]),
                int(message["id_query"]),
                UUID(message["user_id"]),
                subject,
                message.get("start_date"),
                message.get("end_date"),
                str(metadata.get("trace_id", "")),
            )
        except Exception as exc:
            raise TerminalProcessingError("invalid_payload", str(exc)) from exc

    def _format_query_date(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return value
