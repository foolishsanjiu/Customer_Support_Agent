from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Order, Refund, Shipment


class CommerceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_customer(self, customer_id: int) -> Customer | None:
        return await self.session.get(Customer, customer_id)

    async def get_order(self, order_id: int, *, for_update: bool = False) -> Order | None:
        statement = select(Order).where(Order.id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_customer_orders(self, customer_id: int) -> list[Order]:
        result = await self.session.scalars(
            select(Order).where(Order.customer_id == customer_id).order_by(Order.id)
        )
        return list(result)

    async def get_shipment_for_order(self, order_id: int) -> Shipment | None:
        return await self.session.scalar(select(Shipment).where(Shipment.order_id == order_id))

    async def get_refund(self, refund_id: int) -> Refund | None:
        return await self.session.get(Refund, refund_id)

    async def get_refund_for_order(self, order_id: int) -> Refund | None:
        return await self.session.scalar(select(Refund).where(Refund.order_id == order_id))

    def add_refund(self, refund: Refund) -> None:
        self.session.add(refund)
