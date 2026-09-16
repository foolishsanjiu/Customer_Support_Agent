from types import SimpleNamespace

from app.observability import metrics as metrics_module


class Instrument:
    def __init__(self) -> None:
        self.values = []

    def add(self, value, attributes) -> None:
        self.values.append((value, attributes))

    def record(self, value, attributes) -> None:
        self.values.append((value, attributes))


def instruments():
    return SimpleNamespace(
        http_requests=Instrument(),
        http_duration=Instrument(),
        agent_runs=Instrument(),
        agent_duration=Instrument(),
        external_calls=Instrument(),
        external_duration=Instrument(),
        circuit_rejections=Instrument(),
        circuit_state=Instrument(),
    )


def test_metric_recorders_use_bounded_attributes(monkeypatch) -> None:
    recorded = instruments()
    monkeypatch.setattr(metrics_module, "_instruments", lambda: recorded)

    metrics_module.record_http_request(
        method="GET",
        route="/api/v1/orders/{order_id}",
        status_code=200,
        duration_seconds=0.25,
    )
    metrics_module.record_agent_run(trigger="start", outcome="succeeded", duration_seconds=1.5)
    metrics_module.record_external_call(dependency="llm", outcome="failure", duration_seconds=2)
    metrics_module.record_circuit_rejection("llm")
    metrics_module._circuit_states.clear()
    metrics_module.record_circuit_state("llm", "open")

    assert recorded.http_requests.values == [
        (
            1,
            {
                "method": "GET",
                "route": "/api/v1/orders/{order_id}",
                "status_class": "2xx",
            },
        )
    ]
    assert recorded.http_duration.values[0][0] == 0.25
    assert recorded.agent_runs.values == [(1, {"trigger": "start", "outcome": "succeeded"})]
    assert recorded.external_calls.values == [(1, {"dependency": "llm", "outcome": "failure"})]
    assert recorded.circuit_rejections.values == [(1, {"dependency": "llm"})]
    assert [
        (value.value, value.attributes) for value in metrics_module._observe_circuit_states(None)
    ] == [(2, {"dependency": "llm"})]


def test_configure_metrics_builds_one_otlp_provider(monkeypatch) -> None:
    exporters = []
    readers = []
    providers = []
    configured = []
    monkeypatch.setattr(metrics_module, "_provider", None)
    monkeypatch.setattr(
        metrics_module,
        "OTLPMetricExporter",
        lambda **values: exporters.append(values) or "exporter",
    )
    monkeypatch.setattr(
        metrics_module,
        "PeriodicExportingMetricReader",
        lambda exporter, **values: readers.append((exporter, values)) or "reader",
    )
    monkeypatch.setattr(metrics_module.Resource, "create", lambda values: values)
    monkeypatch.setattr(
        metrics_module,
        "MeterProvider",
        lambda **values: providers.append(values) or "provider",
    )
    monkeypatch.setattr(metrics_module.metrics, "set_meter_provider", configured.append)

    first = metrics_module.configure_metrics(
        service="api", endpoint="http://collector:4317", enabled=True
    )
    second = metrics_module.configure_metrics(service="ignored", endpoint="ignored", enabled=True)

    assert first == second == "provider"
    assert exporters == [{"endpoint": "http://collector:4317", "insecure": True}]
    assert readers == [("exporter", {"export_interval_millis": 15_000})]
    assert providers == [{"resource": {"service.name": "api"}, "metric_readers": ["reader"]}]
    assert configured == ["provider"]
