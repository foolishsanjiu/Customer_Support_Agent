from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.errors import ObjectAccessDenied
from app.models.enums import PrincipalRole
from app.schemas.tickets import (
    CustomerMessageCreateRequest,
    CustomerTicketCreateRequest,
    TicketDetailResponse,
    TicketMessageResponse,
    TicketResponse,
)
from app.security.principal import AuthenticatedPrincipal, get_principal
from app.services.tickets import TicketService

router = APIRouter(prefix="/api/v1/chat", tags=["customer-chat"])
page_router = APIRouter(include_in_schema=False)
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]
Session = Annotated[AsyncSession, Depends(get_session)]
CHAT_ROOT = Path(__file__).resolve().parents[1] / "chat"
CHAT_ASSET_DIR = CHAT_ROOT / "static"


@page_router.get("/chat", response_class=FileResponse)
async def customer_chat() -> FileResponse:
    return FileResponse(
        CHAT_ROOT / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    principal: Principal,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> object:
    customer_id = _require_customer(principal)
    return await TicketService(session).list_customer_tickets(customer_id, limit)


@router.post("/tickets", response_model=TicketResponse, status_code=201)
async def create_ticket(
    payload: CustomerTicketCreateRequest,
    principal: Principal,
    session: Session,
) -> object:
    customer_id = _require_customer(principal)
    return await TicketService(session).create_ticket(
        customer_id=customer_id,
        order_id=payload.order_id,
        category=payload.category,
        subject=payload.subject,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(ticket_id: int, principal: Principal, session: Session) -> object:
    customer_id = _require_customer(principal)
    ticket, messages = await TicketService(session).get_customer_ticket(ticket_id, customer_id)
    return TicketDetailResponse(
        **TicketResponse.model_validate(ticket).model_dump(), messages=messages
    )


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=TicketMessageResponse,
    status_code=201,
)
async def add_message(
    ticket_id: int,
    payload: CustomerMessageCreateRequest,
    principal: Principal,
    session: Session,
) -> object:
    customer_id = _require_customer(principal)
    return await TicketService(session).add_customer_message(
        ticket_id,
        customer_id,
        payload.content,
    )


def _require_customer(principal: AuthenticatedPrincipal) -> int:
    if principal.role is not PrincipalRole.CUSTOMER or principal.customer_id is None:
        raise ObjectAccessDenied("customer identity is required")
    return principal.customer_id
