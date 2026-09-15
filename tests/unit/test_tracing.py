from contextlib import contextmanager
from types import SimpleNamespace

from app.observability import tracing


class FakeProvider:
    def __init__(self, resource=None) -> None:
        self.resource = resource
        self.processors = []
        self.shutdown_called = False

    def add_span_processor(self, processor) -> None:
        self.processors.append(processor)

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeInstrumentor:
    calls: list[dict] = []

    def instrument(self, **values) -> None:
        self.calls.append(values)


def test_configure_tracing_builds_one_provider(monkeypatch) -> None:
    provider = FakeProvider()
    exporters: list[dict] = []
    set_providers: list[object] = []
    FakeInstrumentor.calls = []
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr(tracing, "TracerProvider", lambda resource: provider)
    monkeypatch.setattr(tracing.Resource, "create", lambda values: values)
    monkeypatch.setattr(
        tracing, "OTLPSpanExporter", lambda **values: exporters.append(values) or "exporter"
    )
    monkeypatch.setattr(tracing, "BatchSpanProcessor", lambda exporter: ("batch", exporter))
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", set_providers.append)
    monkeypatch.setattr(tracing, "HTTPXClientInstrumentor", FakeInstrumentor)
    monkeypatch.setattr(tracing, "HTTPX2ClientInstrumentor", FakeInstrumentor)
    monkeypatch.setattr(tracing, "SQLAlchemyInstrumentor", FakeInstrumentor)

    assert (
        tracing.configure_tracing(service="worker", endpoint="http://jaeger:4317", enabled=True)
        is provider
    )
    assert (
        tracing.configure_tracing(service="ignored", endpoint="ignored", enabled=True) is provider
    )
    assert exporters == [{"endpoint": "http://jaeger:4317", "insecure": True}]
    assert provider.processors == [("batch", "exporter")]
    assert set_providers == [provider]
    assert len(FakeInstrumentor.calls) == 3


def test_framework_instrumentation_and_shutdown(monkeypatch) -> None:
    provider = FakeProvider()
    fastapi_calls: list[tuple] = []
    celery_calls: list[dict] = []
    monkeypatch.setattr(tracing, "_provider", provider)
    monkeypatch.setattr(tracing, "configure_tracing", lambda **values: provider)
    monkeypatch.setattr(
        tracing.FastAPIInstrumentor,
        "instrument_app",
        lambda app, **values: fastapi_calls.append((app, values)),
    )
    monkeypatch.setattr(
        tracing.CeleryInstrumentor,
        "instrument",
        lambda self, **values: celery_calls.append(values),
    )

    app = object()
    tracing.instrument_fastapi(app, service="api", endpoint="http://jaeger:4317", enabled=True)
    tracing.instrument_celery(service="worker", endpoint="http://jaeger:4317", enabled=True)
    tracing.shutdown_tracing()

    assert fastapi_calls[0][0] is app
    assert fastapi_calls[0][1]["tracer_provider"] is provider
    assert len(celery_calls) == 2
    assert provider.shutdown_called


def test_disabled_tracing_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_provider", None)
    assert tracing.configure_tracing(service="api", endpoint="unused", enabled=False) is None
    tracing.instrument_fastapi(object(), service="api", endpoint="unused", enabled=False)
    tracing.instrument_celery(service="worker", endpoint="unused", enabled=False)
    tracing.shutdown_tracing()


def test_manual_span_and_current_trace_id(monkeypatch) -> None:
    attributes: dict[str, object] = {}

    class Span:
        def set_attribute(self, key, value) -> None:
            attributes[key] = value

        def get_span_context(self):
            return SimpleNamespace(is_valid=True, trace_id=15)

    span = Span()

    @contextmanager
    def current_span(_name):
        yield span

    monkeypatch.setattr(
        tracing.trace,
        "get_tracer",
        lambda _name: SimpleNamespace(start_as_current_span=current_span),
    )
    monkeypatch.setattr(tracing.trace, "get_current_span", lambda: span)

    with tracing.start_span("agent.run", run_id=4, ignored=None) as current:
        assert current is span

    assert attributes == {"run_id": 4}
    assert tracing.current_trace_id() == "0000000000000000000000000000000f"
