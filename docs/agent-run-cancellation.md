# AgentRun cooperative cancellation

Clients request cancellation with:

```http
POST /api/v1/agent-runs/{run_id}/cancel
Authorization: Bearer <JWT>
Content-Type: application/json

{"reason":"The customer no longer needs this run"}
```

The owning customer may cancel their run. `SUPPORT_AGENT`, `MANAGER`, and `ADMIN` may cancel from
the operator console. Object ownership and role checks happen in the database transaction, and each
accepted request creates an audit event. Repeating a request does not create a second execution or
business mutation.

## State semantics

- `PENDING`, `WAITING_APPROVAL`, `RESUME_PENDING`, or `RECOVERY_REQUIRED` becomes `CANCELLED`
  immediately. Pending or approved-but-not-executed approvals become `CANCELLED` as well.
- `RUNNING` becomes `CANCEL_REQUESTED`. The Worker reads this durable MySQL state at LangGraph node
  boundaries and then finalizes `CANCELLED`.
- `SUCCEEDED`, `FAILED`, and `CANCELLED` are terminal and remain unchanged.
- SSE reports both new states and closes when `CANCELLED` is reached.

Cancellation is cooperative rather than a Celery process kill. If a tool call has already started,
ResolveX completes its verify/respond/persist critical section. This avoids reporting cancellation
while leaving a completed external or database mutation unverified. A request that arrives after
that safety boundary may therefore finish as `SUCCEEDED`; its request remains in the audit log.

Worker redelivery, approval resume, and DLQ replay all pass through the same run-state guard. They
cannot restart a cancelled run. Cancellation is not recorded as a task failure and does not create a
dead letter.
