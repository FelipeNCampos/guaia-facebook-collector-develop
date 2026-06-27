import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from starlette.middleware.trustedhost import TrustedHostMiddleware
import uvicorn

from app_shared.config import Settings
from app_shared.observability import log_event
from psycopg import connect


def assert_database_connection(db_dsn: str) -> None:
    with connect(db_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()


def _run_check(check_fn, *args) -> dict:
    try:
        check_fn(*args)
    except Exception as exc:  # pragma: no cover - defensive response path
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok"}


async def run_dependency_health_checks(settings: Settings) -> dict:
    checks = {
        "db": await asyncio.to_thread(_run_check, assert_database_connection, settings.db_dsn),
    }
    status = "ok" if all(check["status"] == "ok" for check in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


class RunnerRuntimeState:
    def __init__(self, service_name: str, queue_name: str, runner_type: str = ""):
        self.service_name = service_name
        self.queue_name = queue_name
        self.runner_type = runner_type
        self.consumer_status = "starting"
        self.last_message_at: str | None = None
        self.last_success_at: str | None = None

    def mark_consumer_started(self) -> None:
        self.consumer_status = "running"

    def mark_message_received(self, timestamp: str) -> None:
        self.consumer_status = "running"
        self.last_message_at = timestamp

    def mark_success(self, timestamp: str) -> None:
        self.consumer_status = "running"
        self.last_success_at = timestamp

    def mark_failure(self) -> None:
        self.consumer_status = "running"


def create_runner_monitoring_app(
    *,
    settings: Settings,
    logger,
    runtime_state: RunnerRuntimeState,
    metrics_registry: CollectorRegistry,
) -> FastAPI:
    app = FastAPI(title=f"{runtime_state.service_name} Monitoring", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.get("/health")
    async def health():
        report = await run_dependency_health_checks(settings)
        payload = {
            "status": report["status"],
            "service": runtime_state.service_name,
            "queue": runtime_state.queue_name,
            "runner_type": runtime_state.runner_type or None,
            "consumer_status": runtime_state.consumer_status,
            "last_message_at": runtime_state.last_message_at,
            "last_success_at": runtime_state.last_success_at,
            "checks": report["checks"],
        }
        status_code = 200 if report["status"] == "ok" else 503
        return JSONResponse(status_code=status_code, content=payload)

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(metrics_registry), media_type=CONTENT_TYPE_LATEST)

    app.state.logger = logger
    app.state.runtime_state = runtime_state
    return app


def start_monitoring_server(app: FastAPI, settings: Settings, logger) -> None:
    config = uvicorn.Config(
        app,
        host=settings.service_http_host,
        port=settings.service_http_port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)

    import threading

    thread = threading.Thread(target=server.run, daemon=True, name=f"{settings.service_http_port}-metrics")
    thread.start()
    log_event(
        logger,
        "info",
        "monitoring_server_started",
        host=settings.service_http_host,
        port=settings.service_http_port,
    )
