from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.agent_runs import router as agent_runs_router
from app.api.approvals import router as approvals_router
from app.api.customers import router as customers_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.refunds import router as refunds_router
from app.api.tickets import router as tickets_router
from app.core.config import get_settings
from app.core.errors import BusinessError
from app.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from app.infrastructure.redis.client import create_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.db_engine = (
        create_database_engine(settings.database_url) if settings.database_url else None
    )
    app.state.db_session_factory = (
        create_session_factory(app.state.db_engine) if app.state.db_engine is not None else None
    )
    app.state.redis_client = create_redis_client(settings.control_redis_url)

    yield

    if app.state.db_engine is not None:
        await app.state.db_engine.dispose()
    await app.state.redis_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.add_exception_handler(BusinessError, business_error_handler)
    application.include_router(health_router)
    application.include_router(agent_runs_router)
    application.include_router(approvals_router)
    application.include_router(customers_router)
    application.include_router(orders_router)
    application.include_router(refunds_router)
    application.include_router(tickets_router)
    return application


async def business_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, BusinessError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )


app = create_app()
