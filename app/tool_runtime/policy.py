from typing import Any, Protocol

from pydantic import BaseModel

from app.core.errors import (
    ApprovalRequired,
    PrincipalAuthenticationError,
    ToolPermissionDenied,
)
from app.models.enums import PrincipalRole, ToolRiskLevel
from app.tool_runtime.models import ToolDefinition, ToolExecutionContext

ROLE_PERMISSIONS: dict[PrincipalRole, frozenset[str]] = {
    PrincipalRole.CUSTOMER: frozenset(
        {
            "customer:read",
            "order:read",
            "shipping:read",
            "refund:read",
            "policy:read",
            "order:cancel",
            "refund:request",
            "ticket:update",
            "ticket:escalate",
            "approval:request",
        }
    ),
    PrincipalRole.SUPPORT_AGENT: frozenset(
        {
            "customer:read",
            "order:read",
            "shipping:read",
            "refund:read",
            "policy:read",
            "order:cancel",
            "refund:request",
            "ticket:update",
            "ticket:escalate",
            "approval:request",
        }
    ),
    PrincipalRole.MANAGER: frozenset(
        {
            "customer:read",
            "order:read",
            "shipping:read",
            "refund:read",
            "policy:read",
            "order:cancel",
            "refund:request",
            "refund:approve",
            "ticket:update",
            "ticket:escalate",
            "approval:request",
        }
    ),
    PrincipalRole.ADMIN: frozenset(
        {
            "customer:read",
            "order:read",
            "shipping:read",
            "refund:read",
            "policy:read",
            "order:cancel",
            "refund:request",
            "refund:approve",
            "ticket:update",
            "ticket:escalate",
            "approval:request",
        }
    ),
}


def permissions_for_role(role: PrincipalRole) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def authenticate(context: ToolExecutionContext) -> None:
    if not context.principal_id.strip() or context.customer_id <= 0:
        raise PrincipalAuthenticationError("authenticated principal is required")


def authorize_permission(definition: ToolDefinition, context: ToolExecutionContext) -> None:
    if definition.required_permission not in permissions_for_role(context.role):
        raise ToolPermissionDenied(f"{context.role.value} lacks {definition.required_permission}")


class ApprovalValidator(Protocol):
    async def validate_approval(
        self,
        *,
        approval_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> None: ...


async def enforce_policy(
    definition: ToolDefinition,
    arguments: BaseModel,
    context: ToolExecutionContext,
    tool_call_id: str,
    validator: ApprovalValidator,
) -> None:
    if definition.risk_level is not ToolRiskLevel.L3:
        return
    if context.approval_id is None:
        raise ApprovalRequired("L3 tool execution requires an approved action")
    await validator.validate_approval(
        approval_id=context.approval_id,
        tool_name=definition.name,
        arguments=arguments.model_dump(mode="json"),
        context=context,
        tool_call_id=tool_call_id,
    )
