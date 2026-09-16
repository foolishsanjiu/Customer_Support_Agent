from functools import lru_cache

from pydantic import Field, SecretStr
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
    service_name: str = "resolvex-api"
    log_level: str = "INFO"
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    database_url: str | None = None
    celery_broker_url: str = "redis://localhost:6379/0"
    langgraph_redis_url: str = "redis://localhost:6379/0"
    control_redis_url: str = "redis://localhost:6379/2"

    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    max_agent_steps: int = 12
    max_recovery_attempts: int = 3
    external_circuit_failure_threshold: int = Field(default=3, ge=1)
    external_circuit_recovery_seconds: float = Field(default=30, gt=0)
    approval_ttl_minutes: int = 1440
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = "resolvex"
    jwt_audience: str = "resolvex-api"
    api_rate_limit_requests: int = Field(default=60, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    chroma_path: str = "data/chroma"
    policy_directory: str = "policies"
    policy_top_k: int = 3
    context_message_limit: int = 20
    embedding_model: str = "BAAI/bge-m3"
    embedding_cache_dir: str | None = "D:/CondaEnvs/resolvex/models"
    logistics_mcp_url: str = "http://localhost:8001/mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
