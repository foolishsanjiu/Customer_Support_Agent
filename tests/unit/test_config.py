from app.core.config import Settings


def test_settings_have_safe_local_defaults(monkeypatch) -> None:
    for variable in ("APP_NAME", "DATABASE_URL", "CONTROL_REDIS_URL"):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "ResolveX"
    assert settings.database_url is None
    assert settings.control_redis_url.endswith("/2")
    assert settings.api_rate_limit_requests == 60
    assert settings.api_rate_limit_window_seconds == 60
