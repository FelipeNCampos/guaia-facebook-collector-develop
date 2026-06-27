import os
import time
from datetime import datetime, timezone

from app.services.processor import FacebookRunnerProcessor
from app_shared.config import get_settings
from app_shared.health import RunnerRuntimeState, create_runner_monitoring_app, start_monitoring_server
from app_shared.metrics import RunnerMetrics
from app_shared.observability import configure_logging, log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError


class DirectFacebookRunner:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.logger = configure_logging("facebook_runner", self.settings.log_level)
        self.metrics = RunnerMetrics()
        self.poll_seconds = float(os.getenv("DIRECT_RUNNER_POLL_SECONDS", "5"))
        self.runtime_state = RunnerRuntimeState(
            "facebook_runner",
            "db.request_links",
            runner_type=self.settings.facebook_runner_type,
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
        self.runtime_state.mark_consumer_started()
        log_event(
            self.logger,
            "info",
            "direct_facebook_runner_started",
            poll_seconds=self.poll_seconds,
            runner_type=self.settings.facebook_runner_type,
            service_http_port=self.settings.service_http_port,
        )

        while True:
            rows = self.processor.repository.claim_queued_links(
                limit=max(1, self.settings.facebook_runner_prefetch_count)
            )
            if not rows:
                time.sleep(self.poll_seconds)
                continue

            for row in rows:
                self._process_row(row)

    def _process_row(self, row: dict) -> None:
        attempt = max(0, int(row.get("attempt_count") or 1) - 1)
        message = {
            "request_id": str(row["request_id"]),
            "id_query": int(row["id_query"]),
            "user_id": str(row["user_id"]),
            "tipo": row["tipo"],
            "fonte": row["fonte"],
            "perfil": row.get("perfil_slug") or "",
            "metadata": {
                "source": "direct_facebook_runner",
                "trace_id": "",
            },
        }
        started_at = datetime.now(timezone.utc)
        self.runtime_state.mark_message_received(started_at.isoformat())
        self.metrics.messages_received_total.labels(
            service="facebook_runner",
            queue="db.request_links",
        ).inc()

        try:
            with self.metrics.message_processing_seconds.labels(
                service="facebook_runner",
                queue="db.request_links",
            ).time():
                self.processor.process_message(
                    message,
                    attempt=attempt,
                    max_attempts=self.settings.message_retry_max_attempts,
                )
        except RetryableProcessingError as exc:
            self.processor.repository.requeue_request_link(
                row["request_id"],
                row["fonte"],
                exc.error_message,
            )
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="facebook_runner",
                queue="db.request_links",
                outcome="retry",
            ).inc()
            log_event(
                self.logger,
                "warning",
                "direct_facebook_link_requeued",
                request_id=str(row["request_id"]),
                id_query=row["id_query"],
                runner_type=row["tipo"],
                fonte=row["fonte"],
                attempt=attempt,
                error_type=exc.error_type,
                error=exc.error_message,
            )
            time.sleep(self.poll_seconds)
            return
        except TerminalProcessingError as exc:
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="facebook_runner",
                queue="db.request_links",
                outcome="failed",
            ).inc()
            log_event(
                self.logger,
                "error",
                "direct_facebook_link_failed",
                request_id=str(row["request_id"]),
                id_query=row["id_query"],
                runner_type=row["tipo"],
                fonte=row["fonte"],
                attempt=attempt,
                error_type=exc.error_type,
                error=exc.error_message,
            )
            return

        finished_at = datetime.now(timezone.utc)
        self.runtime_state.mark_success(finished_at.isoformat())
        self.metrics.messages_processed_total.labels(
            service="facebook_runner",
            queue="db.request_links",
            outcome="success",
        ).inc()
        log_event(
            self.logger,
            "info",
            "direct_facebook_link_processed",
            request_id=str(row["request_id"]),
            id_query=row["id_query"],
            runner_type=row["tipo"],
            fonte=row["fonte"],
            attempt=attempt,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        )
