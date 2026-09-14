from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interfaces import ToolAdapter
from app.tool_runtime.catalog import BusinessToolCatalog
from app.tool_runtime.models import ToolExecutionContext
from app.tool_runtime.runtime import ToolRuntime
from app.tool_runtime.store import DatabaseToolRuntimeStore


class RuntimeToolAdapter(ToolAdapter):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        catalog = BusinessToolCatalog(session_factory)
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
        return await self.runtime.execute(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            tool_call_id=tool_call_id,
        )

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool:
        return await self.runtime.verify(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            context=context,
            tool_call_id=tool_call_id,
        )
