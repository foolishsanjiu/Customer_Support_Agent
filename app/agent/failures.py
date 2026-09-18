from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import (
    ExternalServiceUnavailable,
    InvalidToolArguments,
    MCPToolError,
    ObjectAccessDenied,
    PrincipalAuthenticationError,
    ToolExecutionTimeout,
    ToolPermissionDenied,
    ToolPolicyDenied,
    ToolUnavailable,
)


class FailureStage(StrEnum):
    PLAN = "plan"
    POLICY = "policy"
    EXECUTION = "execution"
    VERIFICATION = "verification"


class FailureCategory(StrEnum):
    INVALID_PLAN = "invalid_plan"
    POLICY_DENIED = "policy_denied"
    AUTHORIZATION_DENIED = "authorization_denied"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    EXECUTION_FAILED = "execution_failed"
    VERIFICATION_MISMATCH = "verification_mismatch"


class RepairAction(StrEnum):
    NONE = "none"
    REPLAN = "replan"
    STOP = "stop"


class FailureAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: FailureStage
    category: FailureCategory
    reason: str
    tool_name: str | None = None
    error_code: str | None = None
    error_type: str | None = None
    repairable: bool = False
    repair_action: RepairAction = RepairAction.NONE
    attempt: int = Field(default=0, ge=0)


_DEPENDENCY_ERRORS = (
    ExternalServiceUnavailable,
    MCPToolError,
    ToolExecutionTimeout,
    ToolUnavailable,
)
_AUTHORIZATION_ERRORS = (
    ObjectAccessDenied,
    PrincipalAuthenticationError,
    ToolPermissionDenied,
)


def attribute_execution_failure(
    error: Exception,
    *,
    tool_name: str,
    read_only: bool,
) -> FailureAttribution:
    if isinstance(error, _DEPENDENCY_ERRORS):
        category = FailureCategory.DEPENDENCY_UNAVAILABLE
        repairable = read_only
    elif isinstance(error, InvalidToolArguments):
        category = FailureCategory.INVALID_TOOL_ARGUMENTS
        repairable = False
    elif isinstance(error, _AUTHORIZATION_ERRORS):
        category = FailureCategory.AUTHORIZATION_DENIED
        repairable = False
    elif isinstance(error, ToolPolicyDenied):
        category = FailureCategory.POLICY_DENIED
        repairable = False
    else:
        category = FailureCategory.EXECUTION_FAILED
        repairable = False
    return FailureAttribution(
        stage=FailureStage.EXECUTION,
        category=category,
        reason=str(error) or type(error).__name__,
        tool_name=tool_name,
        error_code=getattr(error, "code", type(error).__name__),
        error_type=type(error).__name__,
        repairable=repairable,
    )


def attribute_verification_failure(
    *,
    tool_name: str,
    read_only: bool,
    error: Exception | None = None,
) -> FailureAttribution:
    if error is not None:
        attributed = attribute_execution_failure(
            error,
            tool_name=tool_name,
            read_only=read_only,
        )
        return attributed.model_copy(update={"stage": FailureStage.VERIFICATION})
    return FailureAttribution(
        stage=FailureStage.VERIFICATION,
        category=FailureCategory.VERIFICATION_MISMATCH,
        reason="business outcome verification failed",
        tool_name=tool_name,
        error_code="tool_verification_failed",
        repairable=read_only,
    )


def decide_repair(
    failure: FailureAttribution,
    *,
    attempts: int,
    max_attempts: int,
) -> FailureAttribution:
    action = (
        RepairAction.REPLAN
        if failure.repairable and attempts < max_attempts
        else RepairAction.STOP
    )
    return failure.model_copy(update={"repair_action": action, "attempt": attempts + 1})
