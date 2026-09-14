from typing import Any

import pytest

from app.agent.llm import MockLLMClient
from app.agent.models import (
    ChatMessage,
    IntentType,
    PlanAction,
    TicketIntent,
    ToolDecision,
)
from app.agent.state import AgentState
from app.agent.workflow import AgentStepLimitExceeded, AgentWorkflow
from app.tool_runtime.models import ToolExecutionContext


class FakeStore:
    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.completed: dict[str, Any] | None = None

    async def load_ticket(self, ticket_id: int, customer_id: int) -> list[ChatMessage]:
        return [ChatMessage(role="user", content="test request")]

    async def set_current_node(
        self, run_id: int, node: str, intent: IntentType | None = None
    ) -> None:
        self.nodes.append(node)

    async def complete_run(self, **values: Any) -> None:
        self.completed = values


class FakeTools:
    def __init__(self, *, verified: bool = True) -> None:
        self.verified = verified
        self.execute_calls: list[tuple[str, dict[str, Any], ToolExecutionContext, str]] = []
        self.verify_calls: list[str] = []

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]:
        self.execute_calls.append((tool_name, arguments, context, tool_call_id))
        return {"id": arguments["order_id"], "status": "CANCELLED"}

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool:
        self.verify_calls.append(tool_name)
        return self.verified


def initial_state() -> AgentState:
    return {
        "run_id": 1,
        "ticket_id": 10,
        "customer_id": 20,
        "messages": [],
        "intent": None,
        "order": None,
        "shipment": None,
        "plan": None,
        "pending_tool_calls": [],
        "tool_results": [],
        "risk_level": None,
        "approval_status": None,
        "final_response": None,
        "errors": [],
        "retry_count": 0,
        "trace_id": "test-trace",
        "step_count": 0,
        "verification_complete": False,
        "needs_more_action": False,
    }


@pytest.mark.asyncio
async def test_missing_order_routes_to_clarification_without_tool() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.CANCEL_ORDER, confidence=1, order_id=None)]
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert result["final_response"] == "Please provide: order_id."
    assert tools.execute_calls == []
    assert "respond_clarification" in store.nodes
    assert "execute_tool" not in store.nodes
    assert store.completed is not None
    assert store.completed["success"] is True


@pytest.mark.asyncio
async def test_cancel_write_executes_then_verifies() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.CANCEL_ORDER, confidence=1, order_id=7)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="cancel_order")],
        responses=["Order cancellation was verified."],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert len(tools.execute_calls) == 1
    tool_name, arguments, context, tool_call_id = tools.execute_calls[0]
    assert (tool_name, arguments) == ("cancel_order", {"order_id": 7})
    assert context.customer_id == 20
    assert context.agent_run_id == 1
    assert tool_call_id
    assert tools.verify_calls == ["cancel_order"]
    assert store.nodes.index("execute_tool") < store.nodes.index("verify")
    assert result["verification_complete"] is True
    assert result["final_response"] == "Order cancellation was verified."


@pytest.mark.asyncio
async def test_refund_intent_never_executes_m2_tool() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[
            TicketIntent(
                intent=IntentType.REFUND,
                confidence=1,
                order_id=9,
                reason="damaged",
            )
        ],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="refund_order")],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert tools.execute_calls == []
    assert result["pending_tool_calls"] == []
    assert "not executed" in result["final_response"]
    assert store.completed is not None
    assert store.completed["success"] is True


@pytest.mark.asyncio
async def test_tool_decision_must_match_validated_intent() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.ORDER_QUERY, confidence=1, order_id=2)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="cancel_order")],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert tools.execute_calls == []
    assert result["errors"] == ["LLM tool decision did not match intent"]
    assert store.completed is not None
    assert store.completed["success"] is False


@pytest.mark.asyncio
async def test_max_agent_steps_stops_runaway_execution() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(intents=[TicketIntent(intent=IntentType.OTHER, confidence=1)])
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=2)

    with pytest.raises(AgentStepLimitExceeded, match="MAX_AGENT_STEPS=2"):
        await workflow.graph.ainvoke(initial_state())
