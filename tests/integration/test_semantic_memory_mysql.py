import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.memory.models import MemoryToStore
from app.memory.store import DatabaseMemoryStore
from app.models import Customer, Ticket
from app.models.enums import (
    CustomerLevel,
    CustomerStatus,
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


async def memory_scenario() -> None:
    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customers = [
            Customer(
                name=f"Memory Customer {index}",
                email=f"memory-{index}-{marker}@resolvex.example",
                level=CustomerLevel.NORMAL,
                status=CustomerStatus.ACTIVE,
            )
            for index in range(2)
        ]
        session.add_all(customers)
        await session.flush()
        tickets = [
            Ticket(
                customer_id=customer.id,
                status=TicketStatus.OPEN,
                category=TicketCategory.OTHER,
                subject="Remember my preference",
            )
            for customer in customers
        ]
        session.add_all(tickets)
        await session.flush()
        customer_ids = [customer.id for customer in customers]
        ticket_ids = [ticket.id for ticket in tickets]

    store = DatabaseMemoryStore(sessions)
    memory = MemoryToStore(
        content="Prefers concise English replies.",
        content_hash="a" * 64,
        embedding=[1.0, 0.0],
    )
    assert (
        await store.add(
            customer_id=customer_ids[0], source_ticket_id=ticket_ids[0], memories=[memory]
        )
        == 1
    )
    assert (
        await store.add(
            customer_id=customer_ids[0], source_ticket_id=ticket_ids[0], memories=[memory]
        )
        == 0
    )
    assert (
        await store.add(
            customer_id=customer_ids[1], source_ticket_id=ticket_ids[1], memories=[memory]
        )
        == 1
    )

    assert [item.content for item in await store.list_for_customer(customer_ids[0])] == [
        memory.content
    ]

    async with sessions.begin() as session:
        await session.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
        await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
    await engine.dispose()


def test_semantic_memory_is_customer_scoped_and_idempotent() -> None:
    asyncio.run(memory_scenario())
