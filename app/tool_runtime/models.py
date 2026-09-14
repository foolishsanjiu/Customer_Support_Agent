from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PrincipalRole, TicketStatus, ToolRiskLevel

ToolHandler = Callable[[BaseModel, "ToolExecutionContext"], Awaitable[dict[str, Any]]]
ToolAuthorizer = Callable[[BaseModel, "ToolExecutionContext"], Awaitable[None]]
ToolVerifier = Callable[[BaseModel, dict[str, Any], "ToolExecutionContext"], Awaitable[bool]]


class StrictToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(StrictToolInput):
    pass


class OrderInput(StrictToolInput):
    order_id: int = Field(gt=0)


class RefundLookupInput(StrictToolInput):
    refund_id: int = Field(gt=0)


class RefundOrderInput(OrderInput):
    reason: str = Field(min_length=1, max_length=2000)


class PolicySearchInput(StrictToolInput):
    query: str = Field(min_length=1, max_length=1000)


class TicketUpdateInput(StrictToolInput):
    status: TicketStatus


class EscalateTicketInput(StrictToolInput):
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalRequestInput(StrictToolInput):
    tool_name: str
    tool_call_id: str


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True)
class ToolExecutionContext:
    principal_id: str
    customer_id: int
    role: PrincipalRole
    ticket_id: int
    agent_run_id: int
    trace_id: str
    approval_id: int | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    handler: ToolHandler
    risk_level: ToolRiskLevel
    required_permission: str
    timeout_seconds: float
    retry_policy: RetryPolicy
    idempotent: bool
    read_only: bool
    intents: frozenset[str] = frozenset()
    authorizer: ToolAuthorizer | None = None
    verifier: ToolVerifier | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.read_only and not self.idempotent:
            raise ValueError("write tools must be idempotent")
