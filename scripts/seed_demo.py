import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import get_settings
from app.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from app.models import Customer, Order, Refund, Shipment, Ticket, TicketMessage
from app.models.enums import (
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    RefundStatus,
    SenderType,
    ShipmentStatus,
    TicketCategory,
    TicketStatus,
)

CUSTOMER_COUNT = 100
ORDER_COUNT = 300
SCENARIO_COUNT = 24


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def scenario_status(order_id: int) -> OrderStatus:
    required = {
        1: OrderStatus.SHIPPED,
        2: OrderStatus.DELIVERED,
        3: OrderStatus.PAID,
        4: OrderStatus.REFUNDED,
        5: OrderStatus.DELIVERED,
        6: OrderStatus.SHIPPED,
        7: OrderStatus.PAID,
        8: OrderStatus.DELIVERED,
        9: OrderStatus.DELIVERED,
    }
    if order_id in required:
        return required[order_id]
    statuses = (
        OrderStatus.CREATED,
        OrderStatus.PAID,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    )
    return statuses[(order_id - 10) % len(statuses)]


def scenario_customer_id(order_id: int) -> int:
    if order_id in {*range(1, 8), 9}:
        return 1
    if order_id == 8:
        return 2
    return ((order_id - 1) % CUSTOMER_COUNT) + 1


def build_order(order_id: int, current_time: datetime) -> Order:
    status = scenario_status(order_id)
    paid_at = current_time - timedelta(days=10) if status is not OrderStatus.CREATED else None
    shipped_at = (
        current_time - timedelta(days=7)
        if status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.REFUNDED}
        else None
    )
    delivered_at = (
        current_time - timedelta(days=45 if order_id == 5 else 3)
        if status in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}
        else None
    )
    return Order(
        id=order_id,
        customer_id=scenario_customer_id(order_id),
        status=status,
        total_amount=Decimal(f"{50 + (order_id % 200)}.00"),
        paid_at=paid_at,
        shipped_at=shipped_at,
        delivered_at=delivered_at,
    )


async def seed() -> None:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to seed demo data")
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)
    current_time = now()

    async with session_factory() as session:
        existing = await session.scalar(select(func.count(Customer.id)))
        if existing:
            await session.rollback()
            print("Seed skipped: customers table is not empty.")
            await engine.dispose()
            return
        await session.rollback()

        async with session.begin():
            customers = [
                Customer(
                    id=customer_id,
                    name=f"Demo Customer {customer_id:03d}",
                    email=f"customer{customer_id:03d}@resolvex.example",
                    level=(
                        CustomerLevel.SVIP
                        if customer_id % 20 == 0
                        else CustomerLevel.VIP
                        if customer_id % 5 == 0
                        else CustomerLevel.NORMAL
                    ),
                    status=CustomerStatus.ACTIVE,
                )
                for customer_id in range(1, CUSTOMER_COUNT + 1)
            ]
            orders = [build_order(order_id, current_time) for order_id in range(1, ORDER_COUNT + 1)]
            session.add_all(customers)
            session.add_all(orders)
            await session.flush()

            shipment_orders = [
                order
                for order in orders
                if order.status
                in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.REFUNDED}
            ]
            session.add_all(
                [
                    Shipment(
                        order_id=order.id,
                        tracking_number=f"RX{order.id:010d}",
                        carrier="ResolveX Express",
                        status=(
                            ShipmentStatus.DELAYED
                            if order.id == 1
                            else ShipmentStatus.DELIVERED
                            if order.status in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}
                            else ShipmentStatus.IN_TRANSIT
                        ),
                        current_location="Shanghai Hub",
                        last_event="Delivery delayed" if order.id == 1 else "Shipment updated",
                        last_updated_at=current_time,
                        estimated_delivery_at=current_time + timedelta(days=2),
                    )
                    for order in shipment_orders
                ]
            )
            session.add(
                Refund(
                    order_id=4,
                    amount=orders[3].total_amount,
                    reason="Deterministic already-refunded scenario",
                    status=RefundStatus.SUCCESS,
                    processed_at=current_time - timedelta(days=1),
                )
            )

            tickets = [
                Ticket(
                    id=ticket_id,
                    customer_id=orders[ticket_id - 1].customer_id,
                    order_id=ticket_id,
                    status=TicketStatus.OPEN,
                    category=(
                        TicketCategory.REFUND if ticket_id % 3 == 0 else TicketCategory.SHIPPING
                    ),
                    subject=f"Deterministic scenario {ticket_id:02d}",
                )
                for ticket_id in range(1, SCENARIO_COUNT + 1)
            ]
            session.add_all(tickets)
            await session.flush()
            session.add_all(
                [
                    TicketMessage(
                        ticket_id=ticket.id,
                        sender_type=SenderType.CUSTOMER,
                        content=f"Initial message for scenario {ticket.id:02d}",
                    )
                    for ticket in tickets
                ]
            )

    await engine.dispose()
    print(
        f"Seeded {CUSTOMER_COUNT} customers, {ORDER_COUNT} orders, "
        f"and {SCENARIO_COUNT} ticket scenarios."
    )


if __name__ == "__main__":
    asyncio.run(seed())
