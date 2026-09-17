from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.agent_runs import router as agent_runs_router
from app.api.approvals import router as approvals_router
from app.api.customers import router as customers_router
from app.api.dlq import router as dlq_router
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
from app.observability import (
    CorrelationMiddleware,
    configure_logging,
    instrument_fastapi,
    shutdown_tracing,
)
from app.security.rate_limit import RateLimitMiddleware


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
    shutdown_tracing()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.add_middleware(
        RateLimitMiddleware,
        requests=settings.api_rate_limit_requests,
        window_seconds=settings.api_rate_limit_window_seconds,
    )
    application.add_middleware(CorrelationMiddleware)
    instrument_fastapi(
        application,
        service=settings.service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        enabled=settings.otel_enabled,
    )
    application.add_exception_handler(BusinessError, business_error_handler)
    application.include_router(health_router)
    application.include_router(agent_runs_router)
    application.include_router(approvals_router)
    application.include_router(customers_router)
    application.include_router(dlq_router)
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
