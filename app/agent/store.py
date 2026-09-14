from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interfaces import AgentStore
from app.agent.models import ChatMessage, IntentType
from app.core.errors import ObjectAccessDenied, ResourceNotFound
from app.models import AgentRun, Ticket, TicketMessage
from app.models.enums import AgentRunStatus, SenderType
from app.repositories.tickets import TicketRepository


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DatabaseAgentStore(AgentStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_run(self, ticket_id: int) -> AgentRun:
        async with self.session_factory.begin() as session:
            if await session.get(Ticket, ticket_id) is None:
                raise ResourceNotFound("ticket not found")
            run = AgentRun(
                ticket_id=ticket_id,
                status=AgentRunStatus.RUNNING,
                current_node="START",
                started_at=utc_now_naive(),
                recovery_attempts=0,
            )
            session.add(run)
            await session.flush()
            await session.refresh(run)
        return run

    async def load_ticket(self, ticket_id: int, customer_id: int) -> list[ChatMessage]:
        async with self.session_factory() as session:
            repository = TicketRepository(session)
            ticket = await repository.get_ticket(ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            if ticket.customer_id != customer_id:
                raise ObjectAccessDenied("ticket does not belong to customer")
            stored_messages = await repository.list_messages(ticket_id)
            messages = [ChatMessage(role="user", content=ticket.subject)]
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
    ) -> None:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id)
            run.status = AgentRunStatus.SUCCEEDED if success else AgentRunStatus.FAILED
            run.current_node = "persist"
            run.intent = intent.value if intent is not None else None
            run.completed_at = utc_now_naive()
            run.success = success
            run.error_code = None if success else "agent_execution_failed"
            run.error_message = error_message
            session.add(
                TicketMessage(
                    ticket_id=ticket_id,
                    sender_type=SenderType.AGENT,
                    content=response,
                )
            )

    async def fail_run(self, run_id: int, error: Exception) -> None:
        async with self.session_factory.begin() as session:
            run = await self._get_run(session, run_id)
            run.status = AgentRunStatus.FAILED
            run.completed_at = utc_now_naive()
            run.success = False
            run.error_code = type(error).__name__
            run.error_message = str(error)[:2000]

    @staticmethod
    async def _get_run(session: AsyncSession, run_id: int) -> AgentRun:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise ResourceNotFound("agent run not found")
        return run

    @staticmethod
    def _message_role(sender_type: SenderType) -> str:
        return {
            SenderType.CUSTOMER: "user",
            SenderType.AGENT: "assistant",
            SenderType.SYSTEM: "system",
        }[sender_type]
