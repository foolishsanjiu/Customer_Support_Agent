from app.core.errors import (
    PrincipalAuthenticationError,
    ToolPermissionDenied,
    ToolPolicyDenied,
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


def enforce_policy(definition: ToolDefinition) -> None:
    if definition.risk_level is ToolRiskLevel.L3:
        raise ToolPolicyDenied("L3 tool execution requires the M5 approval guard")
