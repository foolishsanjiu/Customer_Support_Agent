from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interfaces import AgentStore
from app.agent.models import ChatMessage, IntentType
from app.core.errors import ObjectAccessDenied, ResourceNotFound
from app.models import AgentRun, Approval, AuditLog, Ticket, TicketMessage
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    PrincipalRole,
    SenderType,
    TicketStatus,
)
from app.observability.tracing import current_trace_id
from app.repositories.tickets import TicketRepository

OPERATOR_ROLES = {
    PrincipalRole.SUPPORT_AGENT,
    PrincipalRole.MANAGER,
    PrincipalRole.ADMIN,
}
TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}
PASSIVE_RUN_STATUSES = {
    AgentRunStatus.PENDING,
    AgentRunStatus.WAITING_APPROVAL,
    AgentRunStatus.RESUME_PENDING,
    AgentRunStatus.RECOVERY_REQUIRED,
}
ACTIVE_RUN_STATUSES = PASSIVE_RUN_STATUSES | {
    AgentRunStatus.RUNNING,
    AgentRunStatus.CANCEL_REQUESTED,
}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DatabaseAgentStore(AgentStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_run(
        self,
        ticket_id: int,
        customer_id: int | None = None,
        trigger_message_id: int | None = None,
    ) -> AgentRun:
        async with self.session_factory.begin() as session:
            ticket = await session.get(Ticket, ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if customer_id is not None and ticket.customer_id != customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")
            if trigger_message_id is None:
                trigger = await session.scalar(
                    select(TicketMessage)
                    .where(
                        TicketMessage.ticket_id == ticket_id,
                        TicketMessage.sender_type == SenderType.CUSTOMER,
                    )
                    .order_by(TicketMessage.id.desc())
                    .limit(1)
                )
            else:
                trigger = await session.get(TicketMessage, trigger_message_id)
            if trigger is None and trigger_message_id is None:
                trigger = TicketMessage(
                    ticket_id=ticket_id,
                    sender_type=SenderType.CUSTOMER,
                    content=ticket.subject,
                )
                session.add(trigger)
                await session.flush()
            if (
                trigger is None
                or trigger.ticket_id != ticket_id
                or trigger.sender_type is not SenderType.CUSTOMER
            ):
                raise ObjectAccessDenied("trigger message does not belong to customer conversation")
            existing = await session.scalar(
                select(AgentRun).where(AgentRun.trigger_message_id == trigger.id)
            )
            if existing is not None:
                return existing
            previous = await session.scalar(
                select(AgentRun)
                .where(AgentRun.ticket_id == ticket_id)
                .order_by(AgentRun.id.desc())
                .limit(1)
            )
            run = AgentRun(
                ticket_id=ticket_id,
                trigger_message_id=trigger.id,
                continuation_run_id=(
                    previous.id
                    if previous is not None
                    and previous.status in TERMINAL_RUN_STATUSES
                    and previous.awaiting_customer_input
                    else None
                ),
                status=AgentRunStatus.PENDING,
                current_node="START",
                recovery_attempts=0,
            )
            session.add(run)
            await session.flush()
            await session.refresh(run)
        return run

    async def get_run_inputs(self, run_id: int) -> tuple[int | None, str | None, list[str]]:
        async with self.session_factory() as session:
            run = await self._get_run(session, run_id)
            previous = (
                await session.get(AgentRun, run.continuation_run_id)
                if run.continuation_run_id is not None
                else None
            )
            return (
                run.trigger_message_id,
                previous.intent if previous is not None else None,
                list(previous.missing_fields or []) if previous is not None else [],
            )

    async def list_active_runs(self, ticket_id: int, customer_id: int) -> list[AgentRun]:
        async with self.session_factory() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.customer_id != customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")
            result = await session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.ticket_id == ticket_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(AgentRun.id)
            )
            return list(result)

    async def start_run(self, run_id: int) -> bool:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id, lock=True)
            if run.status in {AgentRunStatus.CANCEL_REQUESTED, AgentRunStatus.CANCELLED}:
                return False
            run.status = AgentRunStatus.RUNNING
            run.started_at = run.started_at or utc_now_naive()
            return True

    async def request_cancellation(
        self,
        run_id: int,
        *,
        principal_id: str,
        role: PrincipalRole,
        customer_id: int | None,
        reason: str,
    ) -> AgentRun:
        async with self.session_factory.begin() as session:
            approvals = list(
                await session.scalars(
                    select(Approval)
                    .where(
                        Approval.run_id == run_id,
                        Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
                    )
                    .with_for_update()
                )
            )
            run = await self._get_run(session, run_id, lock=True)
            ticket = await session.get(Ticket, run.ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if role is PrincipalRole.CUSTOMER:
                if customer_id is None or ticket.customer_id != customer_id:
                    raise ObjectAccessDenied("agent run does not belong to customer")
            elif role not in OPERATOR_ROLES:
                raise ObjectAccessDenied("run cancellation role is required")
            if run.status in TERMINAL_RUN_STATUSES:
                return run
            previous_status = run.status
            run.status = (
                AgentRunStatus.CANCELLED
                if previous_status in PASSIVE_RUN_STATUSES
                else AgentRunStatus.CANCEL_REQUESTED
            )
            if run.status is AgentRunStatus.CANCELLED:
                self._mark_cancelled(run, ticket)
            for approval in approvals:
                approval.status = ApprovalStatus.CANCELLED
            session.add(
                AuditLog(
                    event_type="agent_run_cancellation_requested",
                    run_id=run.id,
                    ticket_id=run.ticket_id,
                    actor_id=principal_id,
                    actor_role=role,
                    trace_id=current_trace_id(),
                    details={
                        "previous_status": previous_status.value,
                        "result_status": run.status.value,
                        "reason": reason.strip(),
                    },
                )
            )
            await session.flush()
            await session.refresh(run)
            return run

    async def cancellation_requested(self, run_id: int) -> bool:
        async with self.session_factory() as session:
            run = await self._get_run(session, run_id)
            return run.status in {
                AgentRunStatus.CANCEL_REQUESTED,
                AgentRunStatus.CANCELLED,
            }

    async def finalize_cancellation(self, run_id: int) -> bool:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id, lock=True)
            if run.status is AgentRunStatus.CANCELLED:
                return False
            if run.status is not AgentRunStatus.CANCEL_REQUESTED:
                return False
            ticket = await session.get(Ticket, run.ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            self._mark_cancelled(run, ticket)
            session.add(
                AuditLog(
                    event_type="agent_run_cancelled",
                    run_id=run.id,
                    ticket_id=run.ticket_id,
                    trace_id=current_trace_id(),
                    details={},
                )
            )
            return True

    async def get_run_context(self, run_id: int) -> tuple[int, int]:
        async with self.session_factory() as session:
            run = await self._get_run(session, run_id)
            ticket = await session.get(Ticket, run.ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            return ticket.id, ticket.customer_id

    async def get_run(self, run_id: int, customer_id: int) -> AgentRun:
        async with self.session_factory() as session:
            run = await self._get_run(session, run_id)
            ticket = await session.get(Ticket, run.ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.customer_id != customer_id:
                raise ObjectAccessDenied("agent run does not belong to customer")
            return run

    async def load_ticket(
        self,
        ticket_id: int,
        customer_id: int,
        through_message_id: int | None = None,
    ) -> list[ChatMessage]:
        async with self.session_factory() as session:
            repository = TicketRepository(session)
            ticket = await repository.get_ticket(ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.customer_id != customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")
            stored_messages = await repository.list_messages(ticket_id)
            if through_message_id is not None:
                stored_messages = [
                    message for message in stored_messages if message.id <= through_message_id
                ]
            messages: list[ChatMessage] = []
            messages.extend(
                ChatMessage(role=self._message_role(message.sender_type), content=message.content)
                for message in stored_messages
            )
            return messages

    async def set_current_node(
        self, run_id: int, node: str, intent: IntentType | None = None
    ) -> None:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id)
            run.current_node = node
            if intent is not None:
                run.intent = intent.value

    async def complete_run(
        self,
        *,
        run_id: int,
        ticket_id: int,
        response: str,
        intent: IntentType | None,
        success: bool,
        error_message: str | None,
        missing_fields: list[str] | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id, lock=True)
            if run.status is AgentRunStatus.CANCELLED:
                return
            run.status = AgentRunStatus.SUCCEEDED if success else AgentRunStatus.FAILED
            run.current_node = "persist"
            run.intent = intent.value if intent is not None else None
            run.completed_at = utc_now_naive()
            run.success = success
            run.error_code = None if success else "agent_execution_failed"
            run.error_message = error_message
            run.missing_fields = missing_fields or None
            run.awaiting_customer_input = bool(missing_fields) and success
            ticket = await session.get(Ticket, ticket_id, with_for_update=True)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.status is not TicketStatus.ESCALATED:
                ticket.status = TicketStatus.RESOLVED if success else TicketStatus.FAILED
            session.add(
                TicketMessage(
                    ticket_id=ticket_id,
                    sender_type=SenderType.AGENT,
                    content=response,
                )
            )

    async def fail_run(self, run_id: int, error: Exception) -> bool:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id, lock=True)
            if run.status is AgentRunStatus.CANCELLED:
                return False
            if run.status is AgentRunStatus.CANCEL_REQUESTED:
                ticket = await session.get(Ticket, run.ticket_id, with_for_update=True)
                if ticket is None:
                    raise ResourceNotFound("ticket not found")
                self._mark_cancelled(run, ticket)
                session.add(
                    AuditLog(
                        event_type="agent_run_cancelled",
                        run_id=run.id,
                        ticket_id=run.ticket_id,
                        trace_id=current_trace_id(),
                        details={"during_failure": True},
                    )
                )
                return False
            run.status = AgentRunStatus.FAILED
            run.completed_at = utc_now_naive()
            run.success = False
            run.error_code = type(error).__name__
            run.error_message = str(error)[:2000]
            return True

    @staticmethod
    async def _get_run(
        session: AsyncSession,
        run_id: int,
        *,
        lock: bool = False,
    ) -> AgentRun:
        run = (
            await session.get(AgentRun, run_id, with_for_update=True)
            if lock
            else await session.get(AgentRun, run_id)
        )
        if run is None:
            raise ResourceNotFound("agent run not found")
        return run

    @staticmethod
    def _mark_cancelled(run: AgentRun, ticket: Ticket) -> None:
        run.status = AgentRunStatus.CANCELLED
        run.current_node = "cancelled"
        run.completed_at = utc_now_naive()
        run.success = False
        run.error_code = "agent_run_cancelled"
        run.error_message = None
        if ticket.status is TicketStatus.WAITING_APPROVAL:
            ticket.status = TicketStatus.OPEN

    @staticmethod
    def _message_role(sender_type: SenderType) -> str:
        return {
            SenderType.CUSTOMER: "user",
            SenderType.AGENT: "assistant",
            SenderType.SYSTEM: "system",
        }[sender_type]
