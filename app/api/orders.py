from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.schemas.commerce import (
    CustomerActionRequest,
    OrderResponse,
    RefundCreateRequest,
    RefundResponse,
    ShipmentResponse,
)
from app.services.commerce import CommerceService

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: int, session: Session) -> object:
    return await CommerceService(session).get_order(order_id)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(order_id: int, request: CustomerActionRequest, session: Session) -> object:
    return await CommerceService(session).cancel_order(order_id, request.customer_id)


@router.get("/{order_id}/shipment", response_model=ShipmentResponse)
async def get_shipment(order_id: int, session: Session) -> object:
    return await CommerceService(session).get_shipment(order_id)


@router.post("/{order_id}/refund", response_model=RefundResponse, status_code=201)
async def refund_order(order_id: int, request: RefundCreateRequest, session: Session) -> object:
    return await CommerceService(session).refund_order(
        order_id, request.customer_id, request.reason
    )
