import json
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.interfaces import (
    AgentContextBuilder,
    AgentStore,
    ApprovalCoordinator,
    RiskPolicy,
    ToolAdapter,
)
from app.agent.llm import LLMClient
from app.agent.models import ChatMessage, IntentType, PlanAction, TicketIntent
from app.agent.state import AgentState
from app.context.models import AgentContext
from app.models.enums import ApprovalStatus, PolicyDecision, PrincipalRole
from app.tool_runtime.models import ToolExecutionContext

REQUIRED_ORDER_INTENTS = {
    IntentType.ORDER_QUERY,
    IntentType.SHIPPING_QUERY,
    IntentType.CANCEL_ORDER,
    IntentType.REFUND,
}
EXPECTED_TOOLS = {
    IntentType.ORDER_QUERY: "get_order",
    IntentType.SHIPPING_QUERY: "get_tracking",
    IntentType.CANCEL_ORDER: "cancel_order",
    IntentType.POLICY_QUESTION: "search_policy",
    IntentType.REFUND: "refund_order",
}


class AgentStepLimitExceeded(RuntimeError):
    pass


class AgentWorkflow:
    def __init__(
        self,
        *,
        llm: LLMClient,
        store: AgentStore,
        tools: ToolAdapter,
        max_steps: int,
        context_builder: AgentContextBuilder | None = None,
        risk_policy: RiskPolicy | None = None,
        approvals: ApprovalCoordinator | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.llm = llm
        self.store = store
        self.tools = tools
        self.max_steps = max_steps
        self.context_builder = context_builder
        self.risk_policy = risk_policy
        self.approvals = approvals
        self.checkpointer = checkpointer
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("load_ticket", self.load_ticket)
        builder.add_node("understand", self.understand)
        builder.add_node("build_context", self.build_context)
        builder.add_node("validate_request", self.validate_request)
        builder.add_node("respond_clarification", self.respond_clarification)
        builder.add_node("plan", self.plan)
        builder.add_node("policy_check", self.policy_check)
        builder.add_node("prepare_approval", self.prepare_approval)
        builder.add_node("wait_for_approval", self.wait_for_approval)
        builder.add_node("revalidate_approval", self.revalidate_approval)
        builder.add_node("execute_tool", self.execute_tool)
        builder.add_node("verify", self.verify)
        builder.add_node("respond", self.respond)
        builder.add_node("persist", self.persist)

        builder.add_edge(START, "load_ticket")
        builder.add_edge("load_ticket", "understand")
        builder.add_edge("understand", "build_context")
        builder.add_edge("build_context", "validate_request")
        builder.add_conditional_edges(
            "validate_request",
            self.route_after_validation,
            {"clarify": "respond_clarification", "plan": "plan"},
        )
        builder.add_edge("respond_clarification", "persist")
        builder.add_conditional_edges(
            "plan",
            self.route_after_plan,
            {"execute": "policy_check", "respond": "respond"},
        )
        builder.add_conditional_edges(
            "policy_check",
            self.route_after_policy,
            {
                "execute": "execute_tool",
                "approve": "prepare_approval",
                "respond": "respond",
            },
        )
        builder.add_conditional_edges(
            "prepare_approval",
            self.route_after_prepare_approval,
            {"wait": "wait_for_approval", "respond": "respond"},
        )
        builder.add_conditional_edges(
            "wait_for_approval",
            self.route_after_approval,
            {"execute": "revalidate_approval", "respond": "respond"},
        )
        builder.add_conditional_edges(
            "revalidate_approval",
            self.route_after_revalidation,
            {"execute": "execute_tool", "respond": "respond"},
        )
        builder.add_edge("execute_tool", "verify")
        builder.add_conditional_edges(
            "verify",
            self.route_after_verification,
            {"plan": "plan", "respond": "respond"},
        )
        builder.add_edge("respond", "persist")
        builder.add_edge("persist", END)
        return builder.compile(checkpointer=self.checkpointer)

    async def load_ticket(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "load_ticket")
        messages = await self.store.load_ticket(state["ticket_id"], state["customer_id"])
        return {"messages": messages, "step_count": step_count}

    async def understand(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "understand")
        intent = await self.llm.structured_output(state["messages"], TicketIntent)
        await self.store.set_current_node(state["run_id"], "understand", intent.intent)
        return {"intent": intent.model_dump(mode="json"), "step_count": step_count}

    async def validate_request(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "validate_request")
        intent = self._intent(state)
        missing: list[str] = []
        if intent.intent in REQUIRED_ORDER_INTENTS and intent.order_id is None:
            missing.append("order_id")
        if intent.intent is IntentType.REFUND and not (intent.reason or "").strip():
            missing.append("reason")
        return {
            "plan": {"missing_fields": missing} if missing else None,
            "step_count": step_count,
        }

    async def build_context(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "build_context")
        if self.context_builder is None:
            return {"context": None, "step_count": step_count}
        context = await self.context_builder.build(
            ticket_id=state["ticket_id"],
            customer_id=state["customer_id"],
            intent=self._intent(state),
            messages=state["messages"],
        )
        return {"context": context.model_dump(mode="json"), "step_count": step_count}

    async def respond_clarification(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "respond_clarification")
        missing = (state.get("plan") or {}).get("missing_fields", [])
        return {
            "final_response": "Please provide: " + ", ".join(missing) + ".",
            "step_count": step_count,
        }

    async def plan(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "plan")
        intent = self._intent(state)
        decision = await self.llm.tool_decision(
            self._contextual_messages(state), intent, tuple(EXPECTED_TOOLS.values())
        )

        expected_tool = EXPECTED_TOOLS.get(intent.intent)
        if expected_tool is None:
            if decision.action is not PlanAction.ANSWER_DIRECTLY:
                return self._invalid_plan(step_count, "tool is not allowed for this intent")
            return {
                "plan": decision.model_dump(mode="json"),
                "pending_tool_calls": [],
                "step_count": step_count,
            }

        if decision.action is not PlanAction.TOOL_CALL or decision.tool_name != expected_tool:
            return self._invalid_plan(step_count, "LLM tool decision did not match intent")

        if expected_tool == "search_policy":
            arguments = {"query": state["messages"][-1].content}
        elif expected_tool == "refund_order":
            arguments = {"order_id": intent.order_id, "reason": intent.reason}
        else:
            arguments = {"order_id": intent.order_id}
        tool_call = {
            "tool_call_id": uuid4().hex,
            "tool_name": expected_tool,
            "arguments": arguments,
        }
        return {
            "plan": decision.model_dump(mode="json"),
            "pending_tool_calls": [tool_call],
            "step_count": step_count,
        }

    async def policy_check(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "policy_check")
        call = state["pending_tool_calls"][0]
        if self.risk_policy is None:
            if call["tool_name"] == "refund_order":
                return {
                    "risk_level": "L3",
                    "errors": ["approval policy is not configured"],
                    "step_count": step_count,
                }
            return {"risk_level": "L1", "step_count": step_count}
        decision = await self.risk_policy.evaluate(
            call["tool_name"], call["arguments"], self._tool_context(state)
        )
        updates: dict[str, Any] = {
            "risk_level": decision.risk_level.value,
            "plan": {**(state.get("plan") or {}), "policy": decision.model_dump(mode="json")},
            "step_count": step_count,
        }
        if decision.decision is PolicyDecision.DENY:
            updates["errors"] = [*state.get("errors", []), decision.reason]
        return updates

    async def prepare_approval(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "prepare_approval")
        if self.approvals is None:
            return {
                "errors": [*state.get("errors", []), "approval service is not configured"],
                "step_count": step_count,
            }
        call = state["pending_tool_calls"][0]
        approval = await self.approvals.prepare_refund(
            arguments=call["arguments"],
            context=self._tool_context(state),
            tool_call_id=call["tool_call_id"],
            reason=(state.get("plan") or {}).get("policy", {}).get("reason", "approval required"),
        )
        return {
            "approval_id": approval.id,
            "approval_status": approval.status.value,
            "step_count": step_count,
        }

    async def wait_for_approval(self, state: AgentState) -> dict[str, Any]:
        await self.store.set_current_node(state["run_id"], "wait_for_approval")
        decision = interrupt(
            {
                "approval_id": state["approval_id"],
                "required_role": PrincipalRole.MANAGER.value,
            }
        )
        if not isinstance(decision, dict) or decision.get("approval_id") != state["approval_id"]:
            return {
                "approval_status": ApprovalStatus.CANCELLED.value,
                "errors": [*state.get("errors", []), "invalid approval resume payload"],
            }
        status = ApprovalStatus(decision.get("status"))
        if status is ApprovalStatus.REJECTED:
            return {
                "approval_status": status.value,
                "final_response": "The refund request was rejected by an authorized approver.",
            }
        if status is not ApprovalStatus.APPROVED:
            return {
                "approval_status": status.value,
                "errors": [*state.get("errors", []), "approval is not executable"],
            }
        return {"approval_status": status.value}

    async def revalidate_approval(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "revalidate_approval")
        if self.approvals is None or state.get("approval_id") is None:
            return {
                "errors": [*state.get("errors", []), "approval is unavailable"],
                "step_count": step_count,
            }
        call = state["pending_tool_calls"][0]
        await self.approvals.validate_for_execution(
            approval_id=state["approval_id"],
            tool_name=call["tool_name"],
            arguments=call["arguments"],
            context=self._tool_context(state),
            tool_call_id=call["tool_call_id"],
        )
        return {"step_count": step_count}

    async def execute_tool(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "execute_tool")
        call = state["pending_tool_calls"][0]
        try:
            result = await self.tools.execute(
                call["tool_name"],
                call["arguments"],
                self._tool_context(state),
                call["tool_call_id"],
            )
            result_record = {"tool_name": call["tool_name"], "ok": True, "data": result}
            updates: dict[str, Any] = {
                "tool_results": [result_record],
                "step_count": step_count,
            }
            if call["tool_name"] in {"get_order", "cancel_order"}:
                updates["order"] = result
            elif call["tool_name"] in {"get_shipping", "get_tracking"}:
                updates["shipment"] = result
            return updates
        except Exception as exc:
            result_record = {
                "tool_name": call["tool_name"],
                "ok": False,
                "error": type(exc).__name__,
            }
            return {
                "tool_results": [result_record],
                "errors": [*state.get("errors", []), str(exc)],
                "verification_complete": False,
                "needs_more_action": False,
                "step_count": step_count,
            }

    async def verify(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "verify")
        call = state["pending_tool_calls"][0]
        result_record = state["tool_results"][0]
        if not result_record["ok"]:
            return {
                "verification_complete": False,
                "needs_more_action": False,
                "step_count": step_count,
            }
        verified = await self.tools.verify(
            call["tool_name"],
            call["arguments"],
            result_record["data"],
            self._tool_context(state),
            call["tool_call_id"],
        )
        if verified:
            return {
                "verification_complete": True,
                "needs_more_action": False,
                "step_count": step_count,
            }
        if call["tool_name"] != "cancel_order" and state.get("retry_count", 0) < 1:
            return {
                "verification_complete": False,
                "needs_more_action": True,
                "retry_count": state.get("retry_count", 0) + 1,
                "step_count": step_count,
            }
        return {
            "verification_complete": False,
            "needs_more_action": False,
            "errors": [*state.get("errors", []), "business outcome verification failed"],
            "step_count": step_count,
        }

    async def respond(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "respond")
        if state.get("final_response"):
            return {"step_count": step_count}
        if state.get("errors"):
            return {
                "final_response": "Request could not be completed: " + state["errors"][-1],
                "step_count": step_count,
            }
        context = ChatMessage(
            role="system",
            content="Verified tool results: "
            + json.dumps(state.get("tool_results", []), ensure_ascii=False),
        )
        response = await self.llm.generate([*self._contextual_messages(state), context])
        return {"final_response": response, "step_count": step_count}

    async def persist(self, state: AgentState) -> dict[str, Any]:
        step_count = await self._enter(state, "persist")
        intent = self._intent(state)
        errors = state.get("errors", [])
        response = state.get("final_response") or "No response generated."
        await self.store.complete_run(
            run_id=state["run_id"],
            ticket_id=state["ticket_id"],
            response=response,
            intent=intent.intent,
            success=not errors,
            error_message=errors[-1] if errors else None,
        )
        return {"step_count": step_count, "final_response": response}

    @staticmethod
    def route_after_validation(state: AgentState) -> Literal["clarify", "plan"]:
        return "clarify" if (state.get("plan") or {}).get("missing_fields") else "plan"

    @staticmethod
    def route_after_plan(state: AgentState) -> Literal["execute", "respond"]:
        return "execute" if state.get("pending_tool_calls") else "respond"

    @staticmethod
    def route_after_policy(state: AgentState) -> Literal["execute", "approve", "respond"]:
        if state.get("errors"):
            return "respond"
        decision = (state.get("plan") or {}).get("policy", {}).get("decision")
        if decision in {
            PolicyDecision.REQUIRE_APPROVAL.value,
            PolicyDecision.REQUIRE_MANAGER_APPROVAL.value,
        }:
            return "approve"
        return "execute"

    @staticmethod
    def route_after_prepare_approval(state: AgentState) -> Literal["wait", "respond"]:
        return "respond" if state.get("errors") else "wait"

    @staticmethod
    def route_after_approval(state: AgentState) -> Literal["execute", "respond"]:
        return (
            "execute"
            if state.get("approval_status") == ApprovalStatus.APPROVED.value
            else "respond"
        )

    @staticmethod
    def route_after_revalidation(state: AgentState) -> Literal["execute", "respond"]:
        return "respond" if state.get("errors") else "execute"

    @staticmethod
    def route_after_verification(state: AgentState) -> Literal["plan", "respond"]:
        return "plan" if state.get("needs_more_action") else "respond"

    async def _enter(self, state: AgentState, node: str) -> int:
        step_count = state.get("step_count", 0) + 1
        if step_count > self.max_steps:
            raise AgentStepLimitExceeded(f"agent exceeded MAX_AGENT_STEPS={self.max_steps}")
        await self.store.set_current_node(state["run_id"], node)
        return step_count

    @staticmethod
    def _intent(state: AgentState) -> TicketIntent:
        return TicketIntent.model_validate(state["intent"])

    @staticmethod
    def _tool_context(state: AgentState) -> ToolExecutionContext:
        return ToolExecutionContext(
            principal_id=str(state["customer_id"]),
            customer_id=state["customer_id"],
            role=PrincipalRole.CUSTOMER,
            ticket_id=state["ticket_id"],
            agent_run_id=state["run_id"],
            trace_id=state["trace_id"],
            approval_id=state.get("approval_id"),
        )

    @staticmethod
    def _contextual_messages(state: AgentState) -> list[ChatMessage]:
        context = state.get("context")
        if context is None:
            return state["messages"]
        return [
            AgentContext.model_validate(context).as_system_message(),
            *state["messages"],
        ]

    @staticmethod
    def _invalid_plan(step_count: int, message: str) -> dict[str, Any]:
        return {
            "plan": {"action": PlanAction.ANSWER_DIRECTLY.value, "reason": message},
            "pending_tool_calls": [],
            "errors": [message],
            "final_response": "The requested action was not executed.",
            "step_count": step_count,
        }
