import os
from pathlib import Path

from app.services.discovery import GoogleSearchResult, SeleniumGoogleSearchClient
from app_shared.config import Settings


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeDriver:
    def __init__(self):
        self.title = "Unusual traffic"
        self.current_url = "https://www.google.com/sorry/index"
        self.page_source = """
        <html><body>
          <div class="g-recaptcha" data-sitekey="captcha-key"></div>
          <form id="captcha-form"></form>
        </body></html>
        """

    def get(self, url):
        return None

    def quit(self):
        return None

    def find_elements(self, by=None, value=None):
        return []


class FakeCaptchaResolver:
    configured = True

    def __init__(self):
        self.called = False

    def resolve_google_captcha(self, driver, timeout_seconds=None):
        self.called = True
        driver.title = "Search results"
        driver.current_url = "https://www.google.com/search?q=openai"
        driver.page_source = """
        <html><body>
          <div id="search"></div>
          <a href="/url?q=https://www.facebook.com/openai/posts/123&sa=U">post</a>
        </body></html>
        """
        return True


class SearchClientDouble(SeleniumGoogleSearchClient):
    def __init__(self, settings, driver, captcha_resolver, logger=None):
        super().__init__(settings, captcha_resolver=captcha_resolver, logger=logger)
        self._driver = driver

    def _create_driver(self):
        return self._driver, None


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, level, message, extra=None):
        self.events.append(
            {
                "level": level,
                "message": message,
                "event_data": (extra or {}).get("event_data", {}),
            }
        )


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
        google_runner_wait_timeout=1,
        google_captcha_timeout_seconds=240,
        google_chrome_profile_directory=None,
        google_2captcha_api_key="test-key",
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
        facebook_media_local_path="",
        facebook_media_collect_from_dom=True,
        facebook_media_prefer_video=True,
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


def test_collect_results_attempts_2captcha_before_failing_request():
    captcha_resolver = FakeCaptchaResolver()
    logger = FakeLogger()
    client = SearchClientDouble(build_settings(), FakeDriver(), captcha_resolver, logger=logger)

    result = client.collect_results(
        "openai",
        request_id="request-1",
        id_query=2,
        user_id="user-1",
        trace_id="trace-1",
        attempt=0,
    )

    assert captcha_resolver.called is True
    assert isinstance(result, GoogleSearchResult)
    assert len(result.records) == 1
    assert result.records[0]["categoria"] == "Posts"
    assert logger.events[0]["message"] == "google_captcha_resolution_started"
    completed_events = [
        event for event in logger.events
        if event["message"] == "google_captcha_resolution_completed"
    ]
    assert completed_events
    assert completed_events[-1]["event_data"]["id_query"] == 2
