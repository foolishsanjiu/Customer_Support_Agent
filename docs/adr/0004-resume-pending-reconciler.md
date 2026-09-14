# ADR 0004: RESUME_PENDING reconciler scheduling

- Status: Accepted
- Date: 2026-09-14
- Scope: Lean P0

## Context

Approval commits in MySQL before the resume task is sent to Celery. If enqueue fails, the run safely remains `RESUME_PENDING`, but it needs a durable retry mechanism.

## Decision

1. The approval API commits `Approval = APPROVED` and `AgentRun = RESUME_PENDING` in one MySQL transaction, then attempts immediate enqueue after commit.
2. P0 adds one `resolvex-scheduler` container running Celery Beat. It invokes the reconciler every 30 seconds.
3. The reconciler selects at most 100 `RESUME_PENDING` runs whose `updated_at` is at least 60 seconds old and enqueues `resume_agent_run(run_id, approval_id)`.
4. P0 runs exactly one scheduler replica. The resume task remains idempotent, so an enqueue/crash race or manual duplicate enqueue is harmless.
5. Every resume task passes through Run State Guard, uses the same LangGraph thread ID, verifies the approved action fingerprint, and revalidates current MySQL state before mutation.
6. Reconciliation attempts and enqueue failures are logged and audited. `MAX_RECOVERY_ATTEMPTS` remains reserved for worker-loss checkpoint recovery; enqueue failures do not consume that counter. A missing or invalid checkpoint is classified by Run State Guard as `RECOVERY_REQUIRED`.
7. Celery result backend is not a source of truth and is not required for this protocol.

## Consequences

- The commit/enqueue gap is recovered without a full transactional outbox.
- Recovery delay is normally between 60 and 90 seconds after a failed immediate enqueue.
- Multiple scheduler replicas are out of P0 scope.

## Verification

- An integration test forces immediate enqueue failure and observes later reconciler enqueue.
- Duplicate reconciler executions produce one business outcome.
- A missing checkpoint transitions safely to `RECOVERY_REQUIRED`.
