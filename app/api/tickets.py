from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.schemas.tickets import (
    TicketCreateRequest,
    TicketDetailResponse,
    TicketMessageCreateRequest,
    TicketMessageResponse,
    TicketResponse,
)
from app.services.tickets import TicketService

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=TicketResponse, status_code=201)
async def create_ticket(request: TicketCreateRequest, session: Session) -> object:
    return await TicketService(session).create_ticket(
        customer_id=request.customer_id,
        order_id=request.order_id,
        category=request.category,
        subject=request.subject,
    )


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(ticket_id: int, session: Session) -> object:
    ticket, messages = await TicketService(session).get_ticket(ticket_id)
    return TicketDetailResponse(
        **TicketResponse.model_validate(ticket).model_dump(), messages=messages
    )


@router.post("/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=201)
async def add_message(
    ticket_id: int, request: TicketMessageCreateRequest, session: Session
) -> object:
    return await TicketService(session).add_message(ticket_id, request.sender_type, request.content)
