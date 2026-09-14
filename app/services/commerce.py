from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessConflict, ObjectAccessDenied, ResourceNotFound
from app.models import Customer, Order, Refund, Shipment
from app.models.enums import OrderStatus, RefundStatus
from app.repositories.commerce import CommerceRepository

REFUND_WINDOW_DAYS = 30
CANCELLABLE_ORDER_STATUSES = {OrderStatus.CREATED, OrderStatus.PAID}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CommerceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = CommerceRepository(session)

    async def get_customer(self, customer_id: int) -> Customer:
        customer = await self.repository.get_customer(customer_id)
        if customer is None:
            raise ResourceNotFound("customer not found")
        return customer

    async def get_order(self, order_id: int) -> Order:
        order = await self.repository.get_order(order_id)
        if order is None:
            raise ResourceNotFound("order not found")
        return order

    async def list_customer_orders(self, customer_id: int) -> list[Order]:
        await self.get_customer(customer_id)
        return await self.repository.list_customer_orders(customer_id)

    async def get_shipment(self, order_id: int) -> Shipment:
        await self.get_order(order_id)
        shipment = await self.repository.get_shipment_for_order(order_id)
        if shipment is None:
            raise ResourceNotFound("shipment not found")
        return shipment

    async def get_refund(self, refund_id: int) -> Refund:
        refund = await self.repository.get_refund(refund_id)
        if refund is None:
            raise ResourceNotFound("refund not found")
        return refund

    async def cancel_order(self, order_id: int, customer_id: int) -> Order:
        async with self.session.begin():
            order = await self.repository.get_order(order_id, for_update=True)
            if order is None:
                raise ResourceNotFound("order not found")
            self._check_ownership(order, customer_id)
            if order.status not in CANCELLABLE_ORDER_STATUSES:
                raise BusinessConflict(f"order in {order.status.value} state cannot be cancelled")
            order.status = OrderStatus.CANCELLED
            await self.session.flush()
            await self.session.refresh(order)
        return order

    async def refund_order(self, order_id: int, customer_id: int, reason: str) -> Refund:
        try:
            async with self.session.begin():
                order = await self.repository.get_order(order_id, for_update=True)
                if order is None:
                    raise ResourceNotFound("order not found")
                self._check_ownership(order, customer_id)

                existing = await self.repository.get_refund_for_order(order_id)
                if existing is not None:
                    raise BusinessConflict("refund already exists for this order")
                self._check_refund_eligibility(order)

                refund = Refund(
                    order_id=order.id,
                    amount=order.total_amount,
                    reason=reason.strip(),
                    status=RefundStatus.SUCCESS,
                    processed_at=utc_now_naive(),
                )
                self.repository.add_refund(refund)
                order.status = OrderStatus.REFUNDED
                await self.session.flush()
                await self.session.refresh(refund)
        except IntegrityError as exc:
            raise BusinessConflict("refund already exists for this order") from exc
        return refund

    @staticmethod
    def _check_ownership(order: Order, customer_id: int) -> None:
        if order.customer_id != customer_id:
            raise ObjectAccessDenied("order does not belong to customer")

    @staticmethod
    def _check_refund_eligibility(order: Order) -> None:
        if order.status is not OrderStatus.DELIVERED or order.delivered_at is None:
            raise BusinessConflict(
                f"order in {order.status.value} state is not eligible for refund"
            )
        deadline = order.delivered_at + timedelta(days=REFUND_WINDOW_DAYS)
        if utc_now_naive() > deadline:
            raise BusinessConflict("order is outside the 30-day refund window")
