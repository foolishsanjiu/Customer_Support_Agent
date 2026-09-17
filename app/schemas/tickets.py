from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import SenderType, TicketCategory, TicketStatus


class TicketCreateRequest(BaseModel):
    customer_id: int = Field(gt=0)
    order_id: int | None = Field(default=None, gt=0)
    category: TicketCategory
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class CustomerTicketCreateRequest(BaseModel):
    order_id: int | None = Field(default=None, gt=0)
    category: TicketCategory
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class TicketMessageCreateRequest(BaseModel):
    sender_type: SenderType
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)
    ]


class CustomerMessageCreateRequest(BaseModel):
    content: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=10000),
    ]


class TicketMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    sender_type: SenderType
    content: str
    created_at: datetime


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    order_id: int | None
    status: TicketStatus
    category: TicketCategory
    subject: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class TicketDetailResponse(TicketResponse):
    messages: list[TicketMessageResponse]
