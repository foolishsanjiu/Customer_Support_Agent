import json
from pathlib import Path

import yaml


def load_yaml(path: str):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_collector_exports_otlp_metrics_for_prometheus() -> None:
    collector = load_yaml("docker/otel-collector-config.yaml")

    assert collector["exporters"]["prometheus"]["endpoint"] == "0.0.0.0:8889"
    assert collector["service"]["pipelines"]["metrics"] == {
        "receivers": ["otlp"],
        "processors": ["memory_limiter", "batch"],
        "exporters": ["prometheus"],
    }


def test_prometheus_and_grafana_are_pinned_and_provisioned() -> None:
    compose = load_yaml("docker-compose.yml")
    prometheus = compose["services"]["prometheus"]
    grafana = compose["services"]["grafana"]
    scrape = load_yaml("docker/prometheus.yml")
    datasource = load_yaml("docker/grafana/provisioning/datasources/prometheus.yml")

    assert prometheus["image"] == "prom/prometheus:v3.14.0"
    assert grafana["image"] == "grafana/grafana:13.2.1"
    assert prometheus["ports"] == ["127.0.0.1:9090:9090"]
    assert grafana["ports"] == ["127.0.0.1:3000:3000"]
    assert scrape["scrape_configs"][0]["static_configs"][0]["targets"] == ["otel-collector:8889"]
    assert datasource["datasources"][0]["url"] == "http://prometheus:9090"


def test_dashboard_contains_core_operational_queries() -> None:
    dashboard = json.loads(
        Path("docker/grafana/dashboards/resolvex-overview.json").read_text(encoding="utf-8")
    )
    expressions = {
        target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    }

    assert dashboard["uid"] == "resolvex-overview"
    assert any("resolvex_http_server_requests_total" in query for query in expressions)
    assert any("resolvex_agent_runs_total" in query for query in expressions)
    assert any("resolvex_external_calls_total" in query for query in expressions)
    assert any("resolvex_circuit_state" in query for query in expressions)
