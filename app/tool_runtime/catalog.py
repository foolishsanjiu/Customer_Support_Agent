from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ObjectAccessDenied, ResourceNotFound, ToolUnavailable
from app.mcp.client import LogisticsClient
from app.models import Order, Refund, Ticket
from app.models.enums import TicketStatus, ToolRiskLevel
from app.policy.retriever import ChromaPolicyRetriever
from app.schemas.commerce import (
    CustomerResponse,
    OrderResponse,
    RefundResponse,
    ShipmentResponse,
)
from app.schemas.tickets import TicketResponse
from app.services.commerce import CommerceService
from app.tool_runtime.models import (
    ApprovalRequestInput,
    EmptyInput,
    EscalateTicketInput,
    OrderInput,
    PolicySearchInput,
    RefundLookupInput,
    RefundOrderInput,
    RetryPolicy,
    TicketUpdateInput,
    ToolDefinition,
    ToolExecutionContext,
)
from app.tool_runtime.registry import ToolRegistry


class BusinessToolCatalog:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        policy_retriever: ChromaPolicyRetriever | None = None,
        logistics_client: LogisticsClient | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.policy_retriever = policy_retriever
        self.logistics_client = logistics_client

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        definitions = (
            self._definition(
                "get_customer",
                "Get current customer",
                EmptyInput,
                self.get_customer,
                ToolRiskLevel.L1,
                "customer:read",
                True,
                {"OTHER"},
                verifier=self.verify_customer,
            ),
            self._definition(
                "get_order",
                "Get customer order",
                OrderInput,
                self.get_order,
                ToolRiskLevel.L1,
                "order:read",
                True,
                {"ORDER_QUERY"},
                authorizer=self.authorize_order,
                verifier=self.verify_order,
            ),
            self._definition(
                "get_shipping",
                "Get order shipping",
                OrderInput,
                self.get_shipping,
                ToolRiskLevel.L1,
                "shipping:read",
                True,
                authorizer=self.authorize_order,
                verifier=self.verify_shipping,
            ),
            self._definition(
                "get_tracking",
                "Get external tracking for an owned order",
                OrderInput,
                self.get_tracking,
                ToolRiskLevel.L1,
                "shipping:read",
                True,
                {"SHIPPING_QUERY"},
                authorizer=self.authorize_order,
                verifier=self.verify_tracking,
            ),
            self._definition(
                "get_delivery_estimate",
                "Get external delivery estimate for an owned order",
                OrderInput,
                self.get_delivery_estimate,
                ToolRiskLevel.L1,
                "shipping:read",
                True,
                authorizer=self.authorize_order,
                verifier=self.verify_delivery_estimate,
            ),
            self._definition(
                "get_refund",
                "Get customer refund",
                RefundLookupInput,
                self.get_refund,
                ToolRiskLevel.L1,
                "refund:read",
                True,
                authorizer=self.authorize_refund,
                verifier=self.verify_refund,
            ),
            self._definition(
                "search_policy",
                "Search policy",
                PolicySearchInput,
                self.search_policy,
                ToolRiskLevel.L1,
                "policy:read",
                True,
                {"POLICY_QUESTION"},
            ),
            self._definition(
                "cancel_order",
                "Cancel eligible order",
                OrderInput,
                self.cancel_order,
                ToolRiskLevel.L2,
                "order:cancel",
                False,
                {"CANCEL_ORDER"},
                authorizer=self.authorize_order,
                verifier=self.verify_cancel,
            ),
            self._definition(
                "refund_order",
                "Refund after approval",
                RefundOrderInput,
                self.refund_order,
                ToolRiskLevel.L3,
                "refund:request",
                False,
                {"REFUND"},
                authorizer=self.authorize_order,
                verifier=self.verify_refund_order,
            ),
            self._definition(
                "update_ticket",
                "Update ticket status",
                TicketUpdateInput,
                self.update_ticket,
                ToolRiskLevel.L2,
                "ticket:update",
                False,
                authorizer=self.authorize_ticket,
                verifier=self.verify_ticket,
            ),
            self._definition(
                "escalate_ticket",
                "Escalate ticket",
                EscalateTicketInput,
                self.escalate_ticket,
                ToolRiskLevel.L2,
                "ticket:escalate",
                False,
                authorizer=self.authorize_ticket,
                verifier=self.verify_escalation,
            ),
            self._definition(
                "request_human_approval",
                "Request material-action approval",
                ApprovalRequestInput,
                self.unavailable,
                ToolRiskLevel.L2,
                "approval:request",
                False,
            ),
        )
        for definition in definitions:
            registry.register(definition)
        return registry

    @staticmethod
    def _definition(
        name: str,
        description: str,
        schema: type[BaseModel],
        handler,
        risk: ToolRiskLevel,
        permission: str,
        read_only: bool,
        intents=frozenset(),
        *,
        authorizer=None,
        verifier=None,
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=schema,
            handler=handler,
            risk_level=risk,
            required_permission=permission,
            timeout_seconds=2,
            retry_policy=RetryPolicy(max_attempts=2 if read_only else 1),
            idempotent=not read_only,
            read_only=read_only,
            intents=frozenset(intents),
            authorizer=authorizer,
            verifier=verifier,
        )

    async def authorize_order(self, arguments: OrderInput, context: ToolExecutionContext) -> None:
        async with self.session_factory() as session:
            order = await session.get(Order, arguments.order_id)
            if order is None:
                raise ResourceNotFound("order not found")
            if order.customer_id != context.customer_id:
                raise ObjectAccessDenied("order does not belong to customer")

    async def authorize_refund(
        self, arguments: RefundLookupInput, context: ToolExecutionContext
    ) -> None:
        async with self.session_factory() as session:
            refund = await session.get(Refund, arguments.refund_id)
            if refund is None:
                raise ResourceNotFound("refund not found")
            order = await session.get(Order, refund.order_id)
            if order is None or order.customer_id != context.customer_id:
                raise ObjectAccessDenied("refund does not belong to customer")

    async def authorize_ticket(self, arguments: BaseModel, context: ToolExecutionContext) -> None:
        async with self.session_factory() as session:
            ticket = await session.get(Ticket, context.ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.customer_id != context.customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")

    async def get_customer(
        self, arguments: EmptyInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            customer = await CommerceService(session).get_customer(context.customer_id)
            return CustomerResponse.model_validate(customer).model_dump(mode="json")

    async def get_order(
        self, arguments: OrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            order = await CommerceService(session).get_order(arguments.order_id)
            return OrderResponse.model_validate(order).model_dump(mode="json")

    async def get_shipping(
        self, arguments: OrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            shipment = await CommerceService(session).get_shipment(arguments.order_id)
            return ShipmentResponse.model_validate(shipment).model_dump(mode="json")

    async def get_tracking(
        self, arguments: OrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        if self.logistics_client is None:
            raise ToolUnavailable("logistics MCP is not configured")
        async with self.session_factory() as session:
            shipment = await CommerceService(session).get_shipment(arguments.order_id)
            result = await self.logistics_client.get_tracking(shipment.tracking_number)
            return result.model_dump(mode="json")

    async def get_delivery_estimate(
        self, arguments: OrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        if self.logistics_client is None:
            raise ToolUnavailable("logistics MCP is not configured")
        async with self.session_factory() as session:
            shipment = await CommerceService(session).get_shipment(arguments.order_id)
            result = await self.logistics_client.get_delivery_estimate(shipment.tracking_number)
            return result.model_dump(mode="json")

    async def get_refund(
        self, arguments: RefundLookupInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            refund = await CommerceService(session).get_refund(arguments.refund_id)
            return RefundResponse.model_validate(refund).model_dump(mode="json")

    async def search_policy(
        self, arguments: PolicySearchInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        if self.policy_retriever is None:
            raise ToolUnavailable("policy retrieval is not configured")
        matches = await self.policy_retriever.search(arguments.query)
        return {"matches": [match.model_dump(mode="json") for match in matches]}

    async def cancel_order(
        self, arguments: OrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            order = await CommerceService(session).cancel_order(
                arguments.order_id, context.customer_id
            )
            return OrderResponse.model_validate(order).model_dump(mode="json")

    async def refund_order(
        self, arguments: RefundOrderInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            refund = await CommerceService(session).refund_order(
                arguments.order_id, context.customer_id, arguments.reason
            )
            return RefundResponse.model_validate(refund).model_dump(mode="json")

    async def update_ticket(
        self, arguments: TicketUpdateInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            ticket = await session.get(Ticket, context.ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            ticket.status = arguments.status
            await session.flush()
            await session.refresh(ticket)
            return TicketResponse.model_validate(ticket).model_dump(mode="json")

    async def escalate_ticket(
        self, arguments: EscalateTicketInput, context: ToolExecutionContext
    ) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            ticket = await session.get(Ticket, context.ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            ticket.status = TicketStatus.ESCALATED
            await session.flush()
            await session.refresh(ticket)
            return TicketResponse.model_validate(ticket).model_dump(mode="json")

    async def unavailable(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> dict[str, Any]:
        raise ToolUnavailable("tool capability is registered but not enabled in this milestone")

    async def verify_customer(
        self, arguments: EmptyInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        return result.get("id") == context.customer_id

    async def verify_order(
        self, arguments: OrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        return (
            result.get("id") == arguments.order_id
            and result.get("customer_id") == context.customer_id
        )

    async def verify_shipping(
        self, arguments: OrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        return result.get("order_id") == arguments.order_id

    async def verify_tracking(
        self, arguments: OrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        async with self.session_factory() as session:
            shipment = await CommerceService(session).get_shipment(arguments.order_id)
            return result.get("tracking_number") == shipment.tracking_number

    async def verify_delivery_estimate(
        self, arguments: OrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        async with self.session_factory() as session:
            shipment = await CommerceService(session).get_shipment(arguments.order_id)
            return (
                result.get("tracking_number") == shipment.tracking_number
                and "estimated_delivery_at" in result
            )

    async def verify_refund(
        self, arguments: RefundLookupInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        async with self.session_factory() as session:
            refund = await session.get(Refund, arguments.refund_id)
            if refund is None:
                return False
            order = await session.get(Order, refund.order_id)
            return (
                order is not None
                and order.customer_id == context.customer_id
                and result.get("id") == refund.id
            )

    async def verify_cancel(
        self, arguments: OrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        async with self.session_factory() as session:
            order = await session.get(Order, arguments.order_id)
            return order is not None and order.status.value == "CANCELLED"

    async def verify_refund_order(
        self, arguments: RefundOrderInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        async with self.session_factory() as session:
            refund = await session.scalar(
                select(Refund).where(Refund.order_id == arguments.order_id)
            )
            return refund is not None and refund.id == result.get("id")

    async def verify_ticket(
        self, arguments: TicketUpdateInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        return (
            result.get("id") == context.ticket_id and result.get("status") == arguments.status.value
        )

    async def verify_escalation(
        self, arguments: EscalateTicketInput, result: dict[str, Any], context: ToolExecutionContext
    ) -> bool:
        return result.get("id") == context.ticket_id and result.get("status") == "ESCALATED"
