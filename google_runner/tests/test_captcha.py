import os
from pathlib import Path

from app.services.captcha import CaptchaResolver, extract_recaptcha_parameters
from app_shared.config import Settings


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeTwoCaptchaClient:
    def __init__(self):
        self.create_calls = []
        self.wait_calls = []

    def create_recaptcha_v2_task(self, **kwargs):
        self.create_calls.append(kwargs)
        return 123456

    def wait_for_result(self, task_id, timeout_seconds=120, poll_interval_seconds=5):
        self.wait_calls.append(
            {
                "task_id": task_id,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
        )
        return "captcha-token"


class FakeDriver:
    def __init__(self, page_source):
        self.page_source = page_source
        self.current_url = "https://www.google.com/sorry/index?continue=https://www.google.com/search%3Fq%3Dopenai"
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if script == "return navigator.userAgent;":
            return "Mozilla/5.0 Test"
        return True

    def get_cookies(self):
        return [{"name": "NID", "value": "cookie-1"}]


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


def test_extract_recaptcha_parameters_reads_data_sitekey_and_data_s():
    html = '<div class="g-recaptcha" data-sitekey="site-key-123" data-s="data-s-456"></div>'

    sitekey, data_s = extract_recaptcha_parameters(html)

    assert sitekey == "site-key-123"
    assert data_s == "data-s-456"


def test_extract_recaptcha_parameters_reads_iframe_sitekey():
    html = '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=iframe-key-123&s=data-s-789"></iframe>'

    sitekey, data_s = extract_recaptcha_parameters(html)

    assert sitekey == "iframe-key-123"
    assert data_s == "data-s-789"


def test_resolve_google_captcha_submits_token_with_2captcha():
    client = FakeTwoCaptchaClient()
    resolver = CaptchaResolver(build_settings(), client=client)
    driver = FakeDriver('<div class="g-recaptcha" data-sitekey="site-key-123" data-s="data-s-456"></div><form id="captcha-form"></form>')

    resolved = resolver.resolve_google_captcha(driver, timeout_seconds=95)

    assert resolved is True
    assert client.create_calls[0]["sitekey"] == "site-key-123"
    assert client.create_calls[0]["data_s"] == "data-s-456"
    assert client.create_calls[0]["user_agent"] == "Mozilla/5.0 Test"
    assert client.create_calls[0]["cookies"] == "NID=cookie-1"
    assert client.wait_calls[0]["task_id"] == 123456
    assert client.wait_calls[0]["timeout_seconds"] == 95
    assert driver.scripts[1][1] == ("captcha-token",)
