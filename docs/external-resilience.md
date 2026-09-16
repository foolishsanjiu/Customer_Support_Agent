# External dependency resilience

ResolveX applies an in-process circuit breaker to the two remote execution boundaries used by an
Agent run: the OpenAI-compatible LLM endpoint and the logistics MCP service. The breaker is not an
authorization or correctness mechanism; deterministic validation, ownership, approval,
idempotency, and verification still apply independently.

The default policy opens a dependency circuit after three consecutive failed calls and keeps it
open for 30 seconds. Calls fail immediately while open. After the cooldown, exactly one caller is
allowed through as a half-open probe: success closes the circuit, while failure starts a new full
cooldown. A generation token prevents an older in-flight success from closing a circuit opened by
a newer failure.

Configuration:

```dotenv
EXTERNAL_CIRCUIT_FAILURE_THRESHOLD=3
EXTERNAL_CIRCUIT_RECOVERY_SECONDS=30
```

The state is intentionally shared within each worker process but not placed in Redis. This keeps
the failure path independent of another network service and prevents one unhealthy worker from
globally blocking healthy workers. The tradeoff is that a multi-process deployment can send up to
the configured threshold per process before every process opens its circuit.

LLM transport, HTTP-status, and response-envelope failures become a retryable
`ExternalServiceUnavailable` error. Before tool verification, the Agent run fails explicitly
rather than inventing an intent or customer-facing answer. If a tool outcome was already verified,
the workflow persists success and returns a fixed degraded message without asking the LLM to
restate or reinterpret the result. Logistics MCP transport, protocol, and schema failures retain
the existing `MCPToolError` contract; the workflow records the failed tool result and responds
through its existing controlled failure path. Structured logs include circuit state and fast-fail
cooldown information.

The circuit breaker does not retry calls itself. LLM work is not automatically replayed because a
blind replay can duplicate cost. MCP read tools continue to use the Tool Runtime's existing
bounded retry policy; once the breaker opens, later attempts fast-fail without contacting MCP.
