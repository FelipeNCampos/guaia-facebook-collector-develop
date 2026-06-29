import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.services import google_drive
from app_shared.config import Settings

MEDIA_REFERER = "https://www.facebook.com/"
PRODUCTION_ENVS = {"prod", "production"}
STORAGE_AUTO = "auto"
STORAGE_GOOGLE_DRIVE = "google_drive"
STORAGE_LOCAL = "local"


def infer_media_mimetype(url: str, content_type: str = "") -> str:
    if content_type:
        return content_type.split(";", 1)[0].strip()

    guessed_type, _ = mimetypes.guess_type(urlparse(url).path)
    return guessed_type or "application/octet-stream"


def media_filename(runner_type: str, source_url: str, mimetype: str) -> str:
    content_id = re.sub(r"[^A-Za-z0-9_-]+", "_", source_url).strip("_")[-80:]
    extension = mimetypes.guess_extension(mimetype) or ".bin"
    return f"facebook_{runner_type}_{content_id or 'media'}{extension}"


def safe_slug(value: str, default: str = "sem-valor") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80] or default


def publication_date(record: dict) -> str:
    raw = record.get("data")
    if raw:
        for candidate in (str(raw), str(raw).replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidate).date().isoformat()
            except ValueError:
                continue
    return datetime.now(timezone.utc).date().isoformat()


def drive_folder_path(record: dict, runner_type: str, query_context: dict | None = None) -> list[str]:
    query_context = query_context or {}
    id_query = query_context.get("id_query") or "manual"
    post_id = record.get("fonte") or record.get("media_url") or "sem-id"
    media_type = record.get("media_type") or runner_type
    return [
        "facebook",
        f"query_{safe_slug(str(id_query), 'manual')}",
        publication_date(record),
        f"post_{safe_slug(post_id, 'sem-id')}",
        safe_slug(media_type, "media"),
    ]


class FacebookMediaArchiver:
    def __init__(self, settings: Settings, logger=None):
        self.settings = settings
        self.logger = logger

    def archive(self, record: dict, runner_type: str, query_context: dict | None = None) -> dict:
        if not self.settings.facebook_download_media:
            return record

        media_url = str(record.get("media_url") or "").strip()
        if not media_url:
            return record

        storage = self._storage_mode()
        if storage == STORAGE_LOCAL:
            if not self.settings.facebook_media_local_path:
                self._warn("facebook_media_local_path_not_configured", media_url=media_url)
                return record
            return self._archive_locally(record, runner_type)

        if not google_drive.is_configured():
            self._warn("facebook_media_drive_not_configured", media_url=media_url)
            return record

        try:
            data, mimetype = self._download(media_url)
            filename = media_filename(runner_type, record.get("fonte") or media_url, mimetype)
            file_id, drive_url = google_drive.upload_bytes(
                data,
                filename,
                mimetype=mimetype,
                folder_path=drive_folder_path(record, runner_type, query_context),
            )
        except Exception as exc:
            self._warn("facebook_media_archive_failed", media_url=media_url, error=str(exc))
            return record

        record["drive_file_id"] = file_id
        record["drive_url"] = drive_url
        return record

    def _archive_locally(self, record: dict, runner_type: str) -> dict:
        media_url = str(record.get("media_url") or "").strip()
        try:
            data, mimetype = self._download(media_url)
            filename = media_filename(runner_type, record.get("fonte") or media_url, mimetype)
            base_path = Path(self.settings.facebook_media_local_path)
            base_path.mkdir(parents=True, exist_ok=True)
            local_path = base_path / filename
            local_path.write_bytes(data)
        except Exception as exc:
            self._warn("facebook_media_local_archive_failed", media_url=media_url, error=str(exc))
            return record

        record["drive_file_id"] = ""
        record["drive_url"] = f"file://{local_path}"
        return record

    def _storage_mode(self) -> str:
        requested = str(getattr(self.settings, "facebook_media_storage", "") or STORAGE_AUTO).strip().lower()
        requested = requested.replace("-", "_")
        if requested in {"drive", "google", "gdrive"}:
            return STORAGE_GOOGLE_DRIVE
        if requested in {STORAGE_GOOGLE_DRIVE, STORAGE_LOCAL}:
            return requested
        if requested not in {"", STORAGE_AUTO}:
            self._warn("facebook_media_storage_invalid", storage=requested)

        app_env = str(getattr(self.settings, "app_env", "") or "").strip().lower()
        if app_env in PRODUCTION_ENVS:
            return STORAGE_GOOGLE_DRIVE
        return STORAGE_LOCAL

    def _download(self, media_url: str) -> tuple[bytes, str]:
        headers = {
            "Accept": "*/*",
            "Referer": MEDIA_REFERER,
            "User-Agent": "Mozilla/5.0",
        }
        with requests.get(
            media_url,
            headers=headers,
            stream=True,
            timeout=self.settings.facebook_media_download_timeout_seconds,
        ) as response:
            response.raise_for_status()
            mimetype = infer_media_mimetype(media_url, response.headers.get("Content-Type", ""))
            chunks = bytearray()
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                chunks.extend(chunk)
                if len(chunks) > self.settings.facebook_media_max_bytes:
                    raise RuntimeError("facebook_media_max_bytes_exceeded")
            return bytes(chunks), mimetype

    def _warn(self, event: str, **extra) -> None:
        if self.logger:
            try:
                self.logger.warning("%s | %s", event, extra)
            except Exception:
                pass
