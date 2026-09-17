from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket, TicketMessage


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_ticket(self, ticket_id: int) -> Ticket | None:
        return await self.session.get(Ticket, ticket_id)

    async def list_customer_tickets(self, customer_id: int, limit: int) -> list[Ticket]:
        result = await self.session.scalars(
            select(Ticket)
            .where(Ticket.customer_id == customer_id)
            .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
            .limit(limit)
        )
        return list(result)

    async def list_messages(self, ticket_id: int) -> list[TicketMessage]:
        result = await self.session.scalars(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.id)
        )
        return list(result)

    def add_ticket(self, ticket: Ticket) -> None:
        self.session.add(ticket)

    def add_message(self, message: TicketMessage) -> None:
        self.session.add(message)
