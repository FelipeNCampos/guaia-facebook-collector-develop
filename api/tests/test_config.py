from app_shared.config import get_settings


def test_get_settings_uses_db_port_in_fallback_dsn(monkeypatch):
    values = {
        "APP_ENV": "test",
        "API_HOST": "0.0.0.0",
        "API_PORT": "8080",
        "DB_HOST": "db",
        "DB_PORT": "5544",
        "DB_NAME": "guaia",
        "DB_USER": "guaia",
        "DB_PASSWORD": "coto1423",
        "LOG_LEVEL": "INFO",
    }

    for key, value in values.items():
        monkeypatch.setenv(key, value)

    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.db_port == 5544
    assert settings.db_dsn == "postgresql://guaia:coto1423@db:5544/guaia"
    assert settings.allowed_hosts == ("*",)

    get_settings.cache_clear()


def test_get_settings_preserves_explicit_db_dsn(monkeypatch):
    values = {
        "APP_ENV": "test",
        "API_HOST": "0.0.0.0",
        "API_PORT": "8080",
        "DB_HOST": "db",
        "DB_PORT": "5544",
        "DB_NAME": "guaia",
        "DB_USER": "guaia",
        "DB_PASSWORD": "coto1423",
        "DB_DSN": "postgresql://custom:custom@remote:9999/customdb",
        "ALLOWED_HOSTS": "api.local, localhost",
        "LOG_LEVEL": "INFO",
    }

    for key, value in values.items():
        monkeypatch.setenv(key, value)

    get_settings.cache_clear()

    settings = get_settings()

    assert settings.db_port == 5544
    assert settings.db_dsn == "postgresql://custom:custom@remote:9999/customdb"
    assert settings.allowed_hosts == ("api.local", "localhost")

    get_settings.cache_clear()
