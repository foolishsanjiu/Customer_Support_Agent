from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.logging import get_logger
from app.security.principal import authenticate_token

RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""
logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RedisFixedWindowRateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        requests: int,
        window_seconds: int,
        clock: Callable[[], float] = time,
    ) -> None:
        self.redis = redis
        self.requests = requests
        self.window_seconds = window_seconds
        self.clock = clock

    async def check(self, identity: str) -> RateLimitDecision:
        now = int(self.clock())
        bucket = now // self.window_seconds
        key = f"resolvex:rate-limit:{bucket}:{identity}"
        count = int(
            await self.redis.eval(
                RATE_LIMIT_SCRIPT,
                1,
                key,
                self.window_seconds + 1,
            )
        )
        return RateLimitDecision(
            allowed=count <= self.requests,
            limit=self.requests,
            remaining=max(0, self.requests - count),
            retry_after=max(1, (bucket + 1) * self.window_seconds - now),
        )


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, requests: int, window_seconds: int) -> None:
        self.app = app
        self.requests = requests
        self.window_seconds = window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_business_api(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        limiter = RedisFixedWindowRateLimiter(
            scope["app"].state.redis_client,
            requests=self.requests,
            window_seconds=self.window_seconds,
        )
        try:
            decision = await limiter.check(request_identity(scope))
        except RedisError as exc:
            logger.warning("api_rate_limit_unavailable", error_type=type(exc).__name__)
            await self.app(scope, receive, send)
            return

        headers = [
            (b"x-ratelimit-limit", str(decision.limit).encode("ascii")),
            (b"x-ratelimit-remaining", str(decision.remaining).encode("ascii")),
        ]
        if not decision.allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Too many requests.",
                    }
                },
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_rate_limit(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *headers]
            await send(message)

        await self.app(scope, receive, send_with_rate_limit)


def request_identity(scope: Scope) -> str:
    authorization = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and token:
        try:
            principal = authenticate_token(token)
        except HTTPException:
            pass
        else:
            return "principal:" + _digest(f"{principal.role.value}:{principal.principal_id}")
    client = scope.get("client")
    host = client[0] if client else "unknown"
    return "ip:" + _digest(host)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _is_business_api(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")
