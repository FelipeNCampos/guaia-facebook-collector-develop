import os
from pathlib import Path

from fastapi.testclient import TestClient

from app_shared.config import Settings
from app_shared.health import RunnerRuntimeState, create_runner_monitoring_app
from app_shared.metrics import RunnerMetrics


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


def build_settings() -> Settings:
    return Settings(
        app_env="test",
        api_host="0.0.0.0",
        api_port=8080,
        allowed_hosts=("testserver",),
        db_host="db",
        db_port=TEST_DB_PORT,
        db_name="guaia",
        db_user="guaia",
        db_password="coto1423",
        db_dsn=f"postgresql://guaia:coto1423@db:{TEST_DB_PORT}/guaia",
        amqp_url="amqp://guest:guest@rabbitmq:5672/",
        user_requests_queue="user_requests",
        google_runner_queue="user_requests",
        google_runner_headless=True,
        google_runner_max_pages=3,
        google_runner_page_load_timeout=20,
        google_runner_page_delay_seconds=3,
        google_runner_wait_timeout=15,
        google_captcha_timeout_seconds=240,
        google_chrome_profile_directory=None,
        google_2captcha_api_key=None,
        facebook_runner_queue="facebook.perfil",
        facebook_runner_type="perfil",
        facebook_runner_headless=True,
        facebook_runner_page_load_timeout=20,
        facebook_runner_wait_timeout=15,
        facebook_chrome_profile_directory=None,
        facebook_runner_prefetch_count=1,
        facebook_extractor_concurrency_limit=4,
        facebook_extractor_slot_wait_seconds=120,
        facebook_download_media=False,
        facebook_media_download_timeout_seconds=60,
        facebook_media_max_bytes=262144000,
        facebook_fetch_comments=False,
        facebook_comments_per_post=50,
        message_retry_max_attempts=3,
        message_retry_base_delay_ms=30000,
        service_http_host="0.0.0.0",
        service_http_port=8091,
        log_level="INFO",
        migrations_path=Path("db/migrations"),
    )


def test_runner_monitoring_app_exposes_health_and_metrics(monkeypatch):
    async def fake_health_checks(settings):
        return {
            "status": "ok",
            "checks": {
                "db": {"status": "ok"},
                "rabbitmq": {"status": "ok"},
            },
        }

    monkeypatch.setattr("app_shared.health.run_dependency_health_checks", fake_health_checks)

    metrics = RunnerMetrics()
    metrics.messages_received_total.labels(service="google_runner", queue="user_requests").inc()
    state = RunnerRuntimeState("google_runner", "user_requests")
    state.mark_consumer_started()

    app = create_runner_monitoring_app(
        settings=build_settings(),
        logger=None,
        runtime_state=state,
        metrics_registry=metrics.registry,
    )

    with TestClient(app) as client:
        health_response = client.get("/health")
        metrics_response = client.get("/metrics")

    assert health_response.status_code == 200
    assert health_response.json()["service"] == "google_runner"
    assert metrics_response.status_code == 200
    assert "runner_messages_received_total" in metrics_response.text


def test_runner_monitoring_app_rejects_disallowed_host(monkeypatch):
    async def fake_health_checks(settings):
        return {
            "status": "ok",
            "checks": {
                "db": {"status": "ok"},
                "rabbitmq": {"status": "ok"},
            },
        }

    monkeypatch.setattr("app_shared.health.run_dependency_health_checks", fake_health_checks)

    metrics = RunnerMetrics()
    state = RunnerRuntimeState("google_runner", "user_requests")
    settings = build_settings()
    settings = Settings(**{**settings.__dict__, "allowed_hosts": ("monitor.local",)})

    app = create_runner_monitoring_app(
        settings=settings,
        logger=None,
        runtime_state=state,
        metrics_registry=metrics.registry,
    )

    with TestClient(app) as client:
        response = client.get("/health", headers={"host": "evil.local"})

    assert response.status_code == 400
