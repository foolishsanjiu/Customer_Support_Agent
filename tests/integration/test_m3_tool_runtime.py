import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.agent.tools import RuntimeToolAdapter
from app.core.errors import (
    ApprovalRequired,
    DuplicateToolCallConflict,
    ObjectAccessDenied,
    ToolUnavailable,
)
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import (
    AgentRun,
    Customer,
    IdempotencyRecord,
    Order,
    Refund,
    Shipment,
    Ticket,
    ToolCall,
)
from app.models.enums import (
    AgentRunStatus,
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    PrincipalRole,
    RefundStatus,
    ShipmentStatus,
    TicketCategory,
    TicketStatus,
)
from app.tool_runtime.models import ToolExecutionContext

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


async def run_catalog_scenario() -> None:
    engine = create_database_engine(database_url())
    session_factory = create_session_factory(engine)
    marker = uuid4().hex
    async with session_factory.begin() as session:
        customer = Customer(
            name="M3 Customer",
            email=f"m3-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        other = Customer(
            name="M3 Other",
            email=f"m3-other-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add_all([customer, other])
        await session.flush()
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.PAID,
            total_amount=Decimal("70.00"),
            paid_at=datetime.now(UTC).replace(tzinfo=None),
        )
        foreign_order = Order(
            customer_id=other.id,
            status=OrderStatus.PAID,
            total_amount=Decimal("80.00"),
            paid_at=datetime.now(UTC).replace(tzinfo=None),
        )
        delivered_order = Order(
            customer_id=customer.id,
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("90.00"),
            delivered_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add_all([order, foreign_order, delivered_order])
        await session.flush()
        shipment = Shipment(
            order_id=order.id,
            tracking_number=f"M3-{marker}",
            carrier="ResolveX Test",
            status=ShipmentStatus.IN_TRANSIT,
        )
        refund = Refund(
            order_id=delivered_order.id,
            amount=delivered_order.total_amount,
            reason="fixture",
            status=RefundStatus.SUCCESS,
            processed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        ticket = Ticket(
            customer_id=customer.id,
            order_id=order.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.CANCELLATION,
            subject="M3 runtime integration",
        )
        session.add_all([shipment, refund, ticket])
        await session.flush()
        run = AgentRun(
            ticket_id=ticket.id,
            status=AgentRunStatus.RUNNING,
            started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(run)
        await session.flush()
        ids = {
            "customer": customer.id,
            "other": other.id,
            "order": order.id,
            "foreign_order": foreign_order.id,
            "delivered_order": delivered_order.id,
            "shipment": shipment.id,
            "refund": refund.id,
            "ticket": ticket.id,
            "run": run.id,
        }

    adapter = RuntimeToolAdapter(session_factory)
    context = ToolExecutionContext(
        principal_id=str(ids["customer"]),
        customer_id=ids["customer"],
        role=PrincipalRole.CUSTOMER,
        ticket_id=ids["ticket"],
        agent_run_id=ids["run"],
        trace_id=marker,
    )

    async def call(name: str, arguments: dict, call_id: str):
        unique_call_id = f"{marker}-{call_id}"
        result = await adapter.execute(name, arguments, context, unique_call_id)
        assert await adapter.verify(name, arguments, result, context, unique_call_id)
        return result

    assert (await call("get_customer", {}, "customer"))["id"] == ids["customer"]
    assert (await call("get_order", {"order_id": ids["order"]}, "order"))["id"] == ids["order"]
    assert (await call("get_shipping", {"order_id": ids["order"]}, "shipping"))["id"] == ids[
        "shipment"
    ]
    assert (await call("get_refund", {"refund_id": ids["refund"]}, "refund-read"))["id"] == ids[
        "refund"
    ]
    assert (
        await call("update_ticket", {"status": TicketStatus.PROCESSING.value}, "ticket-update")
    )["status"] == TicketStatus.PROCESSING.value
    assert (await call("escalate_ticket", {"reason": "specialist needed"}, "escalate"))[
        "status"
    ] == TicketStatus.ESCALATED.value
    cancel_result = await call("cancel_order", {"order_id": ids["order"]}, "cancel")
    assert cancel_result["status"] == OrderStatus.CANCELLED.value
    assert (
        await adapter.execute(
            "cancel_order", {"order_id": ids["order"]}, context, f"{marker}-cancel"
        )
        == cancel_result
    )
    with pytest.raises(DuplicateToolCallConflict):
        await adapter.execute(
            "cancel_order",
            {"order_id": ids["foreign_order"]},
            context,
            f"{marker}-cancel",
        )

    with pytest.raises(ObjectAccessDenied):
        await adapter.execute(
            "get_order",
            {"order_id": ids["foreign_order"]},
            context,
            f"{marker}-cross-user",
        )
    with pytest.raises(ToolUnavailable):
        await adapter.execute(
            "search_policy", {"query": "refund"}, context, f"{marker}-policy"
        )

    manager_context = ToolExecutionContext(
        principal_id="manager-1",
        customer_id=ids["customer"],
        role=PrincipalRole.MANAGER,
        ticket_id=ids["ticket"],
        agent_run_id=ids["run"],
        trace_id=marker,
    )
    with pytest.raises(ApprovalRequired):
        await adapter.execute(
            "refund_order",
            {"order_id": ids["delivered_order"], "reason": "approved role, no approval"},
            manager_context,
            f"{marker}-refund-denied",
        )

    async with session_factory.begin() as session:
        calls = (
            await session.scalars(select(ToolCall).where(ToolCall.agent_run_id == ids["run"]))
        ).all()
        assert len(calls) == 10
        assert all(call.arguments.get("reason") != "fixture" for call in calls)

        await session.execute(delete(ToolCall).where(ToolCall.agent_run_id == ids["run"]))
        await session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.key.like(f"{ids['run']}:%"))
        )
        await session.execute(delete(AgentRun).where(AgentRun.id == ids["run"]))
        await session.execute(delete(Refund).where(Refund.id == ids["refund"]))
        await session.execute(delete(Shipment).where(Shipment.id == ids["shipment"]))
        await session.execute(delete(Ticket).where(Ticket.id == ids["ticket"]))
        await session.execute(
            delete(Order).where(
                Order.id.in_([ids["order"], ids["foreign_order"], ids["delivered_order"]])
            )
        )
        await session.execute(
            delete(Customer).where(Customer.id.in_([ids["customer"], ids["other"]]))
        )
    await engine.dispose()


def test_registered_catalog_uses_runtime_boundary() -> None:
    asyncio.run(run_catalog_scenario())
