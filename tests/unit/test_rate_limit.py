from collections import defaultdict

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app import main as main_module
from app.core.config import Settings
from app.main import create_app
from app.models.enums import PrincipalRole
from app.security.principal import AuthenticatedPrincipal
from app.security.rate_limit import (
    RateLimitMiddleware,
    RedisFixedWindowRateLimiter,
    request_identity,
)


class FakeRedis:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.unavailable = unavailable

    async def eval(self, script: str, numkeys: int, key: str, ttl: int) -> int:
        assert "INCR" in script
        assert numkeys == 1
        assert ttl > 0
        if self.unavailable:
            raise RedisError("redis unavailable")
        self.counts[key] += 1
        return self.counts[key]

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_fixed_window_enforces_limit_and_resets() -> None:
    current_time = [100.0]
    limiter = RedisFixedWindowRateLimiter(
        FakeRedis(),
        requests=2,
        window_seconds=10,
        clock=lambda: current_time[0],
    )

    first = await limiter.check("principal:one")
    second = await limiter.check("principal:one")
    rejected = await limiter.check("principal:one")

    assert first.allowed is True and first.remaining == 1
    assert second.allowed is True and second.remaining == 0
    assert rejected.allowed is False and rejected.retry_after == 10

    current_time[0] = 110.0
    reset = await limiter.check("principal:one")
    assert reset.allowed is True and reset.remaining == 1


def test_request_identity_prefers_valid_principal_and_isolates_subjects(monkeypatch) -> None:
    def authenticate(token: str) -> AuthenticatedPrincipal:
        if token == "invalid":
            raise HTTPException(401, "invalid")
        return AuthenticatedPrincipal(token, PrincipalRole.CUSTOMER, 1)

    monkeypatch.setattr("app.security.rate_limit.authenticate_token", authenticate)
    first = request_identity(_scope(token="customer-a", client="203.0.113.9"))
    second = request_identity(_scope(token="customer-b", client="203.0.113.9"))
    fallback = request_identity(_scope(token="invalid", client="203.0.113.9"))

    assert first.startswith("principal:")
    assert first != second
    assert fallback.startswith("ip:")


def test_middleware_limits_business_api_but_not_health() -> None:
    application = _application(FakeRedis(), requests=2)

    with TestClient(application) as client:
        assert client.get("/api/v1/ping").status_code == 200
        second = client.get("/api/v1/ping")
        rejected = client.get("/api/v1/ping")
        for _ in range(3):
            assert client.get("/health/live").status_code == 200

    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"]
    assert rejected.json()["error"]["code"] == "rate_limit_exceeded"


def test_middleware_fails_open_when_redis_is_unavailable() -> None:
    application = _application(FakeRedis(unavailable=True), requests=1)

    with TestClient(application) as client:
        first = client.get("/api/v1/ping")
        second = client.get("/api/v1/ping")

    assert first.status_code == 200
    assert second.status_code == 200


def test_application_rate_limit_response_keeps_correlation_id(monkeypatch) -> None:
    redis = FakeRedis()
    settings = Settings(
        _env_file=None,
        database_url=None,
        api_rate_limit_requests=1,
        api_rate_limit_window_seconds=60,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "create_redis_client", lambda _url: redis)
    application = create_app()

    @application.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(application) as client:
        assert client.get("/api/v1/ping").status_code == 200
        rejected = client.get(
            "/api/v1/ping",
            headers={"X-Request-ID": "rate-limit-request"},
        )

    assert rejected.status_code == 429
    assert rejected.headers["X-Request-ID"] == "rate-limit-request"


def _application(redis: FakeRedis, *, requests: int) -> FastAPI:
    application = FastAPI()
    application.state.redis_client = redis
    application.add_middleware(
        RateLimitMiddleware,
        requests=requests,
        window_seconds=60,
    )

    @application.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    return application


def _scope(*, token: str | None = None, client: str) -> dict:
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"headers": headers, "client": (client, 1234)}
