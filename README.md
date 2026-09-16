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

M1 business endpoints are exposed under `/api/v1` for customers, orders, shipments,
refunds, tickets, and ticket messages. Interactive API documentation is available at
`http://localhost:8000/docs`.

The deployed API intentionally does not expose a direct refund mutation endpoint. Refund
requests remain non-executable until the M5 approval flow is implemented.

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

The P0 catalog contains read, write, and system tool definitions. Capabilities scheduled for
M4/M5 are registered but fail closed until their milestone is implemented. In particular,
`refund_order` remains denied for every role because L3 execution requires the M5 approval
guard. Successful write calls are cached under `agent_run_id + tool_call_id`, and all calls are
recorded in `tool_calls`.

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

All 20 security cases execute deterministic code-level controls in `tests/security/`. Each case
has an independent pytest id, and the aggregate suite feeds its observations through the same
zero-tolerance security gate used by evaluation reports. These tests cover prompt/RAG injection,
malicious MCP output, cross-user access, unauthorized tools, approval bypass, refund replay, and
material-action argument tampering without treating the LLM as a security boundary.

CI enforces the 90% coverage gate and uploads JUnit XML, coverage XML, deterministic security
observations, and a security-evaluation JSON report tied to the workflow Git SHA.

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
