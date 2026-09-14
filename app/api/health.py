import asyncio
import logging

from fastapi import APIRouter, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


async def check_database(engine: AsyncEngine | None) -> bool:
    if engine is None:
        return False
    try:
        async with asyncio.timeout(4):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database readiness check failed: %s", type(exc).__name__)
        return False


async def check_redis(client: Redis) -> bool:
    try:
        async with asyncio.timeout(2):
            return bool(await client.ping())
    except Exception:
        return False


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    database_ok, redis_ok = await asyncio.gather(
        check_database(request.app.state.db_engine),
        check_redis(request.app.state.redis_client),
    )
    ready_status = database_ok and redis_ok
    if not ready_status:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready_status else "not_ready",
        "checks": {
            "mysql": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
    }
