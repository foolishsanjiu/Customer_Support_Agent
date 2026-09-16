import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.security.rate_limit import RedisFixedWindowRateLimiter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with Redis running",
    ),
]


@pytest.mark.asyncio
async def test_redis_rate_limit_counter_is_atomic() -> None:
    redis = Redis.from_url(
        os.getenv("CONTROL_REDIS_URL", "redis://localhost:6379/2"),
        decode_responses=True,
    )
    identity = f"integration:{uuid4().hex}"
    limiter = RedisFixedWindowRateLimiter(redis, requests=5, window_seconds=60)
    try:
        decisions = await asyncio.gather(*(limiter.check(identity) for _ in range(20)))

        assert sum(decision.allowed for decision in decisions) == 5
        assert min(decision.remaining for decision in decisions) == 0
        keys = [key async for key in redis.scan_iter(f"resolvex:rate-limit:*:{identity}")]
        assert len(keys) == 1
        assert 0 < await redis.ttl(keys[0]) <= 61
    finally:
        keys = [key async for key in redis.scan_iter(f"resolvex:rate-limit:*:{identity}")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
