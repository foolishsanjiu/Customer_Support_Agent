# AgentRun SSE

ResolveX exposes authenticated, customer-scoped AgentRun state updates at:

```text
GET /api/v1/agent-runs/{run_id}/events
Accept: text/event-stream
Authorization: Bearer <customer JWT>
```

Each `agent_run.status` event contains the run ID, status, current graph node, update time,
and whether the run is terminal. The server sends an event only when the authoritative MySQL
state changes and closes the stream after `SUCCEEDED` or `FAILED`. The client should close its
connection when `terminal` is `true`.

The event `id` is stable for a particular persisted state. On reconnect, clients may send the
standard `Last-Event-ID` header to avoid replaying the last snapshot. Browser code should use a
fetch-based SSE client so the JWT remains in the `Authorization` header; tokens are deliberately
not accepted in query parameters.

This implementation polls MySQL once per second per active stream. That keeps MySQL as the source
of truth and makes reconnects independent of transient messages. It is appropriate for the small
P1 operator/customer UI, but it is not an immutable event log and may coalesce very short-lived
intermediate node changes. If measured connection volume later makes polling material, a durable
outbox or Redis Streams fan-out can replace the polling layer without changing the HTTP event
contract. Redis Pub/Sub alone is not used because disconnected clients would miss events.
