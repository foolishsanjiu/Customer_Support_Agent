# ADR 0006: Use a dedicated Redis instance for LangGraph checkpoints

- Status: Accepted
- Date: 2026-09-17
- Scope: P1 checkpoint initialization and deployment correctness
- Supersedes: ADR 0005

## Context

ADR 0005 assigned Celery to Redis DB0, LangGraph checkpoints to DB1, and control-plane state to DB2
on one Redis 8 instance. That layout passed local tests against a long-lived persistent volume.

A clean GitHub Actions service exposed the missing first-deployment check. `AsyncRedisSaver.asetup()`
must create Redis Search indexes for checkpoints, blobs, and pending writes. Redis rejected index
creation on DB1 with `Cannot create index on db != 0`. The prior local probe was a false assurance:
it reused pre-existing index state rather than proving that an empty DB1 could initialize.

There were no active or approval-pending AgentRuns when this decision was made, so no live workflow
checkpoint requires migration. Historical data remains in the old Redis volume and is not deleted.

## Decision

1. Add a dedicated `checkpoint-redis` service using its DB0 and a separate persistent volume.
2. Keep Celery broker and control-plane state on the existing Redis instance at DB0 and DB2.
3. Give both Redis instances AOF `everysec`, `noeviction`, health checks, and pinned Redis 8.4.4
   images.
4. Bind the development host port to loopback only; containers use the private Compose network.
5. Run CI against two fresh Redis services so checkpoint index initialization is tested on every
   change.
6. Keep MySQL as business truth. Missing checkpoint state continues to fail closed as
   `RECOVERY_REQUIRED`.

The second instance is an isolation and compatibility boundary, not a high-availability claim.

## Rejected alternatives

### Put checkpoints and Celery together in DB0

This would initialize successfully, but it would mix workflow recovery state with broker lifecycle,
cleanup, capacity, and failure behavior. A separate instance is a small operational cost and keeps
those responsibilities independently governable.

### Keep DB1 and special-case CI

That would hide a production first-deployment failure. CI must use the same supported checkpoint
topology as Compose.

### Replace the LangGraph checkpointer

The saver itself remains suitable once given a supported Redis Search database. Replacing it would
add migration and correctness risk without solving an application requirement.

## Verification

- Configuration tests require a dedicated checkpoint service and forbid the old DB1 URL.
- CI starts both Redis services from empty state and runs approval-resume and recovery tests.
- Local integration tests initialize, write, read, resume, and clean up checkpoints on port 6380.
- Both Redis instances retain independent persistent volumes and durability settings.

## References

- [Redis `SELECT` and logical database limitations](https://redis.io/docs/latest/commands/select/)
- [LangGraph Redis saver module and setup requirements](https://github.com/redis-developer/langgraph-redis)
