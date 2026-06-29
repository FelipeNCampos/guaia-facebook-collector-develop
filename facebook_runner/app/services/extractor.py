import json
import hashlib
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

from app_shared.config import Settings

RUNNER_TYPE_TO_CATEGORY = {
    "perfil": "Perfil",
    "video": "Videos",
    "reel": "Reels",
    "post": "Posts",
    "storie": "Stories",
    "foto": "Fotos",
    "permalink": "Permalinks",
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


def get_profile_name_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if path_parts and path_parts[0].lower() not in FACEBOOK_PROFILE_PATH_EXCLUSIONS:
        return path_parts[0]
    return ""


def get_facebook_content_id_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    path_parts = [part for part in parsed_url.path.split("/") if part]

    for index, path_part in enumerate(path_parts):
        if path_part.lower() in {"reel", "reels", "videos", "watch", "posts", "photos", "photo"} and index + 1 < len(path_parts):
            if path_parts[index + 1].isdigit():
                return path_parts[index + 1]
        if path_part.isdigit():
            return path_part

    query = parse_qs(parsed_url.query)
    for key in ("v", "fbid", "story_fbid"):
        values = query.get(key)
        if values:
            return values[0]

    return ""


def get_text_value(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("__html")
        if isinstance(text, str):
            return text.strip()
    return ""


def get_nested_value(value, path):
    current_value = value
    for key in path:
        if not isinstance(current_value, dict):
            return None
        current_value = current_value.get(key)
    return current_value


def first_filled(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def parse_count(value):
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)

    normalized_value = str(value).strip().lower().replace("\xa0", " ")
    multiplier = 1
    if "mil" in normalized_value or normalized_value.endswith("k"):
        multiplier = 1000
    elif "mi" in normalized_value or normalized_value.endswith("m"):
        multiplier = 1000000

    number_match = re.search(r"\d+(?:[.,]\d+)?", normalized_value)
    if not number_match:
        return ""

    number_text = number_match.group(0)
    if "," in number_text and "." in number_text:
        number_text = number_text.replace(".", "").replace(",", ".")
    elif "," in number_text:
        number_text = number_text.replace(",", ".")

    try:
        return int(float(number_text) * multiplier)
    except ValueError:
        return ""


MEDIA_URL_KEYS = (
    "playable_url_quality_hd",
    "playable_url",
    "browser_native_hd_url",
    "browser_native_sd_url",
    "hd_src",
    "sd_src",
    "video_url",
    "src",
    "uri",
    "url",
)

MEDIA_CONTAINER_KEYS = (
    "image",
    "thumbnail",
    "preferred_thumbnail",
    "photo_image",
    "video",
    "media",
)

MEDIA_HOST_HINTS = ("fbcdn.net", "fbsbx.com", "cdninstagram.com", "scontent")
MEDIA_PATH_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".m4v",
)


def is_candidate_media_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate.startswith(("http://", "https://")):
        return False

    parsed = urlparse(candidate)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if any(host_hint in host for host_hint in MEDIA_HOST_HINTS):
        return True
    return any(path.endswith(extension) for extension in MEDIA_PATH_EXTENSIONS)


def infer_media_type(media_url: str) -> str:
    path = urlparse(media_url).path.lower()
    if any(path.endswith(extension) for extension in (".mp4", ".mov", ".m4v")):
        return "video"
    if media_url:
        return "imagem"
    return ""


def extract_media_url_from_data(data) -> str:
    if not isinstance(data, dict):
        return ""

    for key in MEDIA_URL_KEYS:
        value = data.get(key)
        if is_candidate_media_url(value):
            return value.strip()

    for key in MEDIA_CONTAINER_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            nested_value = extract_media_url_from_data(value)
            if nested_value:
                return nested_value
        elif is_candidate_media_url(value):
            return value.strip()

    return ""


def extract_media_url_from_dom(page_source: str, prefer_video: bool = True) -> tuple[str, str]:
    soup = BeautifulSoup(page_source, "html.parser")

    video_meta_selectors = (
        'meta[property="og:video"][content]',
        'meta[property="og:video:url"][content]',
    )
    image_meta_selectors = (
        'meta[property="og:image"][content]',
    )

    def first_candidate(selectors: tuple[str, ...], attribute: str, media_type: str) -> tuple[str, str]:
        for selector in selectors:
            for element in soup.select(selector):
                media_url = str(element.get(attribute) or "").strip()
                if is_candidate_media_url(media_url):
                    return media_url, media_type
        return "", ""

    meta_groups = (
        (video_meta_selectors, "video"),
        (image_meta_selectors, "imagem"),
    )
    tag_groups = (
        (("video[src]",), "video"),
        (("img[src]",), "imagem"),
    )
    if not prefer_video:
        meta_groups = tuple(reversed(meta_groups))
        tag_groups = tuple(reversed(tag_groups))

    for selectors, media_type in meta_groups:
        media_url, found_type = first_candidate(selectors, "content", media_type)
        if media_url:
            return media_url, found_type

    for selectors, media_type in tag_groups:
        media_url, found_type = first_candidate(selectors, "src", media_type)
        if media_url:
            return media_url, found_type

    return "", ""


def format_timestamp(value):
    if value in (None, ""):
        return ""
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def iter_json_objects_from_scripts(page_source: str):
    soup = BeautifulSoup(page_source, "html.parser")
    for script in soup.select('script[type="application/json"], script[type="application/ld+json"]'):
        script_content = script.get_text(strip=True)
        if not script_content:
            continue
        try:
            loaded_json = json.loads(script_content)
        except json.JSONDecodeError:
            continue
        yield loaded_json


def walk_json_objects(value):
    if isinstance(value, dict):
        yield value
        for child_value in value.values():
            yield from walk_json_objects(child_value)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json_objects(item)


def json_object_contains_value(value, expected_value: str) -> bool:
    if not expected_value:
        return True
    if isinstance(value, dict):
        return any(json_object_contains_value(child_value, expected_value) for child_value in value.values())
    if isinstance(value, list):
        return any(json_object_contains_value(item, expected_value) for item in value)
    return str(value) == expected_value or expected_value in str(value)


def extract_actor_from_story(story):
    actors = story.get("actors") if isinstance(story, dict) else None
    if isinstance(actors, list) and actors:
        return actors[0]
    return {}


def extract_actor_from_comet_sections(comet_sections):
    if not isinstance(comet_sections, dict):
        return {}
    for section_name in ("title", "actor_photo", "metadata", "message"):
        section = comet_sections.get(section_name)
        section_items = section if isinstance(section, list) else [section]
        for section_item in section_items:
            story = section_item.get("story") if isinstance(section_item, dict) else None
            actor = extract_actor_from_story(story)
            if actor.get("name") or actor.get("is_verified") not in (None, ""):
                return actor
    return {}


def extract_verified_from_comet_sections(comet_sections):
    if not isinstance(comet_sections, dict):
        return ""
    for section_name in ("title", "actor_photo", "metadata", "message"):
        section = comet_sections.get(section_name)
        section_items = section if isinstance(section, list) else [section]
        for section_item in section_items:
            story = section_item.get("story") if isinstance(section_item, dict) else None
            actor = extract_actor_from_story(story)
            if actor.get("is_verified") not in (None, ""):
                return actor.get("is_verified")
            story_sections = story.get("comet_sections") if isinstance(story, dict) else None
            if isinstance(story_sections, dict) and story_sections.get("badge"):
                return True
    return ""


def extract_story_from_comet_sections(comet_sections):
    if not isinstance(comet_sections, dict):
        return {}
    for section_name in ("metadata", "title", "actor_photo", "message"):
        section = comet_sections.get(section_name)
        section_items = section if isinstance(section, list) else [section]
        for section_item in section_items:
            if isinstance(section_item, dict) and isinstance(section_item.get("story"), dict):
                return section_item["story"]
    return {}


def extract_message_from_comet_sections(comet_sections):
    message_section = comet_sections.get("message") if isinstance(comet_sections, dict) else None
    story = message_section.get("story") if isinstance(message_section, dict) else None
    message = story.get("message") if isinstance(story, dict) else None
    return get_text_value(message)


def is_facebook_login_required_page(page_source: str, current_url: str = "") -> bool:
    parsed_url = urlparse(current_url or "")
    if parsed_url.path.lower().rstrip("/") == "/login":
        return True

    soup = BeautifulSoup(page_source, "html.parser")
    canonical = soup.select_one('link[rel="canonical"][href]')
    if canonical:
        canonical_url = urlparse(canonical.get("href", ""))
        if canonical_url.path.lower().rstrip("/") == "/login":
            return True

    page_text = soup.get_text(" ", strip=True).lower()
    has_login_form = bool(
        soup.select_one('input[name="email"], input[type="email"], input[name="pass"], input[type="password"]')
    )
    has_login_preloader = "CAAFBLoginHomepageRootQueryRelayPreloader" in page_source
    has_login_copy = (
        "entrar no facebook" in page_text
        or "entre no facebook" in page_text
        or "log in to facebook" in page_text
    )
    return has_login_form and (has_login_preloader or has_login_copy)


def build_profile_record(source_url: str) -> dict:
    return {
        "fonte": source_url,
        "status": "finished",
        "error": "",
        "data": "",
        "perfil": get_profile_name_from_url(source_url),
        "verificado": "",
    }


def build_content_record(source_url: str, runner_type: str, perfil_slug: str = "") -> dict:
    return {
        "fonte": source_url,
        "status": "finished",
        "error": "",
        "data": "",
        "perfil": perfil_slug or get_profile_name_from_url(source_url),
        "verificado": "",
        "descricao": "",
        "num_comentarios": "",
        "num_visualizacoes": "",
        "num_reacoes": "",
        "categoria": RUNNER_TYPE_TO_CATEGORY[runner_type],
        "media_url": "",
        "media_type": "",
        "drive_file_id": "",
        "drive_url": "",
        "_comments": [],
    }


def merge_profile_data(record: dict, data: dict) -> None:
    comet_sections = data.get("comet_sections")
    story = extract_story_from_comet_sections(comet_sections)
    actor = extract_actor_from_story(story)
    direct_actor = extract_actor_from_story(data)
    if direct_actor:
        actor = {**actor, **direct_actor}
    section_actor = extract_actor_from_comet_sections(comet_sections)
    if section_actor:
        actor = {**actor, **section_actor}
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}

    record["data"] = first_filled(
        record.get("data"),
        format_timestamp(story.get("creation_time") if isinstance(story, dict) else None),
        format_timestamp(data.get("creation_time")),
        format_timestamp(data.get("created_time")),
    )
    record["perfil"] = first_filled(
        actor.get("name") if isinstance(actor, dict) else "",
        owner.get("name"),
        data.get("name"),
        record.get("perfil"),
        get_text_value(data.get("title")),
    )
    record["verificado"] = first_filled(
        actor.get("is_verified") if isinstance(actor, dict) else "",
        owner.get("is_verified"),
        data.get("is_verified"),
        record.get("verificado"),
        extract_verified_from_comet_sections(comet_sections),
    )


def is_facebook_publication_data(data):
    if not isinstance(data, dict):
        return False
    feedback = data.get("feedback")
    return bool(
        isinstance(feedback, dict)
        and (
            "reaction_count" in feedback
            or "total_comment_count" in feedback
            or "comment_rendering_instance" in feedback
            or "comment_rendering_instance_for_feed_location" in feedback
            or "comments_count_summary_renderer" in feedback
            or "video_view_count" in feedback
            or "video_view_count_renderer" in feedback
        )
    )


def merge_publication_data(record: dict, data: dict) -> None:
    feedback = data.get("feedback") if isinstance(data.get("feedback"), dict) else {}
    feedback_owner = feedback.get("owning_profile") if isinstance(feedback.get("owning_profile"), dict) else {}
    reaction_count = feedback.get("reaction_count")
    if isinstance(reaction_count, dict):
        reaction_count = reaction_count.get("count")

    comment_rendering_comments = get_nested_value(
        feedback,
        ("comment_rendering_instance", "comments", "total_count"),
    )
    feed_location_comments = get_nested_value(
        feedback,
        ("comment_rendering_instance_for_feed_location", "comments", "total_count"),
    )
    comments_summary = get_nested_value(
        feedback,
        ("comments_count_summary_renderer", "feedback", "comment_rendering_instance", "comments", "total_count"),
    )
    video_view_feedback = get_nested_value(feedback, ("video_view_count_renderer", "feedback"))
    if not isinstance(video_view_feedback, dict):
        video_view_feedback = {}

    comet_sections = data.get("comet_sections")
    story = extract_story_from_comet_sections(comet_sections)
    actor = extract_actor_from_story(story)
    direct_actor = extract_actor_from_story(data)
    if direct_actor:
        actor = {**actor, **direct_actor}
    section_actor = extract_actor_from_comet_sections(comet_sections)
    if section_actor:
        actor = {**actor, **section_actor}
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    title = get_text_value(data.get("title"))

    record["data"] = first_filled(
        record.get("data"),
        format_timestamp(story.get("creation_time") if isinstance(story, dict) else None),
        format_timestamp(data.get("creation_time")),
        format_timestamp(data.get("created_time")),
    )
    record["perfil"] = first_filled(
        actor.get("name") if isinstance(actor, dict) else "",
        owner.get("name"),
        feedback_owner.get("name"),
        record.get("perfil"),
    )
    record["verificado"] = first_filled(
        record.get("verificado"),
        actor.get("is_verified") if isinstance(actor, dict) else "",
        owner.get("is_verified"),
        extract_verified_from_comet_sections(comet_sections),
        "badge" in story.get("comet_sections", {}) if isinstance(story.get("comet_sections"), dict) else "",
    )
    record["num_comentarios"] = first_filled(
        record.get("num_comentarios"),
        parse_count(feedback.get("total_comment_count")),
        parse_count(comment_rendering_comments),
        parse_count(feed_location_comments),
        parse_count(comments_summary),
        parse_count(feedback.get("comment_count_reduced")),
    )
    record["descricao"] = first_filled(
        record.get("descricao"),
        extract_message_from_comet_sections(comet_sections),
        title,
        data.get("name"),
    )
    record["num_visualizacoes"] = first_filled(
        record.get("num_visualizacoes"),
        parse_count(video_view_feedback.get("video_view_count")),
        parse_count(video_view_feedback.get("video_view_count_reduced")),
        parse_count(feedback.get("video_view_count")),
        parse_count(feedback.get("video_view_count_reduced")),
        parse_count(feedback.get("play_count")),
        parse_count(feedback.get("play_count_reduced")),
        parse_count(data.get("video_view_count")),
    )
    record["num_reacoes"] = first_filled(
        record.get("num_reacoes"),
        parse_count(reaction_count),
        parse_count(feedback.get("i18n_reaction_count")),
    )
    record["media_url"] = first_filled(
        record.get("media_url"),
        extract_media_url_from_data(data),
    )
    record["media_type"] = first_filled(
        record.get("media_type"),
        infer_media_type(record.get("media_url") or ""),
    )
    merge_comments(record, extract_comments_from_data(data))


def stable_comment_id(source_url: str, author: str, text: str, created_at: str = "") -> str:
    raw = f"{source_url}|{author}|{text}|{created_at}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def extract_comments_from_data(data) -> list[dict]:
    comments = []
    if not isinstance(data, dict):
        return comments

    for candidate in walk_json_objects(data):
        if not isinstance(candidate, dict):
            continue

        body = candidate.get("body") or candidate.get("message") or candidate.get("text")
        text = get_text_value(body)
        if not text or len(text) < 2:
            continue

        author = (
            candidate.get("author")
            or candidate.get("commenter")
            or candidate.get("feedback_author")
            or candidate.get("owning_profile")
        )
        author_name = ""
        author_id = ""
        if isinstance(author, dict):
            author_name = get_text_value(author.get("name")) or str(author.get("username") or "")
            author_id = str(author.get("id") or author.get("__id__") or "")

        created_at = first_filled(
            format_timestamp(candidate.get("created_time")),
            format_timestamp(candidate.get("creation_time")),
        )
        likes = parse_count(
            candidate.get("like_count")
            or candidate.get("reaction_count")
            or candidate.get("i18n_reaction_count")
        )

        comments.append(
            {
                "comment_id": str(candidate.get("id") or ""),
                "author_name": author_name,
                "author_id": author_id,
                "content": text,
                "likes": likes if likes not in ("", None) else 0,
                "created_at": created_at,
            }
        )

    return comments


def merge_comments(record: dict, comments: list[dict]) -> None:
    if not comments:
        return

    current = record.setdefault("_comments", [])
    seen = {
        comment.get("comment_id")
        or stable_comment_id(
            record.get("fonte") or "",
            comment.get("author_name") or "",
            comment.get("content") or "",
            comment.get("created_at") or "",
        )
        for comment in current
    }
    for comment in comments:
        comment_id = comment.get("comment_id") or stable_comment_id(
            record.get("fonte") or "",
            comment.get("author_name") or "",
            comment.get("content") or "",
            comment.get("created_at") or "",
        )
        if comment_id in seen:
            continue
        comment["comment_id"] = comment_id
        current.append(comment)


def has_facebook_publication_details(record: dict) -> bool:
    detail_fields = [
        "data",
        "num_comentarios",
        "descricao",
        "num_visualizacoes",
        "num_reacoes",
    ]
    return any(record.get(field) not in ("", None) for field in detail_fields)


def has_complete_facebook_publication_details(record: dict) -> bool:
    detail_fields = [
        "data",
        "perfil",
        "num_comentarios",
        "descricao",
        "num_visualizacoes",
        "num_reacoes",
    ]
    return all(record.get(field) not in ("", None) for field in detail_fields)


def count_facebook_publication_details(record: dict) -> int:
    detail_fields = [
        "data",
        "perfil",
        "verificado",
        "num_comentarios",
        "descricao",
        "num_visualizacoes",
        "num_reacoes",
    ]
    return sum(1 for field in detail_fields if record.get(field) not in ("", None))


def extract_facebook_profile_data(page_source: str, source_url: str, current_url: str = "") -> dict:
    record = build_profile_record(source_url)
    if is_facebook_login_required_page(page_source, current_url=current_url):
        record["status"] = "failed"
        record["error"] = "login_obrigatorio"
        return record

    for json_object in iter_json_objects_from_scripts(page_source):
        for data in walk_json_objects(json_object):
            if isinstance(data.get("comet_sections"), dict) or data.get("name") or data.get("title"):
                merge_profile_data(record, data)

    if record["perfil"] in ("", None):
        soup = BeautifulSoup(page_source, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "")
        record["perfil"] = first_filled(record["perfil"], title.split("|")[0].strip(), get_profile_name_from_url(source_url))

    if record["perfil"] in ("", None):
        record["status"] = "failed"
        record["error"] = "dados_perfil_nao_encontrados"

    return record


def extract_facebook_publication_data(
    page_source: str,
    source_url: str,
    runner_type: str,
    perfil_slug: str = "",
    current_url: str = "",
    collect_media_from_dom: bool = True,
    prefer_video: bool = True,
) -> dict:
    record = build_content_record(source_url, runner_type, perfil_slug=perfil_slug)
    if is_facebook_login_required_page(page_source, current_url=current_url):
        record["status"] = "failed"
        record["error"] = "login_obrigatorio"
        return record

    content_id = get_facebook_content_id_from_url(source_url)

    def scan_publication_data(require_content_match: bool = True, scanned_record: dict | None = None) -> dict:
        current_record = scanned_record or build_content_record(source_url, runner_type, perfil_slug=perfil_slug)
        for json_object in iter_json_objects_from_scripts(page_source):
            for data in walk_json_objects(json_object):
                if require_content_match and content_id and not json_object_contains_value(data, content_id):
                    continue
                if is_facebook_publication_data(data) or isinstance(data.get("comet_sections"), dict):
                    merge_publication_data(current_record, data)
        return current_record

    record = scan_publication_data(require_content_match=True)
    if content_id and not has_complete_facebook_publication_details(record):
        relaxed_record = scan_publication_data(require_content_match=False)
        if count_facebook_publication_details(relaxed_record) > count_facebook_publication_details(record):
            record = relaxed_record
        else:
            record = scan_publication_data(require_content_match=False, scanned_record=record)

    if collect_media_from_dom and not record.get("media_url"):
        dom_url, dom_type = extract_media_url_from_dom(page_source, prefer_video=prefer_video)
        if dom_url:
            record["media_url"] = dom_url
            record["media_type"] = dom_type or infer_media_type(dom_url)

    if not has_facebook_publication_details(record):
        record["status"] = "failed"
        record["error"] = "dados_publicacao_nao_encontrados"

    return record


def close_facebook_login_popup(driver, timeout: int = 3) -> bool:
    end_time = time.time() + timeout
    close_xpaths = [
        "//div[@aria-label='Fechar' or @aria-label='Close']",
        "//div[@role='button' and (@aria-label='Fechar' or @aria-label='Close')]",
        "//span[normalize-space()='Agora não']/ancestor::*[@role='button'][1]",
        "//span[normalize-space()='Not now']/ancestor::*[@role='button'][1]",
        "//span[normalize-space()='Entrar depois']/ancestor::*[@role='button'][1]",
    ]

    while time.time() < end_time:
        for close_xpath in close_xpaths:
            try:
                close_buttons = driver.find_elements(By.XPATH, close_xpath)
            except WebDriverException:
                continue
            for close_button in close_buttons:
                try:
                    if not close_button.is_displayed():
                        continue
                    driver.execute_script("arguments[0].click();", close_button)
                    time.sleep(0.2)
                    return True
                except WebDriverException:
                    continue

        try:
            popup_closed = driver.execute_script(
                """
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
                for (const dialog of dialogs) {
                  const closeButton = dialog.querySelector('[aria-label="Fechar"], [aria-label="Close"]');
                  if (closeButton) {
                    closeButton.click();
                    return true;
                  }
                }
                return false;
                """
            )
            if popup_closed:
                time.sleep(0.2)
                return True
        except WebDriverException:
            pass

        time.sleep(0.2)
    return False


class FacebookExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def enrich_link(self, runner_type: str, fonte: str, perfil_slug: str = "") -> dict:
        driver = self._create_driver()
        try:
            self._load_url(driver, fonte)
            try:
                WebDriverWait(driver, self.settings.facebook_runner_wait_timeout).until(
                    lambda current_driver: current_driver.find_elements(By.TAG_NAME, "body")
                )
            except TimeoutException:
                pass

            close_facebook_login_popup(driver)
            time.sleep(1)
            close_facebook_login_popup(driver, timeout=1)

            if runner_type == "perfil":
                return extract_facebook_profile_data(driver.page_source, fonte, current_url=driver.current_url)

            record = extract_facebook_publication_data(
                driver.page_source,
                fonte,
                runner_type=runner_type,
                perfil_slug=perfil_slug,
                current_url=driver.current_url,
                collect_media_from_dom=self.settings.facebook_media_collect_from_dom,
                prefer_video=self.settings.facebook_media_prefer_video,
            )
            if self.settings.facebook_media_collect_from_dom and not record.get("media_url"):
                live_url, live_type = self._extract_media_from_live_dom(driver, runner_type)
                if live_url:
                    record["media_url"] = live_url
                    record["media_type"] = live_type or infer_media_type(live_url)
            return record
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _create_driver(self):
        options = Options()
        options.page_load_strategy = "eager"
        binary_candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser"]
        chrome_binary = None
        for binary_candidate in binary_candidates:
            if Path(binary_candidate).exists():
                options.binary_location = binary_candidate
                chrome_binary = binary_candidate
                break

        if self.settings.facebook_runner_headless:
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

        if self.settings.facebook_chrome_profile_directory:
            profile_directory = Path(self.settings.facebook_chrome_profile_directory)
            profile_directory.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={profile_directory}")

        driver = uc.Chrome(
            options=options,
            browser_executable_path=chrome_binary,
            driver_executable_path="/usr/bin/chromedriver" if Path("/usr/bin/chromedriver").exists() else None,
            use_subprocess=False,
        )
        driver.set_page_load_timeout(self.settings.facebook_runner_page_load_timeout)
        return driver

    def _load_url(self, driver, url: str) -> None:
        try:
            driver.get(url)
        except TimeoutException:
            self._stop_loading(driver)
        except WebDriverException as exc:
            error = str(exc)
            if "Timed out receiving message from renderer" not in error:
                raise
            self._stop_loading(driver)

    def _stop_loading(self, driver) -> None:
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass

    def _extract_media_url_from_live_dom(self, driver, runner_type: str) -> str:
        media_url, _ = self._extract_media_from_live_dom(driver, runner_type)
        return media_url

    def _extract_media_from_live_dom(self, driver, runner_type: str) -> tuple[str, str]:
        search_order = []
        if self.settings.facebook_media_prefer_video or runner_type in ("video", "reel"):
            search_order.append((By.TAG_NAME, "video", "src", "video"))
            search_order.append((By.CSS_SELECTOR, "img[src]", "src", "imagem"))
        else:
            search_order.append((By.CSS_SELECTOR, "img[src]", "src", "imagem"))
            search_order.append((By.TAG_NAME, "video", "src", "video"))

        for by, selector, attribute, media_type in search_order:
            try:
                elements = driver.find_elements(by, selector)
            except WebDriverException:
                continue
            for element in elements:
                try:
                    media_url = str(element.get_attribute(attribute) or "").strip()
                except WebDriverException:
                    continue
                if is_candidate_media_url(media_url):
                    return media_url, media_type

        return "", ""
