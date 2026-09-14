from typing import Any, Protocol

from app.agent.models import ChatMessage, IntentType
from app.models.agent_run import AgentRun
from app.tool_runtime.models import ToolExecutionContext


class AgentStore(Protocol):
    async def create_run(self, ticket_id: int) -> AgentRun: ...

    async def load_ticket(self, ticket_id: int, customer_id: int) -> list[ChatMessage]: ...

    async def set_current_node(
        self, run_id: int, node: str, intent: IntentType | None = None
    ) -> None: ...

    async def complete_run(
        self,
        *,
        run_id: int,
        ticket_id: int,
        response: str,
        intent: IntentType | None,
        success: bool,
        error_message: str | None,
    ) -> None: ...

    async def fail_run(self, run_id: int, error: Exception) -> None: ...


class ToolAdapter(Protocol):
    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]: ...

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool: ...
