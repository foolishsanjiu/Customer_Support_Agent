# ResolveX

ResolveX is a production-oriented customer-support resolution agent. The current implementation is at **M4 — Context Engineering, Policy RAG, and MCP**.

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

MySQL and Redis run in Linux containers; native Windows installations are not required.

```powershell
docker compose up -d --build
docker compose ps
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_demo
```

Health endpoints:

- `GET http://localhost:8000/health/live`
- `GET http://localhost:8000/health/ready`

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
