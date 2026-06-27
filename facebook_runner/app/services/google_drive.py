import io
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _build_service():
    raw = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON nao definida")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON invalido: {exc}") from exc

    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def is_configured() -> bool:
    return bool(
        os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
        and os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    )


def _quote_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_folder_path(folder_path: list[str] | tuple[str, ...] | None = None) -> str:
    parent_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if not parent_id:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID nao definida")
    if not folder_path:
        return parent_id

    service = _build_service()
    for raw_name in folder_path:
        name = str(raw_name or "").strip()
        if not name:
            continue
        query = (
            "mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{_quote_query_value(name)}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        result = (
            service.files()
            .list(q=query, fields="files(id,name)", pageSize=1, supportsAllDrives=True)
            .execute()
        )
        files = result.get("files") or []
        if files:
            parent_id = files[0]["id"]
            continue

        folder = (
            service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        parent_id = folder["id"]
    return parent_id


def upload_bytes(
    data: bytes,
    filename: str,
    mimetype: str = "application/octet-stream",
    folder_id: str | None = None,
    folder_path: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, str]:
    service = _build_service()
    target_folder = folder_id or ensure_folder_path(folder_path)
    metadata: dict = {"name": filename}
    if target_folder:
        metadata["parents"] = [target_folder]

    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype=mimetype,
        resumable=True,
        chunksize=4 * 1024 * 1024,
    )

    file = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    file_id = file.get("id", "")
    if file_id:
        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()
            file = (
                service.files()
                .get(fileId=file_id, fields="id,webViewLink", supportsAllDrives=True)
                .execute()
            )
        except Exception:
            pass

    return file_id, file.get("webViewLink", "")
