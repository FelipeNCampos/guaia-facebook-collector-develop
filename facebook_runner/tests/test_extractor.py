import os
from pathlib import Path

from app.services.extractor import (
    FacebookExtractor,
    close_facebook_login_popup,
    extract_facebook_profile_data,
    extract_facebook_publication_data,
    extract_media_url_from_dom,
    extract_media_url_from_data,
    format_timestamp,
)
from app_shared.config import Settings


TEST_DB_PORT = int(os.getenv("DB_PORT", "5432"))


class FakeButton:
    def __init__(self, displayed=True):
        self.displayed = displayed
        self.clicked = False

    def is_displayed(self):
        return self.displayed


class FakeDriver:
    def __init__(self):
        self.buttons = [FakeButton()]
        self.script_calls = []

    def find_elements(self, by=None, value=None):
        return self.buttons

    def execute_script(self, script, *args):
        self.script_calls.append((script, args))
        if args:
            args[0].clicked = True
        return False


class FakeMediaElement:
    def __init__(self, src):
        self.src = src

    def get_attribute(self, name):
        if name == "src":
            return self.src
        return ""


class FakeMediaDriver:
    def __init__(self, videos=None, images=None):
        self.videos = [FakeMediaElement(src) for src in videos or []]
        self.images = [FakeMediaElement(src) for src in images or []]

    def find_elements(self, by=None, value=None):
        if value == "video":
            return self.videos
        if value == "img[src]":
            return self.images
        return []


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
        facebook_runner_queue="facebook.video",
        facebook_runner_type="video",
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
        service_http_port=8102,
        log_level="INFO",
        migrations_path=Path("db/migrations"),
    )


def test_extract_facebook_profile_data_reads_name_and_verified():
    html = """
    <html><head><title>OpenAI | Facebook</title></head><body>
    <script type="application/json">
    {"profile":{"name":"OpenAI Brasil","is_verified":true,"creation_time":1735689600}}
    </script>
    </body></html>
    """

    record = extract_facebook_profile_data(html, "https://www.facebook.com/openai")

    assert record["status"] == "finished"
    assert record["perfil"] == "OpenAI Brasil"
    assert record["verificado"] is True
    assert record["data"] == format_timestamp(1735689600)


def test_extract_facebook_publication_data_reads_video_fields():
    html = """
    <html><body>
    <script type="application/json">
    {
      "post": {
        "id": "123",
        "feedback": {
          "reaction_count": {"count": 42},
          "total_comment_count": 7,
          "video_view_count": 1234,
          "owning_profile": {"name": "OpenAI"}
        },
        "comet_sections": {
          "message": {
            "story": {
              "message": {"text": "Descricao do video"},
              "creation_time": 1735689600,
              "actors": [{"name": "OpenAI", "is_verified": true}]
            }
          }
        }
      }
    }
    </script>
    </body></html>
    """

    record = extract_facebook_publication_data(
        html,
        "https://www.facebook.com/openai/videos/123",
        runner_type="video",
        perfil_slug="openai",
    )

    assert record["status"] == "finished"
    assert record["perfil"] == "OpenAI"
    assert record["verificado"] is True
    assert record["descricao"] == "Descricao do video"
    assert record["num_comentarios"] == 7
    assert record["num_visualizacoes"] == 1234
    assert record["num_reacoes"] == 42


def test_extract_facebook_publication_data_reads_direct_media_url():
    html = """
    <html><body>
    <script type="application/json">
    {
      "post": {
        "id": "123",
        "playable_url": "https://video.xx.fbcdn.net/v/t42.1790-2/abc.mp4",
        "feedback": {
          "reaction_count": {"count": 42},
          "total_comment_count": 7,
          "owning_profile": {"name": "OpenAI"}
        },
        "comet_sections": {
          "message": {
            "story": {
              "message": {"text": "Descricao do video"},
              "creation_time": 1735689600,
              "actors": [{"name": "OpenAI", "is_verified": true}]
            }
          }
        }
      }
    }
    </script>
    </body></html>
    """

    record = extract_facebook_publication_data(
        html,
        "https://www.facebook.com/openai/videos/123",
        runner_type="video",
        perfil_slug="openai",
    )

    assert record["media_url"] == "https://video.xx.fbcdn.net/v/t42.1790-2/abc.mp4"
    assert record["media_type"] == "video"


def test_extract_media_url_from_dom_reads_og_video_first():
    html = """
    <html><head>
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/photo.jpg" />
      <meta property="og:video:url" content="https://video.xx.fbcdn.net/v/t42.1790-2/video.mp4" />
    </head></html>
    """

    media_url, media_type = extract_media_url_from_dom(html)

    assert media_url == "https://video.xx.fbcdn.net/v/t42.1790-2/video.mp4"
    assert media_type == "video"


def test_extract_media_url_from_dom_reads_og_image():
    html = """
    <html><head>
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/photo.jpg" />
    </head></html>
    """

    media_url, media_type = extract_media_url_from_dom(html)

    assert media_url == "https://scontent.xx.fbcdn.net/v/t39.30808-6/photo.jpg"
    assert media_type == "imagem"


def test_extract_media_url_from_dom_reads_video_tag():
    html = """
    <html><body>
      <video src="https://video.xx.fbcdn.net/v/t42.1790-2/rendered.mp4"></video>
    </body></html>
    """

    media_url, media_type = extract_media_url_from_dom(html)

    assert media_url == "https://video.xx.fbcdn.net/v/t42.1790-2/rendered.mp4"
    assert media_type == "video"


def test_extract_media_url_from_dom_reads_img_tag():
    html = """
    <html><body>
      <img src="https://scontent.xx.fbcdn.net/v/t39.30808-6/rendered.webp" />
    </body></html>
    """

    media_url, media_type = extract_media_url_from_dom(html)

    assert media_url == "https://scontent.xx.fbcdn.net/v/t39.30808-6/rendered.webp"
    assert media_type == "imagem"


def test_extract_facebook_publication_data_uses_dom_media_fallback():
    html = """
    <html><head>
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/fallback.jpg" />
    </head><body>
    <script type="application/json">
    {
      "post": {
        "id": "123",
        "feedback": {
          "reaction_count": {"count": 42},
          "total_comment_count": 7,
          "owning_profile": {"name": "OpenAI"}
        },
        "comet_sections": {
          "message": {
            "story": {
              "message": {"text": "Descricao do post"},
              "creation_time": 1735689600,
              "actors": [{"name": "OpenAI", "is_verified": true}]
            }
          }
        }
      }
    }
    </script>
    </body></html>
    """

    record = extract_facebook_publication_data(
        html,
        "https://www.facebook.com/openai/posts/123",
        runner_type="post",
        perfil_slug="openai",
    )

    assert record["media_url"] == "https://scontent.xx.fbcdn.net/v/t39.30808-6/fallback.jpg"
    assert record["media_type"] == "imagem"


def test_extract_media_url_from_data_ignores_post_page_url():
    assert extract_media_url_from_data({"url": "https://www.facebook.com/openai/posts/123"}) == ""


def test_extract_facebook_publication_data_reads_supported_content_types():
    html = """
    <html><body>
    <script type="application/json">
    {
      "post": {
        "id": "123",
        "feedback": {
          "reaction_count": {"count": 42},
          "total_comment_count": 7,
          "video_view_count": 1234,
          "owning_profile": {"name": "OpenAI"}
        },
        "comet_sections": {
          "message": {
            "story": {
              "message": {"text": "Descricao do conteudo"},
              "creation_time": 1735689600,
              "actors": [{"name": "OpenAI", "is_verified": true}]
            }
          }
        }
      }
    }
    </script>
    </body></html>
    """

    supported_sources = {
        "post": "https://www.facebook.com/openai/posts/123",
        "storie": "https://www.facebook.com/story.php?story_fbid=123&id=456",
        "foto": "https://www.facebook.com/openai/photos/123",
        "permalink": "https://www.facebook.com/permalink.php?story_fbid=123&id=456",
    }

    for runner_type, source_url in supported_sources.items():
        record = extract_facebook_publication_data(
            html,
            source_url,
            runner_type=runner_type,
            perfil_slug="openai",
        )

        assert record["status"] == "finished"
        assert record["perfil"] == "OpenAI"
        assert record["verificado"] is True
        assert record["descricao"] == "Descricao do conteudo"
        assert record["num_comentarios"] == 7
        assert record["num_visualizacoes"] == 1234
        assert record["num_reacoes"] == 42


def test_extract_facebook_publication_data_marks_login_required():
    html = """
    <html><body>
      <form><input name="email" /><input name="pass" type="password" /></form>
      <div>Entrar no Facebook</div>
      <script>var x = "CAAFBLoginHomepageRootQueryRelayPreloader";</script>
    </body></html>
    """

    record = extract_facebook_publication_data(
        html,
        "https://www.facebook.com/openai/reel/123",
        runner_type="reel",
        perfil_slug="openai",
        current_url="https://www.facebook.com/login",
    )

    assert record["status"] == "failed"
    assert record["error"] == "login_obrigatorio"


def test_close_facebook_login_popup_clicks_visible_button():
    driver = FakeDriver()

    closed = close_facebook_login_popup(driver, timeout=1)

    assert closed is True
    assert driver.buttons[0].clicked is True


def test_extract_media_url_from_live_dom_reads_video_tag():
    extractor = FacebookExtractor(build_settings())
    driver = FakeMediaDriver(
        videos=["https://video.xx.fbcdn.net/v/t42.1790-2/live.mp4"],
        images=["https://scontent.xx.fbcdn.net/v/t39.30808-6/live.jpg"],
    )

    media_url = extractor._extract_media_url_from_live_dom(driver, "video")
    media_url_with_type, media_type = extractor._extract_media_from_live_dom(driver, "video")

    assert media_url == "https://video.xx.fbcdn.net/v/t42.1790-2/live.mp4"
    assert media_url_with_type == media_url
    assert media_type == "video"


def test_extract_media_url_from_live_dom_falls_back_to_image():
    extractor = FacebookExtractor(build_settings())
    driver = FakeMediaDriver(images=["https://scontent.xx.fbcdn.net/v/t39.30808-6/live.jpg"])

    media_url = extractor._extract_media_url_from_live_dom(driver, "post")
    media_url_with_type, media_type = extractor._extract_media_from_live_dom(driver, "post")

    assert media_url == "https://scontent.xx.fbcdn.net/v/t39.30808-6/live.jpg"
    assert media_url_with_type == media_url
    assert media_type == "imagem"
