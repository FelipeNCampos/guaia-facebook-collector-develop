import json
from datetime import datetime, timezone
from uuid import UUID

import pika

from app.services.messaging import declare_durable_queue, ensure_runner_topology
from app.services.processor import FacebookRunnerProcessor
from app_shared.amqp import build_consumer_connection_parameters, get_retry_attempt, publish_to_retry_or_dlq
from app_shared.config import get_settings
from app_shared.health import RunnerRuntimeState, create_runner_monitoring_app, start_monitoring_server
from app_shared.metrics import RunnerMetrics
from app_shared.observability import configure_logging, log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError


class FacebookRunner:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.queue_names = self._configured_queue_names()
        self.service_name = f"facebook_runner_{self.settings.facebook_runner_type}"
        self.logger = configure_logging(self.service_name, self.settings.log_level)
        self.metrics = RunnerMetrics()
        self.runtime_state = RunnerRuntimeState(
            self.service_name,
            ",".join(self.queue_names),
            self.settings.facebook_runner_type,
        )
        self.processor = FacebookRunnerProcessor(self.settings, logger=self.logger)
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
        channel.basic_qos(prefetch_count=self.settings.facebook_runner_prefetch_count)
        for queue_name in self.queue_names:
            declare_durable_queue(channel, queue_name)
            channel.basic_consume(
                queue=queue_name,
                on_message_callback=self._handle_message,
            )
        self.runtime_state.mark_consumer_started()
        log_event(
            self.logger,
            "info",
            "facebook_runner_started",
            queue_name=",".join(self.queue_names),
            runner_type=self.settings.facebook_runner_type,
            service_http_port=self.settings.service_http_port,
        )
        channel.start_consuming()

    def _handle_message(self, channel, method, properties, body) -> None:
        started_at = datetime.now(timezone.utc)
        current_attempt = get_retry_attempt(properties)
        queue_name = self._message_queue_name(method)
        runner_type = self.settings.facebook_runner_type
        payload = {}
        self.runtime_state.mark_message_received(started_at.isoformat())
        self.metrics.messages_received_total.labels(
            service=self.service_name,
            queue=queue_name,
        ).inc()

        try:
            payload = json.loads(body.decode("utf-8"))
            runner_type = str(payload.get("tipo") or runner_type)
            with self.metrics.message_processing_seconds.labels(
                service=self.service_name,
                queue=queue_name,
            ).time():
                self.processor.process_message(
                    payload,
                    attempt=current_attempt,
                    max_attempts=self.settings.message_retry_max_attempts,
                )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            finished_at = datetime.now(timezone.utc)
            self.runtime_state.mark_success(finished_at.isoformat())
            self.metrics.messages_processed_total.labels(
                service=self.service_name,
                queue=queue_name,
                outcome="success",
            ).inc()
            self.metrics.last_success_unixtime.labels(
                service=self.service_name,
                queue=queue_name,
            ).set(finished_at.timestamp())
            log_event(
                self.logger,
                "info",
                "facebook_message_processed",
                queue_name=queue_name,
                runner_type=runner_type,
                request_id=payload.get("request_id", ""),
                user_id=payload.get("user_id", ""),
                trace_id=(payload.get("metadata") or {}).get("trace_id", ""),
                fonte=payload.get("fonte", ""),
                attempt=current_attempt,
                status="success",
                duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            )
        except RetryableProcessingError as exc:
            routing_outcome = publish_to_retry_or_dlq(
                channel,
                queue_name=queue_name,
                body=body,
                properties=properties,
                error_type=exc.error_type,
                error_message=exc.error_message,
                retryable=True,
                max_attempts=self.settings.message_retry_max_attempts,
                base_delay_ms=self.settings.message_retry_base_delay_ms,
            )
            if routing_outcome == "dead_letter":
                self._mark_link_failed_from_payload(payload, exc.error_message)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service=self.service_name,
                queue=queue_name,
                outcome=routing_outcome,
            ).inc()
            self.metrics.processing_failures_total.labels(
                service=self.service_name,
                queue=queue_name,
                error_type=exc.error_type,
            ).inc()
            log_event(
                self.logger,
                "warning",
                "facebook_message_routed_after_retryable_error",
                queue_name=queue_name,
                runner_type=runner_type,
                request_id=payload.get("request_id", ""),
                id_query=payload.get("id_query", ""),
                user_id=payload.get("user_id", ""),
                trace_id=(payload.get("metadata") or {}).get("trace_id", ""),
                fonte=payload.get("fonte", ""),
                attempt=current_attempt,
                outcome=routing_outcome,
                error_type=exc.error_type,
                error=exc.error_message,
            )
        except TerminalProcessingError as exc:
            self._mark_link_failed_from_payload(payload, exc.error_message)
            publish_to_retry_or_dlq(
                channel,
                queue_name=queue_name,
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
                service=self.service_name,
                queue=queue_name,
                outcome="dead_letter",
            ).inc()
            self.metrics.processing_failures_total.labels(
                service=self.service_name,
                queue=queue_name,
                error_type=exc.error_type,
            ).inc()
            if exc.error_type == "login_obrigatorio":
                self.metrics.facebook_login_required_total.inc()
            log_event(
                self.logger,
                "error",
                "facebook_message_sent_to_dead_letter",
                queue_name=queue_name,
                runner_type=runner_type,
                request_id=payload.get("request_id", ""),
                id_query=payload.get("id_query", ""),
                user_id=payload.get("user_id", ""),
                trace_id=(payload.get("metadata") or {}).get("trace_id", ""),
                fonte=payload.get("fonte", ""),
                attempt=current_attempt,
                outcome="dead_letter",
                error_type=exc.error_type,
                error=exc.error_message,
            )
        except Exception as exc:  # pragma: no cover - runtime defensive path
            routing_outcome = publish_to_retry_or_dlq(
                channel,
                queue_name=queue_name,
                body=body,
                properties=properties,
                error_type="unexpected_error",
                error_message=str(exc),
                retryable=True,
                max_attempts=self.settings.message_retry_max_attempts,
                base_delay_ms=self.settings.message_retry_base_delay_ms,
            )
            if routing_outcome == "dead_letter":
                self._mark_link_failed_from_payload(payload, str(exc))
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service=self.service_name,
                queue=queue_name,
                outcome=routing_outcome,
            ).inc()
            self.metrics.processing_failures_total.labels(
                service=self.service_name,
                queue=queue_name,
                error_type="unexpected_error",
            ).inc()
            log_event(
                self.logger,
                "error",
                "facebook_message_unexpected_failure",
                queue_name=queue_name,
                runner_type=runner_type,
                request_id=payload.get("request_id", ""),
                id_query=payload.get("id_query", ""),
                user_id=payload.get("user_id", ""),
                trace_id=(payload.get("metadata") or {}).get("trace_id", ""),
                fonte=payload.get("fonte", ""),
                attempt=current_attempt,
                outcome=routing_outcome,
                error_type="unexpected_error",
                error=str(exc),
            )

    def _mark_link_failed_from_payload(self, payload: dict, error: str) -> None:
        request_id_value = payload.get("request_id")
        fonte = payload.get("fonte")
        if not request_id_value or not fonte:
            return

        try:
            request_id = UUID(str(request_id_value))
            self.processor.repository.update_request_link_status(request_id, fonte, "failed", error)
            self.processor.repository.refresh_request_status(request_id)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            log_event(
                self.logger,
                "warning",
                "facebook_dead_letter_status_update_failed",
                queue_name=self.settings.facebook_runner_queue,
                runner_type=self.settings.facebook_runner_type,
                request_id=request_id_value,
                fonte=fonte,
                error=str(exc),
            )

    def _configured_queue_names(self) -> list[str]:
        return [
            queue_name.strip()
            for queue_name in str(self.settings.facebook_runner_queue).split(",")
            if queue_name.strip()
        ]

    def _message_queue_name(self, method) -> str:
        return str(getattr(method, "routing_key", "") or self.settings.facebook_runner_queue)
