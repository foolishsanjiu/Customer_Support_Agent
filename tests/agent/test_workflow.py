from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

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
from app.context.models import AgentContext
from app.core.errors import MCPToolError
from app.models.enums import (
    ApprovalStatus,
    PolicyDecision,
    PrincipalRole,
    ToolRiskLevel,
)
from app.policy.decisions import RiskDecision
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
        return {"id": arguments.get("order_id", 1), "status": "CANCELLED"}

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


class FailingMCPTools(FakeTools):
    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]:
        raise MCPToolError("logistics unavailable")


class FakeRefundPolicy:
    async def evaluate(self, tool_name, arguments, context) -> RiskDecision:
        return RiskDecision(
            decision=PolicyDecision.REQUIRE_MANAGER_APPROVAL,
            risk_level=ToolRiskLevel.L3,
            reason="manager approval required",
            required_role=PrincipalRole.MANAGER,
        )


class FakeApprovals:
    def __init__(self) -> None:
        self.validations: list[int] = []

    async def prepare_refund(self, **values: Any) -> Any:
        return SimpleNamespace(id=77, status=ApprovalStatus.PENDING)

    async def validate_for_execution(self, **values: Any) -> None:
        self.validations.append(values["approval_id"])


class FakeContextBuilder:
    def __init__(self, business_state: dict[str, Any]) -> None:
        self.business_state = business_state

    async def build(self, **values: Any) -> AgentContext:
        return AgentContext(
            system_instructions="Use authoritative fixture state.",
            recent_ticket_history=[],
            current_business_state=self.business_state,
            policy_context=[],
            relevant_tools=[],
        )


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
        "approval_id": None,
        "final_response": None,
        "errors": [],
        "retry_count": 0,
        "trace_id": "test-trace",
        "step_count": 0,
        "verification_complete": False,
        "needs_more_action": False,
        "context": None,
        "business_outcome": None,
    }


def test_guard_routes_fail_closed_on_controlled_errors() -> None:
    assert AgentWorkflow.route_after_policy({"errors": ["denied"]}) == "respond"
    assert AgentWorkflow.route_after_prepare_approval({"errors": ["unavailable"]}) == "respond"
    assert AgentWorkflow.route_after_revalidation({"errors": ["changed"]}) == "respond"


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
async def test_understanding_prompt_defines_order_and_shipping_boundary() -> None:
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.ORDER_QUERY, confidence=1, order_id=2)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_order")],
        responses=["Order status returned."],
    )
    workflow = AgentWorkflow(llm=llm, store=FakeStore(), tools=FakeTools(), max_steps=12)

    await workflow.graph.ainvoke(initial_state())

    guidance = llm.message_batches[0][0]
    assert guidance.role == "system"
    assert "ORDER_QUERY" in guidance.content
    assert "SHIPPING_QUERY" in guidance.content
    assert "delivered" in guidance.content
    assert "has shipped" in guidance.content
    assert "requesting money back is not a refund reason" in guidance.content


@pytest.mark.asyncio
async def test_refund_preflight_denies_already_refunded_before_reason_prompt() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(intents=[TicketIntent(intent=IntentType.REFUND, confidence=1, order_id=4)])
    workflow = AgentWorkflow(
        llm=llm,
        store=store,
        tools=tools,
        max_steps=12,
        context_builder=FakeContextBuilder(
            {
                "customer": {"id": 20},
                "order": {"id": 4, "customer_id": 20, "status": "REFUNDED"},
                "refund": {"id": 1, "order_id": 4, "status": "SUCCESS"},
            }
        ),
    )

    result = await workflow.graph.ainvoke(initial_state())

    assert result["business_outcome"] == "denied_already_refunded"
    assert "already been refunded" in result["final_response"]
    assert tools.execute_calls == []
    assert llm.calls == ["structured_output"]
    assert store.completed["success"] is True
    assert store.completed["error_message"] is None


@pytest.mark.asyncio
async def test_refund_preflight_requires_reason_only_for_potentially_eligible_order() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(intents=[TicketIntent(intent=IntentType.REFUND, confidence=1, order_id=9)])
    workflow = AgentWorkflow(
        llm=llm,
        store=store,
        tools=tools,
        max_steps=12,
        context_builder=FakeContextBuilder(
            {
                "customer": {"id": 20},
                "order": {
                    "id": 9,
                    "customer_id": 20,
                    "status": "DELIVERED",
                    "delivered_at": "2099-01-01T00:00:00",
                },
            }
        ),
    )

    result = await workflow.graph.ainvoke(initial_state())

    assert result["business_outcome"] is None
    assert result["final_response"] == "Please provide: reason."
    assert tools.execute_calls == []


@pytest.mark.asyncio
async def test_refund_preflight_denies_expired_window_without_reason() -> None:
    llm = MockLLMClient(intents=[TicketIntent(intent=IntentType.REFUND, confidence=1, order_id=5)])
    workflow = AgentWorkflow(
        llm=llm,
        store=FakeStore(),
        tools=FakeTools(),
        max_steps=12,
        context_builder=FakeContextBuilder(
            {
                "customer": {"id": 20},
                "order": {
                    "id": 5,
                    "customer_id": 20,
                    "status": "DELIVERED",
                    "delivered_at": "2020-01-01T00:00:00",
                },
            }
        ),
    )

    result = await workflow.graph.ainvoke(initial_state())

    assert result["business_outcome"] == "denied_outside_window"
    assert "30-day refund window" in result["final_response"]


@pytest.mark.asyncio
async def test_refund_preflight_denies_invalid_delivery_time_without_crashing() -> None:
    llm = MockLLMClient(intents=[TicketIntent(intent=IntentType.REFUND, confidence=1, order_id=6)])
    workflow = AgentWorkflow(
        llm=llm,
        store=FakeStore(),
        tools=FakeTools(),
        max_steps=12,
        context_builder=FakeContextBuilder(
            {
                "customer": {"id": 20},
                "order": {
                    "id": 6,
                    "customer_id": 20,
                    "status": "DELIVERED",
                    "delivered_at": "not-a-date",
                },
            }
        ),
    )

    result = await workflow.graph.ainvoke(initial_state())

    assert result["business_outcome"] == "denied_not_delivered"
    assert result["final_response"] == "The order has no valid delivery time."


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
async def test_refund_interrupts_then_resumes_only_after_approval() -> None:
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
        responses=["Refund was approved and verified."],
    )
    approvals = FakeApprovals()
    workflow = AgentWorkflow(
        llm=llm,
        store=store,
        tools=tools,
        max_steps=12,
        risk_policy=FakeRefundPolicy(),
        approvals=approvals,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "refund-test"}}

    interrupted = await workflow.graph.ainvoke(initial_state(), config=config)

    assert tools.execute_calls == []
    assert interrupted["approval_id"] == 77
    assert interrupted["approval_status"] == ApprovalStatus.PENDING.value
    assert store.completed is None

    result = await workflow.graph.ainvoke(
        Command(resume={"approval_id": 77, "status": ApprovalStatus.APPROVED.value}),
        config=config,
    )

    assert len(tools.execute_calls) == 1
    assert tools.execute_calls[0][0] == "refund_order"
    assert tools.execute_calls[0][2].approval_id == 77
    assert approvals.validations == [77]
    assert result["approval_status"] == ApprovalStatus.APPROVED.value
    assert store.completed is not None


@pytest.mark.asyncio
async def test_rejected_refund_resumes_without_executing_tool() -> None:
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
    workflow = AgentWorkflow(
        llm=llm,
        store=store,
        tools=tools,
        max_steps=12,
        risk_policy=FakeRefundPolicy(),
        approvals=FakeApprovals(),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "rejected-refund"}}
    await workflow.graph.ainvoke(initial_state(), config=config)
    result = await workflow.graph.ainvoke(
        Command(resume={"approval_id": 77, "status": ApprovalStatus.REJECTED.value}),
        config=config,
    )
    assert tools.execute_calls == []
    assert "rejected" in result["final_response"]
    assert store.completed is not None


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


@pytest.mark.asyncio
async def test_policy_question_uses_registered_policy_tool() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.POLICY_QUESTION, confidence=1)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="search_policy")],
        responses=["Policy answer based on retrieved context."],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert tools.execute_calls[0][0] == "search_policy"
    assert tools.execute_calls[0][1] == {"query": "test request"}
    assert result["final_response"] == "Policy answer based on retrieved context."


@pytest.mark.asyncio
async def test_mcp_failure_maps_to_controlled_agent_result() -> None:
    store = FakeStore()
    tools = FailingMCPTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.SHIPPING_QUERY, confidence=1, order_id=7)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_tracking")],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert result["tool_results"] == [
        {"tool_name": "get_tracking", "ok": False, "error": "MCPToolError"}
    ]
    assert result["final_response"] == "Request could not be completed: logistics unavailable"
    assert store.completed is not None
    assert store.completed["success"] is False
