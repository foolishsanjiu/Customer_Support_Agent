from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.schemas.commerce import CustomerResponse, OrderResponse
from app.services.commerce import CommerceService

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: int, session: Session) -> object:
    return await CommerceService(session).get_customer(customer_id)


@router.get("/{customer_id}/orders", response_model=list[OrderResponse])
async def list_orders(customer_id: int, session: Session) -> object:
    return await CommerceService(session).list_customer_orders(customer_id)
