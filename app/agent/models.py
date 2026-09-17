from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class IntentType(StrEnum):
    ORDER_QUERY = "ORDER_QUERY"
    ORDER_LIST = "ORDER_LIST"
    REFUND_STATUS = "REFUND_STATUS"
    SHIPPING_QUERY = "SHIPPING_QUERY"
    CANCEL_ORDER = "CANCEL_ORDER"
    REFUND = "REFUND"
    POLICY_QUESTION = "POLICY_QUESTION"
    SOCIAL = "SOCIAL"
    OTHER = "OTHER"


class TicketIntent(BaseModel):
    intent: IntentType
    order_id: int | None = Field(default=None, gt=0)
    reason: str | None = None
    confidence: float = Field(ge=0, le=1)


class PlanAction(StrEnum):
    ANSWER_DIRECTLY = "answer_directly"
    TOOL_CALL = "tool_call"


class ToolDecision(BaseModel):
    action: PlanAction
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def tool_call_requires_name(self) -> "ToolDecision":
        if self.action is PlanAction.TOOL_CALL and not self.tool_name:
            raise ValueError("tool_name is required for tool_call")
        return self


class ChatMessage(BaseModel):
    role: str
    content: str
