# ADR 0003: MySQL and Redis transition recovery

- Status: Accepted
- Date: 2026-09-14
- Scope: Lean P0

## Context

MySQL owns business and AgentRun lifecycle state, while Redis owns the LangGraph resume position. They cannot participate in one atomic transaction.

## Decision

1. MySQL remains authoritative for whether a run is permitted to execute; Redis remains authoritative for where a permitted non-new run resumes. Execution requires a compatible state in both stores.
2. `prepare_approval` idempotently commits the `PENDING` approval and `AgentRun = WAITING_APPROVAL` in one MySQL transaction before `wait_for_approval` interrupts and persists the checkpoint.
3. A run that is not `PENDING` must never restart from `START` when its required checkpoint is absent or corrupt. It transitions to `RECOVERY_REQUIRED` and produces an audit event.
4. Celery redelivery for `WAITING_APPROVAL` is a no-op when a valid checkpoint exists. If the checkpoint is absent, the guard transitions the run to `RECOVERY_REQUIRED`; it does not execute a tool.
5. A checkpoint with MySQL `RUNNING` may be resumed only through the recovery trigger and bounded `recovery_attempts`. A terminal MySQL state always wins and produces a no-op.
6. Approval, ToolCall, AuditLog, current business state, and checkpoint metadata form the reconciliation evidence. Automated reconciliation may classify and stop a run, but P0 does not infer or repeat a destructive business action from incomplete evidence.

## Required failure matrix

| MySQL state | Checkpoint | Trigger | Result |
|---|---|---|---|
| `PENDING` | absent | start | Start graph |
| `RUNNING` | present | recovery/redelivery | Bounded checkpoint resume |
| `RUNNING` | absent/corrupt | recovery/redelivery | `RECOVERY_REQUIRED` |
| `WAITING_APPROVAL` | present | non-approval trigger | No-op |
| `WAITING_APPROVAL` | absent/corrupt | any trigger | `RECOVERY_REQUIRED` |
| `RESUME_PENDING` | present | approval resume | Resume and revalidate |
| `RESUME_PENDING` | absent/corrupt | any trigger | `RECOVERY_REQUIRED` |
| terminal | any | any | No-op |

## Consequences

- Split-brain failures fail closed.
- Some failures require manual reconciliation in P0.
- Safety is prioritized over automatic availability.

## Verification

- Integration tests inject a crash before and after each MySQL commit, checkpoint write, interrupt return, enqueue, and Celery ACK boundary.
- Every matrix row has a deterministic Run State Guard test.
- No failure-injection case produces a duplicate successful refund.

