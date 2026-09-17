from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import ApprovalStatus, PrincipalRole, ToolRiskLevel


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    ticket_id: int
    tool_call_id: str
    tool_name: str
    requested_by: str
    risk_level: ToolRiskLevel
    arguments_snapshot: dict[str, Any]
    reason: str
    status: ApprovalStatus
    required_role: PrincipalRole
    created_at: datetime
    expires_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    decision_reason: str | None


class ApprovalDecisionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class AgentRunCreateRequest(BaseModel):
    ticket_id: int = Field(gt=0)
    trigger_message_id: int | None = Field(default=None, gt=0)


class AgentRunCancelRequest(BaseModel):
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]


class AgentRunResponse(BaseModel):
    run_id: int
    status: str
