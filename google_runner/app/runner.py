import json
from datetime import datetime, timezone
from uuid import UUID

import pika

from app.services.google_processor import GoogleRunnerProcessor
from app.services.messaging import declare_durable_queue, ensure_runner_topology
from app_shared.amqp import build_consumer_connection_parameters, get_retry_attempt, publish_to_retry_or_dlq
from app_shared.config import get_settings
from app_shared.health import RunnerRuntimeState, create_runner_monitoring_app, start_monitoring_server
from app_shared.metrics import RunnerMetrics
from app_shared.observability import configure_logging, log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError


class GoogleRunner:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.logger = configure_logging("google_runner", self.settings.log_level)
        self.metrics = RunnerMetrics()
        self.runtime_state = RunnerRuntimeState("google_runner", self.settings.google_runner_queue)
        self.processor = GoogleRunnerProcessor(self.settings, logger=self.logger)
        monitoring_app = create_runner_monitoring_app(
            settings=self.settings,
            logger=self.logger,
            runtime_state=self.runtime_state,
            metrics_registry=self.metrics.registry,
        )
        start_monitoring_server(monitoring_app, self.settings, self.logger)

    def run_forever(self) -> None:
        ensure_runner_topology(self.settings.amqp_url, self.settings)
        connection = pika.BlockingConnection(build_consumer_connection_parameters(self.settings.amqp_url))
        channel = connection.channel()
        channel.basic_qos(prefetch_count=1)

        declare_durable_queue(channel, self.settings.google_runner_queue)
        for queue_name in self.processor.downstream_publishers:
            declare_durable_queue(channel, queue_name)

        channel.basic_consume(
            queue=self.settings.google_runner_queue,
            on_message_callback=self._handle_message,
        )
        self.runtime_state.mark_consumer_started()
        log_event(
            self.logger,
            "info",
            "google_runner_started",
            queue_name=self.settings.google_runner_queue,
            service_http_port=self.settings.service_http_port,
        )
        channel.start_consuming()

    def _handle_message(self, channel, method, properties, body) -> None:
        started_at = datetime.now(timezone.utc)
        current_attempt = get_retry_attempt(properties)
        payload = {}
        request_id = ""
        id_query = ""
        user_id = ""
        trace_id = ""
        parsed_request_id = None
        self.runtime_state.mark_message_received(started_at.isoformat())
        self.metrics.messages_received_total.labels(
            service="google_runner",
            queue=self.settings.google_runner_queue,
        ).inc()

        try:
            payload = json.loads(body.decode("utf-8"))
            request_id = payload.get("request_id", "")
            id_query = payload.get("id_query", "")
            user_id = payload.get("user_id", "")
            trace_id = (payload.get("metadata") or {}).get("trace_id", "")

            if request_id:
                try:
                    parsed_request_id = UUID(request_id)
                except (TypeError, ValueError):
                    parsed_request_id = None

            if parsed_request_id is not None:
                self.processor.repository.update_request_status(parsed_request_id, 2, error=None)
                log_event(
                    self.logger,
                    "info",
                    "google_message_received",
                    queue_name=self.settings.google_runner_queue,
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=current_attempt,
                    status="2",
                )

            with self.metrics.message_processing_seconds.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
            ).time():
                self.processor.process_message(
                    payload,
                    attempt=current_attempt,
                    max_attempts=self.settings.message_retry_max_attempts,
                    mark_processing=False,
                )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            finished_at = datetime.now(timezone.utc)
            self.runtime_state.mark_success(finished_at.isoformat())
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                outcome="success",
            ).inc()
            self.metrics.last_success_unixtime.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
            ).set(finished_at.timestamp())
            log_event(
                self.logger,
                "info",
                "google_message_processed",
                queue_name=self.settings.google_runner_queue,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=current_attempt,
                status="success",
                duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            )
        except RetryableProcessingError as exc:
            routing_outcome = publish_to_retry_or_dlq(
                channel,
                queue_name=self.settings.google_runner_queue,
                body=body,
                properties=properties,
                error_type=exc.error_type,
                error_message=exc.error_message,
                retryable=True,
                max_attempts=self.settings.message_retry_max_attempts,
                base_delay_ms=self.settings.message_retry_base_delay_ms,
            )
            if routing_outcome == "dead_letter" and parsed_request_id is not None:
                self._safe_update_request_status(
                    parsed_request_id,
                    5,
                    error=exc.error_message,
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=current_attempt,
                )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                outcome=routing_outcome,
            ).inc()
            self.metrics.processing_failures_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                error_type=exc.error_type,
            ).inc()
            log_event(
                self.logger,
                "warning",
                "google_message_routed_after_retryable_error",
                queue_name=self.settings.google_runner_queue,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=current_attempt,
                outcome=routing_outcome,
                error_type=exc.error_type,
                error=exc.error_message,
            )
        except TerminalProcessingError as exc:
            if parsed_request_id is not None:
                self._safe_update_request_status(
                    parsed_request_id,
                    5,
                    error=exc.error_message,
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=current_attempt,
                )
            publish_to_retry_or_dlq(
                channel,
                queue_name=self.settings.google_runner_queue,
                body=body,
                properties=properties,
                error_type=exc.error_type,
                error_message=exc.error_message,
                retryable=False,
                max_attempts=self.settings.message_retry_max_attempts,
                base_delay_ms=self.settings.message_retry_base_delay_ms,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                outcome="dead_letter",
            ).inc()
            self.metrics.processing_failures_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                error_type=exc.error_type,
            ).inc()
            if exc.error_type == "google_captcha_detected":
                self.metrics.google_captcha_total.inc()
            log_event(
                self.logger,
                "error",
                "google_message_sent_to_dead_letter",
                queue_name=self.settings.google_runner_queue,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=current_attempt,
                outcome="dead_letter",
                error_type=exc.error_type,
                error=exc.error_message,
            )
        except Exception as exc:  # pragma: no cover - runtime defensive path
            routing_outcome = publish_to_retry_or_dlq(
                channel,
                queue_name=self.settings.google_runner_queue,
                body=body,
                properties=properties,
                error_type="unexpected_error",
                error_message=str(exc),
                retryable=True,
                max_attempts=self.settings.message_retry_max_attempts,
                base_delay_ms=self.settings.message_retry_base_delay_ms,
            )
            if routing_outcome == "dead_letter" and parsed_request_id is not None:
                self._safe_update_request_status(
                    parsed_request_id,
                    5,
                    error=str(exc),
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=current_attempt,
                )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                outcome=routing_outcome,
            ).inc()
            self.metrics.processing_failures_total.labels(
                service="google_runner",
                queue=self.settings.google_runner_queue,
                error_type="unexpected_error",
            ).inc()
            log_event(
                self.logger,
                "error",
                "google_message_unexpected_failure",
                queue_name=self.settings.google_runner_queue,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=current_attempt,
                outcome=routing_outcome,
                error_type="unexpected_error",
                error=str(exc),
            )

    def _safe_update_request_status(
        self,
        parsed_request_id: UUID,
        status: int,
        *,
        error: str | None = None,
        request_id: str = "",
        id_query: int | str | None = None,
        user_id: str = "",
        trace_id: str = "",
        attempt: int = 0,
    ) -> bool:
        try:
            self.processor.repository.update_request_status(parsed_request_id, status, error=error)
            return True
        except Exception as exc:  # pragma: no cover - runtime defensive path
            log_event(
                self.logger,
                "warning",
                "google_request_status_update_failed",
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=attempt,
                status=status,
                error=str(exc),
            )
            return False
