from time import monotonic
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.contextvars import bound_contextvars

from app.agent.interfaces import ToolAdapter
from app.mcp.client import LogisticsClient
from app.observability import get_logger, start_span
from app.policy.retriever import ChromaPolicyRetriever
from app.tool_runtime.catalog import BusinessToolCatalog
from app.tool_runtime.models import ToolExecutionContext
from app.tool_runtime.runtime import ToolRuntime
from app.tool_runtime.store import DatabaseToolRuntimeStore

logger = get_logger(__name__)


class RuntimeToolAdapter(ToolAdapter):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        policy_retriever: ChromaPolicyRetriever | None = None,
        logistics_client: LogisticsClient | None = None,
    ) -> None:
        catalog = BusinessToolCatalog(
            session_factory,
            policy_retriever=policy_retriever,
            logistics_client=logistics_client,
        )
        self.runtime = ToolRuntime(
            registry=catalog.build_registry(),
            store=DatabaseToolRuntimeStore(session_factory),
        )

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]:
        started = monotonic()
        with (
            bound_contextvars(
                run_id=context.agent_run_id,
                ticket_id=context.ticket_id,
                tool_call_id=tool_call_id,
                approval_id=context.approval_id,
            ),
            start_span(
                "tool.execute",
                tool_name=tool_name,
                run_id=context.agent_run_id,
                ticket_id=context.ticket_id,
                tool_call_id=tool_call_id,
                approval_id=context.approval_id,
            ),
        ):
            try:
                result = await self.runtime.execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    context=context,
                    tool_call_id=tool_call_id,
                )
            except Exception as exc:
                logger.exception(
                    "tool_failed",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise
            logger.info(
                "tool_completed",
                tool=tool_name,
                latency_ms=_elapsed_ms(started),
            )
            return result

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool:
        with start_span(
            "tool.verify",
            tool_name=tool_name,
            run_id=context.agent_run_id,
            ticket_id=context.ticket_id,
            tool_call_id=tool_call_id,
        ):
            return await self.runtime.verify(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                context=context,
                tool_call_id=tool_call_id,
            )


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
