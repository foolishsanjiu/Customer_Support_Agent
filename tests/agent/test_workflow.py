from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.cancellation import AgentRunCancellation
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
from app.context.models import AgentContext, RelevantTool
from app.core.errors import ExternalServiceUnavailable, MCPToolError
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
        self.cancel_requested = False

    async def cancellation_requested(self, run_id: int) -> bool:
        return self.cancel_requested

    async def finalize_cancellation(self, run_id: int) -> bool:
        return self.cancel_requested

    async def load_ticket(
        self, ticket_id: int, customer_id: int, through_message_id: int | None = None
    ) -> list[ChatMessage]:
        return [ChatMessage(role="user", content="test request")]

    async def set_current_node(
        self, run_id: int, node: str, intent: IntentType | None = None
    ) -> None:
        self.nodes.append(node)

    async def complete_run(self, **values: Any) -> None:
        self.completed = values


class FakeMemory:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def remember(self, **values: Any) -> int:
        self.calls.append(values)
        if self.error is not None:
            raise self.error
        return 1


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


class UnavailableResponseLLM(MockLLMClient):
    async def generate(self, messages: list[ChatMessage]) -> str:
        raise ExternalServiceUnavailable("LLM unavailable")


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
        "trigger_message_id": 31,
        "continuation_intent": None,
        "continuation_missing_fields": [],
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
async def test_current_explicit_intent_overrides_previous_clarification() -> None:
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.ORDER_QUERY, confidence=1, order_id=1)]
    )
    workflow = AgentWorkflow(llm=llm, store=FakeStore(), tools=FakeTools(), max_steps=12)
    state = initial_state()
    state["messages"] = [
        ChatMessage(role="user", content="订单9买错了，我想退款"),
        ChatMessage(role="assistant", content="请提供退款原因"),
        ChatMessage(role="user", content="我想查询订单1的信息"),
    ]
    state["continuation_intent"] = IntentType.REFUND.value
    state["continuation_missing_fields"] = ["reason"]

    result = await workflow.understand(state)

    assert result["intent"]["intent"] == IntentType.ORDER_QUERY.value
    prompt = llm.message_batches[0]
    assert prompt[-1] == ChatMessage(role="user", content="我想查询订单1的信息")
    assert all("买错" not in message.content for message in prompt)
    assert "only when the current message directly supplies" in prompt[-2].content


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
async def test_order_list_does_not_require_order_id() -> None:
    store = FakeStore()
    tools = FakeTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.ORDER_LIST, confidence=1)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="list_customer_orders")],
        responses=["你目前共有 2 个订单。"],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert result["final_response"] == "你目前共有 2 个订单。"
    assert tools.execute_calls[0][:2] == ("list_customer_orders", {})
    assert "respond_clarification" not in store.nodes


@pytest.mark.asyncio
async def test_social_acknowledgement_does_not_revisit_verified_refund() -> None:
    class ThankYouStore(FakeStore):
        async def load_ticket(
            self, ticket_id: int, customer_id: int, through_message_id: int | None = None
        ) -> list[ChatMessage]:
            return [
                ChatMessage(role="assistant", content="订单 #2 已退款成功。"),
                ChatMessage(role="user", content="谢谢你"),
            ]

    llm = MockLLMClient(intents=[])
    tools = FakeTools()
    result = await AgentWorkflow(
        llm=llm, store=ThankYouStore(), tools=tools, max_steps=12
    ).graph.ainvoke(initial_state())

    assert result["intent"]["intent"] == "SOCIAL"
    assert result["final_response"] == "不客气！如果还有其他问题，请继续告诉我。"
    assert llm.calls == []
    assert tools.execute_calls == []


@pytest.mark.asyncio
async def test_refund_status_without_order_id_uses_customer_scoped_lookup() -> None:
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.REFUND_STATUS, confidence=1)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_refund_status")],
        responses=["最近一笔退款已成功。"],
    )
    tools = FakeTools()
    result = await AgentWorkflow(
        llm=llm, store=FakeStore(), tools=tools, max_steps=12
    ).graph.ainvoke(initial_state())

    assert result["final_response"] == "最近一笔退款已成功。"
    assert tools.execute_calls[0][:2] == ("get_refund_status", {})
    assert "respond_clarification" not in result


@pytest.mark.asyncio
async def test_chinese_missing_order_prompt_is_customer_friendly() -> None:
    class ChineseStore(FakeStore):
        async def load_ticket(
            self, ticket_id: int, customer_id: int, through_message_id: int | None = None
        ) -> list[ChatMessage]:
            return [ChatMessage(role="user", content="查询我的订单")]

    store = ChineseStore()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.ORDER_QUERY, confidence=1, order_id=None)]
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=FakeTools(), max_steps=12)
    result = await workflow.graph.ainvoke(initial_state())

    assert result["final_response"] == "请提供订单号。"


@pytest.mark.asyncio
async def test_cancellation_stops_at_next_safe_node_boundary() -> None:
    store = FakeStore()
    store.cancel_requested = True
    workflow = AgentWorkflow(
        llm=MockLLMClient(intents=[]),
        store=store,
        tools=FakeTools(),
        max_steps=12,
    )

    with pytest.raises(AgentRunCancellation):
        await workflow.graph.ainvoke(initial_state())

    assert store.nodes == []


@pytest.mark.asyncio
async def test_cancellation_during_tool_execution_defers_until_safe_completion() -> None:
    store = FakeStore()

    class CancellingTools(FakeTools):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            store.cancel_requested = True
            return result

    tools = CancellingTools()
    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.CANCEL_ORDER, confidence=1, order_id=2)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="cancel_order")],
        responses=["Cancellation completed."],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=tools, max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert result["verification_complete"] is True
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
    assert "ORDER_LIST" in guidance.content
    assert "REFUND_STATUS" in guidance.content
    assert "SOCIAL" in guidance.content
    assert "SHIPPING_QUERY" in guidance.content
    assert "delivered" in guidance.content
    assert "has shipped" in guidance.content
    assert "requesting money back is not a refund reason" in guidance.content


@pytest.mark.asyncio
async def test_response_marks_tool_results_as_current_run_only_and_removes_unavailable_offer() -> (
    None
):
    class ShippingStore(FakeStore):
        async def load_ticket(
            self, ticket_id: int, customer_id: int, through_message_id: int | None = None
        ) -> list[ChatMessage]:
            return [ChatMessage(role="user", content="订单1的物流怎么样？")]

    llm = MockLLMClient(
        intents=[TicketIntent(intent=IntentType.SHIPPING_QUERY, confidence=1, order_id=1)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_tracking")],
        responses=["订单仍在运输中。是否需要我为订单发起升级？"],
    )
    result = await AgentWorkflow(
        llm=llm, store=ShippingStore(), tools=FakeTools(), max_steps=12
    ).graph.ainvoke(initial_state())

    response_prompt = llm.message_batches[-1]
    system_text = "\n".join(
        message.content for message in response_prompt if message.role == "system"
    )
    assert "CURRENT RUN ONLY" in system_text
    assert "must not invalidate" in system_text
    assert "must not offer to perform" in system_text
    assert "是否需要我为订单发起升级" not in result["final_response"]
    assert "没有注册可执行该后续操作的工具" in result["final_response"]


def test_contextual_messages_revalidate_checkpoint_deserialized_dicts() -> None:
    state = initial_state()
    state["messages"] = [
        {
            "lc": 2,
            "type": "constructor",
            "id": ["app", "agent", "models", "ChatMessage"],
            "kwargs": {"role": "user", "content": "Refund order 9"},
        }
    ]

    messages = AgentWorkflow._contextual_messages(state)

    assert messages == [ChatMessage(role="user", content="Refund order 9")]


@pytest.mark.asyncio
async def test_plan_exposes_only_context_tool_matching_validated_intent() -> None:
    llm = MockLLMClient(
        intents=[],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_order")],
    )
    workflow = AgentWorkflow(llm=llm, store=FakeStore(), tools=FakeTools(), max_steps=12)
    state = initial_state()
    state["messages"] = [ChatMessage(role="user", content="Check order 2")]
    state["intent"] = TicketIntent(
        intent=IntentType.ORDER_QUERY, confidence=1, order_id=2
    ).model_dump(mode="json")
    state["context"] = AgentContext(
        system_instructions="Use registered tools only.",
        recent_ticket_history=[],
        current_business_state={},
        policy_context=[],
        relevant_tools=[
            RelevantTool(
                name="get_order", description="Read order", input_schema={}, read_only=True
            ),
            RelevantTool(
                name="cancel_order", description="Cancel order", input_schema={}, read_only=False
            ),
        ],
    ).model_dump(mode="json")

    result = await workflow.plan(state)

    assert llm.available_tool_batches == [("get_order",)]
    assert result["pending_tool_calls"][0]["tool_name"] == "get_order"


@pytest.mark.asyncio
async def test_plan_fails_closed_when_context_omits_required_tool() -> None:
    llm = MockLLMClient(intents=[])
    workflow = AgentWorkflow(llm=llm, store=FakeStore(), tools=FakeTools(), max_steps=12)
    state = initial_state()
    state["messages"] = [ChatMessage(role="user", content="Check order 2")]
    state["intent"] = TicketIntent(
        intent=IntentType.ORDER_QUERY, confidence=1, order_id=2
    ).model_dump(mode="json")
    state["context"] = AgentContext(
        system_instructions="Use registered tools only.",
        recent_ticket_history=[],
        current_business_state={},
        policy_context=[],
        relevant_tools=[],
    ).model_dump(mode="json")

    result = await workflow.plan(state)

    assert llm.calls == []
    assert result["pending_tool_calls"] == []
    assert result["errors"] == ["required tool is not available for this intent"]


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


@pytest.mark.asyncio
async def test_verified_tool_result_uses_safe_fallback_when_llm_is_unavailable() -> None:
    store = FakeStore()
    llm = UnavailableResponseLLM(
        intents=[TicketIntent(intent=IntentType.ORDER_QUERY, confidence=1, order_id=7)],
        decisions=[ToolDecision(action=PlanAction.TOOL_CALL, tool_name="get_order")],
    )
    workflow = AgentWorkflow(llm=llm, store=store, tools=FakeTools(), max_steps=12)

    result = await workflow.graph.ainvoke(initial_state())

    assert result["verification_complete"] is True
    assert result["final_response"] == (
        "The request was completed and verified, but response generation is temporarily "
        "unavailable. Please retry later for additional details."
    )
    assert store.completed is not None
    assert store.completed["success"] is True


@pytest.mark.asyncio
async def test_llm_outage_without_verified_result_fails_instead_of_inventing_answer() -> None:
    llm = UnavailableResponseLLM(
        intents=[TicketIntent(intent=IntentType.OTHER, confidence=1)],
        decisions=[ToolDecision(action=PlanAction.ANSWER_DIRECTLY)],
    )
    workflow = AgentWorkflow(llm=llm, store=FakeStore(), tools=FakeTools(), max_steps=12)

    with pytest.raises(ExternalServiceUnavailable, match="LLM unavailable"):
        await workflow.graph.ainvoke(initial_state())


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_error", [None, RuntimeError("memory unavailable")])
async def test_successful_persist_records_memory_without_affecting_run(memory_error) -> None:
    store = FakeStore()
    memory = FakeMemory(memory_error)
    workflow = AgentWorkflow(
        llm=MockLLMClient(intents=[]),
        store=store,
        tools=FakeTools(),
        max_steps=12,
        semantic_memory=memory,
    )
    state = initial_state()
    state.update(
        intent=TicketIntent(intent=IntentType.OTHER, confidence=1).model_dump(mode="json"),
        messages=[ChatMessage(role="user", content="Please use concise English.")],
        final_response="Understood.",
    )

    result = await workflow.persist(state)

    assert result["final_response"] == "Understood."
    assert store.completed is not None
    assert store.completed["success"] is True
    assert memory.calls == [
        {
            "customer_id": 20,
            "ticket_id": 10,
            "messages": [ChatMessage(role="user", content="Please use concise English.")],
            "response": "Understood.",
        }
    ]
