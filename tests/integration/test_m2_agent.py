import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.agent.llm import MockLLMClient
from app.agent.models import IntentType, PlanAction, TicketIntent, ToolDecision
from app.agent.runner import AgentRunner
from app.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from app.mcp.models import FulfillmentStatusResponse
from app.models import (
    AgentRun,
    Customer,
    IdempotencyRecord,
    Order,
    Ticket,
    TicketMessage,
    ToolCall,
)
from app.models.enums import (
    AgentRunStatus,
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    SenderType,
    TicketCategory,
    TicketStatus,
    ToolCallStatus,
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


async def run_persisted_cancel_scenario() -> None:
    engine = create_database_engine(database_url())
    session_factory = create_session_factory(engine)
    marker = uuid4().hex
    async with session_factory.begin() as session:
        customer = Customer(
            name="M2 Agent Customer",
            email=f"m2-agent-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.PAID,
            total_amount=Decimal("66.00"),
            paid_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(order)
        await session.flush()
        ticket = Ticket(
            customer_id=customer.id,
            order_id=order.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.CANCELLATION,
            subject=f"Please cancel order {order.id}",
        )
        session.add(ticket)
        await session.flush()
        customer_id, order_id, ticket_id = customer.id, order.id, ticket.id

    llm = MockLLMClient(
        intents=[
            TicketIntent(
                intent=IntentType.CANCEL_ORDER,
                order_id=order_id,
                confidence=1,
            )
        ],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="cancel_order")],
        responses=["The cancellation was verified."],
    )

    class Fulfillment:
        async def get_fulfillment_status(self, order_reference: str):
            return FulfillmentStatusResponse(
                order_reference=order_reference,
                status="READY_TO_PICK",
                cancellable=True,
                updated_at=datetime.now(UTC),
            )

    result = await AgentRunner(
        session_factory=session_factory,
        llm=llm,
        max_steps=12,
        enable_context=False,
        fulfillment_client=Fulfillment(),
    ).run_ticket(ticket_id, customer_id)

    assert result["verification_complete"] is True
    assert result["final_response"] == "The cancellation was verified."
    async with session_factory.begin() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.ticket_id == ticket_id))
        persisted_order = await session.get(Order, order_id)
        agent_message = await session.scalar(
            select(TicketMessage).where(
                TicketMessage.ticket_id == ticket_id,
                TicketMessage.sender_type == SenderType.AGENT,
            )
        )
        assert run is not None
        assert run.status is AgentRunStatus.SUCCEEDED
        assert run.intent == IntentType.CANCEL_ORDER.value
        assert run.current_node == "persist"
        assert run.success is True
        assert run.recovery_attempts == 0
        assert persisted_order is not None
        assert persisted_order.status is OrderStatus.CANCELLED
        assert agent_message is not None
        assert agent_message.content == "The cancellation was verified."

        tool_call = await session.scalar(select(ToolCall).where(ToolCall.agent_run_id == run.id))
        assert tool_call is not None
        assert tool_call.tool_name == "cancel_order"
        assert tool_call.status is ToolCallStatus.SUCCEEDED
        assert tool_call.error_code is None
        idempotency = await session.get(IdempotencyRecord, f"{run.id}:{tool_call.tool_call_id}")
        assert idempotency is not None
        assert idempotency.result is not None
        assert idempotency.result["status"] == OrderStatus.CANCELLED.value

        await session.delete(idempotency)
        await session.delete(tool_call)
        await session.flush()
        await session.execute(delete(AgentRun).where(AgentRun.ticket_id == ticket_id))
        await session.execute(delete(TicketMessage).where(TicketMessage.ticket_id == ticket_id))
        await session.execute(delete(Ticket).where(Ticket.id == ticket_id))
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_agent_run_and_verified_write_persist_to_mysql() -> None:
    asyncio.run(run_persisted_cancel_scenario())
