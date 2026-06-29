from types import SimpleNamespace

from app.services import google_drive
from app.services.media_archiver import FacebookMediaArchiver


def build_settings(tmp_path, app_env="development", storage="auto"):
    return SimpleNamespace(
        app_env=app_env,
        facebook_download_media=True,
        facebook_media_storage=storage,
        facebook_media_local_path=str(tmp_path),
        facebook_media_download_timeout_seconds=60,
        facebook_media_max_bytes=262144000,
    )


def build_record():
    return {
        "fonte": "https://www.facebook.com/openai/posts/123",
        "media_url": "https://scontent.xx.fbcdn.net/v/t39.30808-6/photo.jpg",
        "media_type": "imagem",
    }


def test_archive_uses_local_path_in_development(monkeypatch, tmp_path):
    settings = build_settings(tmp_path, app_env="development")
    archiver = FacebookMediaArchiver(settings)
    record = build_record()

    monkeypatch.setattr(google_drive, "is_configured", lambda: True)
    monkeypatch.setattr(
        google_drive,
        "upload_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected drive upload")),
    )
    monkeypatch.setattr(archiver, "_download", lambda media_url: (b"image-bytes", "image/jpeg"))

    archived = archiver.archive(record, "post")

    assert archived["drive_file_id"] == ""
    assert archived["drive_url"].startswith("file://")
    local_path = archived["drive_url"].removeprefix("file://")
    assert local_path.startswith(str(tmp_path))
    assert open(local_path, "rb").read() == b"image-bytes"


def test_archive_uses_google_drive_in_production(monkeypatch, tmp_path):
    settings = build_settings(tmp_path, app_env="production")
    archiver = FacebookMediaArchiver(settings)
    record = build_record()
    uploaded = {}

    def fake_upload(data, filename, mimetype, folder_path):
        uploaded["data"] = data
        uploaded["filename"] = filename
        uploaded["mimetype"] = mimetype
        uploaded["folder_path"] = folder_path
        return "drive-file-id", "https://drive.google.com/file/d/drive-file-id/view"

    monkeypatch.setattr(google_drive, "is_configured", lambda: True)
    monkeypatch.setattr(google_drive, "upload_bytes", fake_upload)
    monkeypatch.setattr(archiver, "_download", lambda media_url: (b"image-bytes", "image/jpeg"))

    archived = archiver.archive(record, "post", query_context={"id_query": 42})

    assert archived["drive_file_id"] == "drive-file-id"
    assert archived["drive_url"] == "https://drive.google.com/file/d/drive-file-id/view"
    assert uploaded["data"] == b"image-bytes"
    assert uploaded["mimetype"] == "image/jpeg"
    assert uploaded["folder_path"][0:2] == ["facebook", "query_42"]
    assert not list(tmp_path.iterdir())


def test_archive_does_not_fallback_to_local_in_production_without_drive(monkeypatch, tmp_path):
    settings = build_settings(tmp_path, app_env="production")
    archiver = FacebookMediaArchiver(settings)
    record = build_record()

    monkeypatch.setattr(google_drive, "is_configured", lambda: False)
    monkeypatch.setattr(
        archiver,
        "_download",
        lambda media_url: (_ for _ in ()).throw(AssertionError("unexpected download")),
    )

    archived = archiver.archive(record, "post")

    assert archived is record
    assert "drive_file_id" not in archived
    assert "drive_url" not in archived
    assert not list(tmp_path.iterdir())
