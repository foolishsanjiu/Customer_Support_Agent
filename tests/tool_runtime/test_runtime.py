import asyncio
from typing import Any

import pytest

from app.core.errors import (
    BusinessConflict,
    DuplicateToolCallConflict,
    InvalidToolArguments,
    ObjectAccessDenied,
    ToolPermissionDenied,
    ToolPolicyDenied,
    UnknownToolError,
)
from app.models.enums import PrincipalRole, ToolCallStatus, ToolRiskLevel
from app.tool_runtime.models import (
    OrderInput,
    RetryPolicy,
    ToolDefinition,
    ToolExecutionContext,
)
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.runtime import ToolRuntime, redact_arguments
from app.tool_runtime.store import IdempotencyClaim, summarize_result


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[str, str, dict[str, Any] | None]] = {}
        self.failed_keys: list[str] = []

    async def start_call(self, **values: Any) -> None:
        self.calls.setdefault(values["tool_call_id"], values)

    async def finish_call(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        **values: Any,
    ) -> None:
        self.calls.setdefault(tool_call_id, {}).update(status=status, **values)

    async def claim_idempotency(
        self, key: str, tool_call_id: str, tool_name: str, request_hash: str
    ) -> IdempotencyClaim:
        existing = self.idempotency.get(key)
        if existing is None:
            self.idempotency[key] = (tool_name, request_hash, None)
            return IdempotencyClaim()
        existing_name, existing_hash, result = existing
        if existing_name != tool_name or existing_hash != request_hash or result is None:
            raise DuplicateToolCallConflict("duplicate conflict")
        return IdempotencyClaim(cached_result=result)

    async def complete_idempotency(self, key: str, result: dict[str, Any]) -> None:
        tool_name, request_hash, _ = self.idempotency[key]
        self.idempotency[key] = (tool_name, request_hash, result)

    async def fail_idempotency(self, key: str) -> None:
        self.failed_keys.append(key)


def context(role: PrincipalRole = PrincipalRole.CUSTOMER) -> ToolExecutionContext:
    return ToolExecutionContext(
        principal_id="42",
        customer_id=42,
        role=role,
        ticket_id=10,
        agent_run_id=20,
        trace_id="trace",
    )


def definition(
    handler,
    *,
    permission: str = "order:read",
    attempts: int = 1,
    timeout: float = 1,
    authorizer=None,
    read_only: bool = True,
) -> ToolDefinition:
    return ToolDefinition(
        name="get_order" if read_only else "cancel_order",
        description="test tool",
        input_schema=OrderInput,
        handler=handler,
        risk_level=ToolRiskLevel.L1 if read_only else ToolRiskLevel.L2,
        required_permission=permission,
        timeout_seconds=timeout,
        retry_policy=RetryPolicy(max_attempts=attempts),
        idempotent=not read_only,
        read_only=read_only,
        authorizer=authorizer,
    )


def runtime_for(tool: ToolDefinition) -> tuple[ToolRuntime, FakeRuntimeStore]:
    registry = ToolRegistry()
    registry.register(tool)
    store = FakeRuntimeStore()
    return ToolRuntime(registry, store), store


async def execute(runtime: ToolRuntime, tool_name: str, arguments: dict[str, Any], call_id="c1"):
    return await runtime.execute(
        tool_name=tool_name,
        arguments=arguments,
        context=context(),
        tool_call_id=call_id,
    )


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected() -> None:
    runtime = ToolRuntime(ToolRegistry(), FakeRuntimeStore())
    with pytest.raises(UnknownToolError):
        await execute(runtime, "missing", {"order_id": 1})


@pytest.mark.asyncio
async def test_invalid_schema_is_rejected_and_audited() -> None:
    async def handler(arguments, execution_context):
        return {"id": arguments.order_id}

    runtime, store = runtime_for(definition(handler))
    with pytest.raises(InvalidToolArguments):
        await execute(runtime, "get_order", {"order_id": 0, "unexpected": True})
    assert store.calls["c1"]["status"] is ToolCallStatus.FAILED
    assert store.calls["c1"]["error_code"] == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_role_permission_denied_before_handler() -> None:
    called = False

    async def handler(arguments, execution_context):
        nonlocal called
        called = True
        return {}

    runtime, _ = runtime_for(definition(handler, permission="refund:approve"))
    with pytest.raises(ToolPermissionDenied):
        await execute(runtime, "get_order", {"order_id": 1})
    assert called is False


@pytest.mark.asyncio
async def test_object_authorization_failure_prevents_handler() -> None:
    called = False

    async def handler(arguments, execution_context):
        nonlocal called
        called = True
        return {}

    async def deny(arguments, execution_context):
        raise ObjectAccessDenied("wrong customer")

    runtime, _ = runtime_for(definition(handler, authorizer=deny))
    with pytest.raises(ObjectAccessDenied):
        await execute(runtime, "get_order", {"order_id": 1})
    assert called is False


@pytest.mark.asyncio
async def test_transient_timeout_is_retried_then_succeeds() -> None:
    attempts = 0

    async def handler(arguments, execution_context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.sleep(0.03)
        return {"id": arguments.order_id}

    runtime, _ = runtime_for(definition(handler, attempts=2, timeout=0.01))
    assert await execute(runtime, "get_order", {"order_id": 7}) == {"id": 7}
    assert attempts == 2


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_cached_result_once() -> None:
    executions = 0

    async def handler(arguments, execution_context):
        nonlocal executions
        executions += 1
        return {"id": arguments.order_id, "status": "CANCELLED"}

    runtime, _ = runtime_for(definition(handler, read_only=False))
    first = await execute(runtime, "cancel_order", {"order_id": 7})
    second = await execute(runtime, "cancel_order", {"order_id": 7})
    assert first == second
    assert executions == 1


@pytest.mark.asyncio
async def test_conflicting_duplicate_does_not_invalidate_successful_record() -> None:
    async def handler(arguments, execution_context):
        return {"id": arguments.order_id, "status": "CANCELLED"}

    runtime, store = runtime_for(definition(handler, read_only=False))
    await execute(runtime, "cancel_order", {"order_id": 7})
    with pytest.raises(DuplicateToolCallConflict):
        await execute(runtime, "cancel_order", {"order_id": 8})
    assert store.failed_keys == []
    assert await execute(runtime, "cancel_order", {"order_id": 7}) == {
        "id": 7,
        "status": "CANCELLED",
    }


@pytest.mark.asyncio
async def test_non_retryable_business_error_is_not_retried() -> None:
    attempts = 0

    async def handler(arguments, execution_context):
        nonlocal attempts
        attempts += 1
        raise BusinessConflict("not cancellable")

    runtime, _ = runtime_for(definition(handler, attempts=3))
    with pytest.raises(BusinessConflict, match="not cancellable"):
        await execute(runtime, "get_order", {"order_id": 7})
    assert attempts == 1


@pytest.mark.asyncio
async def test_l3_policy_denies_even_authorized_manager() -> None:
    called = False

    async def handler(arguments, execution_context):
        nonlocal called
        called = True
        return {}

    tool = ToolDefinition(
        name="refund_order",
        description="refund",
        input_schema=OrderInput,
        handler=handler,
        risk_level=ToolRiskLevel.L3,
        required_permission="refund:approve",
        timeout_seconds=1,
        retry_policy=RetryPolicy(),
        idempotent=True,
        read_only=False,
    )
    runtime, _ = runtime_for(tool)
    with pytest.raises(ToolPolicyDenied, match="M5 approval"):
        await runtime.execute(
            tool_name="refund_order",
            arguments={"order_id": 1},
            context=context(PrincipalRole.MANAGER),
            tool_call_id="refund-1",
        )
    assert called is False


@pytest.mark.asyncio
async def test_failed_verification_is_audited() -> None:
    async def handler(arguments, execution_context):
        return {"id": arguments.order_id}

    async def verifier(arguments, result, execution_context):
        return False

    tool = definition(handler)
    tool = ToolDefinition(**{**tool.__dict__, "verifier": verifier})
    runtime, store = runtime_for(tool)
    result = await execute(runtime, "get_order", {"order_id": 2})
    verified = await runtime.verify(
        tool_name="get_order",
        arguments={"order_id": 2},
        result=result,
        context=context(),
        tool_call_id="c1",
    )
    assert verified is False
    assert store.calls["c1"]["status"] is ToolCallStatus.FAILED
    assert store.calls["c1"]["error_code"] == "tool_verification_failed"


def test_sensitive_nested_arguments_are_redacted() -> None:
    assert redact_arguments(
        {"order_id": 1, "metadata": {"access_token": "secret"}, "items": [{"api_key": "x"}]}
    ) == {
        "order_id": 1,
        "metadata": {"access_token": "[REDACTED]"},
        "items": [{"api_key": "[REDACTED]"}],
    }


def test_result_summary_excludes_customer_pii() -> None:
    summary = summarize_result({"id": 1, "status": "ACTIVE", "email": "private@example.com"})
    assert "private@example.com" not in summary
    assert '"field_count": 3' in summary
