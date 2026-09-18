from time import monotonic
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.contextvars import bound_contextvars

from app.agent.cancellation import AgentRunCancellation
from app.agent.interfaces import AgentContextBuilder
from app.agent.llm import LLMClient
from app.agent.state import AgentState
from app.agent.store import DatabaseAgentStore
from app.agent.tools import RuntimeToolAdapter
from app.agent.workflow import AgentWorkflow
from app.approvals.service import ApprovalService
from app.context.builder import ContextAssembler
from app.context.summary import ConversationSummaryService
from app.core.config import get_settings
from app.mcp.client import FulfillmentClient, FulfillmentMCPClient, LogisticsMCPClient
from app.memory import DatabaseMemoryStore, SemanticMemoryService
from app.observability import start_span
from app.observability.metrics import record_agent_run
from app.observability.tracing import current_trace_id
from app.policy.embeddings import BGEEmbeddingClient
from app.policy.engine import RiskPolicyEngine
from app.policy.retriever import ChromaPolicyRetriever
from app.resilience import shared_circuit_breaker


class AgentRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        max_steps: int = 12,
        max_repair_attempts: int = 1,
        enable_context: bool = True,
        context_builder: AgentContextBuilder | None = None,
        fulfillment_client: FulfillmentClient | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.store = DatabaseAgentStore(session_factory)
        conversation_summarizer = None
        semantic_memory = None
        if enable_context:
            settings = get_settings()
            embeddings = BGEEmbeddingClient(
                settings.embedding_model,
                settings.embedding_cache_dir,
            )
            conversation_summarizer = ConversationSummaryService(
                session_factory=session_factory,
                llm=llm,
                recent_message_limit=settings.context_message_limit,
            )
            semantic_memory = SemanticMemoryService(
                store=DatabaseMemoryStore(session_factory),
                embeddings=embeddings,
                llm=llm,
            )
        if enable_context and context_builder is None:
            retriever = ChromaPolicyRetriever(
                path=settings.chroma_path,
                policy_directory=settings.policy_directory,
                embeddings=embeddings,
                top_k=settings.policy_top_k,
            )
            logistics = LogisticsMCPClient(
                settings.logistics_mcp_url,
                circuit_breaker=shared_circuit_breaker(
                    "logistics_mcp",
                    settings.external_circuit_failure_threshold,
                    settings.external_circuit_recovery_seconds,
                ),
            )
            fulfillment = fulfillment_client or FulfillmentMCPClient(
                settings.fulfillment_mcp_url,
                circuit_breaker=shared_circuit_breaker(
                    "fulfillment_mcp",
                    settings.external_circuit_failure_threshold,
                    settings.external_circuit_recovery_seconds,
                ),
            )
            tools = RuntimeToolAdapter(
                session_factory,
                policy_retriever=retriever,
                logistics_client=logistics,
                fulfillment_client=fulfillment,
            )
            context_builder = ContextAssembler(
                session_factory=session_factory,
                policy_retriever=retriever,
                tool_registry=tools.runtime.registry,
                semantic_memory=semantic_memory,
                message_limit=settings.context_message_limit,
                max_estimated_tokens=settings.context_max_estimated_tokens,
            )
        else:
            tools = RuntimeToolAdapter(
                session_factory,
                fulfillment_client=fulfillment_client,
            )
        self.workflow = AgentWorkflow(
            llm=llm,
            store=self.store,
            tools=tools,
            max_steps=max_steps,
            max_repair_attempts=max_repair_attempts,
            context_builder=context_builder,
            conversation_summarizer=conversation_summarizer,
            semantic_memory=semantic_memory,
            risk_policy=RiskPolicyEngine(session_factory),
            approvals=ApprovalService(
                session_factory,
                ttl_minutes=get_settings().approval_ttl_minutes,
            ),
            checkpointer=checkpointer,
        )
        self.max_steps = max_steps

    async def run_ticket(self, ticket_id: int, customer_id: int) -> AgentState:
        run = await self.store.create_run(ticket_id, customer_id)
        return await self.run_existing(run.id, ticket_id, customer_id)

    async def run_existing(self, run_id: int, ticket_id: int, customer_id: int) -> AgentState:
        started = monotonic()
        if not await self.store.start_run(run_id):
            return await self._cancelled_result(run_id, "start", started)
        trace_id = current_trace_id() or uuid4().hex
        (
            trigger_message_id,
            continuation_intent,
            continuation_missing_fields,
        ) = await self.store.get_run_inputs(run_id)
        initial: AgentState = {
            "run_id": run_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "trigger_message_id": trigger_message_id,
            "continuation_intent": continuation_intent,
            "continuation_missing_fields": continuation_missing_fields,
            "messages": [],
            "conversation_summary": None,
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
            "trace_id": trace_id,
            "step_count": 0,
            "verification_complete": False,
            "needs_more_action": False,
            "failure_attribution": None,
            "failure_history": [],
            "context": None,
            "business_outcome": None,
        }
        with (
            bound_contextvars(run_id=run_id, ticket_id=ticket_id, trace_id=trace_id),
            start_span("agent.run", run_id=run_id, ticket_id=ticket_id),
        ):
            try:
                result = await self.workflow.graph.ainvoke(
                    initial,
                    config={
                        "recursion_limit": self.max_steps + 8,
                        "configurable": {"thread_id": str(run_id)},
                    },
                )
            except AgentRunCancellation:
                return await self._cancelled_result(run_id, "start", started)
            except Exception as exc:
                if await self.store.cancellation_requested(run_id):
                    return await self._cancelled_result(run_id, "start", started)
                if not await self.store.fail_run(run_id, exc):
                    return await self._cancelled_result(run_id, "start", started)
                record_agent_run(
                    trigger="start",
                    outcome="failed",
                    duration_seconds=monotonic() - started,
                )
                raise
        record_agent_run(
            trigger="start",
            outcome=_agent_outcome(result),
            duration_seconds=monotonic() - started,
        )
        return result

    async def resume(self, run_id: int, decision: dict[str, Any]) -> AgentState:
        started = monotonic()
        with (
            bound_contextvars(run_id=run_id),
            start_span("agent.resume", run_id=run_id, approval_id=decision.get("approval_id")),
        ):
            try:
                result = await self.workflow.graph.ainvoke(
                    Command(resume=decision),
                    config={
                        "recursion_limit": self.max_steps + 8,
                        "configurable": {"thread_id": str(run_id)},
                    },
                )
            except AgentRunCancellation:
                return await self._cancelled_result(run_id, "resume", started)
            except Exception as exc:
                if await self.store.cancellation_requested(run_id):
                    return await self._cancelled_result(run_id, "resume", started)
                if not await self.store.fail_run(run_id, exc):
                    return await self._cancelled_result(run_id, "resume", started)
                record_agent_run(
                    trigger="resume",
                    outcome="failed",
                    duration_seconds=monotonic() - started,
                )
                raise
        record_agent_run(
            trigger="resume",
            outcome=_agent_outcome(result),
            duration_seconds=monotonic() - started,
        )
        return result

    async def recover(self, run_id: int) -> AgentState:
        started = monotonic()
        with bound_contextvars(run_id=run_id), start_span("agent.recover", run_id=run_id):
            try:
                result = await self.workflow.graph.ainvoke(
                    None,
                    config={
                        "recursion_limit": self.max_steps + 8,
                        "configurable": {"thread_id": str(run_id)},
                    },
                )
            except AgentRunCancellation:
                return await self._cancelled_result(run_id, "recovery", started)
            except Exception as exc:
                if await self.store.cancellation_requested(run_id):
                    return await self._cancelled_result(run_id, "recovery", started)
                if not await self.store.fail_run(run_id, exc):
                    return await self._cancelled_result(run_id, "recovery", started)
                record_agent_run(
                    trigger="recovery",
                    outcome="failed",
                    duration_seconds=monotonic() - started,
                )
                raise
        record_agent_run(
            trigger="recovery",
            outcome=_agent_outcome(result),
            duration_seconds=monotonic() - started,
        )
        return result

    async def _cancelled_result(
        self,
        run_id: int,
        trigger: str,
        started: float,
    ) -> AgentState:
        await self.store.finalize_cancellation(run_id)
        record_agent_run(
            trigger=trigger,
            outcome="cancelled",
            duration_seconds=monotonic() - started,
        )
        return {
            "run_id": run_id,
            "business_outcome": "cancelled",
            "errors": [],
        }


def _agent_outcome(state: AgentState) -> str:
    if state.get("business_outcome") == "cancelled":
        return "cancelled"
    if state.get("errors"):
        return "failed"
    if state.get("approval_status") == "PENDING":
        return "waiting_approval"
    return "succeeded"
