import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from app_shared.config import Settings

TWO_CAPTCHA_CREATE_TASK_URL = "https://api.2captcha.com/createTask"
TWO_CAPTCHA_GET_TASK_RESULT_URL = "https://api.2captcha.com/getTaskResult"


class CaptchaResolutionError(Exception):
    pass


def extract_recaptcha_parameters(page_source: str) -> tuple[str | None, str | None]:
    sitekey_match = re.search(r'data-sitekey=["\']([^"\']+)["\']', page_source, re.IGNORECASE)
    data_s_match = re.search(r'data-s=["\']([^"\']+)["\']', page_source, re.IGNORECASE)

    sitekey = sitekey_match.group(1) if sitekey_match else None
    data_s = data_s_match.group(1) if data_s_match else None

    if sitekey:
        return sitekey, data_s

    iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']*recaptcha[^"\']+)["\']', page_source, re.IGNORECASE)
    for iframe_src in iframe_matches:
        query = parse_qs(urlparse(iframe_src).query)
        iframe_sitekey = query.get("k", [None])[0]
        iframe_data_s = query.get("data-s", [None])[0] or query.get("s", [None])[0]
        if iframe_sitekey:
            return iframe_sitekey, iframe_data_s

    return None, None


def serialize_cookies(cookies: list[dict[str, Any]]) -> str | None:
    cookie_items = []
    for cookie in cookies:
        cookie_name = cookie.get("name")
        cookie_value = cookie.get("value")
        if not cookie_name or cookie_value is None:
            continue
        cookie_items.append(f"{cookie_name}={cookie_value}")

    if not cookie_items:
        return None

    return "; ".join(cookie_items)


class TwoCaptchaClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def create_recaptcha_v2_task(
        self,
        page_url: str,
        sitekey: str,
        data_s: str | None = None,
        user_agent: str | None = None,
        cookies: str | None = None,
    ) -> int:
        task: dict[str, Any] = {
            "type": "RecaptchaV2TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "apiDomain": "google.com",
        }

        if data_s:
            task["recaptchaDataSValue"] = data_s
        if user_agent:
            task["userAgent"] = user_agent
        if cookies:
            task["cookies"] = cookies

        response = self._post_json(
            TWO_CAPTCHA_CREATE_TASK_URL,
            {
                "clientKey": self.api_key,
                "task": task,
            },
        )

        if response.get("errorId"):
            raise CaptchaResolutionError(response.get("errorDescription") or response.get("errorCode") or "2Captcha createTask failed")

        task_id = response.get("taskId")
        if task_id is None:
            raise CaptchaResolutionError("2Captcha createTask returned no taskId")

        return int(task_id)

    def wait_for_result(self, task_id: int, timeout_seconds: int = 120, poll_interval_seconds: int = 5) -> str:
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            response = self._post_json(
                TWO_CAPTCHA_GET_TASK_RESULT_URL,
                {
                    "clientKey": self.api_key,
                    "taskId": task_id,
                },
            )

            if response.get("errorId"):
                raise CaptchaResolutionError(response.get("errorDescription") or response.get("errorCode") or "2Captcha getTaskResult failed")

            if response.get("status") == "ready":
                solution = response.get("solution", {})
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                if not token:
                    raise CaptchaResolutionError("2Captcha returned no reCAPTCHA token")
                return token

            time.sleep(poll_interval_seconds)

        raise CaptchaResolutionError("2Captcha timed out while waiting for captcha solution")

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


class CaptchaResolver:
    def __init__(self, settings: Settings, client: TwoCaptchaClient | None = None):
        self.settings = settings
        self.client = client or (
            TwoCaptchaClient(settings.google_2captcha_api_key) if settings.google_2captcha_api_key else None
        )

    @property
    def configured(self) -> bool:
        return self.client is not None

    def resolve_google_captcha(self, driver, timeout_seconds: int | None = None) -> bool:
        if not self.configured or self.client is None:
            return False

        sitekey, data_s = extract_recaptcha_parameters(driver.page_source)
        if not sitekey:
            raise CaptchaResolutionError("Could not extract Google reCAPTCHA sitekey from captcha page")

        user_agent = driver.execute_script("return navigator.userAgent;")
        cookies = serialize_cookies(driver.get_cookies())
        task_id = self.client.create_recaptcha_v2_task(
            page_url=driver.current_url,
            sitekey=sitekey,
            data_s=data_s,
            user_agent=user_agent,
            cookies=cookies,
        )
        token = self.client.wait_for_result(
            task_id,
            timeout_seconds=timeout_seconds
            or max(self.settings.google_captcha_timeout_seconds, self.settings.google_runner_wait_timeout * 6),
        )

        driver.execute_script(
            """
            const token = arguments[0];
            let textarea = document.getElementById("g-recaptcha-response");
            if (!textarea) {
                textarea = document.createElement("textarea");
                textarea.id = "g-recaptcha-response";
                textarea.name = "g-recaptcha-response";
                textarea.style.display = "none";
                (document.forms[0] || document.body).appendChild(textarea);
            }
            textarea.value = token;
            textarea.innerHTML = token;
            textarea.dispatchEvent(new Event("input", { bubbles: true }));
            textarea.dispatchEvent(new Event("change", { bubbles: true }));
            """,
            token,
        )
        driver.execute_script(
            """
            const form = document.querySelector("#captcha-form")
                || document.querySelector("form[action*='/sorry/']")
                || document.querySelector("form");
            if (!form) {
                return false;
            }
            const submitButton = form.querySelector("button[type='submit'], input[type='submit']");
            if (submitButton) {
                submitButton.click();
                return true;
            }
            form.submit();
            return true;
            """
        )
        return True
