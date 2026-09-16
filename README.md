# ResolveX

ResolveX is a production-oriented customer-support resolution agent. Milestones M0–M7 are
complete. The current implementation is at **M8 — Evaluation, Security, and Deployment**.

## Local environment

```powershell
conda activate D:\CondaEnvs\resolvex
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
```

Copy `.env.example` to `.env` and keep all real credentials local.

## Quality checks

```powershell
ruff check .
ruff format --check .
pytest
```

## Infrastructure

MySQL, Redis, OpenTelemetry Collector, and Jaeger run in Linux containers; native Windows
installations are not required.

```powershell
docker compose up -d --build
docker compose ps
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_demo
python -m scripts.verify_deployment
```

Health endpoints:

- `GET http://localhost:8000/health/live`
- `GET http://localhost:8000/health/ready`
- `GET http://localhost:13133/` (OpenTelemetry Collector)

Jaeger is available at `http://localhost:16686`. Applications export OTLP to the Collector on
ports `4317`/`4318`; the Collector applies memory limiting and batching before forwarding traces
to Jaeger. `scripts.verify_deployment` creates a fresh API trace and verifies that it reaches
Jaeger through this path.

Verify the deployed refund path with the configured real model:

```powershell
docker compose exec -T api python -m scripts.verify_golden_path `
  --api-url http://127.0.0.1:8000 `
  --timeout 180
```

This creates an isolated customer and delivered order, then exercises the HTTP API, customer and
manager JWT authorization, Celery execution and resume, Redis checkpoint, approval, refund,
idempotency, audit, and final response. A successful run removes its database fixture and Redis
checkpoint. A failed run prints its exact IDs and retains the fixture for diagnosis.

M1 business endpoints are exposed under `/api/v1` for customers, orders, shipments,
refunds, tickets, and ticket messages. Interactive API documentation is available at
`http://localhost:8000/docs`.

Business API requests use a Redis-backed fixed-window rate limit. The default is 60 requests per
60 seconds, configurable with `API_RATE_LIMIT_REQUESTS` and
`API_RATE_LIMIT_WINDOW_SECONDS`. A valid JWT is keyed by a one-way hash of role and principal ID;
missing or invalid credentials fall back to a hash of the direct client IP. Health and documentation
routes are excluded. Responses expose `X-RateLimit-Limit` and `X-RateLimit-Remaining`; rejected
requests return `429` with `Retry-After`. If Control Redis is temporarily unavailable, the limiter
fails open and emits a structured warning so that an infrastructure failure does not make the
support API unavailable.

The deployed API intentionally does not expose a direct refund mutation endpoint. Refunds execute
only through the agent workflow after the M5 manager-approval guard succeeds.

## M2 agent runtime

M2 uses a LangGraph `StateGraph` with this stable path:

```text
load_ticket → understand → validate_request → plan → execute_tool → verify → respond → persist
```

Missing fields route to `respond_clarification` without tool execution. Every successful
cancellation is re-read from MySQL by the explicit `verify` node. `MAX_AGENT_STEPS` defaults
to `12` and terminates runaway graph execution.

`MockLLMClient` is used by deterministic CI tests. `OpenAICompatibleClient` uses the configured
`LLM_BASE_URL`, `LLM_MODEL`, and local-only `LLM_API_KEY`; tests do not call a real model.

## M3 tool runtime

Every Agent tool action now crosses the same deterministic runtime pipeline: registry lookup,
strict input validation, principal and role checks, object ownership, risk policy, persistent
idempotency, bounded timeout/retry, execution, verification, and audit. Tool arguments are
redacted before persistence, and result audit summaries exclude customer PII.

The P0 catalog contains read, write, and system tool definitions. `refund_order` is an L3 action:
it fails closed unless a manager approval is bound to the exact run, tool call, and arguments and
is revalidated immediately before execution. Successful write calls are cached under
`agent_run_id + tool_call_id`, and all calls are recorded in `tool_calls`.

## M4 context, policy retrieval, and MCP

The Context Builder combines recent ticket messages with authoritative MySQL business state,
dense policy retrieval, and the tools allowed for the current intent and role. Conversation,
policy, and MCP text are always treated as untrusted data and cannot grant authorization or
override database state.

Policy Markdown files live in `policies/`. BGE-M3 embeddings are loaded from the local cache
configured by `EMBEDDING_CACHE_DIR`; Chroma persists its index at `CHROMA_PATH`. P0 deliberately
uses dense Top-K retrieval with metadata filters only—BM25, reranking, HyDE, and query rewriting
remain out of scope.
The container runs Hugging Face and Transformers in offline mode, so production indexing fails
closed when the mounted model cache is missing instead of downloading a model implicitly.

The external logistics boundary is a streamable-HTTP MCP server exposing `get_tracking` and
`get_delivery_estimate`. Its responses must pass strict local schemas before entering the Tool
Runtime. Docker Compose starts it at `http://localhost:8001/mcp`; the API uses the internal
service URL.

Build the policy index explicitly, or include a query for a retrieval smoke test:

```powershell
python -m scripts.index_policies --query "delayed shipment" --policy-type shipping
```

## Deterministic demo scenarios

The seed command creates 100 customers, 300 orders, and 24 tickets. Important stable
examples owned by customer `1` are:

- order `1`: delayed shipment;
- order `2`: delivered normally;
- order `3`: paid but not shipped;
- order `4`: already refunded;
- order `5`: outside the 30-day refund window;
- order `6`: illegal cancellation state;
- order `7`: legal cancellation state;
- order `8`: owned by customer `2`, for ownership-denial tests;
- order `9`: valid full-refund scenario.

The seed operation is idempotent: it skips insertion when customer data already exists.

Stop the stack without deleting persisted data:

```powershell
docker compose down
```

## M5–M7 reliability and observability

L3 refunds bind approval to an immutable action snapshot and fingerprint, pause through a
LangGraph interrupt, and revalidate ownership, eligibility, approval status, tool call, and
arguments immediately before execution. Celery tasks use late acknowledgement, bounded
recovery, Redis checkpoints, and MySQL state guards; scheduled reconcilers close the
database-commit/message-publish failure window.

Application logs are structured JSON with sensitive-field redaction. Request and business
correlation fields propagate into Celery tasks, while OpenTelemetry traces cover FastAPI,
SQLAlchemy, HTTPX/HTTPX2, Celery, LangGraph nodes, LLM calls, tools, MCP, and approval actions.
Jaeger is available at `http://localhost:16686` when the Compose stack is running.

## M8 evaluation status

The versioned P0 datasets are:

- `evals/datasets/functional_v1.json`: 60 functional cases;
- `evals/datasets/functional_holdout_v1.json`: 20 frozen, previously unseen functional cases;
- `evals/datasets/security_v1.json`: 20 adversarial security cases.

`scripts/run_evaluation.py` scores externally captured observations and writes a report tied to
the current Git commit, dataset, prompt, provider, model version, and evaluation configuration.
Quality regression comparison is allowed only within the same comparison series. Critical
security metrics always use a zero-tolerance gate, even when the model or dataset changes.
Combined reports also enforce absolute floors on every run, including first runs and new
comparison series: Task Success Rate defaults to 80% and Tool Selection Accuracy to 90%. The
values can be overridden with `--minimum-task-success-rate` and
`--minimum-tool-selection-accuracy`, and the effective thresholds are stored in the report.
Security-only CI reports explicitly omit functional quality checks because they contain no
functional observations; their security invariants remain zero tolerance.

All 20 security cases execute deterministic code-level controls in `tests/security/`. Each case
has an independent pytest id, and the aggregate suite feeds its observations through the same
zero-tolerance security gate used by evaluation reports. These tests cover prompt/RAG injection,
malicious MCP output, cross-user access, unauthorized tools, approval bypass, refund replay, and
material-action argument tampering without treating the LLM as a security boundary.

CI enforces the 90% coverage gate and uploads JUnit XML, coverage XML, deterministic security
observations, and a security-evaluation JSON report tied to the workflow Git SHA.

The separate `Real-model gate` GitHub Actions workflow keeps paid, provider-dependent calls out of
ordinary pushes and pull requests. A manual run can select a seven-case `smoke` suite or the full
60-functional + 20-security `benchmark`; publishing a GitHub Release automatically selects that
full benchmark. Configure a protected GitHub environment named `real-model` with an `LLM_API_KEY`
secret and optional `LLM_BASE_URL`, `LLM_MODEL`, and `EVAL_PROVIDER` variables. The workflow is
serialized, times out after 30 minutes, and retains all observations, capture metadata, security
evidence, and the combined report for 30 days.

The smoke suite requires 100% task success and tool selection across one representative case from
each functional category. The release benchmark uses the project-wide absolute floors of 80% task
success, 90% tool selection, and zero critical security events. Its combined report is tied to the
workflow Git SHA and records dataset, prompt, provider, requested model, returned model, provider
fingerprint, evaluation configuration, timestamp, metrics, and gate result. A provider alias without
an immutable fingerprint starts a commit-specific comparison series; a changed fingerprint starts a
new series instead of being mislabeled as a code regression.

Capture a resumable real-model functional run without mutating development business data:

```powershell
python -m scripts.capture_real_model_eval `
  --output evals/observations/deepseek_functional_v1.json `
  --resume
```

The capture metadata records the requested model, provider-returned model, system fingerprint,
token usage, and functional metrics. Generated observations and reports remain local-only. A
model or fingerprint change starts a new comparison series rather than being reported as a code
regression.

The first P0 real-model baseline and its limitations are documented in
[`docs/evaluation-baseline.md`](docs/evaluation-baseline.md).
