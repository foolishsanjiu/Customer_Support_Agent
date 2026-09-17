import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.agent.llm import MockLLMClient
from app.context.summary import ConversationSummaryService
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import Customer, Ticket, TicketMessage
from app.models.enums import (
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


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


async def summary_scenario() -> None:
    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customer = Customer(
            name="Summary Customer",
            email=f"summary-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        ticket = Ticket(
            customer_id=customer.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.OTHER,
            subject="Help with my account",
        )
        session.add(ticket)
        await session.flush()
        stored = [
            TicketMessage(
                ticket_id=ticket.id,
                sender_type=SenderType.CUSTOMER if index % 2 else SenderType.AGENT,
                content=f"message {index}",
            )
            for index in range(1, 5)
        ]
        session.add_all(stored)
        await session.flush()
        customer_id, ticket_id, through_id = customer.id, ticket.id, stored[1].id

    llm = MockLLMClient(intents=[], responses=["Customer needs account help."])
    summarizer = ConversationSummaryService(
        session_factory=sessions,
        llm=llm,
        recent_message_limit=2,
    )
    first = await summarizer.compact(ticket_id, customer_id)
    second = await summarizer.compact(ticket_id, customer_id)

    assert first == second
    assert first.summary == "Customer needs account help."
    assert [message.content for message in first.messages] == [
        "Help with my account",
        "message 3",
        "message 4",
    ]
    assert llm.calls == ["generate"]
    async with sessions() as session:
        persisted = await session.get(Ticket, ticket_id)
        assert persisted is not None
        assert persisted.conversation_summary == first.summary
        assert persisted.summary_through_message_id == through_id

    async with sessions.begin() as session:
        await session.execute(delete(Ticket).where(Ticket.id == ticket_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_conversation_summary_is_persisted_and_reused() -> None:
    asyncio.run(summary_scenario())
