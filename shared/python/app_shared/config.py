import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str
    api_host: str
    api_port: int
    allowed_hosts: tuple[str, ...]
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_dsn: str
    amqp_url: str
    user_requests_queue: str
    google_runner_queue: str
    google_runner_headless: bool
    google_runner_max_pages: int
    google_runner_page_load_timeout: int
    google_runner_page_delay_seconds: float
    google_runner_wait_timeout: int
    google_captcha_timeout_seconds: int
    google_chrome_profile_directory: str | None
    google_2captcha_api_key: str | None
    facebook_runner_queue: str
    facebook_runner_type: str
    facebook_runner_headless: bool
    facebook_runner_page_load_timeout: int
    facebook_runner_wait_timeout: int
    facebook_chrome_profile_directory: str | None
    facebook_runner_prefetch_count: int
    facebook_extractor_concurrency_limit: int
    facebook_extractor_slot_wait_seconds: int
    facebook_download_media: bool
    facebook_media_local_path: str
    facebook_media_collect_from_dom: bool
    facebook_media_prefer_video: bool
    facebook_media_download_timeout_seconds: int
    facebook_media_max_bytes: int
    facebook_fetch_comments: bool
    facebook_comments_per_post: int
    message_retry_max_attempts: int
    message_retry_base_delay_ms: int
    service_http_host: str
    service_http_port: int
    log_level: str
    migrations_path: Path
    facebook_media_storage: str = "auto"

    @property
    def google_chrome_startup_attempts(self) -> int:
        try:
            return max(1, int(os.getenv("GOOGLE_CHROME_STARTUP_ATTEMPTS", "3")))
        except ValueError:
            return 3


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def _parse_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    migrations_path = Path(
        os.getenv("MIGRATIONS_PATH", Path(__file__).resolve().parents[3] / "db" / "migrations")
    )
    db_host = os.getenv("DB_HOST", "db")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_user = _required_env("DB_USER")
    db_password = _required_env("DB_PASSWORD")
    db_name = os.getenv("DB_NAME", db_user)
    db_dsn = os.getenv(
        "DB_DSN",
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}",
    )

    return Settings(
        app_env=_required_env("APP_ENV"),
        api_host=_required_env("API_HOST"),
        api_port=int(_required_env("API_PORT")),
        allowed_hosts=_parse_csv(os.getenv("ALLOWED_HOSTS"), ("*",)),
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_dsn=db_dsn,
        amqp_url=os.getenv("AMQP_URL", ""),
        user_requests_queue=os.getenv("USER_REQUESTS_QUEUE", "user_requests"),
        google_runner_queue=os.getenv("GOOGLE_RUNNER_QUEUE", "user_requests"),
        google_runner_headless=_parse_bool(os.getenv("GOOGLE_RUNNER_HEADLESS"), True),
        google_runner_max_pages=int(os.getenv("GOOGLE_RUNNER_MAX_PAGES", "3")),
        google_runner_page_load_timeout=int(os.getenv("GOOGLE_RUNNER_PAGE_LOAD_TIMEOUT", "20")),
        google_runner_page_delay_seconds=float(os.getenv("GOOGLE_RUNNER_PAGE_DELAY_SECONDS", "3")),
        google_runner_wait_timeout=int(os.getenv("GOOGLE_RUNNER_WAIT_TIMEOUT", "15")),
        google_captcha_timeout_seconds=int(os.getenv("GOOGLE_CAPTCHA_TIMEOUT_SECONDS", "240")),
        google_chrome_profile_directory=os.getenv("GOOGLE_CHROME_PROFILE_DIRECTORY"),
        google_2captcha_api_key=os.getenv("GOOGLE_2CAPTCHA_API_KEY"),
        facebook_runner_queue=os.getenv("FACEBOOK_RUNNER_QUEUE", "facebook.perfil"),
        facebook_runner_type=os.getenv("FACEBOOK_RUNNER_TYPE", "perfil"),
        facebook_runner_headless=_parse_bool(os.getenv("FACEBOOK_RUNNER_HEADLESS"), True),
        facebook_runner_page_load_timeout=int(os.getenv("FACEBOOK_RUNNER_PAGE_LOAD_TIMEOUT", "20")),
        facebook_runner_wait_timeout=int(os.getenv("FACEBOOK_RUNNER_WAIT_TIMEOUT", "15")),
        facebook_chrome_profile_directory=os.getenv("FACEBOOK_CHROME_PROFILE_DIRECTORY"),
        facebook_runner_prefetch_count=int(os.getenv("FACEBOOK_RUNNER_PREFETCH_COUNT", "1")),
        facebook_extractor_concurrency_limit=int(os.getenv("FACEBOOK_EXTRACTOR_CONCURRENCY_LIMIT", "4")),
        facebook_extractor_slot_wait_seconds=int(os.getenv("FACEBOOK_EXTRACTOR_SLOT_WAIT_SECONDS", "120")),
        facebook_download_media=_parse_bool(os.getenv("FACEBOOK_DOWNLOAD_MEDIA"), True),
        facebook_media_local_path=os.getenv("FACEBOOK_MEDIA_LOCAL_PATH", ""),
        facebook_media_collect_from_dom=_parse_bool(os.getenv("FACEBOOK_MEDIA_COLLECT_FROM_DOM"), True),
        facebook_media_prefer_video=_parse_bool(os.getenv("FACEBOOK_MEDIA_PREFER_VIDEO"), True),
        facebook_media_download_timeout_seconds=int(os.getenv("FACEBOOK_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "30")),
        facebook_media_max_bytes=int(os.getenv("FACEBOOK_MEDIA_MAX_BYTES", str(50 * 1024 * 1024))),
        facebook_fetch_comments=_parse_bool(os.getenv("FACEBOOK_FETCH_COMMENTS"), False),
        facebook_comments_per_post=int(os.getenv("FACEBOOK_COMMENTS_PER_POST", "50")),
        message_retry_max_attempts=int(os.getenv("MESSAGE_RETRY_MAX_ATTEMPTS", "3")),
        message_retry_base_delay_ms=int(os.getenv("MESSAGE_RETRY_BASE_DELAY_MS", "30000")),
        service_http_host=os.getenv("SERVICE_HTTP_HOST", "0.0.0.0"),
        service_http_port=int(os.getenv("SERVICE_HTTP_PORT", "8090")),
        log_level=_required_env("LOG_LEVEL"),
        migrations_path=migrations_path,
        facebook_media_storage=os.getenv("FACEBOOK_MEDIA_STORAGE", "auto"),
    )
