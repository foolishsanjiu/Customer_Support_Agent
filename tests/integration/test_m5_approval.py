import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from sqlalchemy import delete, select

from app.agent.llm import MockLLMClient
from app.agent.models import IntentType, PlanAction, TicketIntent, ToolDecision
from app.agent.run_guard import RunAction, RunStateGuard, RunTrigger
from app.agent.runner import AgentRunner
from app.approvals.service import ApprovalService
from app.core.errors import ApprovalDecisionConflict, ApprovalInvalid, ObjectAccessDenied
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import (
    AgentRun,
    Approval,
    AuditLog,
    Customer,
    IdempotencyRecord,
    Order,
    Refund,
    Ticket,
    TicketMessage,
    ToolCall,
)
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    PrincipalRole,
    TicketCategory,
    TicketStatus,
)
from app.tool_runtime.models import ToolExecutionContext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with MySQL and Redis running",
    ),
]


async def run_approved_refund_scenario() -> None:
    database_url = os.environ["DATABASE_URL"]
    redis_url = os.getenv("LANGGRAPH_REDIS_URL", "redis://localhost:6379/0")
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    async with sessions.begin() as session:
        customer = Customer(
            name="M5 Approval Customer",
            email=f"m5-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("88.00"),
            paid_at=now,
            delivered_at=now,
        )
        session.add(order)
        await session.flush()
        ticket = Ticket(
            customer_id=customer.id,
            order_id=order.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.REFUND,
            subject=f"Refund order {order.id} because it arrived damaged",
        )
        session.add(ticket)
        await session.flush()
        customer_id, order_id, ticket_id = customer.id, order.id, ticket.id

    llm = MockLLMClient(
        intents=[
            TicketIntent(
                intent=IntentType.REFUND,
                order_id=order_id,
                reason="arrived damaged",
                confidence=1,
            )
        ],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="refund_order")],
        responses=["The approved refund was completed."],
    )
    async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
        await saver.asetup()
        runner = AgentRunner(
            session_factory=sessions,
            llm=llm,
            max_steps=12,
            enable_context=False,
            checkpointer=saver,
        )
        interrupted = await runner.run_ticket(ticket_id, customer_id)
        assert interrupted["approval_status"] == ApprovalStatus.PENDING.value

        async with sessions() as session:
            approval = await session.scalar(select(Approval).where(Approval.ticket_id == ticket_id))
            assert approval is not None
            assert approval.status is ApprovalStatus.PENDING
            approval_id = approval.id
            run_id = approval.run_id
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status is AgentRunStatus.WAITING_APPROVAL

        call = interrupted["pending_tool_calls"][0]
        execution_context = ToolExecutionContext(
            principal_id=str(customer_id),
            customer_id=customer_id,
            role=PrincipalRole.CUSTOMER,
            ticket_id=ticket_id,
            agent_run_id=run_id,
            trace_id=interrupted["trace_id"],
        )
        approval_service = ApprovalService(sessions)
        duplicate = await approval_service.prepare_refund(
            arguments=call["arguments"],
            context=execution_context,
            tool_call_id=call["tool_call_id"],
            reason="same request",
        )
        assert duplicate.id == approval_id
        assert (await approval_service.get(approval_id)).id == approval_id
        assert approval_id in {
            item.id for item in await approval_service.list(ApprovalStatus.PENDING)
        }
        with pytest.raises(ApprovalInvalid, match="fingerprint changed"):
            await approval_service.prepare_refund(
                arguments={"order_id": order_id, "reason": "different reason"},
                context=execution_context,
                tool_call_id=call["tool_call_id"],
                reason="changed request",
            )
        with pytest.raises(ObjectAccessDenied, match="manager"):
            await approval_service.decide(
                approval_id=approval_id,
                approve=True,
                principal_id=str(customer_id),
                role=PrincipalRole.CUSTOMER,
                reason="self approval",
            )

        decided = await approval_service.decide(
            approval_id=approval_id,
            approve=True,
            principal_id="manager-1",
            role=PrincipalRole.MANAGER,
            reason="refund evidence accepted",
        )
        with pytest.raises(ApprovalDecisionConflict, match="no longer pending"):
            await approval_service.decide(
                approval_id=approval_id,
                approve=True,
                principal_id="manager-2",
                role=PrincipalRole.MANAGER,
                reason="duplicate decision",
            )
        assert approval_id in {item.id for item in await approval_service.list()}
        result = await runner.resume(
            run_id,
            {"approval_id": approval_id, "status": decided.status.value},
        )
        assert result["final_response"] == "The approved refund was completed."

    async with sessions.begin() as session:
        run = await session.get(AgentRun, run_id)
        persisted_order = await session.get(Order, order_id)
        approval = await session.get(Approval, approval_id)
        refund = await session.scalar(select(Refund).where(Refund.order_id == order_id))
        assert run is not None and run.status is AgentRunStatus.SUCCEEDED
        assert persisted_order is not None and persisted_order.status is OrderStatus.REFUNDED
        assert approval is not None and approval.status is ApprovalStatus.APPROVED
        assert refund is not None and refund.amount == Decimal("88.00")

        assert (
            await RunStateGuard(sessions, max_recovery_attempts=3).acquire(
                run_id, RunTrigger.RECOVERY, checkpoint_exists=True
            )
            is RunAction.NOOP
        )

        await session.execute(delete(AuditLog).where(AuditLog.run_id == run_id))
        await session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.key.like(f"{run_id}:%"))
        )
        await session.execute(delete(ToolCall).where(ToolCall.agent_run_id == run_id))
        await session.execute(delete(Approval).where(Approval.id == approval_id))
        await session.execute(delete(Refund).where(Refund.order_id == order_id))
        await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await session.execute(delete(TicketMessage).where(TicketMessage.ticket_id == ticket_id))
        await session.execute(delete(Ticket).where(Ticket.id == ticket_id))
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
    await engine.dispose()


def test_refund_waits_for_manager_and_resumes_from_redis_checkpoint() -> None:
    asyncio.run(run_approved_refund_scenario())
