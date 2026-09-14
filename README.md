# ResolveX

ResolveX is a production-oriented customer-support resolution agent. The current implementation is at **M3 — Production Tool Runtime**.

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
