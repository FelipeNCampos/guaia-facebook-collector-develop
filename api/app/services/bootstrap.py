import asyncio
import time

from fastapi import FastAPI

from app.services.database import assert_database_connection
from app.services.migrations import apply_sql_migrations
from app_shared.metrics import ApiMetrics
from app_shared.observability import configure_logging
from app_shared.config import Settings, get_settings


async def bootstrap_platform(app: FastAPI) -> None:
    settings = get_settings()
    app.state.settings = settings
    app.state.logger = configure_logging("api", settings.log_level)
    app.state.api_metrics = ApiMetrics()
    await asyncio.to_thread(_initialize_dependencies, settings)


def _initialize_dependencies(settings: Settings) -> None:
    _retry_dependency("database", lambda: assert_database_connection(settings.db_dsn))
    apply_sql_migrations(settings.db_dsn, settings.migrations_path)


def _retry_dependency(name: str, operation) -> None:
    last_error = None
    for attempt in range(1, 11):
        try:
            operation()
            return
        except Exception as exc:
            last_error = exc
            if attempt == 10:
                break
            time.sleep(min(attempt * 2, 10))
    raise RuntimeError(f"{name} dependency unavailable after retries") from last_error
