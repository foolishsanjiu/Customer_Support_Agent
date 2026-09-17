from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AgentRunStatus


class OperatorRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    status: AgentRunStatus
    intent: str | None
    current_node: str | None
    success: bool | None
    error_code: str | None
    recovery_attempts: int
    created_at: datetime
    updated_at: datetime
