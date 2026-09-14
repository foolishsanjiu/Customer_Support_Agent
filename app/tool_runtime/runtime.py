import asyncio
import hashlib
import json
from time import monotonic
from typing import Any

from pydantic import ValidationError

from app.core.errors import (
    InvalidToolArguments,
    ToolExecutionTimeout,
    ToolVerificationFailed,
)
from app.models.enums import ToolCallStatus
from app.tool_runtime.models import ToolExecutionContext
from app.tool_runtime.policy import authenticate, authorize_permission, enforce_policy
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.store import ToolRuntimeStore, summarize_result

SENSITIVE_ARGUMENT_PARTS = ("password", "secret", "token", "api_key", "authorization")


class ToolRuntime:
    def __init__(self, registry: ToolRegistry, store: ToolRuntimeStore) -> None:
        self.registry = registry
        self.store = store

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]:
        definition = self.registry.get(tool_name)
        started = monotonic()
        redacted = redact_arguments(arguments)
        await self.store.start_call(
            tool_call_id=tool_call_id,
            definition=definition,
            arguments=redacted,
            context=context,
        )
        idempotency_key: str | None = None
        idempotency_claimed = False
        try:
            try:
                validated = definition.input_schema.model_validate(arguments)
            except ValidationError as exc:
                raise InvalidToolArguments(str(exc)) from exc
            authenticate(context)
            authorize_permission(definition, context)
            if definition.authorizer is not None:
                await definition.authorizer(validated, context)
            enforce_policy(definition)

            request_hash = _request_hash(tool_name, validated.model_dump(mode="json"))
            if definition.idempotent:
                idempotency_key = f"{context.agent_run_id}:{tool_call_id}"
                claim = await self.store.claim_idempotency(
                    idempotency_key, tool_call_id, tool_name, request_hash
                )
                if claim.cached_result is not None:
                    await self._finish(
                        tool_call_id, ToolCallStatus.EXECUTED, started, claim.cached_result
                    )
                    return claim.cached_result
                idempotency_claimed = True

            result = await self._execute_with_retry(definition, validated, context)
            if idempotency_key is not None:
                await self.store.complete_idempotency(idempotency_key, result)
            await self._finish(tool_call_id, ToolCallStatus.EXECUTED, started, result)
            return result
        except Exception as exc:
            if idempotency_key is not None and idempotency_claimed:
                await self.store.fail_idempotency(idempotency_key)
            code = getattr(exc, "code", type(exc).__name__)
            await self.store.finish_call(
                tool_call_id,
                ToolCallStatus.FAILED,
                error_code=code,
                latency_ms=_elapsed_ms(started),
            )
            raise

    async def verify(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool:
        definition = self.registry.get(tool_name)
        try:
            if definition.verifier is None:
                verified = True
            else:
                validated = definition.input_schema.model_validate(arguments)
                verified = await asyncio.wait_for(
                    definition.verifier(validated, result, context),
                    timeout=definition.timeout_seconds,
                )
        except Exception as exc:
            await self.store.finish_call(
                tool_call_id,
                ToolCallStatus.FAILED,
                error_code=getattr(exc, "code", type(exc).__name__),
            )
            raise
        status = ToolCallStatus.SUCCEEDED if verified else ToolCallStatus.FAILED
        await self.store.finish_call(
            tool_call_id,
            status,
            result_summary=summarize_result(result),
            error_code=None if verified else ToolVerificationFailed.code,
        )
        return verified

    @staticmethod
    async def _execute_with_retry(definition, validated, context) -> dict[str, Any]:
        for attempt in range(1, definition.retry_policy.max_attempts + 1):
            try:
                return await asyncio.wait_for(
                    definition.handler(validated, context),
                    timeout=definition.timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt == definition.retry_policy.max_attempts:
                    raise ToolExecutionTimeout(
                        f"tool timed out after {attempt} attempt(s)"
                    ) from exc
        raise AssertionError("unreachable")

    async def _finish(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        started: float,
        result: dict[str, Any],
    ) -> None:
        await self.store.finish_call(
            tool_call_id,
            status,
            result_summary=summarize_result(result),
            latency_ms=_elapsed_ms(started),
        )


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "[REDACTED]"
        if any(part in key.lower() for part in SENSITIVE_ARGUMENT_PARTS)
        else _redact_value(value)
        for key, value in arguments.items()
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_arguments(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _request_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool_name": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
