import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    ApprovalDecisionConflict,
    ApprovalInvalid,
    ObjectAccessDenied,
    ResourceNotFound,
)
from app.models import AgentRun, Approval, AuditLog, Customer, Order, Ticket
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    CustomerStatus,
    PrincipalRole,
    TicketStatus,
    ToolRiskLevel,
)
from app.observability.tracing import current_trace_id
from app.services.commerce import CommerceService
from app.tool_runtime.models import RefundOrderInput, ToolExecutionContext

CURRENCY = "CNY"
APPROVER_ROLES = {PrincipalRole.MANAGER, PrincipalRole.ADMIN}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def action_fingerprint(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def refund_snapshot(
    *,
    order: Order,
    arguments: RefundOrderInput,
    context: ToolExecutionContext,
    tool_call_id: str,
) -> dict[str, Any]:
    return {
        "tool_name": "refund_order",
        "order_id": order.id,
        "amount": format(order.total_amount, ".2f"),
        "currency": CURRENCY,
        "reason": arguments.reason.strip(),
        "customer_id": context.customer_id,
        "run_id": context.agent_run_id,
        "tool_call_id": tool_call_id,
    }


class ApprovalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ttl_minutes: int = 1440,
    ) -> None:
        if ttl_minutes < 1:
            raise ValueError("ttl_minutes must be positive")
        self.session_factory = session_factory
        self.ttl_minutes = ttl_minutes

    async def prepare_refund(
        self,
        *,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
        reason: str,
    ) -> Approval:
        validated = RefundOrderInput.model_validate(arguments)
        async with self.session_factory.begin() as session:
            order = await self.validate_refund_scope(session, validated, context, lock=True)
            snapshot = refund_snapshot(
                order=order,
                arguments=validated,
                context=context,
                tool_call_id=tool_call_id,
            )
            fingerprint = action_fingerprint(snapshot)
            existing = await session.scalar(
                select(Approval).where(Approval.tool_call_id == tool_call_id).with_for_update()
            )
            if existing is not None:
                if existing.action_fingerprint != fingerprint:
                    raise ApprovalInvalid("tool call approval fingerprint changed")
                return existing

            run = await session.get(AgentRun, context.agent_run_id, with_for_update=True)
            ticket = await session.get(Ticket, context.ticket_id, with_for_update=True)
            if run is None or run.ticket_id != context.ticket_id:
                raise ApprovalInvalid("agent run does not match ticket")
            if ticket is None or ticket.customer_id != context.customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")
            now = utc_now_naive()
            approval = Approval(
                run_id=run.id,
                ticket_id=ticket.id,
                tool_call_id=tool_call_id,
                tool_name="refund_order",
                requested_by=context.principal_id,
                risk_level=ToolRiskLevel.L3,
                arguments_snapshot=snapshot,
                action_fingerprint=fingerprint,
                reason=reason,
                status=ApprovalStatus.PENDING,
                required_role=PrincipalRole.MANAGER,
                expires_at=now + timedelta(minutes=self.ttl_minutes),
            )
            session.add(approval)
            await session.flush()
            run.status = AgentRunStatus.WAITING_APPROVAL
            run.current_node = "prepare_approval"
            ticket.status = TicketStatus.WAITING_APPROVAL
            session.add(
                AuditLog(
                    event_type="approval_requested",
                    run_id=run.id,
                    ticket_id=ticket.id,
                    approval_id=approval.id,
                    tool_call_id=tool_call_id,
                    actor_id=context.principal_id,
                    actor_role=context.role,
                    trace_id=context.trace_id,
                    details={"risk_level": ToolRiskLevel.L3.value},
                )
            )
            await session.flush()
            await session.refresh(approval)
            return approval

    async def validate_for_execution(
        self,
        *,
        approval_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> None:
        if tool_name != "refund_order":
            raise ApprovalInvalid("approval is not valid for this tool")
        validated = RefundOrderInput.model_validate(arguments)
        validation_error: str | None = None
        async with self.session_factory.begin() as session:
            approval = await session.get(Approval, approval_id, with_for_update=True)
            if approval is None:
                raise ApprovalInvalid("approval not found")
            now = utc_now_naive()
            if approval.expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                validation_error = "approval has expired"
            elif approval.status is not ApprovalStatus.APPROVED:
                raise ApprovalInvalid("approval is not approved")
            elif any(
                (
                    approval.run_id != context.agent_run_id,
                    approval.ticket_id != context.ticket_id,
                    approval.tool_call_id != tool_call_id,
                    approval.tool_name != tool_name,
                )
            ):
                raise ApprovalInvalid("approval scope does not match tool execution")
            elif validation_error is None:
                order = await self.validate_refund_scope(session, validated, context, lock=True)
                snapshot = refund_snapshot(
                    order=order,
                    arguments=validated,
                    context=context,
                    tool_call_id=tool_call_id,
                )
                if action_fingerprint(snapshot) != approval.action_fingerprint:
                    approval.status = ApprovalStatus.CANCELLED
                    validation_error = "approved material action changed"
            if validation_error is not None:
                run = await session.get(AgentRun, context.agent_run_id, with_for_update=True)
                ticket = await session.get(Ticket, context.ticket_id, with_for_update=True)
                if run is not None:
                    run.status = AgentRunStatus.FAILED
                    run.error_code = "approval_revalidation_denied"
                if ticket is not None:
                    ticket.status = TicketStatus.ESCALATED
                session.add(
                    AuditLog(
                        event_type="approval_revalidation_denied",
                        run_id=context.agent_run_id,
                        ticket_id=context.ticket_id,
                        approval_id=approval.id,
                        tool_call_id=tool_call_id,
                        trace_id=context.trace_id,
                        details={"reason": validation_error},
                    )
                )
        if validation_error is not None:
            raise ApprovalInvalid(validation_error)

    async def decide(
        self,
        *,
        approval_id: int,
        approve: bool,
        principal_id: str,
        role: PrincipalRole,
        reason: str,
    ) -> Approval:
        if role not in APPROVER_ROLES:
            raise ObjectAccessDenied("manager approval role is required")
        decision_error: str | None = None
        async with self.session_factory.begin() as session:
            approval = await session.get(Approval, approval_id, with_for_update=True)
            if approval is None:
                raise ResourceNotFound("approval not found")
            run = await session.get(AgentRun, approval.run_id, with_for_update=True)
            if run is None:
                raise ResourceNotFound("agent run not found")
            ticket = await session.get(Ticket, approval.ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            now = utc_now_naive()
            if approval.status is not ApprovalStatus.PENDING:
                raise ApprovalDecisionConflict("approval is no longer pending")
            if approval.expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                run.status = AgentRunStatus.FAILED
                run.error_code = "approval_expired"
                ticket.status = TicketStatus.ESCALATED
                decision_error = "approval has expired"
            else:
                approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
                approval.decision_reason = reason.strip()
                if approve:
                    approval.approved_by = principal_id
                    approval.approved_at = now
                else:
                    approval.rejected_by = principal_id
                    approval.rejected_at = now
                    ticket.status = TicketStatus.ESCALATED
                run.status = AgentRunStatus.RESUME_PENDING
                run.current_node = "approval_decided"
            session.add(
                AuditLog(
                    event_type=(
                        "approval_expired"
                        if decision_error
                        else "approval_approved"
                        if approve
                        else "approval_rejected"
                    ),
                    run_id=approval.run_id,
                    ticket_id=approval.ticket_id,
                    approval_id=approval.id,
                    tool_call_id=approval.tool_call_id,
                    actor_id=principal_id,
                    actor_role=role,
                    trace_id=current_trace_id(),
                    details={},
                )
            )
            await session.flush()
            await session.refresh(approval)
        if decision_error is not None:
            raise ApprovalDecisionConflict(decision_error)
        return approval

    async def get(self, approval_id: int) -> Approval:
        async with self.session_factory() as session:
            approval = await session.get(Approval, approval_id)
            if approval is None:
                raise ResourceNotFound("approval not found")
            return approval

    async def list(self, status: ApprovalStatus | None = None) -> list[Approval]:
        async with self.session_factory() as session:
            statement = select(Approval).order_by(Approval.id.desc()).limit(100)
            if status is not None:
                statement = statement.where(Approval.status == status)
            return list((await session.scalars(statement)).all())

    @staticmethod
    async def validate_refund_scope(
        session: AsyncSession,
        arguments: RefundOrderInput,
        context: ToolExecutionContext,
        *,
        lock: bool,
    ) -> Order:
        customer = await session.get(Customer, context.customer_id)
        if customer is None:
            raise ResourceNotFound("customer not found")
        if customer.status is not CustomerStatus.ACTIVE:
            raise ApprovalInvalid("customer is not active")
        order = await session.get(Order, arguments.order_id, with_for_update=lock)
        if order is None:
            raise ResourceNotFound("order not found")
        if order.customer_id != context.customer_id:
            raise ObjectAccessDenied("order does not belong to customer")
        CommerceService.validate_refund_eligibility(order)
        return order
