from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.schemas.commerce import RefundResponse
from app.services.commerce import CommerceService

router = APIRouter(prefix="/api/v1/refunds", tags=["refunds"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{refund_id}", response_model=RefundResponse)
async def get_refund(refund_id: int, session: Session) -> object:
    return await CommerceService(session).get_refund(refund_id)
