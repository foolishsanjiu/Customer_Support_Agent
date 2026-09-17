# ADR 0005: Retain one Redis deployment for checkpoints

- Status: Accepted
- Date: 2026-09-17
- Scope: P1 checkpoint-backend decision

## Context

ResolveX uses Redis for the Celery broker, LangGraph checkpoints, and control-plane state. The P1
roadmap requires a dedicated checkpoint Redis or an alternate durable checkpointer only when
operational goals justify the additional component.

The reviewed local deployment has no production availability SLO, independent maintenance window,
multi-host topology, or zero-data-loss requirement. At review time Redis used 12.11 MiB, AOF writes
were healthy, the `noeviction` policy was active, and the persistent volume contained 828 checkpoint
documents and 3,653 pending-write records. The current HTTP load baseline does not exercise enough
Agent concurrency to demonstrate checkpoint contention.

The review also found a configuration drift: Celery and LangGraph both selected logical database 0,
although the frozen architecture assigns broker state to DB0, checkpoints to DB1, and control state
to DB2. A LangGraph write/read/delete probe against DB1 passed on the deployed Redis 8 instance.

## Decision

1. Retain the existing Redis-backed `AsyncRedisSaver` and one Redis deployment.
2. Restore logical responsibility separation: Celery DB0, LangGraph DB1, control-plane DB2.
3. Keep AOF `everysec`, the persistent Docker volume, and `noeviction` as local durability controls.
4. Keep MySQL as business truth. Missing or corrupt workflow state continues to fail closed as
   `RECOVERY_REQUIRED`; checkpoint persistence is not an exactly-once guarantee.
5. Do not migrate historical terminal-run checkpoints from DB0. The review found no resumable
   `WAITING_APPROVAL` or `RESUME_PENDING` run, so new work can start in DB1 without moving live
   workflow state. Existing DB0 data remains untouched.

Logical databases provide namespacing, not resource, persistence, security, or failure isolation.
This decision therefore does not claim that DB1 is a high-availability boundary.

## Rejected alternatives

### Dedicated Redis container now

A second container on the same Docker Desktop host would add configuration, monitoring, backup, and
upgrade work without satisfying a real high-availability objective. No measured memory, latency, or
broker-contention problem currently justifies it.

### PostgreSQL or another checkpointer now

Adding a new database solely for workflow state would duplicate operational responsibilities while
the current Redis saver already passes interrupt/resume and crash-recovery tests. MySQL remains the
authoritative business store, so changing the checkpointer does not simplify the correctness model.

## Revisit triggers

Re-open this decision when any of the following becomes true:

- production requires independent checkpoint backup, restore, maintenance, or access control;
- a multi-host deployment or an availability SLO requires replication or managed failover;
- measured checkpoint latency, memory growth, or broker traffic causes interference;
- the accepted recovery-point objective is stricter than AOF `everysec` can provide;
- checkpoint retention requires a separately governed lifecycle; or
- the deployment moves to Redis Cluster or a managed product where logical databases are unavailable.

At that point, prefer a dedicated managed Redis deployment first. Re-evaluate another durable
checkpointer only if its recovery, migration, and operational model is demonstrably simpler.

## Verification

- Configuration tests freeze DB0/DB1/DB2 responsibility separation and Redis durability flags.
- MySQL/Redis integration tests cover approval interrupt/resume and crash recovery from checkpoint.
- A deployed DB1 probe verifies LangGraph checkpoint write, read, and cleanup.
- A restart drill verified that a DB1 checkpoint survived Redis restart and was then deleted.

## References

- [Redis `SELECT` and logical database limitations](https://redis.io/docs/latest/commands/select/)
- [LangGraph Redis saver requirements and persistence behavior](https://github.com/redis-developer/langgraph-redis)
