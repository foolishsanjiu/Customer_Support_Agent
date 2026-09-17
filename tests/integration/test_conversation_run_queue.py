import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.agent.run_guard import RunAction, RunStateGuard, RunTrigger
from app.agent.store import DatabaseAgentStore
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import AgentRun, AuditLog, Customer, Ticket, TicketMessage
from app.models.enums import (
    AgentRunStatus,
    CustomerLevel,
    CustomerStatus,
    SenderType,
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


@pytest.mark.asyncio
async def test_message_bound_runs_are_fifo_and_waiting_approval_releases_slot() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customer = Customer(
            name="Conversation Queue",
            email=f"conversation-queue-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        ticket = Ticket(
            customer_id=customer.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.OTHER,
            subject="新对话",
        )
        session.add(ticket)
        await session.flush()
        first_message = TicketMessage(
            ticket_id=ticket.id,
            sender_type=SenderType.CUSTOMER,
            content="订单9买错了，我想退款",
        )
        second_message = TicketMessage(
            ticket_id=ticket.id,
            sender_type=SenderType.CUSTOMER,
            content="我想查询订单1的信息",
        )
        session.add_all([first_message, second_message])
        await session.flush()
        customer_id = customer.id
        ticket_id = ticket.id
        first_message_id = first_message.id
        second_message_id = second_message.id

    store = DatabaseAgentStore(sessions)
    first = await store.create_run(ticket_id, customer_id, first_message_id)
    second = await store.create_run(ticket_id, customer_id, second_message_id)
    duplicate = await store.create_run(ticket_id, customer_id, first_message_id)
    assert duplicate.id == first.id

    guard = RunStateGuard(sessions, max_recovery_attempts=3)
    assert (
        await guard.acquire(second.id, RunTrigger.START, checkpoint_exists=False) is RunAction.NOOP
    )
    assert (
        await guard.acquire(first.id, RunTrigger.START, checkpoint_exists=False) is RunAction.START
    )
    assert (
        await guard.acquire(second.id, RunTrigger.START, checkpoint_exists=False) is RunAction.NOOP
    )

    async with sessions.begin() as session:
        stored_first = await session.get(AgentRun, first.id, with_for_update=True)
        assert stored_first is not None
        stored_first.status = AgentRunStatus.WAITING_APPROVAL
    assert (
        await guard.acquire(second.id, RunTrigger.START, checkpoint_exists=False) is RunAction.START
    )

    active = await store.list_active_runs(ticket_id, customer_id)
    assert [(run.id, run.status) for run in active] == [
        (first.id, AgentRunStatus.WAITING_APPROVAL),
        (second.id, AgentRunStatus.RUNNING),
    ]

    async with sessions.begin() as session:
        await session.execute(delete(AuditLog).where(AuditLog.ticket_id == ticket_id))
        await session.execute(delete(AgentRun).where(AgentRun.ticket_id == ticket_id))
        await session.execute(delete(Ticket).where(Ticket.id == ticket_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()
