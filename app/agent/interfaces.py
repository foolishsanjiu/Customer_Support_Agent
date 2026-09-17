from typing import Any, Protocol

from app.agent.models import ChatMessage, IntentType, TicketIntent
from app.context.models import AgentContext, ConversationWindow
from app.memory.models import SemanticMemoryMatch
from app.models.agent_run import AgentRun
from app.models.enums import PrincipalRole
from app.policy.decisions import RiskDecision
from app.tool_runtime.models import ToolExecutionContext


class AgentStore(Protocol):
    async def create_run(self, ticket_id: int, customer_id: int | None = None) -> AgentRun: ...

    async def load_ticket(self, ticket_id: int, customer_id: int) -> list[ChatMessage]: ...

    async def cancellation_requested(self, run_id: int) -> bool: ...

    async def finalize_cancellation(self, run_id: int) -> bool: ...

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

    async def fail_run(self, run_id: int, error: Exception) -> bool: ...


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


class AgentContextBuilder(Protocol):
    async def build(
        self,
        *,
        ticket_id: int,
        customer_id: int,
        intent: TicketIntent,
        messages: list[ChatMessage],
        conversation_summary: str | None = None,
        role: PrincipalRole = PrincipalRole.CUSTOMER,
    ) -> AgentContext: ...


class ConversationSummarizer(Protocol):
    async def compact(self, ticket_id: int, customer_id: int) -> ConversationWindow: ...


class SemanticMemory(Protocol):
    async def remember(
        self,
        *,
        customer_id: int,
        ticket_id: int,
        messages: list[ChatMessage],
        response: str,
    ) -> int: ...

    async def search(self, *, customer_id: int, query: str) -> list[SemanticMemoryMatch]: ...


class RiskPolicy(Protocol):
    async def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> RiskDecision: ...


class ApprovalCoordinator(Protocol):
    async def prepare_refund(
        self,
        *,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
        reason: str,
    ) -> Any: ...

    async def validate_for_execution(
        self,
        *,
        approval_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> None: ...
