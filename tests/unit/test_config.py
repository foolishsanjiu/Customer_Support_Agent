from app.core.config import Settings


def test_settings_have_safe_local_defaults(monkeypatch) -> None:
    for variable in (
        "APP_NAME",
        "DATABASE_URL",
        "CELERY_BROKER_URL",
        "LANGGRAPH_REDIS_URL",
        "CONTROL_REDIS_URL",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "ResolveX"
    assert settings.database_url is None
    assert settings.celery_broker_url.endswith("/0")
    assert settings.langgraph_redis_url.endswith("/1")
    assert settings.control_redis_url.endswith("/2")
    assert (
        len(
            {
                settings.celery_broker_url,
                settings.langgraph_redis_url,
                settings.control_redis_url,
            }
        )
        == 3
    )
    assert settings.api_rate_limit_requests == 60
    assert settings.api_rate_limit_window_seconds == 60
    assert settings.external_circuit_failure_threshold == 3
    assert settings.external_circuit_recovery_seconds == 30
    assert settings.max_agent_steps == 18
    assert settings.max_agent_repair_attempts == 1
    assert settings.context_max_estimated_tokens == 6000
