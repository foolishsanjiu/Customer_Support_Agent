from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interfaces import ToolAdapter
from app.core.errors import ObjectAccessDenied
from app.models.enums import OrderStatus
from app.schemas.commerce import OrderResponse, ShipmentResponse
from app.services.commerce import CommerceService


class BusinessToolAdapter(ToolAdapter):
    ALLOWED_TOOLS = ("get_order", "get_shipment", "cancel_order")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def execute(
        self, tool_name: str, arguments: dict[str, Any], customer_id: int
    ) -> dict[str, Any]:
        order_id = int(arguments["order_id"])
        async with self.session_factory() as session:
            service = CommerceService(session)
            if tool_name == "get_order":
                order = await service.get_order(order_id)
                if order.customer_id != customer_id:
                    raise ObjectAccessDenied("order does not belong to customer")
                return OrderResponse.model_validate(order).model_dump(mode="json")
            if tool_name == "get_shipment":
                order = await service.get_order(order_id)
                if order.customer_id != customer_id:
                    raise ObjectAccessDenied("order does not belong to customer")
                shipment = await service.get_shipment(order_id)
                return ShipmentResponse.model_validate(shipment).model_dump(mode="json")
            if tool_name == "cancel_order":
                order = await service.cancel_order(order_id, customer_id)
                return OrderResponse.model_validate(order).model_dump(mode="json")
        raise ValueError(f"unsupported M2 tool: {tool_name}")

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        customer_id: int,
    ) -> bool:
        order_id = int(arguments["order_id"])
        async with self.session_factory() as session:
            service = CommerceService(session)
            order = await service.get_order(order_id)
            if order.customer_id != customer_id:
                return False
            if tool_name == "cancel_order":
                return order.status is OrderStatus.CANCELLED
            if tool_name == "get_order":
                return result.get("id") == order.id
            if tool_name == "get_shipment":
                shipment = await service.get_shipment(order_id)
                return result.get("id") == shipment.id
        return False
