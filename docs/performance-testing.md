# Performance testing

ResolveX uses a bounded asynchronous HTTP harness so the initial performance baseline has no new
runtime or developer dependency. The harness measures the deployed service from outside the
process and writes a machine-readable JSON report containing throughput, success rate, status-code
distribution, errors, and successful-request latency at P50, P95, and P99.
Reports bind results to the Git commit and runtime platform and include both aggregate and per-path
latency, so a fast endpoint cannot conceal a slower endpoint in a mixed read profile.

Start with dependency readiness, which exercises FastAPI, the MySQL connection pool, and Redis
without consuming LLM quota:

```powershell
python -m scripts.run_load_test `
  --path /health/ready `
  --requests 500 `
  --concurrency 10 `
  --output artifacts/load-ready.json
```

For business reads, restart the API with a rate limit above the planned request count, seed the
demo data, and test only non-mutating endpoints:

```powershell
$env:API_RATE_LIMIT_REQUESTS = "2000"
docker compose up -d --build api
docker compose exec api python -m scripts.seed_demo
python -m scripts.run_load_test `
  --path /api/v1/orders/1 `
  --path /api/v1/customers/1/orders `
  --requests 1000 `
  --concurrency 20 `
  --output artifacts/load-business-reads.json
```

The default gate requires at least 99% successful responses. `--max-p95-ms` may add a latency gate
after a reviewed baseline exists; inventing a latency threshold before measurement would make the
gate arbitrary. Latency percentiles exclude failed responses, while the report preserves all
status codes and transport error types.

This harness deliberately does not mix LLM-backed Agent runs into the infrastructure or business
read baseline. Agent performance depends heavily on provider latency, token volume, and cost and
will be measured as a separate low-concurrency scenario.
