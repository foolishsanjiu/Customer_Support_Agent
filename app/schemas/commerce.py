from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import (
    CustomerLevel,
    CustomerStatus,
    OrderStatus,
    RefundStatus,
    ShipmentStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CustomerResponse(ORMModel):
    id: int
    name: str
    email: str
    level: CustomerLevel
    status: CustomerStatus
    created_at: datetime
    updated_at: datetime


class OrderResponse(ORMModel):
    id: int
    customer_id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime
    paid_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    updated_at: datetime


class ShipmentResponse(ORMModel):
    id: int
    order_id: int
    tracking_number: str
    carrier: str
    status: ShipmentStatus
    current_location: str | None
    last_event: str | None
    last_updated_at: datetime | None
    estimated_delivery_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RefundResponse(ORMModel):
    id: int
    order_id: int
    amount: Decimal
    reason: str
    status: RefundStatus
    created_at: datetime
    processed_at: datetime | None


class CustomerActionRequest(BaseModel):
    customer_id: int = Field(gt=0)


class RefundCreateRequest(CustomerActionRequest):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
