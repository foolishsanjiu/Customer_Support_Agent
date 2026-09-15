from typing import Any
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interfaces import AgentContextBuilder
from app.agent.llm import LLMClient
from app.agent.state import AgentState
from app.agent.store import DatabaseAgentStore
from app.agent.tools import RuntimeToolAdapter
from app.agent.workflow import AgentWorkflow
from app.approvals.service import ApprovalService
from app.context.builder import ContextBuilder
from app.core.config import get_settings
from app.mcp.client import LogisticsMCPClient
from app.policy.embeddings import BGEEmbeddingClient
from app.policy.engine import RiskPolicyEngine
from app.policy.retriever import ChromaPolicyRetriever


class AgentRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        max_steps: int = 12,
        enable_context: bool = True,
        context_builder: AgentContextBuilder | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.store = DatabaseAgentStore(session_factory)
        if enable_context and context_builder is None:
            settings = get_settings()
            retriever = ChromaPolicyRetriever(
                path=settings.chroma_path,
                policy_directory=settings.policy_directory,
                embeddings=BGEEmbeddingClient(
                    settings.embedding_model,
                    settings.embedding_cache_dir,
                ),
                top_k=settings.policy_top_k,
            )
            logistics = LogisticsMCPClient(settings.logistics_mcp_url)
            tools = RuntimeToolAdapter(
                session_factory,
                policy_retriever=retriever,
                logistics_client=logistics,
            )
            context_builder = ContextBuilder(
                session_factory=session_factory,
                policy_retriever=retriever,
                tool_registry=tools.runtime.registry,
                message_limit=settings.context_message_limit,
            )
        else:
            tools = RuntimeToolAdapter(session_factory)
        self.workflow = AgentWorkflow(
            llm=llm,
            store=self.store,
            tools=tools,
            max_steps=max_steps,
            context_builder=context_builder,
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

    async def run_existing(
        self, run_id: int, ticket_id: int, customer_id: int
    ) -> AgentState:
        await self.store.start_run(run_id)
        initial: AgentState = {
            "run_id": run_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
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
            "trace_id": uuid4().hex,
            "step_count": 0,
            "verification_complete": False,
            "needs_more_action": False,
            "context": None,
        }
        try:
            return await self.workflow.graph.ainvoke(
                initial,
                config={
                    "recursion_limit": self.max_steps + 8,
                    "configurable": {"thread_id": str(run_id)},
                },
            )
        except Exception as exc:
            await self.store.fail_run(run_id, exc)
            raise

    async def resume(self, run_id: int, decision: dict[str, Any]) -> AgentState:
        try:
            return await self.workflow.graph.ainvoke(
                Command(resume=decision),
                config={
                    "recursion_limit": self.max_steps + 8,
                    "configurable": {"thread_id": str(run_id)},
                },
            )
        except Exception as exc:
            await self.store.fail_run(run_id, exc)
            raise

    async def recover(self, run_id: int) -> AgentState:
        try:
            return await self.workflow.graph.ainvoke(
                None,
                config={
                    "recursion_limit": self.max_steps + 8,
                    "configurable": {"thread_id": str(run_id)},
                },
            )
        except Exception as exc:
            await self.store.fail_run(run_id, exc)
            raise
