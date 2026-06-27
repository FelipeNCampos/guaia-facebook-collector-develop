from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.request_service import (
    RequestConflictError,
    RequestNotFoundError,
    RequestService,
)
from app_shared.config import Settings
from app_shared.request_status import (
    FACEBOOK_QUERY_SOURCE,
    FACEBOOK_SOURCE_TYPE,
    RequestStatusCode,
)


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeRepository:
    def __init__(self):
        self.user_id = uuid4()
        self.request_id = uuid4()
        self.created_at = datetime.now(timezone.utc)
        self.updated_statuses = []
        self.saved_requests = []
        self.request_metadata = None
        self.request_data = {
            "profiles": [],
            "posts": [],
            "videos": [],
            "pages": [],
            "groups": [],
            "items": [],
        }
        self.raise_conflict = False

    def create_request_for_ip(
        self,
        *,
        ip_request,
        id_query,
        subject,
        start_date,
        end_date,
        status_current,
        payload,
    ):
        if self.raise_conflict:
            raise RequestConflictError(id_query)
        self.saved_requests.append(
            {
                "ip_request": ip_request,
                "id_query": id_query,
                "subject": subject,
                "start_date": start_date,
                "end_date": end_date,
                "status_current": status_current,
                "payload": payload,
            }
        )
        return (
            {
                "id": self.user_id,
                "ip_request": ip_request,
                "numero_requests": 1,
                "created_at": self.created_at,
                "updated_at": self.created_at,
            },
            {
                "id": self.request_id,
                "user_id": self.user_id,
                "id_query": id_query,
                "subject": subject,
                "start_date": start_date,
                "end_date": end_date,
                "status_current": int(status_current),
                "query_source": FACEBOOK_QUERY_SOURCE,
                "source_type": FACEBOOK_SOURCE_TYPE,
                "status": "received",
                "error": None,
                "created_at": self.created_at,
                "updated_at": self.created_at,
            },
        )

    def update_request_status(self, request_id, status_current, *, error=None, legacy_status=None):
        self.updated_statuses.append((request_id, status_current, error, legacy_status))
        return {
            "id": request_id,
            "user_id": self.user_id,
            "id_query": 45,
            "subject": "facebook gui ai",
            "start_date": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "end_date": datetime(2025, 12, 31, tzinfo=timezone.utc),
            "status_current": int(status_current),
            "query_source": FACEBOOK_QUERY_SOURCE,
            "source_type": FACEBOOK_SOURCE_TYPE,
            "status": legacy_status or "queued",
            "error": error,
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }

    def get_request_metadata(self, request_id):
        return self.request_metadata

    def get_request_metadata_by_query(self, id_query):
        return self.request_metadata

    def get_request_data(self, request_id):
        return self.request_data


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
        service_http_port=8090,
        log_level="INFO",
        migrations_path=Path("db/migrations"),
    )


def test_create_request_marks_request_queued_for_direct_runner():
    repository = FakeRepository()
    service = RequestService(build_settings(), repository=repository)

    result = service.create_request(
        "127.0.0.1",
        {
            "id_query": 45,
            "subject": "facebook gui ai",
            "start_date": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "end_date": datetime(2025, 12, 31, tzinfo=timezone.utc),
            "status_current": RequestStatusCode.PENDING,
        },
    )

    assert result["metadata"]["status_current"] == RequestStatusCode.PENDING
    assert repository.saved_requests[0]["payload"]["source"] == "api"
    assert "trace_id" in repository.saved_requests[0]["payload"]
    assert repository.updated_statuses[-1][1] == RequestStatusCode.PENDING
    assert repository.updated_statuses[-1][3] == "queued"


def test_create_request_raises_conflict_for_duplicate_id_query():
    repository = FakeRepository()
    repository.raise_conflict = True
    service = RequestService(build_settings(), repository=repository)

    with pytest.raises(RequestConflictError):
        service.create_request(
            "127.0.0.1",
            {
                "id_query": 45,
                "subject": "facebook gui ai",
                "status_current": RequestStatusCode.PENDING,
            },
        )


def test_get_request_raises_not_found_for_unknown_id():
    repository = FakeRepository()
    service = RequestService(build_settings(), repository=repository)

    with pytest.raises(RequestNotFoundError):
        service.get_request(uuid4())


def test_get_request_by_query_returns_metadata_and_data():
    repository = FakeRepository()
    repository.request_metadata = {
        "request_id": uuid4(),
        "id_query": 45,
        "user_id": uuid4(),
        "ip_request": "127.0.0.1",
        "numero_requests": 2,
        "subject": "facebook gui ai",
        "query_source": FACEBOOK_QUERY_SOURCE,
        "source_type": FACEBOOK_SOURCE_TYPE,
        "status_current": 2,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "start_date": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "end_date": datetime(2025, 12, 31, tzinfo=timezone.utc),
    }
    repository.request_data = {
        "profiles": [],
        "posts": [],
        "videos": [{"fonte": "https://www.facebook.com/openai/videos/1"}],
        "pages": [],
        "groups": [],
        "items": [],
    }
    service = RequestService(build_settings(), repository=repository)

    result = service.get_request_by_query(45)

    assert result["metadata"]["id_query"] == 45
    assert result["metadata"]["status_current"] == RequestStatusCode.PROCESSING
    assert result["data"]["videos"][0]["fonte"].endswith("/1")
