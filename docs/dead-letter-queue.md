# Dead-letter queue

ResolveX uses a MySQL-backed operational dead-letter queue for terminal Celery task failures.
Redis remains a delivery mechanism and the Celery result backend is not treated as a source of
truth. A dead-letter record contains only identifiers and failure classification; task payloads,
tool arguments, credentials, and exception text are not copied into the queue.

`run_id + task_name` identifies one logical queue item. Repeated failures reopen that item and
increment `failure_count` instead of creating unbounded duplicates. Automatic worker-loss recovery
that exhausts its limit, a missing required checkpoint, and uncaught start/resume task exceptions
all produce a record and an audit event.

Only an authenticated administrator can query or request replay through:

- `GET /api/v1/dlq?status=OPEN`
- `POST /api/v1/dlq/{dead_letter_id}/replay`

Replay first atomically claims the record as `REPLAYING`, then sends a dedicated Celery replay
task. It resumes the same AgentRun and LangGraph checkpoint, preserving Tool Runtime idempotency
keys and approval bindings. A failed replay reopens the same record. A run that failed before any
workflow checkpoint may restart only when its persisted current node is still `START`; an already
started run without a checkpoint fails closed as `RECOVERY_REQUIRED`.

Advanced replay inspection and bulk replay UI remain P2 scope.
