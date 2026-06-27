import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse
from datetime import datetime, timezone
from uuid import uuid4

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

from app.services.captcha import CaptchaResolutionError, CaptchaResolver
from app_shared.config import Settings
from app_shared.observability import log_event

LEGACY_CATEGORY_TO_TYPE = {
    "Perfil": "perfil",
    "Videos": "video",
    "Reels": "reel",
    "Posts": "post",
    "Stories": "storie",
    "Fotos": "foto",
    "Permalinks": "permalink",
}

FACEBOOK_PROFILE_PATH_EXCLUSIONS = {
    "watch",
    "reel",
    "reels",
    "story.php",
    "permalink.php",
    "photo.php",
    "photos",
    "photo",
}

GOOGLE_SEARCH_URL = "https://www.google.com/search?q={query}"


class GoogleCaptchaDetectedError(Exception):
    pass


@dataclass(frozen=True)
class GoogleSearchResult:
    records: list[dict]
    pages_visited: int


def build_google_query(
    search_term: str,
    after: str | None = None,
    before: str | None = None,
    *,
    exact_phrase: bool = True,
) -> str:
    query_parts = ["site:facebook.com"]

    if search_term:
        query_parts.append(f'"{search_term}"' if exact_phrase else search_term)
    if after:
        query_parts.append(f"after:{after}")
    if before:
        query_parts.append(f"before:{before}")

    return " ".join(query_parts)


def normalize_google_result_url(href: str | None) -> str | None:
    if not href:
        return None

    target_url = None
    if href.startswith("/url?"):
        parsed_href = urlparse(href)
        target_url = parse_qs(parsed_href.query).get("q", [None])[0]
    elif href.startswith(("http://", "https://")):
        target_url = href

    if not target_url:
        return None

    parsed_target = urlparse(target_url)
    host = parsed_target.netloc.lower()
    if "google." in host or "facebook.com" not in host:
        return None

    return parsed_target._replace(fragment="").geturl()


def classify_facebook_url(url: str) -> str:
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    path_parts = [part for part in path.split("/") if part]
    query = parse_qs(parsed_url.query)

    if len(path_parts) >= 2 and path_parts[1] == "posts":
        return "Posts"
    if "/posts/" in path:
        return "Posts"
    if "/permalink.php" in path:
        return "Permalinks"
    if "/story.php" in path or "story_fbid" in query:
        return "Stories"
    if "/reel/" in path or "/reels/" in path:
        return "Reels"
    if "/photo/" in path or "/photos/" in path or "/photo.php" in path:
        return "Fotos"
    if "fbid" in query:
        return "Fotos"
    if "/videos/" in path or "/watch/" in path or "v" in query:
        return "Videos"
    return "Perfil"


def get_profile_name_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if path_parts and path_parts[0].lower() not in FACEBOOK_PROFILE_PATH_EXCLUSIONS:
        return path_parts[0]
    return ""


def extract_facebook_results(page_source: str) -> list[dict]:
    soup = BeautifulSoup(page_source, "html.parser")
    records = []
    seen_links = set()

    for anchor in soup.select("a[href]"):
        link = normalize_google_result_url(anchor.get("href", ""))
        if not link or link in seen_links:
            continue

        seen_links.add(link)
        category = classify_facebook_url(link)
        records.append(
            {
                "fonte": link,
                "categoria": category,
                "perfil": get_profile_name_from_url(link),
            }
        )

    return records


def is_google_captcha_page(driver) -> bool:
    current_url = driver.current_url.lower()
    parsed_url = urlparse(current_url)
    current_host = parsed_url.netloc.lower()
    current_path = parsed_url.path.lower()
    page_source = driver.page_source.lower()
    page_title = (driver.title or "").lower()

    if "/sorry/" in current_path or "/sorry/" in current_url:
        return True
    if "google." not in current_host:
        return False

    captcha_markers = [
        "our systems have detected unusual traffic",
        "detected unusual traffic from your computer network",
        "unusual traffic from your computer network",
        "this page appears when google automatically detects requests",
        "not a robot",
        'id="captcha-form"',
        'action="/sorry/index"',
        'name="captcha"',
        "g-recaptcha",
        "recaptcha/api.js",
    ]
    title_markers = ["unusual traffic", "not a robot", "sorry"]

    return any(marker in page_source for marker in captcha_markers) or any(
        marker in page_title for marker in title_markers
    )


def wait_for_results_page(driver, timeout: int = 15) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            lambda current_driver: extract_facebook_results(current_driver.page_source)
            or current_driver.find_elements(By.CSS_SELECTOR, "#search")
            or current_driver.find_elements(By.CSS_SELECTOR, "a#pnnext")
            or is_google_captcha_page(current_driver)
        )
    except TimeoutException:
        pass


def get_next_page_url(driver) -> str | None:
    next_selectors = ["a#pnnext", "a[aria-label='Next page']"]

    for selector in next_selectors:
        next_links = driver.find_elements(By.CSS_SELECTOR, selector)
        for next_link in next_links:
            href = next_link.get_attribute("href")
            if href:
                return href

    current_url = urlparse(driver.current_url)
    current_query = parse_qs(current_url.query)
    current_start = int(current_query.get("start", ["0"])[0])

    for next_link in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = next_link.get_attribute("href")
        if not href or "google." not in urlparse(href).netloc.lower():
            continue

        parsed_href = urlparse(href)
        if "/search" not in parsed_href.path:
            continue

        next_query = parse_qs(parsed_href.query)
        next_start = next_query.get("start", [None])[0]
        if next_start is None:
            continue

        try:
            if int(next_start) > current_start:
                return href
        except ValueError:
            continue

    return None


class SeleniumGoogleSearchClient:
    def __init__(
        self,
        settings: Settings,
        captcha_resolver: CaptchaResolver | None = None,
        logger=None,
    ):
        self.settings = settings
        self.captcha_resolver = captcha_resolver
        self.logger = logger

    def collect_results(
        self,
        search_term: str,
        after: str | None = None,
        before: str | None = None,
        *,
        request_id: str = "",
        id_query: int | str | None = None,
        user_id: str = "",
        trace_id: str = "",
        attempt: int = 0,
    ) -> GoogleSearchResult:
        driver, profile_directory = self._create_driver()
        query_variants = [("exact_phrase", build_google_query(search_term, after=after, before=before))]
        if self._should_retry_without_exact_phrase(search_term):
            query_variants.append(
                (
                    "broad_terms",
                    build_google_query(
                        search_term,
                        after=after,
                        before=before,
                        exact_phrase=False,
                    ),
                )
            )
        collected_records = []
        seen_links = set()
        visited_pages = set()
        total_pages_visited = 0
        result_limit = max(1, int(os.getenv("FACEBOOK_RESULT_LIMIT", "200")))

        try:
            for query_mode, google_query in query_variants:
                search_url = GOOGLE_SEARCH_URL.format(query=quote_plus(google_query))
                variant_pages_visited = 0

                if query_mode == "broad_terms":
                    self._log_captcha_event(
                        "info",
                        "google_search_retrying_without_exact_phrase",
                        request_id=request_id,
                        id_query=id_query,
                        user_id=user_id,
                        trace_id=trace_id,
                        attempt=attempt,
                        search_term=search_term,
                    )

                self._load_url(
                    driver,
                    search_url,
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=attempt,
                )
                while variant_pages_visited < self.settings.google_runner_max_pages:
                    wait_for_results_page(driver, timeout=self.settings.google_runner_wait_timeout)

                    if is_google_captcha_page(driver):
                        if not self.captcha_resolver or not self.captcha_resolver.configured:
                            raise GoogleCaptchaDetectedError("google_captcha_detected")

                        captcha_started_at = datetime.now(timezone.utc)
                        self._log_captcha_event(
                            "info",
                            "google_captcha_resolution_started",
                            request_id=request_id,
                            id_query=id_query,
                            user_id=user_id,
                            trace_id=trace_id,
                            attempt=attempt,
                            captcha_provider="2captcha",
                            current_url=driver.current_url,
                        )
                        try:
                            resolved = self.captcha_resolver.resolve_google_captcha(driver)
                        except CaptchaResolutionError as exc:
                            duration_ms = int((datetime.now(timezone.utc) - captcha_started_at).total_seconds() * 1000)
                            self._log_captcha_event(
                                "warning",
                                "google_captcha_resolution_failed",
                                request_id=request_id,
                                id_query=id_query,
                                user_id=user_id,
                                trace_id=trace_id,
                                attempt=attempt,
                                captcha_provider="2captcha",
                                current_url=driver.current_url,
                                duration_ms=duration_ms,
                                error_type="captcha_resolution_failed",
                                error=str(exc),
                            )
                            raise GoogleCaptchaDetectedError("google_captcha_detected") from exc

                        if not resolved:
                            duration_ms = int((datetime.now(timezone.utc) - captcha_started_at).total_seconds() * 1000)
                            self._log_captcha_event(
                                "warning",
                                "google_captcha_resolution_returned_false",
                                request_id=request_id,
                                id_query=id_query,
                                user_id=user_id,
                                trace_id=trace_id,
                                attempt=attempt,
                                captcha_provider="2captcha",
                                current_url=driver.current_url,
                                duration_ms=duration_ms,
                                error_type="captcha_resolution_failed",
                                error="captcha_resolution_returned_false",
                            )
                            raise GoogleCaptchaDetectedError("google_captcha_detected")

                        wait_for_results_page(driver, timeout=self.settings.google_runner_wait_timeout)
                        if is_google_captcha_page(driver):
                            duration_ms = int((datetime.now(timezone.utc) - captcha_started_at).total_seconds() * 1000)
                            self._log_captcha_event(
                                "warning",
                                "google_captcha_resolution_did_not_clear_page",
                                request_id=request_id,
                                id_query=id_query,
                                user_id=user_id,
                                trace_id=trace_id,
                                attempt=attempt,
                                captcha_provider="2captcha",
                                current_url=driver.current_url,
                                duration_ms=duration_ms,
                                error_type="captcha_resolution_failed",
                                error="captcha_page_still_present",
                            )
                            raise GoogleCaptchaDetectedError("google_captcha_detected")
                        duration_ms = int((datetime.now(timezone.utc) - captcha_started_at).total_seconds() * 1000)
                        self._log_captcha_event(
                            "info",
                            "google_captcha_resolution_completed",
                            request_id=request_id,
                            id_query=id_query,
                            user_id=user_id,
                            trace_id=trace_id,
                            attempt=attempt,
                            captcha_provider="2captcha",
                            current_url=driver.current_url,
                            duration_ms=duration_ms,
                        )

                    current_page_url = driver.current_url
                    if current_page_url in visited_pages:
                        break

                    visited_pages.add(current_page_url)
                    variant_pages_visited += 1
                    total_pages_visited += 1

                    page_records = extract_facebook_results(driver.page_source)
                    for record in page_records:
                        if record["fonte"] in seen_links:
                            continue
                        seen_links.add(record["fonte"])
                        collected_records.append(record)
                        if len(collected_records) >= result_limit:
                            break

                    if len(collected_records) >= result_limit:
                        self._log_captcha_event(
                            "info",
                            "google_search_result_limit_reached",
                            request_id=request_id,
                            id_query=id_query,
                            user_id=user_id,
                            trace_id=trace_id,
                            attempt=attempt,
                            result_limit=result_limit,
                        )
                        break

                    next_page_url = get_next_page_url(driver)
                    if not next_page_url or next_page_url in visited_pages:
                        break

                    time.sleep(max(0, self.settings.google_runner_page_delay_seconds))
                    self._load_url(
                        driver,
                        next_page_url,
                        request_id=request_id,
                        id_query=id_query,
                        user_id=user_id,
                        trace_id=trace_id,
                        attempt=attempt,
                    )

                if collected_records:
                    break
        finally:
            try:
                driver.quit()
            except Exception as exc:
                self._log_captcha_event(
                    "warning",
                    "google_chrome_quit_failed",
                    request_id=request_id,
                    id_query=id_query,
                    user_id=user_id,
                    trace_id=trace_id,
                    attempt=attempt,
                    error=str(exc),
                )
            finally:
                self._cleanup_browser_processes_for_profile(profile_directory)
                self._cleanup_profile_directory(profile_directory)

        return GoogleSearchResult(records=collected_records, pages_visited=total_pages_visited)

    def _should_retry_without_exact_phrase(self, search_term: str) -> bool:
        normalized_term = " ".join(search_term.split())
        return len(normalized_term.split(" ")) > 1

    def _create_driver(self):
        last_error: Exception | None = None

        for startup_attempt in range(1, self.settings.google_chrome_startup_attempts + 1):
            profile_directory = self._create_profile_directory()
            try:
                driver = self._build_driver(profile_directory)
                driver.set_page_load_timeout(self.settings.google_runner_page_load_timeout)
                return driver, profile_directory
            except Exception as exc:
                last_error = exc
                self._log_captcha_event(
                    "warning",
                    "google_chrome_startup_failed",
                    startup_attempt=startup_attempt,
                    max_startup_attempts=self.settings.google_chrome_startup_attempts,
                    error=str(exc),
                )
                self._cleanup_profile_directory(profile_directory)
                self._cleanup_stale_browser_processes()
                time.sleep(min(2 * startup_attempt, 10))

        if last_error is not None:
            raise last_error
        raise RuntimeError("Chrome startup failed")

    def _build_driver(self, profile_directory: Path):
        options = Options()
        options.page_load_strategy = "eager"
        binary_candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser"]
        chrome_binary = None
        for binary_candidate in binary_candidates:
            if Path(binary_candidate).exists():
                options.binary_location = binary_candidate
                chrome_binary = binary_candidate
                break

        if self.settings.google_runner_headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-sync")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--no-first-run")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(f"--user-data-dir={profile_directory}")

        driver = uc.Chrome(
            options=options,
            browser_executable_path=chrome_binary,
            driver_executable_path="/usr/bin/chromedriver" if Path("/usr/bin/chromedriver").exists() else None,
            use_subprocess=False,
        )
        return driver

    def _create_profile_directory(self) -> Path:
        if self.settings.google_chrome_profile_directory:
            base_directory = Path(self.settings.google_chrome_profile_directory)
            base_directory.mkdir(parents=True, exist_ok=True)
            profile_directory = base_directory / f"session-{int(time.time() * 1000)}-{os.getpid()}-{uuid4().hex}"
            profile_directory.mkdir(parents=True, exist_ok=False)
            return profile_directory

        return Path(tempfile.mkdtemp(prefix="guaia-google-chrome-"))

    def _cleanup_profile_directory(self, profile_directory: Path) -> None:
        try:
            shutil.rmtree(profile_directory, ignore_errors=True)
        except Exception as exc:
            self._log_captcha_event(
                "warning",
                "google_chrome_profile_cleanup_failed",
                profile_directory=str(profile_directory),
                error=str(exc),
            )

    def _cleanup_browser_processes_for_profile(self, profile_directory: Path) -> None:
        try:
            subprocess.run(
                ["pkill", "-f", f"--user-data-dir={profile_directory}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception as exc:
            self._log_captcha_event(
                "warning",
                "google_chrome_process_cleanup_failed",
                profile_directory=str(profile_directory),
                error=str(exc),
            )

    def _cleanup_stale_browser_processes(self) -> None:
        try:
            subprocess.run(
                ["pkill", "-f", "chromedriver|chromium"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception as exc:
            self._log_captcha_event(
                "warning",
                "google_chrome_process_cleanup_failed",
                error=str(exc),
            )

    def _load_url(
        self,
        driver,
        url: str,
        *,
        request_id: str = "",
        id_query: int | str | None = None,
        user_id: str = "",
        trace_id: str = "",
        attempt: int = 0,
    ) -> None:
        try:
            driver.get(url)
        except TimeoutException as exc:
            self._stop_loading_after_timeout(
                driver,
                event="google_page_load_timeout_recovered",
                url=url,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=attempt,
                error=str(exc),
            )
        except WebDriverException as exc:
            error = str(exc)
            if "Timed out receiving message from renderer" not in error:
                raise
            self._stop_loading_after_timeout(
                driver,
                event="google_renderer_timeout_recovered",
                url=url,
                request_id=request_id,
                id_query=id_query,
                user_id=user_id,
                trace_id=trace_id,
                attempt=attempt,
                error=error,
            )

    def _stop_loading_after_timeout(self, driver, *, event: str, **fields) -> None:
        try:
            driver.execute_script("window.stop();")
        except Exception as exc:
            fields["stop_error"] = str(exc)
        self._log_captcha_event("warning", event, **fields)

    def _log_captcha_event(self, level: str, event: str, **fields) -> None:
        if self.logger is None:
            return
        log_event(self.logger, level, event, **fields)
