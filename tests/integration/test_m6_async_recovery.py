import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, select

from app.core.errors import BusinessConflict
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import Customer, Order, Refund
from app.models.enums import (
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
)
from app.services.commerce import CommerceService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with MySQL running",
    ),
]


async def run_concurrent_refund_scenario() -> None:
    engine = create_database_engine(os.environ["DATABASE_URL"])
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customer = Customer(
            name="M6 Concurrent Refund",
            email=f"m6-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("99.00"),
            paid_at=now,
            delivered_at=now,
        )
        session.add(order)
        await session.flush()
        customer_id, order_id = customer.id, order.id

    async def refund(reason: str):
        async with sessions() as session:
            return await CommerceService(session).refund_order(
                order_id, customer_id, reason
            )

    outcomes = await asyncio.gather(
        refund("concurrent request A"),
        refund("concurrent request B"),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, Refund) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, BusinessConflict) for outcome in outcomes) == 1

    async with sessions.begin() as session:
        refunds = list(
            (await session.scalars(select(Refund).where(Refund.order_id == order_id))).all()
        )
        persisted_order = await session.get(Order, order_id)
        assert len(refunds) == 1
        assert persisted_order is not None
        assert persisted_order.status is OrderStatus.REFUNDED
        await session.execute(delete(Refund).where(Refund.order_id == order_id))
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_concurrent_duplicate_refund_has_one_business_outcome() -> None:
    asyncio.run(run_concurrent_refund_scenario())


class CrashState(TypedDict):
    count: int


async def run_checkpoint_recovery_scenario() -> None:
    redis_url = os.getenv("LANGGRAPH_REDIS_URL", "redis://localhost:6379/0")
    thread_id = f"m6-crash-{uuid4().hex}"
    calls: list[str] = []
    should_crash = True

    async def first(state: CrashState) -> dict[str, int]:
        calls.append("first")
        return {"count": state["count"] + 1}

    async def second(state: CrashState) -> dict[str, int]:
        nonlocal should_crash
        if should_crash:
            should_crash = False
            raise RuntimeError("simulated worker crash")
        calls.append("second")
        return {"count": state["count"] + 1}

    async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
        graph = StateGraph(CrashState)
        graph.add_node("first", first)
        graph.add_node("second", second)
        graph.add_edge(START, "first")
        graph.add_edge("first", "second")
        graph.add_edge("second", END)
        compiled = graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}
        with pytest.raises(RuntimeError, match="simulated worker crash"):
            await compiled.ainvoke({"count": 0}, config=config)
        result = await compiled.ainvoke(None, config=config)
        assert result["count"] == 2
        assert calls == ["first", "second"]
        await saver.adelete_thread(thread_id)


def test_checkpoint_recovery_continues_without_restarting_from_start() -> None:
    asyncio.run(run_checkpoint_recovery_scenario())
