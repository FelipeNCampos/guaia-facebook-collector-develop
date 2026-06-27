import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.processor import FacebookRunnerProcessor, RUNNER_QUEUE_TO_TYPE
from app_shared.config import Settings
from app_shared.processing import RetryableProcessingError, TerminalProcessingError


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeRepository:
    def __init__(self):
        self.link_updates = []
        self.domain_upserts = []
        self.request_status_refreshes = []

    def update_request_link_status(self, request_id, fonte, status, error=""):
        self.link_updates.append(
            {
                "request_id": request_id,
                "fonte": fonte,
                "status": status,
                "error": error,
            }
        )

    def upsert_domain_record(self, runner_type, record):
        self.domain_upserts.append((runner_type, record))

    def refresh_request_status(self, request_id):
        self.request_status_refreshes.append(request_id)


class FakeExtractor:
    def __init__(self, record=None, error=None):
        self.record = record or {
            "fonte": "https://www.facebook.com/openai/videos/123",
            "status": "finished",
            "error": "",
            "data": "2025-01-01 00:00:00",
            "perfil": "OpenAI",
            "verificado": True,
            "descricao": "Descricao",
            "num_comentarios": 7,
            "num_visualizacoes": 1234,
            "num_reacoes": 42,
        }
        self.error = error

    def enrich_link(self, runner_type, fonte, perfil_slug=""):
        if self.error:
            raise self.error
        return self.record


class FakeSlotLimiter:
    @contextmanager
    def acquire(self):
        yield 0


def build_settings(runner_type="video", queue_name="facebook.video") -> Settings:
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
        facebook_runner_queue=queue_name,
        facebook_runner_type=runner_type,
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
        service_http_port=8102,
        log_level="INFO",
        migrations_path=Path("db/migrations"),
    )


def build_message(runner_type: str, fonte: str):
    return {
        "request_id": str(uuid4()),
        "id_query": 45,
        "user_id": str(uuid4()),
        "tipo": runner_type,
        "fonte": fonte,
        "perfil": "openai",
        "metadata": {"trace_id": "trace-1"},
    }


def build_processor(settings, repository, extractor):
    return FacebookRunnerProcessor(
        settings,
        repository=repository,
        extractor=extractor,
        slot_limiter=FakeSlotLimiter(),
    )


def test_processor_persists_video_and_finishes_request_link():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(),
        repository=repository,
        extractor=FakeExtractor(),
    )
    message = build_message("video", "https://www.facebook.com/openai/videos/123")

    processor.process_message(message)

    assert repository.link_updates[0]["status"] == "processing"
    assert repository.domain_upserts[0][0] == "video"
    assert repository.link_updates[-1]["status"] == "finished"
    assert len(repository.request_status_refreshes) == 1


def test_processor_marks_request_link_failed_on_extraction_error():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(runner_type="reel", queue_name="facebook.reel"),
        repository=repository,
        extractor=FakeExtractor(error=RuntimeError("boom")),
    )
    message = build_message("reel", "https://www.facebook.com/openai/reel/123")

    with pytest.raises(RetryableProcessingError) as exc_info:
        processor.process_message(message)

    assert exc_info.value.error_type == "processing_error"
    assert repository.domain_upserts == []
    assert repository.link_updates[-1]["status"] == "processing"
    assert repository.request_status_refreshes == []


def test_processor_rejects_message_for_wrong_runner_type():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(runner_type="perfil", queue_name="facebook.perfil"),
        repository=repository,
        extractor=FakeExtractor(),
    )

    with pytest.raises(TerminalProcessingError) as exc_info:
        processor.process_message(build_message("video", "https://www.facebook.com/openai/videos/123"))
    assert exc_info.value.error_type == "invalid_payload"
    assert "Runner type mismatch" in str(exc_info.value)


def test_processor_accepts_supported_type_when_runner_is_multi_type():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(runner_type="all", queue_name="facebook.perfil,facebook.video"),
        repository=repository,
        extractor=FakeExtractor(),
    )

    processor.process_message(build_message("video", "https://www.facebook.com/openai/videos/123"))

    assert repository.domain_upserts[0][0] == "video"
    assert repository.link_updates[-1]["status"] == "finished"


def test_runner_queue_mapping_covers_all_supported_runners():
    assert RUNNER_QUEUE_TO_TYPE == {
        "facebook.perfil": "perfil",
        "facebook.video": "video",
        "facebook.reel": "reel",
        "facebook.post": "post",
        "facebook.storie": "storie",
        "facebook.foto": "foto",
        "facebook.permalink": "permalink",
    }


def test_processor_persists_all_supported_content_runner_types():
    test_cases = (
        ("post", "facebook.post", "https://www.facebook.com/openai/posts/123"),
        ("storie", "facebook.storie", "https://www.facebook.com/story.php?story_fbid=123&id=456"),
        ("foto", "facebook.foto", "https://www.facebook.com/openai/photos/123"),
        ("permalink", "facebook.permalink", "https://www.facebook.com/permalink.php?story_fbid=123&id=456"),
    )

    for runner_type, queue_name, fonte in test_cases:
        repository = FakeRepository()
        extractor = FakeExtractor(
            record={
                "fonte": fonte,
                "status": "finished",
                "error": "",
                "data": "2025-01-01 00:00:00",
                "perfil": "OpenAI",
                "verificado": True,
                "descricao": "Descricao",
                "num_comentarios": 7,
                "num_visualizacoes": 1234,
                "num_reacoes": 42,
            }
        )
        processor = build_processor(
            build_settings(runner_type=runner_type, queue_name=queue_name),
            repository=repository,
            extractor=extractor,
        )

        processor.process_message(build_message(runner_type, fonte))

        assert repository.link_updates[0]["status"] == "processing"
        assert repository.domain_upserts[0][0] == runner_type
        assert repository.domain_upserts[0][1]["fonte"] == fonte
        assert repository.link_updates[-1]["status"] == "finished"
        assert len(repository.request_status_refreshes) == 1


def test_processor_marks_login_required_as_terminal_failure():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(runner_type="perfil", queue_name="facebook.perfil"),
        repository=repository,
        extractor=FakeExtractor(
            record={
                "fonte": "https://www.facebook.com/openai",
                "status": "failed",
                "error": "login_obrigatorio",
                "data": "",
                "perfil": "openai",
                "verificado": "",
            }
        ),
    )

    with pytest.raises(TerminalProcessingError) as exc_info:
        processor.process_message(build_message("perfil", "https://www.facebook.com/openai"))

    assert exc_info.value.error_type == "login_obrigatorio"
    assert repository.link_updates[-1]["status"] == "failed"
    assert len(repository.request_status_refreshes) == 1


def test_processor_marks_retryable_error_as_terminal_when_attempts_are_exhausted():
    repository = FakeRepository()
    processor = build_processor(
        build_settings(runner_type="video", queue_name="facebook.video"),
        repository=repository,
        extractor=FakeExtractor(error=RuntimeError("webdriver timeout")),
    )

    with pytest.raises(TerminalProcessingError) as exc_info:
        processor.process_message(
            build_message("video", "https://www.facebook.com/openai/videos/123"),
            attempt=3,
            max_attempts=3,
        )

    assert exc_info.value.error_type == "processing_error"
    assert repository.domain_upserts[0][1]["status"] == "failed"
    assert repository.link_updates[-1]["status"] == "failed"
    assert len(repository.request_status_refreshes) == 1
