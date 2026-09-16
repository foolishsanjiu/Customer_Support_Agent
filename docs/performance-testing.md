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

## Standard repeated matrix

The formal local baseline repeats four profiles three times: dependency readiness at concurrency
10 and the mixed business-read profile at concurrency 1, 10, and 25. Each profile uses the median
P50/P95/P99 and throughput across its runs while retaining the minimum success rate and maximum
P95. Run it only after raising `API_RATE_LIMIT_REQUESTS` above the total business request count:

```powershell
python -m scripts.run_load_matrix --output artifacts/load-matrix.json
```

The matrix is sequential by design, avoiding cross-profile interference. The load generator and
Docker Desktop share the same workstation, so this baseline is suitable for detecting local
regressions but is not a production capacity claim.

## First reviewed local baseline

The first matrix ran against commit `9c2a41c4d9cb8771cfab7c5ad351d50cba6cfbc8` on a single API
container with OpenTelemetry enabled. All 12 runs completed with 100% successful HTTP 200
responses.

| Profile | Concurrency | Median RPS | Median P50 | Median P95 | Maximum P95 |
|---|---:|---:|---:|---:|---:|
| readiness | 10 | 362.417 | 27.115 ms | 33.782 ms | 41.313 ms |
| business reads | 1 | 46.308 | 20.376 ms | 29.608 ms | 30.427 ms |
| business reads | 10 | 175.393 | 54.529 ms | 72.365 ms | 77.153 ms |
| business reads | 25 | 159.778 | 152.065 ms | 193.548 ms | 198.167 ms |

Throughput stops improving between concurrency 10 and 25 while latency increases substantially,
so the single local API instance shows saturation in that range. This does not yet justify a Redis
application cache: every request succeeded, no production SLO has been defined, and the test does
not isolate database-pool queueing, tracing overhead, or the single Uvicorn process. Adding cache
invalidation complexity before isolating those factors would turn a measurement into a guess.

The reviewed aggregate baseline is versioned at
`performance/baselines/p1-local-docker-9c2a41c.json`. The complete 12-run source report remains in
the ignored `artifacts/` directory; its SHA-256 is frozen in the baseline.
