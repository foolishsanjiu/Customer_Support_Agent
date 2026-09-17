# Metrics monitoring

ResolveX sends application metrics through the same OpenTelemetry OTLP gateway used for traces:

```text
API / Celery worker
        ↓ OTLP metrics
OpenTelemetry Collector :4317
        ↓ Prometheus exporter :8889
Prometheus :9090
        ↓ provisioned datasource
Grafana :3000
```

This avoids a second Python metrics client and keeps export, resource identity, batching, and
failure isolation at the existing Collector boundary. Prometheus and Grafana listen only on the
local loopback interface in the development Compose stack. Grafana anonymous access is read-only;
set `GRAFANA_ADMIN_PASSWORD` locally before using administrative features.

The application records bounded-cardinality metrics:

- HTTP request count and duration by method, route template, and status class;
- Agent invocation count and duration by start/resume/recovery trigger and outcome;
- LLM, logistics MCP, and fulfillment MCP call count and duration by dependency and outcome;
- circuit state (`0=closed`, `1=half-open`, `2=open`) and fast-fail rejection count.

Route templates such as `/api/v1/orders/{order_id}` are used instead of raw URLs, preventing order
IDs from creating unbounded Prometheus label series. No customer identifier, ticket identifier,
prompt, tool argument, or credential is stored in metrics.

Start or rebuild the monitoring path:

```powershell
docker compose up -d --build api worker otel-collector prometheus grafana
docker compose ps
```

Open Prometheus at `http://127.0.0.1:9090` and Grafana at `http://127.0.0.1:3000`. The provisioned
`ResolveX Overview` dashboard contains HTTP rate/P95, Agent outcomes/P95, external call outcomes,
circuit state, and circuit rejection panels. The 15-second exporter and scrape intervals mean a
new application sample may take roughly 30 seconds to appear end-to-end.

This first monitoring batch provisions dashboards but not paging destinations. Alert thresholds
require an agreed service-level objective; inventing a production latency or error-rate threshold
from the local Docker performance baseline would produce noisy, unjustified alerts.
