from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator


class TrackingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracking_number: str
    carrier: str
    status: Literal["PENDING", "IN_TRANSIT", "DELAYED", "DELIVERED", "LOST"]
    current_location: str | None
    last_event: str | None
    updated_at: datetime


class DeliveryEstimateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracking_number: str
    estimated_delivery_at: datetime | None
    confidence: Literal["LOW", "MEDIUM", "HIGH"]


class FulfillmentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_reference: str
    status: Literal["READY_TO_PICK", "PICKING", "PACKED", "HANDED_OVER"]
    cancellable: bool
    updated_at: datetime

    @model_validator(mode="after")
    def validate_cancellable_state(self) -> Self:
        if self.cancellable != (self.status == "READY_TO_PICK"):
            raise ValueError("cancellable flag conflicts with fulfillment status")
        return self
