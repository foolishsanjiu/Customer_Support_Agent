import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.errors import BusinessConflict
from app.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from app.main import create_app
from app.models import Customer, Order, Refund, Shipment, Ticket, TicketMessage
from app.models.enums import (
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    ShipmentStatus,
)
from app.services.commerce import CommerceService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with MySQL and Redis running",
    ),
]


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def create_api_fixture() -> tuple[int, list[int]]:
    engine = create_database_engine(database_url())
    session_factory = create_session_factory(engine)
    marker = uuid4().hex
    current_time = utc_now_naive()
    async with session_factory.begin() as session:
        customer = Customer(
            name="M1 API Customer",
            email=f"m1-api-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        orders = [
            Order(
                customer_id=customer.id,
                status=OrderStatus.PAID,
                total_amount=Decimal("101.00"),
                paid_at=current_time,
            ),
            Order(
                customer_id=customer.id,
                status=OrderStatus.SHIPPED,
                total_amount=Decimal("102.00"),
                paid_at=current_time - timedelta(days=2),
                shipped_at=current_time - timedelta(days=1),
            ),
            Order(
                customer_id=customer.id,
                status=OrderStatus.DELIVERED,
                total_amount=Decimal("103.00"),
                paid_at=current_time - timedelta(days=5),
                shipped_at=current_time - timedelta(days=4),
                delivered_at=current_time - timedelta(days=2),
            ),
            Order(
                customer_id=customer.id,
                status=OrderStatus.DELIVERED,
                total_amount=Decimal("104.00"),
                paid_at=current_time - timedelta(days=50),
                shipped_at=current_time - timedelta(days=48),
                delivered_at=current_time - timedelta(days=45),
            ),
        ]
        session.add_all(orders)
        await session.flush()
        session.add(
            Shipment(
                order_id=orders[1].id,
                tracking_number=f"TEST-{marker}",
                carrier="Test Carrier",
                status=ShipmentStatus.IN_TRANSIT,
                current_location="Test Hub",
                last_event="In transit",
                last_updated_at=current_time,
                estimated_delivery_at=current_time + timedelta(days=1),
            )
        )
    await engine.dispose()
    return customer.id, [order.id for order in orders]


async def cleanup_fixture(customer_id: int, order_ids: list[int]) -> None:
    engine = create_database_engine(database_url())
    session_factory = create_session_factory(engine)
    async with session_factory.begin() as session:
        ticket_ids = select(Ticket.id).where(Ticket.customer_id == customer_id)
        await session.execute(delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids)))
        await session.execute(delete(Ticket).where(Ticket.customer_id == customer_id))
        await session.execute(delete(Refund).where(Refund.order_id.in_(order_ids)))
        await session.execute(delete(Shipment).where(Shipment.order_id.in_(order_ids)))
        await session.execute(delete(Order).where(Order.id.in_(order_ids)))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_m1_api_business_rules() -> None:
    customer_id, order_ids = asyncio.run(create_api_fixture())
    paid_order, shipped_order, refundable_order, expired_order = order_ids
    try:
        with TestClient(create_app()) as client:
            assert client.get(f"/api/v1/customers/{customer_id}").status_code == 200
            assert len(client.get(f"/api/v1/customers/{customer_id}/orders").json()) == 4
            assert client.get(f"/api/v1/orders/{shipped_order}/shipment").status_code == 200

            cancelled = client.post(
                f"/api/v1/orders/{paid_order}/cancel",
                json={"customer_id": customer_id},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "CANCELLED"

            invalid_cancel = client.post(
                f"/api/v1/orders/{shipped_order}/cancel",
                json={"customer_id": customer_id},
            )
            assert invalid_cancel.status_code == 409

            forbidden = client.post(
                f"/api/v1/orders/{refundable_order}/refund",
                json={"customer_id": customer_id + 1, "reason": "not mine"},
            )
            assert forbidden.status_code == 403

            refund = client.post(
                f"/api/v1/orders/{refundable_order}/refund",
                json={"customer_id": customer_id, "reason": "valid test refund"},
            )
            assert refund.status_code == 201
            assert refund.json()["status"] == "SUCCESS"
            assert client.get(f"/api/v1/refunds/{refund.json()['id']}").status_code == 200

            duplicate = client.post(
                f"/api/v1/orders/{refundable_order}/refund",
                json={"customer_id": customer_id, "reason": "duplicate"},
            )
            assert duplicate.status_code == 409

            expired = client.post(
                f"/api/v1/orders/{expired_order}/refund",
                json={"customer_id": customer_id, "reason": "too late"},
            )
            assert expired.status_code == 409

            ticket = client.post(
                "/api/v1/tickets",
                json={
                    "customer_id": customer_id,
                    "order_id": shipped_order,
                    "category": "SHIPPING",
                    "subject": "Where is my order?",
                },
            )
            assert ticket.status_code == 201
            ticket_id = ticket.json()["id"]
            message = client.post(
                f"/api/v1/tickets/{ticket_id}/messages",
                json={"sender_type": "CUSTOMER", "content": "Please check."},
            )
            assert message.status_code == 201
            assert len(client.get(f"/api/v1/tickets/{ticket_id}").json()["messages"]) == 1
    finally:
        asyncio.run(cleanup_fixture(customer_id, order_ids))


async def concurrent_refund_scenario() -> None:
    engine = create_database_engine(database_url())
    session_factory = create_session_factory(engine)
    marker = uuid4().hex
    current_time = utc_now_naive()
    async with session_factory.begin() as session:
        customer = Customer(
            name="Concurrent Refund Customer",
            email=f"m1-concurrent-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("88.00"),
            paid_at=current_time - timedelta(days=5),
            shipped_at=current_time - timedelta(days=4),
            delivered_at=current_time - timedelta(days=1),
        )
        session.add(order)
        await session.flush()
        customer_id, order_id = customer.id, order.id

    async with session_factory() as first, session_factory() as second:
        results = await asyncio.gather(
            CommerceService(first).refund_order(order_id, customer_id, "first"),
            CommerceService(second).refund_order(order_id, customer_id, "second"),
            return_exceptions=True,
        )
    assert sum(isinstance(result, Refund) for result in results) == 1
    assert sum(isinstance(result, BusinessConflict) for result in results) == 1

    async with session_factory.begin() as session:
        refund_count = await session.scalar(
            select(func.count(Refund.id)).where(Refund.order_id == order_id)
        )
        persisted_order = await session.get(Order, order_id)
        assert refund_count == 1
        assert persisted_order is not None
        assert persisted_order.status is OrderStatus.REFUNDED
        await session.execute(delete(Refund).where(Refund.order_id == order_id))
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_concurrent_duplicate_refund_has_one_business_outcome() -> None:
    asyncio.run(concurrent_refund_scenario())
