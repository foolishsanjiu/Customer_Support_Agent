from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
