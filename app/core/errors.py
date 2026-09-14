class BusinessError(Exception):
    status_code = 400
    code = "business_error"


class ResourceNotFound(BusinessError):
    status_code = 404
    code = "not_found"


class ObjectAccessDenied(BusinessError):
    status_code = 403
    code = "object_access_denied"


class BusinessConflict(BusinessError):
    status_code = 409
    code = "business_conflict"


class ToolRuntimeError(Exception):
    code = "tool_runtime_error"


class UnknownToolError(ToolRuntimeError):
    code = "unknown_tool"


class InvalidToolArguments(ToolRuntimeError):
    code = "invalid_tool_arguments"


class PrincipalAuthenticationError(ToolRuntimeError):
    code = "principal_authentication_failed"


class ToolPermissionDenied(ToolRuntimeError):
    code = "permission_denied"


class ToolPolicyDenied(ToolRuntimeError):
    code = "policy_denied"


class DuplicateToolCallConflict(ToolRuntimeError):
    code = "duplicate_idempotency_conflict"


class ToolExecutionTimeout(ToolRuntimeError):
    code = "tool_timeout"


class ToolVerificationFailed(ToolRuntimeError):
    code = "tool_verification_failed"


class ToolUnavailable(ToolRuntimeError):
    code = "tool_unavailable"


class MCPToolError(ToolRuntimeError):
    code = "mcp_tool_error"
