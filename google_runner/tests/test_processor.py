import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.discovery import GoogleCaptchaDetectedError, GoogleSearchResult
from app.services.google_processor import GoogleRunnerProcessor
from app_shared.config import Settings
from app_shared.processing import RetryableProcessingError, TerminalProcessingError
from app_shared.request_status import RequestStatusCode


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeRepository:
    def __init__(self):
        self.request_status_updates = []
        self.request_link_upserts = []
        self.request_link_status_updates = []

    def update_request_status(self, request_id, status, error=None, *, legacy_status=None):
        self.request_status_updates.append((request_id, int(status), error, legacy_status))

    def upsert_request_link(self, **kwargs):
        self.request_link_upserts.append(kwargs)

    def update_request_link_status(self, **kwargs):
        self.request_link_status_updates.append(kwargs)


class FakeSearchClient:
    def __init__(self, result=None, error=None):
        self.result = result or GoogleSearchResult(records=[], pages_visited=1)
        self.error = error
        self.calls = []

    def collect_results(self, search_term, after=None, before=None, **kwargs):
        self.calls.append((search_term, after, before))
        if self.error:
            raise self.error
        return self.result


class FakePublisher:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.messages = []

    def publish(self, message):
        if self.should_fail:
            raise RuntimeError("publish failed")
        self.messages.append(message)


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


def build_message():
    return {
        "request_id": str(uuid4()),
        "id_query": 45,
        "user_id": str(uuid4()),
        "subject": "openai",
        "start_date": "2025-01-01T00:00:00+00:00",
        "end_date": "2025-12-31T00:00:00+00:00",
        "status_current": 1,
        "query_source": 5,
        "source_type": 3,
        "metadata": {"trace_id": "trace-1"},
    }


def test_processor_marks_processing_and_dispatches_links():
    repository = FakeRepository()
    publisher = FakePublisher()
    search_client = FakeSearchClient(
        result=GoogleSearchResult(
            records=[
                {
                    "fonte": "https://www.facebook.com/openai/posts/123",
                    "categoria": "Posts",
                    "perfil": "openai",
                }
            ],
            pages_visited=1,
        )
    )
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=search_client,
        downstream_publishers={"facebook.post": publisher},
    )

    processor.process_message(build_message())

    assert repository.request_status_updates[0][1] == RequestStatusCode.PROCESSING
    assert repository.request_status_updates[-1][1] == RequestStatusCode.PROCESSING
    assert repository.request_link_upserts[0]["tipo"] == "post"
    assert publisher.messages[0]["id_query"] == 45
    assert publisher.messages[0]["metadata"]["categoria_original"] == "Posts"
    assert search_client.calls[0] == ("openai", "2025-01-01", "2025-12-31")


def test_processor_does_not_rewrite_processing_when_runner_already_marked_request():
    repository = FakeRepository()
    publisher = FakePublisher()
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=FakeSearchClient(
            result=GoogleSearchResult(
                records=[
                    {
                        "fonte": "https://www.facebook.com/openai/posts/123",
                        "categoria": "Posts",
                        "perfil": "openai",
                    }
                ],
                pages_visited=1,
            )
        ),
        downstream_publishers={"facebook.post": publisher},
    )

    processor.process_message(build_message(), mark_processing=False)

    assert repository.request_status_updates[0][1] == RequestStatusCode.PROCESSING
    assert len(repository.request_status_updates) == 1


def test_processor_raises_retryable_when_downstream_publication_fails():
    repository = FakeRepository()
    failing_publisher = FakePublisher(should_fail=True)
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=FakeSearchClient(
            result=GoogleSearchResult(
                records=[
                    {
                        "fonte": "https://www.facebook.com/openai/posts/123",
                        "categoria": "Posts",
                        "perfil": "openai",
                    }
                ],
                pages_visited=1,
            )
        ),
        downstream_publishers={"facebook.post": failing_publisher},
    )

    with pytest.raises(RetryableProcessingError) as exc_info:
        processor.process_message(build_message())

    assert exc_info.value.error_type == "downstream_publication_failed"
    assert repository.request_status_updates[0][1] == RequestStatusCode.PROCESSING
    assert repository.request_status_updates[-1][1] == RequestStatusCode.PROCESSING


def test_processor_marks_completed_when_captcha_attempts_are_exhausted():
    repository = FakeRepository()
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=FakeSearchClient(error=GoogleCaptchaDetectedError("google_captcha_detected")),
        downstream_publishers={},
    )

    with pytest.raises(TerminalProcessingError) as exc_info:
        processor.process_message(build_message(), attempt=3, max_attempts=3)

    assert exc_info.value.error_type == "google_captcha_detected"
    assert repository.request_status_updates[-1][1] == RequestStatusCode.COMPLETED
    assert repository.request_status_updates[-1][2] == "google_captcha_detected"
    assert repository.request_status_updates[-1][3] == "finished"


def test_processor_marks_completed_when_retryable_error_exhausts_attempts():
    repository = FakeRepository()
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=FakeSearchClient(error=RuntimeError("driver timeout")),
        downstream_publishers={},
    )

    with pytest.raises(TerminalProcessingError) as exc_info:
        processor.process_message(
            build_message(),
            attempt=3,
            max_attempts=3,
        )

    assert exc_info.value.error_type == "processing_error"
    assert repository.request_status_updates[-1][1] == RequestStatusCode.COMPLETED


def test_processor_marks_completed_when_search_has_no_results():
    repository = FakeRepository()
    processor = GoogleRunnerProcessor(
        build_settings(),
        repository=repository,
        search_client=FakeSearchClient(result=GoogleSearchResult(records=[], pages_visited=1)),
        downstream_publishers={},
    )

    processor.process_message(build_message())

    assert repository.request_status_updates[-1][1] == RequestStatusCode.COMPLETED
