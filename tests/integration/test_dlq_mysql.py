import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.dlq import DeadLetterStore
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import AgentRun, AuditLog, Customer, DeadLetter, Ticket
from app.models.enums import (
    AgentRunStatus,
    CustomerLevel,
    CustomerStatus,
    DeadLetterStatus,
    TicketCategory,
    TicketStatus,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with MySQL running",
    ),
]


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


async def dlq_scenario() -> None:
    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customer = Customer(
            name="DLQ Customer",
            email=f"dlq-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        ticket = Ticket(
            customer_id=customer.id,
            status=TicketStatus.PROCESSING,
            category=TicketCategory.OTHER,
            subject="Failed background task",
        )
        session.add(ticket)
        await session.flush()
        run = AgentRun(
            ticket_id=ticket.id,
            status=AgentRunStatus.RUNNING,
            current_node="understand",
            recovery_attempts=0,
        )
        session.add(run)
        await session.flush()
        ids = customer.id, ticket.id, run.id

    store = DeadLetterStore(sessions)
    first = await store.capture(
        run_id=ids[2],
        task_name="resolvex.run_agent",
        task_id="task-1",
        reason_code="task_exception",
        error_type="RuntimeError",
        mark_run_failed=True,
    )
    second = await store.capture(
        run_id=ids[2],
        task_name="resolvex.run_agent",
        task_id="task-2",
        reason_code="task_exception",
        error_type="RuntimeError",
    )
    assert first.id == second.id
    assert second.failure_count == 2
    assert second.task_id == "task-2"

    async with sessions() as session:
        persisted_run = await session.get(AgentRun, ids[2])
        assert persisted_run is not None
        assert persisted_run.status is AgentRunStatus.FAILED
        assert persisted_run.error_code == "celery_task_failed"
        assert await session.scalar(select(func.count(DeadLetter.id))) == 1

    claimed = await store.request_replay(second.id, actor_id="operator-1")
    assert claimed.status is DeadLetterStatus.REPLAYING
    assert claimed.replay_count == 1
    await store.mark_replayed(second.id)
    assert (await store.get(second.id)).status is DeadLetterStatus.REPLAYED

    reopened = await store.capture(
        run_id=ids[2],
        task_name="resolvex.run_agent",
        reason_code="replay_failed",
        error_type="RuntimeError",
    )
    assert reopened.status is DeadLetterStatus.OPEN
    assert reopened.failure_count == 3
    assert [item.id for item in await store.list(DeadLetterStatus.OPEN)] == [second.id]

    async with sessions.begin() as session:
        await session.execute(delete(AuditLog).where(AuditLog.run_id == ids[2]))
        await session.execute(delete(DeadLetter).where(DeadLetter.run_id == ids[2]))
        await session.execute(delete(AgentRun).where(AgentRun.id == ids[2]))
        await session.execute(delete(Ticket).where(Ticket.id == ids[1]))
        await session.execute(delete(Customer).where(Customer.id == ids[0]))
    await engine.dispose()


def test_dead_letters_are_durable_deduplicated_and_replayable() -> None:
    asyncio.run(dlq_scenario())
