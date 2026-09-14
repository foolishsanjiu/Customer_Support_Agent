from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ResolveX"
    app_env: str = "development"
    app_debug: bool = False

    database_url: str | None = None
    celery_broker_url: str = "redis://localhost:6379/0"
    langgraph_redis_url: str = "redis://localhost:6379/1"
    control_redis_url: str = "redis://localhost:6379/2"

    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
