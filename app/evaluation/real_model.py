from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.llm import LLMCallRecord, OpenAICompatibleClient
from app.agent.models import ChatMessage, IntentType
from app.agent.state import AgentState
from app.agent.workflow import AgentWorkflow
from app.context.models import AgentContext, RelevantTool
from app.evaluation.models import FunctionalCase, FunctionalObservation
from app.models.enums import ApprovalStatus, PolicyDecision, PrincipalRole, ToolRiskLevel
from app.policy.decisions import RiskDecision
from app.tool_runtime.models import ToolExecutionContext


@dataclass(frozen=True)
class CaseCapture:
    observation: FunctionalObservation
    call_records: tuple[LLMCallRecord, ...]


class _EvalStore:
    def __init__(self, case: FunctionalCase) -> None:
        self.case = case

    async def load_ticket(
        self, ticket_id: int, customer_id: int, through_message_id: int | None = None
    ) -> list[ChatMessage]:
        history = [
            ChatMessage.model_validate(message.model_dump())
            for message in self.case.conversation_history
        ]
        return [*history, ChatMessage(role="user", content=self.case.user_message)]

    async def cancellation_requested(self, run_id: int) -> bool:
        return False

    async def finalize_cancellation(self, run_id: int) -> bool:
        return False

    async def set_current_node(self, run_id: int, node: str, intent=None) -> None:
        return None

    async def complete_run(self, **values: Any) -> None:
        return None


class _EvalTools:
    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> dict[str, Any]:
        return {"tool": tool_name, "arguments": arguments, "fixture": True}

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: ToolExecutionContext,
        tool_call_id: str,
    ) -> bool:
        return True


class _EvalPolicy:
    def __init__(self, case: FunctionalCase) -> None:
        self.state = case.initial_state

    async def evaluate(self, tool_name, arguments, context) -> RiskDecision:
        if _is_denied(tool_name, self.state):
            return RiskDecision(
                decision=PolicyDecision.DENY,
                risk_level=ToolRiskLevel.L3 if tool_name == "refund_order" else ToolRiskLevel.L2,
                reason="fixture policy denied the action",
            )
        if tool_name == "refund_order":
            return RiskDecision(
                decision=PolicyDecision.REQUIRE_MANAGER_APPROVAL,
                risk_level=ToolRiskLevel.L3,
                reason="manager approval required",
                required_role=PrincipalRole.MANAGER,
            )
        return RiskDecision(
            decision=PolicyDecision.ALLOW,
            risk_level=ToolRiskLevel.L1,
            reason="fixture policy allowed the action",
        )


class _EvalContextBuilder:
    def __init__(self, case: FunctionalCase) -> None:
        self.case = case

    async def build(self, *, intent, messages, **values) -> AgentContext:
        state = self.case.initial_state
        customer_id = state.get("requester_customer_id", 1)
        business_state: dict[str, Any] = {"customer": {"id": customer_id}}
        order_id = intent.order_id or state.get("order_id")
        if order_id is not None:
            status = state.get("status")
            if status is None:
                status = "DELIVERED"
            delivered_days_ago = state.get("delivered_days_ago", 3)
            delivered_at = (
                datetime.now(UTC) - timedelta(days=delivered_days_ago)
                if status in {"DELIVERED", "REFUNDED"}
                else None
            )
            business_state["order"] = {
                "id": order_id,
                "customer_id": state.get("owner_customer_id", customer_id),
                "status": status,
                "delivered_at": delivered_at.isoformat() if delivered_at else None,
            }
            if status == "REFUNDED":
                business_state["refund"] = {
                    "id": 1,
                    "order_id": order_id,
                    "status": "SUCCESS",
                }
        tool_names = {
            IntentType.ORDER_QUERY: "get_order",
            IntentType.SHIPPING_QUERY: "get_tracking",
            IntentType.CANCEL_ORDER: "cancel_order",
            IntentType.REFUND: "refund_order",
            IntentType.POLICY_QUESTION: "search_policy",
        }
        relevant_tools = []
        if tool_name := tool_names.get(intent.intent):
            relevant_tools.append(
                RelevantTool(
                    name=tool_name,
                    description=f"ResolveX {tool_name} tool",
                    input_schema={},
                    read_only=tool_name in {"get_order", "get_tracking", "search_policy"},
                )
            )
        return AgentContext(
            system_instructions="Use fixture business state as authoritative data.",
            recent_ticket_history=messages,
            current_business_state=business_state,
            policy_context=[],
            relevant_tools=relevant_tools,
        )


class _EvalApprovals:
    async def prepare_refund(self, **values: Any) -> Any:
        return SimpleNamespace(id=1, status=ApprovalStatus.PENDING)

    async def validate_for_execution(self, **values: Any) -> None:
        return None


async def capture_functional_case(
    case: FunctionalCase,
    llm: OpenAICompatibleClient,
    *,
    run_id: int,
    max_steps: int = 12,
) -> CaseCapture:
    first_record = len(llm.call_records)
    workflow = AgentWorkflow(
        llm=llm,
        store=_EvalStore(case),
        tools=_EvalTools(),
        max_steps=max_steps,
        risk_policy=_EvalPolicy(case),
        approvals=_EvalApprovals(),
        context_builder=_EvalContextBuilder(case),
        checkpointer=InMemorySaver(),
    )
    state = await workflow.graph.ainvoke(
        _initial_state(run_id),
        config={
            "recursion_limit": max_steps + 8,
            "configurable": {"thread_id": f"eval-{run_id}"},
        },
    )
    records = tuple(llm.call_records[first_record:])
    intent = state.get("intent") or {}
    calls = state.get("pending_tool_calls", [])
    tools = [call["tool_name"] for call in calls]
    arguments = {call["tool_name"]: call["arguments"] for call in calls}
    observation = FunctionalObservation(
        case_id=case.id,
        actual_intent=IntentType(intent.get("intent", IntentType.OTHER)),
        actual_entities=_actual_entities(intent),
        actual_tools=tools,
        actual_tool_arguments=arguments,
        actual_outcome=state.get("business_outcome") or _outcome(case, intent, tools),
        escalated=False,
        required_approval=state.get("approval_status") == ApprovalStatus.PENDING.value,
        agent_steps=state.get("step_count", 0),
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
    )
    return CaseCapture(observation=observation, call_records=records)


def _initial_state(run_id: int) -> AgentState:
    return {
        "run_id": run_id,
        "ticket_id": run_id,
        "customer_id": 1,
        "messages": [],
        "pending_tool_calls": [],
        "tool_results": [],
        "errors": [],
        "retry_count": 0,
        "trace_id": f"eval-{run_id}",
        "step_count": 0,
        "verification_complete": False,
        "needs_more_action": False,
        "context": None,
        "business_outcome": None,
    }


def _actual_entities(intent: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key in ("order_id", "reason") if (value := intent.get(key)) is not None}


def _is_denied(tool_name: str, state: dict[str, Any]) -> bool:
    if _cross_user(state):
        return True
    if tool_name == "cancel_order":
        return state.get("status") not in {None, "CREATED", "PAID"}
    if tool_name == "refund_order":
        return not state.get("eligible", False)
    return False


def _outcome(case: FunctionalCase, intent: dict[str, Any], tools: list[str]) -> str:
    intent_type = IntentType(intent.get("intent", IntentType.OTHER))
    if (
        intent_type
        in {
            IntentType.ORDER_QUERY,
            IntentType.SHIPPING_QUERY,
            IntentType.CANCEL_ORDER,
            IntentType.REFUND,
        }
        and intent.get("order_id") is None
    ):
        return "clarification_order_id"
    if intent_type is IntentType.REFUND and not (intent.get("reason") or "").strip():
        return "clarification_reason"
    if not tools:
        return "safe_general_response" if intent_type is IntentType.OTHER else "incorrect_plan"

    tool = tools[0]
    state = case.initial_state
    if tool == "get_order":
        return "not_found" if intent.get("order_id") == 999999 else "order_status_returned"
    if tool == "get_tracking":
        return "tracking_returned"
    if tool == "search_policy":
        return "policy_answer_returned"
    if tool == "cancel_order":
        if _cross_user(state):
            return "denied_cross_user"
        return (
            "cancelled"
            if state.get("status") in {None, "CREATED", "PAID"}
            else "denied_illegal_state"
        )
    if tool == "refund_order":
        if _cross_user(state):
            return "denied_cross_user"
        if state.get("status") == "REFUNDED":
            return "denied_already_refunded"
        if state.get("delivered_days_ago", 0) > 30:
            return "denied_outside_window"
        if state.get("status") == "PAID":
            return "denied_not_delivered"
        return "waiting_for_approval" if state.get("eligible") else "denied_ineligible"
    return "incorrect_plan"


def _cross_user(state: dict[str, Any]) -> bool:
    return (
        "owner_customer_id" in state
        and "requester_customer_id" in state
        and state["owner_customer_id"] != state["requester_customer_id"]
    )
