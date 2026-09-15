from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import (
    HTTPX2ClientInstrumentor,
    HTTPXClientInstrumentor,
)
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None


def configure_tracing(*, service: str, endpoint: str, enabled: bool) -> TracerProvider | None:
    global _provider
    if not enabled:
        return None
    if _provider is not None:
        return _provider
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                insecure=endpoint.startswith("http://"),
            )
        )
    )
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    HTTPX2ClientInstrumentor().instrument(tracer_provider=provider)
    SQLAlchemyInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    return provider


def instrument_fastapi(app: FastAPI, *, service: str, endpoint: str, enabled: bool) -> None:
    provider = configure_tracing(service=service, endpoint=endpoint, enabled=enabled)
    if provider is None:
        return
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="/health/live,/health/ready",
        exclude_spans=["receive", "send"],
    )
    CeleryInstrumentor().instrument(tracer_provider=provider)


def instrument_celery(*, service: str, endpoint: str, enabled: bool) -> None:
    provider = configure_tracing(service=service, endpoint=endpoint, enabled=enabled)
    if provider is not None:
        CeleryInstrumentor().instrument(tracer_provider=provider)


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


@contextmanager
def start_span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    tracer = trace.get_tracer("resolvex")
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
