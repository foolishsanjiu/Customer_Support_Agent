import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import DuplicateToolCallConflict
from app.models.enums import IdempotencyStatus, ToolCallStatus
from app.models.tool_call import IdempotencyRecord, ToolCall
from app.tool_runtime.models import ToolDefinition, ToolExecutionContext


@dataclass(frozen=True)
class IdempotencyClaim:
    cached_result: dict[str, Any] | None = None


class ToolRuntimeStore(Protocol):
    async def start_call(
        self,
        *,
        tool_call_id: str,
        definition: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> None: ...

    async def finish_call(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        *,
        result_summary: str | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
    ) -> None: ...

    async def claim_idempotency(
        self, key: str, tool_call_id: str, tool_name: str, request_hash: str
    ) -> IdempotencyClaim: ...

    async def complete_idempotency(self, key: str, result: dict[str, Any]) -> None: ...

    async def fail_idempotency(self, key: str) -> None: ...


class DatabaseToolRuntimeStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def start_call(
        self,
        *,
        tool_call_id: str,
        definition: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> None:
        async with self.session_factory.begin() as session:
            values = {
                "tool_call_id": tool_call_id,
                "agent_run_id": context.agent_run_id,
                "ticket_id": context.ticket_id,
                "trace_id": context.trace_id,
                "tool_name": definition.name,
                "risk_level": definition.risk_level,
                "arguments": arguments,
                "status": ToolCallStatus.RUNNING,
            }
            inserted = await session.execute(
                mysql_insert(ToolCall).values(**values).prefix_with("IGNORE")
            )
            if inserted.rowcount == 1:
                return
            existing = await session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == tool_call_id)
            )
            if existing is None or any(
                (
                    existing.agent_run_id != context.agent_run_id,
                    existing.ticket_id != context.ticket_id,
                    existing.tool_name != definition.name,
                    existing.arguments != arguments,
                )
            ):
                raise DuplicateToolCallConflict(
                    "tool_call_id was already used for a different request"
                )

    async def finish_call(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        *,
        result_summary: str | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            call = await session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == tool_call_id).with_for_update()
            )
            if call is None:
                return
            if call.status is ToolCallStatus.SUCCEEDED and status is ToolCallStatus.EXECUTED:
                return
            call.status = status
            call.result_summary = result_summary
            call.error_code = error_code
            call.latency_ms = latency_ms
            call.completed_at = datetime.now(UTC).replace(tzinfo=None)

    async def claim_idempotency(
        self, key: str, tool_call_id: str, tool_name: str, request_hash: str
    ) -> IdempotencyClaim:
        async with self.session_factory.begin() as session:
            inserted = await session.execute(
                mysql_insert(IdempotencyRecord)
                .values(
                    key=key,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    request_hash=request_hash,
                    status=IdempotencyStatus.IN_PROGRESS,
                )
                .prefix_with("IGNORE")
            )
            if inserted.rowcount == 1:
                return IdempotencyClaim()
            record = await session.get(IdempotencyRecord, key, with_for_update=True)
            if record is None:
                raise DuplicateToolCallConflict("idempotency claim could not be resolved")
            if record.tool_name != tool_name or record.request_hash != request_hash:
                raise DuplicateToolCallConflict(
                    "idempotency key was already used for a different request"
                )
            if record.status is IdempotencyStatus.SUCCEEDED and record.result is not None:
                return IdempotencyClaim(cached_result=record.result)
            raise DuplicateToolCallConflict("idempotent tool call is already in progress or failed")

    async def complete_idempotency(self, key: str, result: dict[str, Any]) -> None:
        async with self.session_factory.begin() as session:
            record = await session.get(IdempotencyRecord, key, with_for_update=True)
            if record is not None:
                record.status = IdempotencyStatus.SUCCEEDED
                record.result = result

    async def fail_idempotency(self, key: str) -> None:
        async with self.session_factory.begin() as session:
            record = await session.get(IdempotencyRecord, key, with_for_update=True)
            if record is not None:
                record.status = IdempotencyStatus.FAILED


def summarize_result(result: dict[str, Any]) -> str:
    safe_keys = {"id", "customer_id", "order_id", "status"}
    summary = {key: value for key, value in result.items() if key in safe_keys}
    summary["field_count"] = len(result)
    return json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)[:2000]
