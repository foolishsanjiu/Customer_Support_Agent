from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

_provider: MeterProvider | None = None
_circuit_states: dict[str, int] = {}
_circuit_states_lock = Lock()


@dataclass(frozen=True)
class Instruments:
    http_requests: Any
    http_duration: Any
    agent_runs: Any
    agent_duration: Any
    external_calls: Any
    external_duration: Any
    circuit_rejections: Any
    circuit_state: Any


def configure_metrics(*, service: str, endpoint: str, enabled: bool) -> MeterProvider | None:
    global _provider
    if not enabled:
        return None
    if _provider is not None:
        return _provider
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=endpoint.startswith("http://"),
    )
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(
        resource=Resource.create({SERVICE_NAME: service}),
        metric_readers=[reader],
    )
    metrics.set_meter_provider(provider)
    _provider = provider
    return provider


def shutdown_metrics() -> None:
    if _provider is not None:
        _provider.shutdown()


def record_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    attributes = {
        "method": method,
        "route": route,
        "status_class": f"{status_code // 100}xx",
    }
    instruments = _instruments()
    instruments.http_requests.add(1, attributes)
    instruments.http_duration.record(duration_seconds, attributes)


def record_agent_run(*, trigger: str, outcome: str, duration_seconds: float) -> None:
    attributes = {"trigger": trigger, "outcome": outcome}
    instruments = _instruments()
    instruments.agent_runs.add(1, attributes)
    instruments.agent_duration.record(duration_seconds, attributes)


def record_external_call(*, dependency: str, outcome: str, duration_seconds: float) -> None:
    attributes = {"dependency": dependency, "outcome": outcome}
    instruments = _instruments()
    instruments.external_calls.add(1, attributes)
    instruments.external_duration.record(duration_seconds, attributes)


def record_circuit_rejection(dependency: str) -> None:
    _instruments().circuit_rejections.add(1, {"dependency": dependency})


def record_circuit_state(dependency: str, state: str) -> None:
    value = {"closed": 0, "half_open": 1, "open": 2}[state]
    with _circuit_states_lock:
        _circuit_states[dependency] = value


def _observe_circuit_states(_options: Any) -> list[Observation]:
    with _circuit_states_lock:
        return [
            Observation(value, {"dependency": dependency})
            for dependency, value in _circuit_states.items()
        ]


@lru_cache
def _instruments() -> Instruments:
    meter = metrics.get_meter("resolvex")
    return Instruments(
        http_requests=meter.create_counter(
            "resolvex_http_server_requests",
            unit="{request}",
            description="HTTP requests handled by ResolveX.",
        ),
        http_duration=meter.create_histogram(
            "resolvex_http_server_duration",
            unit="s",
            description="ResolveX HTTP request duration.",
        ),
        agent_runs=meter.create_counter(
            "resolvex_agent_runs",
            unit="{run}",
            description="Agent run invocations by trigger and outcome.",
        ),
        agent_duration=meter.create_histogram(
            "resolvex_agent_run_duration",
            unit="s",
            description="Agent run invocation duration.",
        ),
        external_calls=meter.create_counter(
            "resolvex_external_calls",
            unit="{call}",
            description="LLM and MCP calls by outcome.",
        ),
        external_duration=meter.create_histogram(
            "resolvex_external_call_duration",
            unit="s",
            description="LLM and MCP call duration.",
        ),
        circuit_rejections=meter.create_counter(
            "resolvex_circuit_rejections",
            unit="{call}",
            description="Calls rejected by an open dependency circuit.",
        ),
        circuit_state=meter.create_observable_gauge(
            "resolvex_circuit_state",
            description="Dependency circuit state: 0 closed, 1 half-open, 2 open.",
            callbacks=[_observe_circuit_states],
        ),
    )
