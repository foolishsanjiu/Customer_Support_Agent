from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ObjectAccessDenied, ResourceNotFound
from app.models import Ticket, TicketMessage
from app.models.enums import SenderType, TicketCategory, TicketStatus
from app.repositories.commerce import CommerceRepository
from app.repositories.tickets import TicketRepository


class TicketService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = TicketRepository(session)
        self.commerce_repository = CommerceRepository(session)

    async def create_ticket(
        self,
        *,
        customer_id: int,
        order_id: int | None,
        category: TicketCategory,
        subject: str,
    ) -> Ticket:
        async with self.session.begin():
            customer = await self.commerce_repository.get_customer(customer_id)
            if customer is None:
                raise ResourceNotFound("customer not found")
            if order_id is not None:
                order = await self.commerce_repository.get_order(order_id)
                if order is None:
                    raise ResourceNotFound("order not found")
                if order.customer_id != customer_id:
                    raise ObjectAccessDenied("order does not belong to customer")

            ticket = Ticket(
                customer_id=customer_id,
                order_id=order_id,
                status=TicketStatus.OPEN,
                category=category,
                subject=subject.strip(),
            )
            self.repository.add_ticket(ticket)
            await self.session.flush()
            await self.session.refresh(ticket)
        return ticket

    async def get_ticket(self, ticket_id: int) -> tuple[Ticket, list[TicketMessage]]:
        ticket = await self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket not found")
        return ticket, await self.repository.list_messages(ticket_id)

    async def add_message(
        self, ticket_id: int, sender_type: SenderType, content: str
    ) -> TicketMessage:
        async with self.session.begin():
            ticket = await self.repository.get_ticket(ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket not found")
            message = TicketMessage(
                ticket_id=ticket_id,
                sender_type=sender_type,
                content=content.strip(),
            )
            self.repository.add_message(message)
            await self.session.flush()
            await self.session.refresh(message)
        return message
