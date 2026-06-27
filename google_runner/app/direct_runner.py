import os
import time
from datetime import datetime, timezone

from app.services.google_processor import DOWNSTREAM_QUEUES, GoogleRunnerProcessor
from app_shared.config import get_settings
from app_shared.health import RunnerRuntimeState, create_runner_monitoring_app, start_monitoring_server
from app_shared.metrics import RunnerMetrics
from app_shared.observability import configure_logging, log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError
from app_shared.request_status import RequestStatusCode


class DatabaseDownstreamPublisher:
    def publish(self, message: dict) -> None:
        del message


class DirectGoogleRunner:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.logger = configure_logging("google_runner", self.settings.log_level)
        self.metrics = RunnerMetrics()
        self.poll_seconds = float(os.getenv("DIRECT_RUNNER_POLL_SECONDS", "5"))
        self.runtime_state = RunnerRuntimeState("google_runner", "db.requests")
        self.processor = GoogleRunnerProcessor(
            self.settings,
            downstream_publishers={
                queue_name: DatabaseDownstreamPublisher()
                for queue_name in DOWNSTREAM_QUEUES.values()
            },
            logger=self.logger,
        )
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
            "direct_google_runner_started",
            poll_seconds=self.poll_seconds,
            service_http_port=self.settings.service_http_port,
        )

        while True:
            rows = self.processor.repository.claim_pending_requests(limit=1)
            if not rows:
                time.sleep(self.poll_seconds)
                continue

            for row in rows:
                self._process_row(row)

    def _process_row(self, row: dict) -> None:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        attempt = max(0, int(payload.get("worker_attempts") or 1) - 1)
        message = self._build_message(row)
        started_at = datetime.now(timezone.utc)

        self.runtime_state.mark_message_received(started_at.isoformat())
        self.metrics.messages_received_total.labels(
            service="google_runner",
            queue="db.requests",
        ).inc()

        try:
            with self.metrics.message_processing_seconds.labels(
                service="google_runner",
                queue="db.requests",
            ).time():
                self.processor.process_message(
                    message,
                    attempt=attempt,
                    max_attempts=self.settings.message_retry_max_attempts,
                    mark_processing=False,
                )
        except RetryableProcessingError as exc:
            self.processor.repository.update_request_status(
                row["id"],
                RequestStatusCode.PENDING,
                error=exc.error_message,
                legacy_status="queued",
            )
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue="db.requests",
                outcome="retry",
            ).inc()
            log_event(
                self.logger,
                "warning",
                "direct_google_request_requeued",
                request_id=str(row["id"]),
                id_query=row["id_query"],
                attempt=attempt,
                error_type=exc.error_type,
                error=exc.error_message,
            )
            time.sleep(self.poll_seconds)
            return
        except TerminalProcessingError as exc:
            self.processor.repository.update_request_status(
                row["id"],
                RequestStatusCode.COMPLETED,
                error=exc.error_message,
                legacy_status="finished",
            )
            self.runtime_state.mark_failure()
            self.metrics.messages_processed_total.labels(
                service="google_runner",
                queue="db.requests",
                outcome="failed",
            ).inc()
            log_event(
                self.logger,
                "error",
                "direct_google_request_failed",
                request_id=str(row["id"]),
                id_query=row["id_query"],
                attempt=attempt,
                error_type=exc.error_type,
                error=exc.error_message,
            )
            return

        finished_at = datetime.now(timezone.utc)
        self.runtime_state.mark_success(finished_at.isoformat())
        self.metrics.messages_processed_total.labels(
            service="google_runner",
            queue="db.requests",
            outcome="success",
        ).inc()
        log_event(
            self.logger,
            "info",
            "direct_google_request_processed",
            request_id=str(row["id"]),
            id_query=row["id_query"],
            attempt=attempt,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        )

    def _build_message(self, row: dict) -> dict:
        return {
            "request_id": str(row["id"]),
            "id_query": int(row["id_query"]),
            "user_id": str(row["user_id"]),
            "subject": row["subject"],
            "start_date": row["start_date"].isoformat() if row.get("start_date") else None,
            "end_date": row["end_date"].isoformat() if row.get("end_date") else None,
            "status_current": int(RequestStatusCode.PENDING),
            "query_source": int(row["query_source"]),
            "source_type": int(row["source_type"]),
            "metadata": {
                "source": "direct_google_runner",
                "trace_id": str((row.get("payload") or {}).get("trace_id") or ""),
            },
        }
