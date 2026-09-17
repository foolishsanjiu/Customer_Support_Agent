import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.agent.store import DatabaseAgentStore
from app.core.errors import ObjectAccessDenied
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import AgentRun, Approval, AuditLog, Customer, Ticket, TicketMessage
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    CustomerLevel,
    CustomerStatus,
    PrincipalRole,
    TicketCategory,
    TicketStatus,
    ToolRiskLevel,
)

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


@pytest.mark.asyncio
async def test_customer_and_operator_cancellation_state_machine() -> None:
    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    marker = uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    async with sessions.begin() as session:
        owner = Customer(
            name="Cancellation Owner",
            email=f"cancel-owner-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        other = Customer(
            name="Cancellation Other",
            email=f"cancel-other-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add_all([owner, other])
        await session.flush()
        active_ticket = Ticket(
            customer_id=owner.id,
            status=TicketStatus.OPEN,
            category=TicketCategory.OTHER,
            subject="Active cancellable run",
        )
        waiting_ticket = Ticket(
            customer_id=owner.id,
            status=TicketStatus.WAITING_APPROVAL,
            category=TicketCategory.REFUND,
            subject="Waiting cancellable run",
        )
        session.add_all([active_ticket, waiting_ticket])
        await session.flush()
        active = AgentRun(
            ticket_id=active_ticket.id,
            status=AgentRunStatus.RUNNING,
            current_node="plan",
            started_at=now,
        )
        waiting = AgentRun(
            ticket_id=waiting_ticket.id,
            status=AgentRunStatus.WAITING_APPROVAL,
            current_node="wait_for_approval",
            started_at=now,
        )
        session.add_all([active, waiting])
        await session.flush()
        approval = Approval(
            run_id=waiting.id,
            ticket_id=waiting_ticket.id,
            tool_call_id=f"cancel-{marker}",
            tool_name="refund_order",
            requested_by=str(owner.id),
            risk_level=ToolRiskLevel.L3,
            arguments_snapshot={"order_id": 1, "reason": "fixture"},
            action_fingerprint="0" * 64,
            reason="fixture",
            status=ApprovalStatus.PENDING,
            required_role=PrincipalRole.MANAGER,
            expires_at=now + timedelta(hours=1),
        )
        session.add(approval)
        await session.flush()
        ids = {
            "owner": owner.id,
            "other": other.id,
            "active_ticket": active_ticket.id,
            "waiting_ticket": waiting_ticket.id,
            "active": active.id,
            "waiting": waiting.id,
            "approval": approval.id,
        }

    store = DatabaseAgentStore(sessions)
    requested = await store.request_cancellation(
        ids["active"],
        principal_id=str(ids["owner"]),
        role=PrincipalRole.CUSTOMER,
        customer_id=ids["owner"],
        reason="Customer stopped the run",
    )
    assert requested.status is AgentRunStatus.CANCEL_REQUESTED
    assert await store.cancellation_requested(ids["active"]) is True
    assert await store.finalize_cancellation(ids["active"]) is True
    assert await store.finalize_cancellation(ids["active"]) is False
    assert await store.start_run(ids["active"]) is False
    await store.complete_run(
        run_id=ids["active"],
        ticket_id=ids["active_ticket"],
        response="This late completion must not be persisted.",
        intent=None,
        success=True,
        error_message=None,
    )
    terminal = await store.request_cancellation(
        ids["active"],
        principal_id=str(ids["owner"]),
        role=PrincipalRole.CUSTOMER,
        customer_id=ids["owner"],
        reason="Repeated terminal request",
    )
    assert terminal.status is AgentRunStatus.CANCELLED

    with pytest.raises(ObjectAccessDenied):
        await store.request_cancellation(
            ids["waiting"],
            principal_id=str(ids["other"]),
            role=PrincipalRole.CUSTOMER,
            customer_id=ids["other"],
            reason="Cross-customer attempt",
        )

    cancelled = await store.request_cancellation(
        ids["waiting"],
        principal_id="manager-1",
        role=PrincipalRole.MANAGER,
        customer_id=None,
        reason="Operator stopped obsolete work",
    )
    assert cancelled.status is AgentRunStatus.CANCELLED

    async with sessions() as session:
        active = await session.get(AgentRun, ids["active"])
        waiting = await session.get(AgentRun, ids["waiting"])
        waiting_ticket = await session.get(Ticket, ids["waiting_ticket"])
        approval = await session.get(Approval, ids["approval"])
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.run_id.in_([ids["active"], ids["waiting"]]))
            )
        )
        assert active is not None and active.status is AgentRunStatus.CANCELLED
        assert active.error_code == "agent_run_cancelled"
        assert waiting is not None and waiting.status is AgentRunStatus.CANCELLED
        assert waiting_ticket is not None and waiting_ticket.status is TicketStatus.OPEN
        assert approval is not None and approval.status is ApprovalStatus.CANCELLED
        assert (
            await session.scalar(
                select(TicketMessage).where(TicketMessage.ticket_id == ids["active_ticket"])
            )
            is None
        )
        assert {audit.event_type for audit in audits} == {
            "agent_run_cancellation_requested",
            "agent_run_cancelled",
        }

    async with sessions.begin() as session:
        await session.execute(
            delete(AuditLog).where(AuditLog.run_id.in_([ids["active"], ids["waiting"]]))
        )
        await session.execute(delete(Approval).where(Approval.id == ids["approval"]))
        await session.execute(
            delete(AgentRun).where(AgentRun.id.in_([ids["active"], ids["waiting"]]))
        )
        await session.execute(
            delete(Ticket).where(Ticket.id.in_([ids["active_ticket"], ids["waiting_ticket"]]))
        )
        await session.execute(delete(Customer).where(Customer.id.in_([ids["owner"], ids["other"]])))
    await engine.dispose()
